"""Muse Glimmer 30B, 2-bit vs 3-bit (Unsloth UD-Q2_K_XL vs UD-Q3_K_XL) -> results/quant.csv.

    .venv\\Scripts\\python scripts\\quant_bench.py

For each file, with Edith's default 6 GPU layers: load time, VRAM over the idle GPU, time to first
token and total time (3 runs), and quality: 6 questions about the class memory (scripts/memory_check)
answered by the local model only ("local only" mode), correct if the answer holds the fact.
"""

from __future__ import annotations

import csv
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
HUB = Path.home() / ".cache" / "huggingface" / "hub" / "models--unsloth--Muse-Glimmer-30B-GGUF" / "snapshots"
QUALITY = [("¿qué dijo el profe del examen hace unos minutos?", ["13"]),
           ("¿cuál es la tarea y para cuándo es?", ["fibonacci"]),
           ("¿qué no entra en el examen?", ["grafo"]),
           ("¿a qué hora hay que subir la tarea?", ["ocho", "8"]),
           ("¿qué hice después de la clase?", ["excel", "invoices"]),
           ("¿de qué era la diapositiva?", ["recursi"])]


def main() -> int:
    os.environ["EDITH_HOME"] = tempfile.mkdtemp(prefix="edith-quant-")
    from benchmark import gpu_sample
    from memory_check import build_memory

    from edith.brain import answer, notes
    from edith.brain.local import LocalModel
    from edith.config import load_config
    from edith.ui.agent import text

    rows = []
    for name in ("Muse-Glimmer-30B-UD-Q2_K_XL.gguf", "Muse-Glimmer-30B-UD-Q3_K_XL.gguf"):
        model = next((s / name for s in HUB.glob("*") if (s / name).exists()), None)
        if model is None:
            print(f"{name}: not downloaded")
            continue
        cfg = load_config()
        cfg.root = Path(tempfile.mkdtemp(prefix="edith-quant-home-"))
        cfg.local = {**cfg.local, "model": str(model), "mmproj": str(model.parent / "mmproj-kquant.gguf")}
        cfg.companion = {**cfg.companion, "learn": False}
        build_memory(cfg)
        base, _ = gpu_sample()
        local = LocalModel(cfg)
        t0 = time.monotonic()
        local.start()
        local.ensure_started()
        load_s = time.monotonic() - t0
        vram = gpu_sample()[0] - base
        ttft, total = [], []
        for _ in range(3):
            first = {}
            t0 = time.monotonic()
            local.generate("Eres Edith. Responde en una frase.", "¿Qué es la memoización en programación?",
                           max_tokens=120, on_token=lambda p: first.setdefault("t", time.monotonic() - t0))
            total.append(time.monotonic() - t0)
            ttft.append(first.get("t", total[-1]))
        r = answer.Router(cfg, local, lambda k, **kw: text("es", k, **kw), notes=notes.NoteBook(cfg))
        r.local_only = True
        good, secs = 0, []
        for q, expect in QUALITY:
            t0 = time.monotonic()
            a = r.respond(q).text.lower()
            secs.append(time.monotonic() - t0)
            good += any(e in a for e in expect)
        local.stop()
        rows.append({"model": name.replace("Muse-Glimmer-30B-", "").replace(".gguf", ""),
                     "file_gib": round(model.stat().st_size / 2 ** 30, 1), "load_s": round(load_s, 1),
                     "vram_mib": round(vram), "ttft_s": round(statistics.mean(ttft), 2),
                     "total_s": round(statistics.mean(total), 2), "quality": f"{good}/{len(QUALITY)}",
                     "answer_s_avg": round(statistics.mean(secs), 1)})
        print(rows[-1], flush=True)
    (ROOT / "results").mkdir(exist_ok=True)
    with open(ROOT / "results" / "quant.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
