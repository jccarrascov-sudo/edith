"""Free mode: an order with no recipe, tried as an agent on copies / in dry runs, then induced."""

from __future__ import annotations

import csv
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
from edith.recipes import free  # noqa: E402

CSV_PROGRAM = '''
import csv
import openpyxl
from pathlib import Path

def run(files, out_dir):
    ws = openpyxl.load_workbook(files["invoices.xlsx"], data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    out = Path(out_dir) / "big.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Client", "Amount"])
        for r in rows[1:]:
            a = float(str(r[3]).replace(",", "")) if r[3] not in (None, "") else 0
            if a > 3000:
                w.writerow([r[0], a])
    return [str(out)]
'''


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.files = make_letters.make(Path(tempfile.mkdtemp()))

    def test_a_program_runs_on_copies_and_finishes(self):
        replies = iter(["ROW: none\n```python\ndef run(files, out_dir):\n    raise ValueError('oops')\n```",
                        "ROW: none\n```python\n" + CSV_PROGRAM + "```", "DONE"])
        seen = []
        o = free.run_files("un CSV de los que deben más de 3000", [self.files["sheet"]],
                           lambda msgs: seen.append(msgs[-1]["content"]) or next(replies))
        self.assertTrue(o.ok)
        self.assertEqual(o.steps, 3)
        self.assertIn("oops", seen[1])                                 # it saw its own error
        rows = list(csv.reader(open(o.made[0], encoding="utf-8")))
        self.assertEqual(len(rows) - 1, 18)
        self.assertTrue(self.files["sheet"].exists())

    def test_it_stops_after_the_step_limit_with_a_reason(self):
        o = free.run_files("x", [self.files["sheet"]], lambda msgs: "no program", max_steps=3)
        self.assertFalse(o.ok)
        self.assertIn("3 steps", o.why)

    def test_a_program_cannot_escape_the_copies(self):
        bad = "ROW: none\n```python\ndef run(files, out_dir):\n    open(files['invoices.xlsx'], 'wb').write(b'x')\n```"
        o = free.run_files("x", [self.files["sheet"]], lambda msgs: bad, max_steps=2)
        self.assertFalse(o.ok)
        self.assertEqual(self.files["sheet"].stat().st_size > 1000, True)   # the user's file is intact


class DesktopTests(unittest.TestCase):
    def test_the_agent_fills_stops_before_saving_and_the_replay_saves(self):
        store = Path(tempfile.mkdtemp()) / "nw.jsonl"
        store.write_text("", encoding="utf-8")
        cmd = (f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ROOT / "demo" / "desktop" / "northwind.ps1"}" '
               f'-Store "{store}"')
        plan = iter([json.dumps({"set": ["Client", "Taller Ñuñoa"]}), json.dumps({"set": ["Amount", "3768.87"]}),
                     json.dumps({"submit": "Save"})])
        looks = []

        def ask(msgs):
            looks.append(msgs[-1]["content"])
            return next(plan)
        o = free.run_desktop("registra a Taller Ñuñoa", cmd, "Northwind Desktop - New invoice", "", ask)
        self.assertTrue(o.ok, o.why)
        self.assertEqual(o.values["Client"], "Taller Ñuñoa")
        self.assertNotIn("Cerrar", o.values)                         # not the title bar's buttons
        self.assertEqual(store.read_text(encoding="utf-8"), "")       # dry: nothing saved
        self.assertEqual(free.replay(cmd, "Northwind Desktop - New invoice", o.actions), [])
        saved = json.loads(store.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((saved["Client"], saved["Amount"]), ("Taller Ñuñoa", "3768.87"))


class RoutingTests(unittest.TestCase):
    def test_orders_and_apps(self):
        self.assertTrue(free.ORDER.search("haz un CSV con los clientes"))
        self.assertFalse(free.ORDER.search("¿qué hice después de la clase?"))
        apps = {"Northwind Desktop - New invoice": "cmd"}
        self.assertEqual(free.app_for("registra en Northwind la factura", apps)[0], "Northwind Desktop - New invoice")
        self.assertIsNone(free.app_for("haz un CSV", apps))


if __name__ == "__main__":
    unittest.main()
