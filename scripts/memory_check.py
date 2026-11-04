"""Memory check: capture -> temporal question -> edit from memory -> undo, in three modes, plus the
real cost of 10 questions. OK/FAIL per step; results also in results/memory_check.csv.

    .venv\\Scripts\\python scripts\\memory_check.py [--modes api,offline,budget] [--no-apply]

The memory is built in an isolated Edith home (never the user's): a class seen 8 minutes ago (a
slide image, its screen text, what the teacher said), then a spreadsheet, then a notes window.
  capture    the timeline holds the class: frame, screen text, transcript
  temporal   "¿qué dijo el profe del examen hace unos minutos?" -> Friday 13 November
  edit       "pon en mis notas lo que dijo el profe del examen y la tarea" -> a proposal with the
             date and the homework (the edit prompt, the notes field as the target)
  apply      the proposal is written into a textarea of a Chromium window this script opens
             (checked by its title before any key; never another window)
  undo       Edith's undo -> the textarea is back to what it was
Modes: api (Muse Spark), offline (the cloud fails: the local Muse Glimmer answers, "Answered
locally"), budget (the month's budget is reached: local only). The 10 questions run in api mode.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

SLIDE = ["Clase 7 · Algoritmos", "Recursión y memoización", "• Caso base y caso recursivo",
         "• Memoización: guardar resultados ya calculados", "Examen: viernes 13 de noviembre"]
SAID = ["Bueno, chicos, empecemos con recursión.",
        "Ojo que esto entra en el examen, que es el viernes 13 de noviembre.",
        "Entra recursión y memoización, nada de grafos todavía.",
        "La tarea es implementar Fibonacci con memoización, para el lunes.",
        "Súbanla al aula virtual antes de las ocho de la noche."]
QUESTIONS = ["hola Edith, ¿cómo estás?", "¿qué dijo el profe del examen hace unos minutos?",
             "¿cuál es la tarea y para cuándo es?", "¿qué estaba en la diapositiva de la clase?",
             "¿qué es la memoización?", "resume la clase en dos líneas",
             "¿qué hice después de la clase?", "¿qué no entra en el examen?",
             "¿a qué hora hay que subir la tarea?", "¿cómo automatizo llenar facturas desde Excel?"]


def build_memory(cfg) -> None:
    from PIL import Image, ImageDraw

    from edith.memory.timeline import Timeline

    for d in (cfg.frames_dir, cfg.audio_dir):
        d.mkdir(parents=True, exist_ok=True)
    tl = Timeline(cfg)
    now = time.time()
    img = Image.new("RGB", (1280, 720), (250, 250, 252))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(SLIDE):
        draw.text((80, 80 + i * 70), line, fill=(20, 20, 30))
    name = f"class-{int(now)}.png"
    img.save(cfg.frames_dir / name)
    t0 = now - 480
    tl.append({"type": "window", "ts": t0, "window": "Clase 7 - Algoritmos - PowerPoint", "process": "POWERPNT.EXE"})
    tl.append({"type": "frame", "ts": t0 + 2, "path": f"frames/{name}", "score": 0.4,
               "window": "Clase 7 - Algoritmos - PowerPoint", "process": "POWERPNT.EXE", "screen": [1280, 720]})
    tl.append({"type": "text", "ts": t0 + 3, "window": "Clase 7 - Algoritmos - PowerPoint", "process": "POWERPNT.EXE",
               "text": "\n".join(SLIDE)})
    for i, said in enumerate(SAID):
        ts = t0 + 20 + i * 40
        tl.append({"type": "transcript", "ts": ts, "start": ts, "end": ts + 8, "text": said, "lang": "es"})
    tl.append({"type": "window", "ts": now - 200, "window": "invoices.xlsx - Excel", "process": "EXCEL.EXE"})
    tl.append({"type": "text", "ts": now - 199, "window": "invoices.xlsx - Excel", "process": "EXCEL.EXE",
               "text": "Client Tax ID Date Amount Description\nPaper Moth Bakery 99815188447 10/09/2026 4216.96"})
    tl.append({"type": "window", "ts": now - 60, "window": "Apuntes - Bloc de notas", "process": "notepad.exe"})


def router_for(cfg, mode: str):
    from edith.brain import answer, notes
    from edith.ui.agent import text

    local = None
    if mode in ("offline", "budget"):
        from edith.brain.local import LocalModel

        local = LocalModel(cfg)
        local.start()
        local.ensure_started()
    if mode == "budget":
        cfg.budget["monthly_limit_usd"] = 0.0                 # spent >= limit: local only
    r = answer.Router(cfg, local, lambda k, **kw: text("es", k, **kw), notes=notes.NoteBook(cfg))
    return r, local


class Offline:
    """The cloud fails like it does without internet."""

    def __enter__(self):
        from edith.brain import client

        self.saved = (client.MetaClient.chat, client.MetaClient.chat_stream)

        def fail(*a, **k):
            raise ConnectionError("no internet (memory check)")
        client.MetaClient.chat = client.MetaClient.chat_stream = fail
        return self

    def __exit__(self, *exc):
        from edith.brain import client

        client.MetaClient.chat, client.MetaClient.chat_stream = self.saved


def apply_and_undo(text_to_write: str) -> tuple[bool, str, bool, str]:
    """Write into a textarea of a Chromium window opened here, then undo. (applied, why, undone, why)"""
    import ctypes

    from playwright.sync_api import sync_playwright
    from PySide6.QtWidgets import QApplication

    from edith.edit import target

    QApplication.instance() or QApplication([])
    title = "EDITH-CHECK apuntes"
    u = ctypes.windll.user32
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=False, args=["--force-renderer-accessibility"])
        page = b.new_page()
        page.set_content(f"<title>{title}</title><textarea id=n rows=12 cols=60 aria-label='Apuntes'>Apuntes:\n</textarea>")
        buf = ctypes.create_unicode_buffer(256)
        for _ in range(20):
            page.bring_to_front()
            page.click("#n")
            time.sleep(0.4)
            u.GetWindowTextW(u.GetForegroundWindow(), buf, 256)
            if title in buf.value:
                break
        if title not in buf.value:
            b.close()
            return False, "the test window could not come to the front (the PC is in use)", False, ""
        t = target.capture(lambda *_: False)
        if t is None:
            b.close()
            return False, "the textarea was not found", False, ""
        ty = target.Typist()
        ok, why = ty.apply(t, "append", text_to_write)
        written = page.input_value("#n")
        undone, why_u = ty.undo() if ok else (False, "")
        after = page.input_value("#n")
        b.close()
    return ok and text_to_write.split("\n")[0][:20] in written, why or "", undone and after.strip() == "Apuntes:", why_u


def run_mode(mode: str, apply: bool) -> list[tuple[str, bool, str]]:
    from edith.brain import edit
    from edith.config import load_config
    from edith.memory.timeline import Timeline

    cfg = load_config()
    cfg.root = Path(tempfile.mkdtemp(prefix=f"edith-memcheck-{mode}-"))
    cfg.companion = {**cfg.companion, "learn": False}
    out = []
    build_memory(cfg)
    ev = Timeline(cfg).read()
    kinds = {e["type"] for e in ev}
    out.append(("capture", {"frame", "text", "transcript"} <= kinds, f"{len(ev)} events: {sorted(kinds)}"))
    r, local = router_for(cfg, mode)
    ctx = Offline() if mode == "offline" else _Nothing()
    try:
        with ctx:
            t0 = time.monotonic()
            rep = r.respond("¿qué dijo el profe del examen hace unos minutos?")
            a = rep.text.lower()
            ok = ("13" in a and ("noviembre" in a or "viernes" in a)) and (rep.where == "cloud") == (mode == "api")
            out.append(("temporal", ok, f"[{rep.where}, {time.monotonic() - t0:.1f} s, ${rep.cost:.4f}] {rep.text[:90]}"))
            t0 = time.monotonic()
            rep = r.respond("pon en mis notas lo que dijo el profe del examen y la tarea", system=edit.EDIT_PROMPT,
                            extra=edit.target_block("Apuntes - Bloc de notas", "notepad.exe", "EditControl", "Editor de texto",
                                                    "Apuntes:\n"))
            p = edit.parse(rep.text, time.time())
            ok = not isinstance(p, str) and "13" in p.text and "fibonacci" in p.text.lower()
            shown = p if isinstance(p, str) else p.text.replace("\n", " / ")
            out.append(("edit", ok, f"[{rep.where}, {time.monotonic() - t0:.1f} s] {shown[:100]}"))
            if apply and not isinstance(p, str):
                applied, why, undone, why_u = apply_and_undo(p.text)
                out.append(("apply", applied, why or "written into the test textarea"))
                out.append(("undo", undone, why_u or "back to \"Apuntes:\""))
    finally:
        if local is not None:
            local.stop()
    return out


class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def ten_questions() -> list[dict]:
    from edith.config import load_config

    cfg = load_config()
    cfg.root = Path(tempfile.mkdtemp(prefix="edith-memcheck-ten-"))
    cfg.companion = {**cfg.companion, "learn": False}
    build_memory(cfg)
    r, _ = router_for(cfg, "api")
    rows = []
    for q in QUESTIONS:
        t0 = time.monotonic()
        rep = r.respond(q)
        rows.append({"question": q, "where": rep.where, "seconds": round(time.monotonic() - t0, 1),
                     "cost_usd": round(rep.cost, 5), "answer": rep.text.replace("\n", " ")[:160]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="api,offline,budget")
    ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--no-ten", action="store_true")
    args = ap.parse_args()
    os.environ["EDITH_HOME"] = tempfile.mkdtemp(prefix="edith-memcheck-")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    allok = True
    rows = []
    for mode in args.modes.split(","):
        steps = run_mode(mode, not args.no_apply)
        print(f"\nmode: {mode}")
        for name, ok, detail in steps:
            allok &= ok
            print(f"  {'OK  ' if ok else 'FAIL'} {name:9} {detail}")
            rows.append({"mode": mode, "step": name, "ok": ok, "detail": detail})
    with open(results / "memory_check.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["mode", "step", "ok", "detail"])
        w.writeheader()
        w.writerows(rows)
    if not args.no_ten:
        ten = ten_questions()
        total = sum(x["cost_usd"] for x in ten)
        print("\n10 questions (api):")
        for x in ten:
            print(f"  ${x['cost_usd']:.4f} {x['seconds']:5.1f} s [{x['where']}] {x['question']} -> {x['answer'][:70]}")
        print(f"  total ${total:.4f}, average {sum(x['seconds'] for x in ten) / len(ten):.1f} s")
        with open(results / "ten_questions.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(ten[0]))
            w.writeheader()
            w.writerows(ten)
    print(f"\nmemory check: {'all OK' if allok else 'FAILURES above'}")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
