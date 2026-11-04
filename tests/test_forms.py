"""Desktop-form recipes at levels 4 (UI Automation) and 5 (points from vision), on the hidden
desktop, with the real demo app; Muse Spark and SAM 3.1 are replaced by fixed replies here."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

import make_letters  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.recipes import forms, run, sandbox  # noqa: E402
from edith.recipes.store import RecipeStore  # noqa: E402

TYPED = {"Client": "Paper Moth Bakery", "Tax ID": "99815188447", "Date": "10/09/2026", "Amount": "4216.96",
         "Description": "Notebooks and pens"}
READ_ROW = '''
    import openpyxl
    from datetime import datetime
    ws = openpyxl.load_workbook(sheet, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h) for h in rows[0]]
    d = None
    for r in rows[1:]:
        x = dict(zip(head, r))
        if str(x["Client"]).strip().lower() == key.strip().lower():
            d = x
    def amount(v):
        if v in (None, ""):
            return ""
        if isinstance(v, str):
            v = float(v.split(",")[0] + v.split(",")[1]) if "," in v else float(v)
        return str(round(float(v), 2)) if float(v) != int(float(v)) else str(int(float(v)))
    def when(v):
        if isinstance(v, datetime):
            return v.strftime("%d/%m/%Y")
        s = str(v or "").strip()
        for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(s, f).strftime("%d/%m/%Y")
            except ValueError:
                pass
        return s
    values = [("Client", str(d["Client"]).strip()), ("Tax ID", str(d["Tax ID"] or "").strip()),
              ("Date", when(d["Date"])), ("Amount", amount(d["Amount"])),
              ("Description", str(d["Description"] or "").strip())]
'''
LEVEL4 = "def run(sheet, template, key, out_dir, ui):" + READ_ROW + '''
    for name, value in values:
        ui.set(name, value)
    ui.invoke("Save")
'''
POINTS = {"Client": [260, 32], "Tax ID": [260, 72], "Date": [260, 112], "Amount": [260, 152],
          "Description": [260, 192], "Save": [340, 271]}
LEVEL5 = "def run(sheet, template, key, out_dir, ui):" + READ_ROW + '''
    points = {"Client": (260, 32), "Tax ID": (260, 72), "Date": (260, 112), "Amount": (260, 152),
              "Description": (260, 192)}
    for name, value in values:
        ui.type(points[name][0], points[name][1], value)
    ui.click(340, 271)
'''


def fenced(program: str) -> str:
    return f"```python\n{program}```"


class FormTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.files = make_letters.make(d)
        self.store_file = d / "northwind.jsonl"
        self.store_file.write_text('{"Client":"earlier"}\n', encoding="utf-8")
        cmd = (f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ROOT / "demo" / "desktop" / "northwind.ps1"}" '
               f'-Store "{self.store_file}"')
        self.demo = forms.FormDemo(cmd, "Northwind Desktop - New invoice", "powershell.exe", dict(TYPED), "Save",
                                   self.files["sheet"], 60, forms.state_files(cmd))
        cfg = load_config()
        cfg.root = Path(tempfile.mkdtemp())
        self.cfg, self.store = cfg, RecipeStore(cfg)

    def test_the_data_file_is_found_in_the_command_line(self):
        self.assertEqual(self.demo.state, [str(self.store_file)])

    def test_level_4_compiles_verifies_without_saving_and_runs_with_undo(self):
        r = forms.compile_form(self.cfg, self.demo, ask=lambda s, u: fenced(LEVEL4), levels=(4,), store=self.store)
        self.assertEqual((r.level, r.key_column, r.extra["submit"]), (4, "Client", "Save"))
        self.assertEqual(self.store_file.read_text(encoding="utf-8"), '{"Client":"earlier"}\n')   # dry runs only
        runner = run.Runner(self.store)
        p = runner.prepare(run.match("para Panadería Ñandú", self.store.all()))
        self.assertIn("Client: Panadería Ñandú", p.new_text)
        self.assertEqual(self.store_file.read_text(encoding="utf-8"), '{"Client":"earlier"}\n')
        runner.apply(p)
        last = json.loads(self.store_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((last["Client"], last["Amount"]), ("Panadería Ñandú", "3241.24"))
        self.assertEqual(runner.undo(), (True, ""))
        self.assertEqual(self.store_file.read_text(encoding="utf-8"), '{"Client":"earlier"}\n')

    def test_a_wrong_program_does_not_pass(self):
        wrong = LEVEL4.replace('ui.set(name, value)', 'ui.set(name, value.upper())')
        with self.assertRaises(Exception):
            forms.compile_form(self.cfg, self.demo, ask=lambda s, u: fenced(wrong), levels=(4,), store=self.store)

    def test_level_5_types_at_the_points_vision_found(self):
        r = forms.compile_form(self.cfg, self.demo, ask=lambda s, u: fenced(LEVEL5), levels=(5,), store=self.store,
                               locate_fn=lambda cfg, demo: POINTS)
        self.assertEqual(r.level, 5)
        p = run.Runner(self.store).prepare(run.match("para Café Señorial", self.store.all()))
        self.assertIn("Client: Café Señorial", p.new_text)

    def test_level_5_locates_again_when_vision_swapped_two_fields(self):
        swapped = dict(POINTS, **{"Client": POINTS["Tax ID"], "Tax ID": POINTS["Client"]})
        located = iter([swapped, POINTS])
        calls = []

        def locate_fn(cfg, demo):
            calls.append(1)
            return next(located)

        def ask(system, user):         # a correct program for whatever points it is given
            pts = json.loads(system.split("The points: ")[1].split("\n")[0])
            return fenced("def run(sheet, template, key, out_dir, ui):" + READ_ROW +
                          f"\n    points = {pts!r}\n    for name, value in values:\n"
                          "        ui.type(points[name][0], points[name][1], value)\n"
                          "    ui.click(points['Save'][0], points['Save'][1])\n")
        r = forms.compile_form(self.cfg, self.demo, ask=ask, levels=(5,), store=self.store, locate_fn=locate_fn)
        self.assertEqual((r.level, len(calls), r.extra["points"]["Client"]), (5, 2, POINTS["Client"]))

    def test_a_row_failing_twice_is_replanned_and_then_goes_down_to_level_5(self):
        from edith.recipes import escalate

        r = forms.compile_form(self.cfg, self.demo, ask=lambda s, u: fenced(LEVEL4), levels=(4,), store=self.store)
        buggy = LEVEL4.replace("    for name, value in values:",
                               "    if key.startswith(\"Taller\"):\n        raise ValueError(\"cannot read this row\")\n"
                               "    for name, value in values:", 1)
        self.store.save(r, buggy)
        statuses = []

        def esc(rr, key, error, line):     # the replan keeps the bug; compiling at level 5 works
            return forms.escalate_form(self.cfg, self.store, rr, key, error, on_status=lambda k, kw: statuses.append(k),
                                       ask=lambda s, u: fenced(LEVEL5 if "points" in s else buggy),
                                       locate_fn=lambda cfg, demo: POINTS)
        runner = run.Runner(self.store)
        runner.escalator = esc
        p = runner.prepare(run.match("para Taller Ñuñoa", self.store.all()))
        self.assertIn("Client: Taller Ñuñoa", p.new_text)
        self.assertEqual(statuses, ["escalate_replan", "escalate_level"])
        self.assertEqual(self.store.all()[0].level, 5)
        self.assertIsInstance(escalate.GaveUp("x"), Exception)

    def test_a_level_4_program_cannot_reach_inside_ui(self):
        with self.assertRaises(sandbox.UnsafeProgram):
            sandbox.check(LEVEL4.replace('ui.invoke("Save")', "ui._win.Click()"), 4)


if __name__ == "__main__":
    unittest.main()
