"""30 invoices into a desktop app, five ways -> results/invoices.csv (real Muse Spark, real app).

    .venv\\Scripts\\python scripts\\invoices_bench.py [--only a,b,c,d,e]

The 30 fictitious invoices of demo/data/invoices.xlsx (accents and ñ, amounts as numbers and as
text with commas, dates as dates and as text, one empty amount) go into Northwind Desktop (only a
user interface), each way into a fresh data file:
  (a) click-by-click agent: free mode, one invoice at a time (Muse Spark picks every action)
  (b) Muse Spark writes the program from a text description only (the form's field names, no
      demonstration, so it does not know how the person writes dates and amounts)
  (c) the level-4 recipe compiled from ONE invoice typed by hand (UI Automation by name)
  (d) the same demonstration compiled at level 5 (SAM 3.1 points)
  (e) the app changes to v2 ("Amount" renamed "Total due (PEN)" and moved): the level-4 recipe
      fails, escalates (Muse Spark replans from the error), and runs the 30 on v2
Correct = the saved record equals what the person types (dd/mm/yyyy, amounts with two decimals);
the row with an empty amount counts as correct if it is skipped or saved with the amount blank.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
TITLE = "Northwind Desktop - New invoice"
DESCRIPTION = ("Copy the invoice whose Client is `key` from the Excel sheet into the Northwind Desktop form and "
               "save it. The form has the fields Client, Tax ID, Date, Amount and Description, and a Save button.")


def expected(row: dict) -> dict:
    d = row["Date"]
    if isinstance(d, (datetime, date)):
        day = d.strftime("%d/%m/%Y")
    else:
        s = str(d or "").strip()
        day = s
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                day = datetime.strptime(s, fmt).strftime("%d/%m/%Y")
                break
            except ValueError:
                pass
    a = row["Amount"]
    amount = "" if a in (None, "") else f"{float(str(a).replace(',', '')):.2f}"
    return {"Client": str(row["Client"]).strip(), "Tax ID": str(row["Tax ID"] or "").strip(), "Date": day,
            "Amount": amount, "Description": str(row["Description"] or "").strip()}


def score(store: Path, rows: list[dict]) -> tuple[int, list[str]]:
    saved = [json.loads(x) for x in store.read_text(encoding="utf-8").splitlines() if x.strip()] if store.exists() else []
    by_client = {}
    for rec in saved:
        by_client.setdefault(rec.get("Client", "").strip(), rec)
    ok, wrong = 0, []
    for r in rows:
        exp = expected(r)
        got = by_client.get(exp["Client"])
        if exp["Amount"] == "" and (got is None or got.get("Amount", "") == ""):
            ok += 1
        elif got is not None and all(str(got.get(k, "")).strip() == v for k, v in exp.items()):
            ok += 1
        else:
            diff = "not saved" if got is None else ", ".join(f"{k}: {got.get(k)!r}≠{v!r}" for k, v in exp.items()
                                                              if str(got.get(k, "")).strip() != v)
            wrong.append(f"{exp['Client']}: {diff}")
    return ok, wrong


def spent() -> float:
    from edith.brain.client import Ledger

    return sum(e.get("cost", 0) for e in Ledger().entries())


def cmd(store: Path, version: str = "v1") -> str:
    return (f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ROOT / "demo" / "desktop" / "northwind.ps1"}" '
            f'-Store "{store}" -Version {version}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="a,b,c,d,e")
    args = ap.parse_args()
    os.environ["EDITH_HOME"] = tempfile.mkdtemp(prefix="edith-invbench-")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import shutil

    from edith.config import load_config
    from edith.recipes import docs, forms, free, sandbox
    from edith.recipes.learn import _muse
    from edith.recipes.store import RecipeStore

    folder = Path(tempfile.mkdtemp(prefix="edith-invbench-user-"))
    sheet = Path(shutil.copy2(ROOT / "demo" / "data" / "invoices.xlsx", folder / "invoices.xlsx"))
    _h, rows = docs.sheet_rows(sheet)
    keys = [str(r["Client"]).strip() for r in rows]
    cfg = load_config()
    cfg.root = Path(tempfile.mkdtemp(prefix="edith-invbench-home-"))
    store = RecipeStore(cfg)
    results = []

    def record(way: str, st: Path, seconds: float, cost: float, note: str = ""):
        ok, wrong = score(st, rows)
        results.append({"way": way, "correct": f"{ok}/30", "seconds_total": round(seconds, 1),
                        "seconds_per_invoice": round(seconds / 30, 2), "cost_usd": round(cost, 4),
                        "cost_per_invoice": round(cost / 30, 5), "note": note, "first_wrong": "; ".join(wrong[:3])})
        print(f"{way}: {ok}/30 · {seconds:.0f} s ({seconds / 30:.2f} s/invoice) · ${cost:.4f} · {note} {wrong[:2]}", flush=True)

    demo_typed = expected(rows[0])
    state0 = folder / "demo.jsonl"
    state0.write_text("", encoding="utf-8")
    demo = forms.FormDemo(cmd(state0), TITLE, "powershell.exe", demo_typed, "Save", sheet, 70, forms.state_files(cmd(state0)))
    recipe4 = None
    only = args.only.split(",")
    if "a" in only:
        st = folder / "a.jsonl"
        st.write_text("", encoding="utf-8")
        c0, t0 = spent(), time.monotonic()
        ask = free.muse_chat(cfg)
        data = "The Excel sheet:\n" + docs.sheet_brief(sheet, limit=40)
        for k in keys:
            o = free.run_desktop(f"registra en Northwind la factura de {k} del Excel", cmd(st), TITLE, data, ask)
            if o.ok:
                free.replay(cmd(st), TITLE, o.actions)
        record("(a) click-by-click agent (free mode)", st, time.monotonic() - t0, spent() - c0, "Muse Spark picks every action")
    if "b" in only:
        st = folder / "b.jsonl"
        st.write_text("", encoding="utf-8")
        c0, t0 = spent(), time.monotonic()
        reply = _muse(cfg)(forms.PROMPT[4].format(submit="Save", key_column="Client", points=""),
                           DESCRIPTION + "\n\n" + docs.sheet_brief(sheet, limit=3))
        import re

        m = re.search(r"```(?:python)?\s*\n(.*?)```", reply or "", re.DOTALL)
        program = m.group(1).strip() + "\n" if m else ""
        with forms.App(cmd(st), TITLE) as app:
            for k in keys:
                sandbox.run(program, 4, str(sheet), "", k, str(folder / "b-out"), pid=app.p.pid)
            time.sleep(0.8)
        record("(b) program from a text description", st, time.monotonic() - t0, spent() - c0, "no demonstration")
    if "c" in only or "e" in only:
        c0, t0 = spent(), time.monotonic()
        recipe4 = forms.compile_form(cfg, demo, store=store, levels=(4,))
        compile_s, compile_c = time.monotonic() - t0, spent() - c0
    if "c" in only:
        st = folder / "c.jsonl"
        st.write_text("", encoding="utf-8")
        r = recipe4
        t0 = time.monotonic()
        with forms.App(cmd(st), TITLE) as app:
            for k in keys:
                sandbox.run(store.program(r), 4, str(sheet), "", k, str(folder / "c-out"), pid=app.p.pid)
            time.sleep(0.8)
        record("(c) level-4 recipe from one demonstration", st, time.monotonic() - t0, 0.0,
               f"compiled once: {compile_s:.0f} s, ${compile_c:.4f}")
    if "d" in only:
        c0, t0 = spent(), time.monotonic()
        store5 = RecipeStore(cfg)
        store5.dir = cfg.root / "state" / "recipes5"
        r5 = forms.compile_form(cfg, demo, store=store5, levels=(5,))
        cs, cc = time.monotonic() - t0, spent() - c0
        st = folder / "d.jsonl"
        st.write_text("", encoding="utf-8")
        t0 = time.monotonic()
        with forms.App(cmd(st), TITLE) as app:
            for k in keys:
                sandbox.run(store5.program(r5), 5, str(sheet), "", k, str(folder / "d-out"), pid=app.p.pid,
                            points=r5.extra.get("points"))
            time.sleep(0.8)
        record("(d) level-5 recipe (SAM 3.1 points)", st, time.monotonic() - t0, 0.0, f"compiled once: {cs:.0f} s, ${cc:.4f}")
    if "e" in only:
        st = folder / "e.jsonl"
        st.write_text("", encoding="utf-8")
        r = recipe4
        r.template = cmd(state0, "v2")                         # the app changed under the recipe
        store.save(r)
        c0, t0 = spent(), time.monotonic()
        out = forms.dry_run(store.program(r), 4, r, keys[1], folder / "e-probe")
        note = ""
        if out[0] is None:                                     # it fails twice -> escalation
            from edith.recipes.escalate import GaveUp

            try:
                r = forms.escalate_form(cfg, store, r, keys[1], out[1])
                note = f"repaired by escalation at level {r.level}"
            except GaveUp as exc:
                note = f"gave up: {exc.line}"
        repair_s, repair_c = time.monotonic() - t0, spent() - c0
        t0 = time.monotonic()
        with forms.App(cmd(st, "v2"), TITLE) as app:
            for k in keys:
                sandbox.run(store.program(r), r.level, str(sheet), "", k, str(folder / "e-out"), pid=app.p.pid,
                            points=r.extra.get("points"))
            time.sleep(0.8)
        record("(e) after the app changed (v2) + repair", st, time.monotonic() - t0, repair_c,
               f"{note}; repair {repair_s:.0f} s")
    (ROOT / "results").mkdir(exist_ok=True)
    with open(ROOT / "results" / "invoices.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
