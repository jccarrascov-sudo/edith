"""Workshop: code guard, problem attribution, and a real headless run on a sandbox portal."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PANTALLITA_HOME", tempfile.mkdtemp(prefix="pantallita-test-"))

from pantallita.workshop import build, codegen, runner, sandbox  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_learn import good_plan  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

FILL = '''def run(page, row, ctx):
    loc = page.get_by_label("{label}", exact=True)
    loc.fill(transform(row["{column}"], {transform}))
    ctx["filled"]["{label}"] = loc.input_value()'''

CODE_BASE = {
    "b1": 'def run(page, row, ctx):\n    ctx["filled"] = {}',
    "b7": 'def run(page, row, ctx):\n    page.get_by_role("button", name="Save invoice", exact=True).click()',
    "b8": 'def run(page, row, ctx):\n    expect(page.get_by_text("saved.")).to_be_visible(timeout=5000)',
    "b9": 'def run(page, row, ctx):\n    if page.get_by_text("saved.").count() == 0:\n        raise AssertionError("not saved")',
}

ROW = {"Client": "Paper Moth Bakery", "Tax ID": "99815188447", "Date": datetime(2026, 9, 10),
       "Amount": 4216.96, "Description": "Notebooks and pens"}


def plan_and_code():
    plan = good_plan()
    plan["demo"] = {"row_index": 0, "values": {"Client": "Paper Moth Bakery", "Tax ID (RUC)": "99815188447",
                                               "Invoice date": "10/09/2026", "Amount (PEN)": "4,216.96",
                                               "Description": "Notebooks and pens"}}
    code = dict(CODE_BASE)
    for b in plan["blocks"]:
        if b["type"] == "fill_field":
            code[b["id"]] = FILL.format(label=b["field"]["label"], column=b["column"], transform=repr(b["transform"]))
    return plan, code


class GuardTests(unittest.TestCase):
    def test_normal_block_is_accepted(self):
        for source in plan_and_code()[1].values():
            codegen.check_code(source)

    def test_escapes_are_rejected(self):
        for bad in ["import os\ndef run(page, row, ctx): pass",
                    "def run(page, row, ctx):\n    open('x', 'w')",
                    "def run(page, row, ctx):\n    row.__class__",
                    "def run(page, row, ctx):\n    '{0.__class__}'.format(row)",
                    "def run(page, row, ctx):\n    __import__('os')",
                    "def run(page, row, ctx):\n    getattr(row, 'x')",
                    "def run(page, row):\n    pass",
                    "def run(page, row, ctx): pass\nx = 1"]:
            with self.assertRaises(codegen.UnsafeCode, msg=bad):
                codegen.check_code(bad)

    def test_compiled_block_has_no_dangerous_builtins(self):
        fn = codegen.compile_block("def run(page, row, ctx):\n    ctx['b'] = sorted(['b', 'a'])", {})
        ctx = {}
        fn(None, None, ctx)
        self.assertEqual(ctx["b"], ["a", "b"])
        self.assertNotIn("open", codegen.SAFE_BUILTINS)


class ProblemTests(unittest.TestCase):
    def test_mismatching_field_blames_its_block(self):
        plan, _ = plan_and_code()
        filled = dict(plan["demo"]["values"], **{"Invoice date": "2026-09-10"})
        problems = build.block_problems(plan, runner.RowResult(0, True, filled))
        self.assertEqual([p[0] for p in problems], ["b4"])

    def test_exception_blames_the_block_that_raised(self):
        plan, _ = plan_and_code()
        problems = build.block_problems(plan, runner.RowResult(0, False, {}, "b7", "Timeout"))
        self.assertEqual(problems, [("b7", "Timeout")])

    def test_stored_column_is_traced_to_its_fill_block(self):
        plan, _ = plan_and_code()
        self.assertEqual(build.blame_column(plan, "amount"), "b5")
        self.assertEqual(build.blame_column(plan, "tax_id"), "b3")
        self.assertEqual(build.blame_column(plan, "created_by"), "b7")  # nothing matches -> save


class SandboxRunTests(unittest.TestCase):
    """A real headless browser filling the real portal app, started as a sandbox."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        cls.port = sandbox._free_port()
        cls.db = Path(tempfile.mkdtemp()) / "base.db"
        cls.proc = subprocess.Popen([sys.executable, str(REPO / "demo" / "portal" / "app.py"), "--version", "v1",
                                     "--db", str(cls.db), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.url = f"http://127.0.0.1:{cls.port}/"
        import time
        for _ in range(100):
            try:
                sandbox.portal_info(cls.url)
                break
            except sandbox.NoSandbox:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    def test_machine_fills_the_portal_and_the_record_matches(self):
        plan, code = plan_and_code()
        plan["target"] = {"url": self.url}
        events = []
        with sandbox.PortalSandbox(self.url) as sb:
            [result] = runner.run_rows(plan, code, [(0, ROW)], sb.url, on_event=events.append)
            [stored] = sb.records()
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.filled["Amount (PEN)"], "4,216.96")
        self.assertEqual((stored["date"], stored["amount"]), ("2026-09-10", 4216.96))
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[:2], ["run_start", "row_in"])
        self.assertEqual(kinds.count("block_done"), len(plan["blocks"]))
        self.assertEqual(kinds[-2:], ["row_out", "run_end"])
        self.assertEqual(sandbox.records(self.url), [])  # the sandbox never touched the real portal

    def test_broken_block_stops_the_row_and_is_reported(self):
        plan, code = plan_and_code()
        code["b5"] = code["b5"].replace("Amount (PEN)", "Total due (PEN)", 1)  # like portal v2
        with sandbox.PortalSandbox(self.url) as sb:
            [result] = runner.run_rows(plan, code, [(0, ROW)], sb.url, timeout_ms=1500)
            self.assertEqual(sb.records(), [])
        self.assertEqual(result.error_block, "b5")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
