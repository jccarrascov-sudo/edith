"""Coverage mini-benchmark: 20 student tasks, each run twice -> results/coverage.csv.

    .venv\\Scripts\\python scripts\\coverage_bench.py [--only id1,id2]

Run 1: free mode (no recipe yet): Muse Spark plans; files on copies (level 1) or the desktop app on
the hidden desktop (level 4, stopped before saving; the replay saves). The result is checked by
the task's verifier, then applied, and the task is induced as a recipe.
Run 2: the same task for another row with the induced recipe (no model; dry run / copies first).
Verifiers: a Word result must hold the row's values and no placeholder of the template left; a
form must hold the row's client and amount. Real Muse Spark; real app; nothing of the user's.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo" / "coverage"))
sys.path.insert(0, str(ROOT / "demo"))
sys.stdout.reconfigure(encoding="utf-8")
PLACEHOLDERS = {"NOMBRE", "CODIGO", "CARRERA", "PROMEDIO", "EVENTO", "FECHA", "LUGAR", "DURACION", "ALUMNO", "MONTO",
                "CONCEPTO", "PARCIAL", "FINAL", "PRACTICAS", "CLIENT", "AMOUNT", "DESCRIPTION", "DATE", "TAX ID"}


def doc_ok(path: Path | None, row: dict, fields: list[str]) -> tuple[bool, str]:
    from edith.recipes import docs
    from edith.recipes.learn import value_forms

    if path is None or not path.exists():
        return False, "no document"
    text = docs.shape(path).text()
    folded = docs.fold(text)
    for f in fields:
        if not any(docs.fold(v) in folded for v in value_forms(row[f]) | {docs.squash(str(row[f]))}):
            return False, f"{f} = {row[f]!r} is missing"
    left = [w for w in PLACEHOLDERS if w in text.split() or f"{w}," in text or f"{w}." in text]
    return (not left), (f"placeholder left: {left}" if left else "")


def form_ok(values: dict, row: dict) -> tuple[bool, str]:
    from edith.recipes import docs

    got = docs.fold(" | ".join(str(v) for v in values.values()))
    for col in ("Client", "Tax ID"):
        if docs.fold(docs.squash(str(row[col]))) not in got:
            return False, f"{col} missing"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    os.environ["EDITH_HOME"] = tempfile.mkdtemp(prefix="edith-coverage-")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import make_tasks
    from PySide6.QtCore import QCoreApplication

    from edith.config import load_config
    from edith.recipes import docs, free, run
    from edith.recipes.store import RecipeStore

    QCoreApplication.instance() or QCoreApplication([])
    only = set(filter(None, args.only.split(",")))
    rows_out = []
    ask = None
    for task in make_tasks.DOC_TASKS + [(t[0], None) for t in make_tasks.FORM_TASKS]:
        tid = task[0]
        if only and tid not in only:
            continue
        cfg = load_config()
        cfg.root = Path(tempfile.mkdtemp(prefix=f"edith-cov-{tid}-"))
        ask = free.muse_chat(cfg)
        store = RecipeStore(cfg)
        folder = Path(tempfile.mkdtemp(prefix=f"edith-cov-user-{tid}-"))
        make_tasks.make(folder)
        rec = {"task": tid, "run1_ok": False, "run1_s": "", "run1_level": "", "run1_steps": "", "run1_why": "",
               "induced": False, "run2_ok": False, "run2_s": "", "run2_level": "", "run2_why": ""}
        try:
            if task[1] is not None:                                     # a Word document task
                _tid, sheet_name, template, _title, _lines, request, i1, i2, fields = task
                sheet = folder / sheet_name
                _h, rows = docs.sheet_rows(sheet)
                o = free.run_files(request, [sheet, folder / template], ask)
                made = next((f for f in o.made if f.suffix.lower() == ".docx"), None)
                ok, why = doc_ok(made, rows[i1], fields) if o.ok else (False, o.why)
                rec.update(run1_ok=ok, run1_s=round(o.seconds, 1), run1_level=1, run1_steps=o.steps, run1_why=why)
                if ok:
                    applied = folder / made.name
                    shutil.copy2(made, applied)
                    r = free.induce_files(cfg, store, o, [sheet, folder / template], [applied])
                    rec["induced"] = r is not None
                    if r is not None:
                        key = str(rows[i2][r.key_column]).strip()
                        t0 = time.monotonic()
                        p = run.Runner(store).prepare(run.Match(r, [key]))
                        ok2, why2 = doc_ok(p.outputs[0].made, rows[i2], fields)
                        rec.update(run2_ok=ok2, run2_s=round(time.monotonic() - t0, 2), run2_level=r.level, run2_why=why2)
                    else:
                        rec["run2_why"] = "not induced (no row task reported)"
            else:                                                       # a desktop form task
                ftask = next(t for t in make_tasks.FORM_TASKS if t[0] == tid)
                _tid, request, i1, i2 = ftask
                sheet = folder / "invoices.xlsx"
                _h, rows = docs.sheet_rows(sheet)
                state = folder / "northwind.jsonl"
                state.write_text("", encoding="utf-8")
                cmd = (f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ROOT / "demo" / "desktop" / "northwind.ps1"}" '
                       f'-Store "{state}"')
                title = "Northwind Desktop - New invoice"
                o = free.run_desktop(request, cmd, title, "The Excel sheet:\n" + docs.sheet_brief(sheet, limit=40), ask)
                ok, why = form_ok(o.values, rows[i1]) if o.ok else (False, o.why)
                rec.update(run1_ok=ok, run1_s=round(o.seconds, 1), run1_level=4, run1_steps=o.steps, run1_why=why)
                if ok:
                    errors = free.replay(cmd, title, o.actions)
                    if errors:
                        rec["run1_why"] = f"replay: {errors[0]}"
                    r = free.induce_desktop(cfg, store, o, cmd, title, sheet)
                    rec["induced"] = r is not None
                    if r is not None:
                        key = str(rows[i2][r.key_column]).strip()
                        t0 = time.monotonic()
                        p = run.Runner(store).prepare(run.Match(r, [key]))
                        values = dict(line.split(": ", 1) for line in p.new_text.splitlines() if ": " in line)
                        ok2, why2 = form_ok(values, rows[i2])
                        rec.update(run2_ok=ok2, run2_s=round(time.monotonic() - t0, 2), run2_level=r.level, run2_why=why2)
        except Exception as exc:
            rec["run2_why" if rec["run1_ok"] else "run1_why"] = f"{type(exc).__name__}: {exc}"[:160]
        rows_out.append(rec)
        print(f"{tid:16} run1 {'OK ' if rec['run1_ok'] else 'FAIL'} {rec['run1_s']:>6}s L{rec['run1_level']} "
              f"steps {rec['run1_steps']} | induced {rec['induced']} | run2 {'OK ' if rec['run2_ok'] else 'FAIL'} "
              f"{rec['run2_s']}s L{rec['run2_level']} {rec['run1_why'] or rec['run2_why']}", flush=True)
    (ROOT / "results").mkdir(exist_ok=True)
    with open(ROOT / "results" / "coverage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0]))
        w.writeheader()
        w.writerows(rows_out)
    n = len(rows_out)
    r1 = sum(r["run1_ok"] for r in rows_out)
    r2 = sum(r["run2_ok"] for r in rows_out)
    t1 = [r["run1_s"] for r in rows_out if r["run1_ok"]]
    t2 = [r["run2_s"] for r in rows_out if r["run2_ok"]]
    print(f"\nrun 1 (free mode): {r1}/{n} ({r1 / n:.0%}), avg {sum(t1) / max(1, len(t1)):.1f} s")
    print(f"run 2 (induced recipe): {r2}/{n} ({r2 / n:.0%}), avg {sum(t2) / max(1, len(t2)):.2f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
