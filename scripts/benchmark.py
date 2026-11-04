"""Benchmark of the memory side, with REAL numbers -> results/*.csv.

    .venv\\Scripts\\python scripts\\benchmark.py [--parts resources,screens,edits,moments]

  resources  VRAM / RAM of the local model (average and peak while it answers), GPU use, time to
             first token and total time, local (Muse Glimmer) and cloud (Muse Spark), 3 runs each
  screens    20 screenshots with known facts (drawn here: an error, a chart, a table, a receipt...)
             -> a visual question through Edith's own path (the frame in an isolated memory)
             -> correct if the answer holds the fact
  edits      10 edit-from-memory scenarios (an isolated memory each) -> the proposal must hold the
             facts; time to preview
  moments    30 lines labelled by hand (key moment or not) -> the no-model filter's precision and
             recall, and the local model's on the filter's candidates
Each item is a row in results/<part>.csv; the summary is results/benchmark.csv.
The screenshots and memories are synthetic (drawn and written by this script), not the user's.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
sys.stdout.reconfigure(encoding="utf-8")
FONT = "C:/Windows/Fonts/segoeui.ttf"


def write_csv(name: str, rows: list[dict]) -> None:
    RESULTS.mkdir(exist_ok=True)
    if not rows:
        return
    with open(RESULTS / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def fresh_cfg(tag: str):
    from edith.config import load_config

    cfg = load_config()
    cfg.root = Path(tempfile.mkdtemp(prefix=f"edith-bench-{tag}-"))
    cfg.companion = {**cfg.companion, "learn": False}
    for d in (cfg.frames_dir, cfg.audio_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def router(cfg, local=None):
    from edith.brain import answer, notes
    from edith.ui.agent import text

    return answer.Router(cfg, local, lambda k, **kw: text("es", k, **kw), notes=notes.NoteBook(cfg))


# --- resources -------------------------------------------------------------------------------------

def gpu_sample() -> tuple[float, float]:
    """(MiB used on the GPU, GPU utilization %)."""
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
    used, util = out[0].split(",")
    return float(used), float(util)


def process_ram_mib(name: str) -> float:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True, timeout=10).stdout
    total = 0.0
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 5 and parts[0].lower() == name.lower():
            total += float(parts[4].replace(".", "").replace(",", "").replace("K", "").replace(" ", "")) / 1024
    return total


def resources() -> list[dict]:
    from edith.brain.client import MetaClient
    from edith.brain.local import LocalModel

    cfg = fresh_cfg("res")
    base_vram, _ = gpu_sample()
    local = LocalModel(cfg)
    t0 = time.monotonic()
    local.start()
    local.ensure_started()
    load_s = time.monotonic() - t0
    rows = []
    prompt = ("Eres Edith. Responde en una frase.", "¿Qué es la memoización en programación?")
    for i in range(3):
        samples, stop = [], threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    samples.append((*gpu_sample(), process_ram_mib("llama-server.exe")))
                except Exception:
                    pass
                time.sleep(0.5)
        th = threading.Thread(target=sample, daemon=True)
        th.start()
        first = {}
        t0 = time.monotonic()
        local.generate(prompt[0], prompt[1], max_tokens=120,
                       on_token=lambda p: first.setdefault("t", time.monotonic() - t0))
        total = time.monotonic() - t0
        stop.set()
        th.join()
        vram = [s[0] - base_vram for s in samples] or [0]
        util = [s[1] for s in samples] or [0]
        ram = [s[2] for s in samples] or [0]
        rows.append({"engine": "local Muse Glimmer 30B (2-bit)", "run": i + 1, "ttft_s": round(first.get("t", total), 2),
                     "total_s": round(total, 2), "vram_avg_mib": round(statistics.mean(vram)), "vram_peak_mib": round(max(vram)),
                     "ram_avg_mib": round(statistics.mean(ram)), "ram_peak_mib": round(max(ram)),
                     "gpu_util_avg_pct": round(statistics.mean(util)), "gpu_util_peak_pct": round(max(util)),
                     "load_s": round(load_s, 1) if i == 0 else ""})
    local.stop()
    client = MetaClient(cfg)
    for i in range(3):
        first = {}
        t0 = time.monotonic()
        client.chat_stream("bench", on_token=lambda p: first.setdefault("t", time.monotonic() - t0),
                           messages=[{"role": "system", "content": prompt[0]}, {"role": "user", "content": prompt[1]}],
                           max_tokens=800, reasoning_effort="minimal")
        total = time.monotonic() - t0
        rows.append({"engine": "cloud Muse Spark", "run": i + 1, "ttft_s": round(first.get("t", total), 2),
                     "total_s": round(total, 2), "vram_avg_mib": 0, "vram_peak_mib": 0, "ram_avg_mib": "",
                     "ram_peak_mib": "", "gpu_util_avg_pct": "", "gpu_util_peak_pct": "", "load_s": ""})
    return rows


# --- 20 screenshots ----------------------------------------------------------------------------------

def _img(lines: list[tuple[str, int]], size=(1280, 720), bars: list[tuple[str, int]] | None = None):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, (245, 246, 248))
    d = ImageDraw.Draw(img)
    y = 50
    for text, px in lines:
        d.text((60, y), text, fill=(25, 25, 30), font=ImageFont.truetype(FONT, px))
        y += int(px * 1.6)
    if bars:
        f = ImageFont.truetype(FONT, 22)
        top = max(v for _l, v in bars)
        for i, (label, v) in enumerate(bars):
            x = 120 + i * 210
            h = int(380 * v / top)
            d.rectangle((x, 640 - h, x + 120, 640), fill=(70, 110, 230))
            d.text((x, 650), label, fill=(25, 25, 30), font=f)
            d.text((x + 20, 640 - h - 34), str(v), fill=(25, 25, 30), font=f)
    return img


SCREENS = [
    ("error", [("Python 3.12 — Traceback (most recent call last):", 30), ('File "main.py", line 42, in load', 28),
               ("KeyError: 'customer_id'", 34)], None, "¿qué error salió en la pantalla?", ["customer_id"]),
    ("chart", [("Ventas por trimestre 2026", 38)], [("Q1", 120), ("Q2", 340), ("Q3", 210), ("Q4", 180)],
     "¿qué trimestre tuvo más ventas en la gráfica que vi?", ["q2"]),
    ("table", [("Inventario", 38), ("Producto    Stock    Precio", 28), ("Lápices     240      S/ 1.50", 28),
               ("Cuadernos   18       S/ 6.90", 28), ("Borradores  95       S/ 0.80", 28)], None,
     "¿cuántos cuadernos quedaban en la tabla que se veía?", ["18"]),
    ("receipt", [("BODEGA SAN JUAN", 36), ("Pan x6 ........ 4.20", 28), ("Leche ......... 5.80", 28),
                 ("TOTAL S/ 10.00", 34)], None, "¿cuál era el total del recibo que se veía?", ["10"]),
    ("slide", [("Capítulo 4", 32), ("La fotosíntesis", 60), ("Ocurre en los cloroplastos", 32)], None,
     "¿de qué tema era la diapositiva que se veía?", ["fotos"]),
    ("chat", [("WhatsApp — Mamá", 34), ("Mamá: no te olvides de comprar huevos", 30), ("Tú: ok", 30)], None,
     "¿qué me pidió mi mamá en el mensaje que se veía?", ["huevo"]),
    ("calendar", [("Noviembre 2026", 40), ("Jue 19 · 10:00  Dentista", 30), ("Vie 20 · 16:00  Partido", 30)], None,
     "¿a qué hora era el dentista según el calendario que vi?", ["10"]),
    ("code", [("def area(r):", 34), ("    return 3.14159 * r ** 2", 34), ("print(area(4))", 34)], None,
     "¿qué función se veía en el código de la pantalla?", ["area"]),
    ("weather", [("Lima", 44), ("19 °C · Nublado", 40), ("Humedad 84 %", 30)], None,
     "¿qué temperatura mostraba la pantalla del clima?", ["19"]),
    ("flight", [("Vuelo LA2041 · Lima → Cusco", 36), ("Puerta 12 · Embarque 07:35", 32)], None,
     "¿en qué puerta era el embarque que vi?", ["12"]),
    ("grade", [("Notas — Matemática II", 36), ("Práctica 1: 15", 30), ("Práctica 2: 17", 30), ("Parcial: 12", 30)], None,
     "¿qué nota saqué en el parcial según la pantalla?", ["12"]),
    ("password", [("Wi-Fi: Biblioteca-PUCP", 34), ("Clave temporal: girasol84", 34)], None,
     "¿cómo se llamaba la red wifi que vi?", ["biblioteca"]),
    ("bars2", [("Horas de estudio por día", 36)], [("Lun", 2), ("Mar", 5), ("Mié", 1), ("Jue", 3)],
     "¿qué día estudié más según la gráfica que vi?", ["mar"]),
    ("menu", [("Menú del día", 40), ("Entrada: Papa a la huancaína", 30), ("Fondo: Ají de gallina", 30),
              ("Precio: S/ 14", 30)], None, "¿cuál era el plato de fondo del menú que vi?", ["aj"]),
    ("address", [("Envío a:", 32), ("Av. Universitaria 1801, San Miguel", 32), ("Lima 15088", 30)], None,
     "¿a qué avenida iba el envío que se veía?", ["universitaria"]),
    ("bank", [("Cuenta de ahorros", 34), ("Saldo disponible: S/ 1,284.50", 36)], None,
     "¿cuánto saldo disponible mostraba la pantalla?", ["1,284", "1284"]),
    ("todo", [("Pendientes", 38), ("☐ Imprimir informe", 30), ("☑ Pagar luz", 30), ("☐ Llamar a Rosa", 30)], None,
     "¿a quién tenía que llamar según mis pendientes que vi?", ["rosa"]),
    ("book", [("Biblioteca — préstamo", 34), ("Cien años de soledad", 38), ("Devolver: 27/11/2026", 30)], None,
     "¿qué libro mostraba la pantalla de la biblioteca?", ["cien a"]),
    ("score", [("Final — Copa", 34), ("Perú 2 – 1 Chile", 50)], None, "¿cómo quedó el partido que se veía?", ["2"]),
    ("battery", [("Configuración — Batería", 34), ("Nivel: 23 %", 40), ("Ahorro de energía activado", 28)], None,
     "¿qué porcentaje de batería se veía?", ["23"]),
]


def screens() -> list[dict]:
    from edith.memory.timeline import Timeline

    rows = []
    for name, lines, bars, question, expect in SCREENS:
        cfg = fresh_cfg("screen")
        now = time.time()
        img = _img(lines, bars=bars)
        img.save(cfg.frames_dir / f"{name}.png")
        tl = Timeline(cfg)
        tl.append({"type": "window", "ts": now - 120, "window": f"{name} - Visor", "process": "app.exe"})
        tl.append({"type": "frame", "ts": now - 119, "path": f"frames/{name}.png", "score": 0.5,
                   "window": f"{name} - Visor", "process": "app.exe", "screen": [1280, 720]})
        tl.append({"type": "window", "ts": now - 30, "window": "Escritorio", "process": "explorer.exe"})
        t0 = time.monotonic()
        rep = router(cfg).respond(question)
        ok = any(e in rep.text.lower() for e in expect)
        rows.append({"screen": name, "question": question, "ok": ok, "seconds": round(time.monotonic() - t0, 1),
                     "cost_usd": round(rep.cost, 5), "sent": rep.footnote, "answer": rep.text.replace("\n", " ")[:140]})
        print(f"  {'OK  ' if ok else 'FAIL'} {name:9} {rows[-1]['seconds']:5.1f} s {rep.text[:70]!r}")
    return rows


# --- 10 edit-from-memory scenarios -------------------------------------------------------------------

EDITS = [
    (["El profe dijo que el examen final es el martes 1 de diciembre."], "pon en mis notas cuándo es el examen final",
     "Apuntes", "", ["1 de diciembre", "martes"]),
    (["Para la tarea hay que leer los capítulos 3 y 4 del libro de Stewart."], "anota la tarea de lectura en mis apuntes",
     "Apuntes", "", ["3", "4"]),
    (["Ana: ¿me pasas el monto de la factura de octubre?", "Factura octubre: S/ 1,250.50"],
     "responde a este correo con el monto que vi", "Correo a Ana", "Hola, ¿me pasas el monto de la factura de octubre?",
     ["1,250.50", "1250.50"]),
    (["La reunión con el cliente se movió al jueves a las 3 pm."], "escribe en mis notas el cambio de la reunión",
     "Notas", "", ["jueves", "3"]),
    (["El código de la clase en Classroom es k7x2mq."], "pon el código de Classroom en mis notas", "Notas", "", ["k7x2mq"]),
    (["Dirección de la fiesta: Calle Los Pinos 345, Surco."], "anota la dirección de la fiesta", "Notas", "", ["pinos", "345"]),
    (["Profesora: el ensayo debe tener entre 1500 y 2000 palabras."], "pon en mis apuntes la extensión del ensayo",
     "Apuntes", "", ["1500", "2000"]),
    (["Error en el servidor: timeout al conectar con la base de datos postgres en el puerto 5432."],
     "anota el error que salió", "Bitácora", "", ["5432"]),
    (["Luis: el número de mi cuenta es 191-2345678-0-12"], "responde a Luis confirmando el número de cuenta que vi",
     "Mensaje a Luis", "Luis: ¿te llegó mi número de cuenta?", ["191-2345678-0-12"]),
    (["En el video dijeron que la mitocondria produce ATP."], "pon en mis apuntes lo que dijeron de la mitocondria",
     "Apuntes", "", ["atp"]),
]


def edits() -> list[dict]:
    from edith.brain import edit
    from edith.memory.timeline import Timeline

    rows = []
    for i, (said, request, field, current, expect) in enumerate(EDITS):
        cfg = fresh_cfg("edit")
        tl = Timeline(cfg)
        now = time.time()
        tl.append({"type": "window", "ts": now - 300, "window": "Clase - Navegador", "process": "chrome.exe"})
        for j, s in enumerate(said):
            ts = now - 280 + j * 20
            if j == 0 and i not in (2, 8):
                tl.append({"type": "transcript", "ts": ts, "start": ts, "end": ts + 6, "text": s, "lang": "es"})
            else:
                tl.append({"type": "text", "ts": ts, "window": "Clase - Navegador", "process": "chrome.exe", "text": s})
        tl.append({"type": "window", "ts": now - 20, "window": f"{field} - Editor", "process": "notepad.exe"})
        t0 = time.monotonic()
        rep = router(cfg).respond(request, system=edit.EDIT_PROMPT,
                                  extra=edit.target_block(f"{field} - Editor", "notepad.exe", "EditControl", field, current))
        p = edit.parse(rep.text, time.time())
        got = p if isinstance(p, str) else p.text
        ok = not isinstance(p, str) and any(e in got.lower() for e in expect)
        rows.append({"scenario": i + 1, "request": request, "ok": ok, "seconds_to_preview": round(time.monotonic() - t0, 1),
                     "cost_usd": round(rep.cost, 5), "proposal": got.replace("\n", " / ")[:140]})
        print(f"  {'OK  ' if ok else 'FAIL'} {i + 1:2} {rows[-1]['seconds_to_preview']:5.1f} s {got[:70]!r}")
    return rows


# --- key moments ---------------------------------------------------------------------------------------

LABELLED = [  # (line, spoken?, key moment by hand: kind or None)
    ("El examen parcial es el jueves 19 de noviembre", True, "exam"),
    ("Esto entra en el examen, apúntenlo", True, "exam"),
    ("La tarea de álgebra es para el lunes", True, "task"),
    ("Entreguen el informe antes del viernes 20", True, "deadline"),
    ("Tengo cita con el dentista el martes a las 10", True, "deadline"),
    ("Recuerden que la práctica calificada es mañana", True, "exam"),
    ("Hay que pagar la cuota de la universidad hasta el 30/11", False, "deadline"),
    ("Proyecto final: fecha límite 05/12/2026", False, "deadline"),
    ("Reunión de grupo el miércoles a las 6 pm", True, "deadline"),
    ("Para el jueves hagan los ejercicios 1 al 10", True, "task"),
    ("El quiz incluye los capítulos 2 y 3", True, "exam"),
    ("La entrevista de prácticas es el 25 de noviembre", True, "deadline"),
    ("No olviden traer la calculadora", True, "exam"),
    ("Vence el recibo de luz el 28/11", False, "deadline"),
    ("Tarea: resumen de la lectura para el martes", False, "task"),
    ("Hoy vamos a ver derivadas parciales", True, None),
    ("Buenos días, ¿cómo están?", True, None),
    ("La fotosíntesis ocurre en los cloroplastos", True, None),
    ("Bandeja de entrada (12)", False, None),
    ("Lunes 16 de noviembre de 2026", False, None),
    ("Me gustó mucho la película del sábado", True, None),
    ("Abran el libro en la página 45", True, None),
    ("El clima en Lima: 19 °C", False, None),
    ("Archivo Editar Ver Insertar Formato", False, None),
    ("La mitocondria produce ATP", True, None),
    ("Ayer jugamos fútbol hasta tarde", True, None),
    ("Total a pagar S/ 14.00", False, None),
    ("Pregunten si tienen dudas", True, None),
    ("Capítulo 4: la célula", False, None),
    ("El viernes es feriado", True, None),
]


def moments() -> list[dict]:
    from edith.brain.notes import kind_of

    rows = []
    for line, spoken, label in LABELLED:
        k = kind_of(line, spoken)
        rows.append({"line": line, "spoken": spoken, "label": label or "", "filter": k or "",
                     "filter_hit": bool(k) == bool(label)})
    return rows


def precision_recall(rows: list[dict], col: str) -> tuple[float, float]:
    tp = sum(1 for r in rows if r["label"] and r[col])
    fp = sum(1 for r in rows if not r["label"] and r[col])
    fn = sum(1 for r in rows if r["label"] and not r[col])
    return (tp / (tp + fp) if tp + fp else 0.0), (tp / (tp + fn) if tp + fn else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default="resources,screens,edits,moments")
    args = ap.parse_args()
    os.environ["EDITH_HOME"] = tempfile.mkdtemp(prefix="edith-bench-")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    summary = []
    parts = args.parts.split(",")
    if "moments" in parts:
        rows = moments()
        write_csv("moments", rows)
        p, r = precision_recall(rows, "filter")
        summary += [{"metric": "key moments: filter precision", "value": f"{p:.0%}", "notes": "30 lines labelled by hand"},
                    {"metric": "key moments: filter recall", "value": f"{r:.0%}", "notes": "no model"}]
        print(f"moments: precision {p:.0%}, recall {r:.0%}")
    if "screens" in parts:
        print("screens:")
        rows = screens()
        write_csv("screens", rows)
        acc = sum(r["ok"] for r in rows) / len(rows)
        summary += [{"metric": "20 screenshots Q&A: accuracy", "value": f"{acc:.0%}", "notes": "Muse Spark, visual questions"},
                    {"metric": "20 screenshots Q&A: avg time", "value": f"{statistics.mean(r['seconds'] for r in rows):.1f} s", "notes": ""},
                    {"metric": "20 screenshots Q&A: cost", "value": f"${sum(r['cost_usd'] for r in rows):.4f}", "notes": "total"}]
    if "edits" in parts:
        print("edits:")
        rows = edits()
        write_csv("edits", rows)
        acc = sum(r["ok"] for r in rows) / len(rows)
        summary += [{"metric": "10 edit-from-memory scenarios: correct", "value": f"{acc:.0%}", "notes": ""},
                    {"metric": "10 edit-from-memory scenarios: time to preview", "value":
                     f"{statistics.mean(r['seconds_to_preview'] for r in rows):.1f} s avg", "notes": ""}]
    if "resources" in parts:
        print("resources:")
        rows = resources()
        write_csv("resources", rows)
        for eng in ("local Muse Glimmer 30B (2-bit)", "cloud Muse Spark"):
            rs = [r for r in rows if r["engine"] == eng]
            summary.append({"metric": f"{eng}: time to first token", "value": f"{statistics.mean(r['ttft_s'] for r in rs):.2f} s", "notes": "avg of 3"})
            summary.append({"metric": f"{eng}: total time", "value": f"{statistics.mean(r['total_s'] for r in rs):.2f} s", "notes": "avg of 3"})
        loc = [r for r in rows if r["engine"].startswith("local")]
        summary += [{"metric": "local: VRAM avg / peak", "value": f"{statistics.mean(r['vram_avg_mib'] for r in loc):.0f} / {max(r['vram_peak_mib'] for r in loc)} MiB", "notes": "over the GPU's idle use"},
                    {"metric": "local: RAM avg / peak (llama-server)", "value": f"{statistics.mean(r['ram_avg_mib'] for r in loc):.0f} / {max(r['ram_peak_mib'] for r in loc)} MiB", "notes": ""},
                    {"metric": "local: GPU use avg / peak", "value": f"{statistics.mean(r['gpu_util_avg_pct'] for r in loc):.0f} / {max(r['gpu_util_peak_pct'] for r in loc)} %", "notes": "while answering"},
                    {"metric": "local: load time", "value": f"{loc[0]['load_s']} s", "notes": "start to ready"}]
        for r in rows:
            print(f"  {r['engine']:32} ttft {r['ttft_s']:5} s total {r['total_s']:5} s vram {r['vram_peak_mib']} MiB")
    old = []
    if (RESULTS / "benchmark.csv").exists():
        with open(RESULTS / "benchmark.csv", encoding="utf-8") as f:
            old = [r for r in csv.DictReader(f) if r["metric"] not in {s["metric"] for s in summary}]
    write_csv("benchmark", old + summary)
    for s in summary:
        print(f"{s['metric']}: {s['value']} {s['notes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
