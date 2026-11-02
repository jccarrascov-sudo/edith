"""Workshop: code guard, problem attribution, and a real headless run on a sandbox portal."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PANTALLITA_HOME", tempfile.mkdtemp(prefix="pantallita-test-"))

from pantallita.workshop import build, codegen, proof, runner, sandbox  # noqa: E402

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


USER_RECORD = {"id": 1, "client": "Paper Moth Bakery", "tax_id": "99815188447", "date": "2026-09-10",
               "amount": 4216.96, "description": "Notebooks and pens", "subtotal": None,
               "portal_version": "v1", "created_at": "2026-11-01T18:48:33"}
OTHER_ROWS = [
    {"Client": "Velvet Cactus Supplies", "Tax ID": "99940557616", "Date": datetime(2026, 10, 8),
     "Amount": 2697.0, "Description": "Event photography"},
    {"Client": "Panadería Ñandú", "Tax ID": "99273177848", "Date": "2026-10-03",
     "Amount": "1,787.26", "Description": "Pan de maíz y empanadas"},
    {"Client": "Round Pebble Print House", "Tax ID": "99222646861", "Date": datetime(2026, 9, 25),
     "Amount": None, "Description": "Ergonomic chairs x3"},
]


class ProofTests(unittest.TestCase):
    def test_relation_is_learned_from_the_demo_and_applied_to_other_rows(self):
        relation = proof.relate(USER_RECORD, ROW)
        self.assertEqual(relation["date"], {"column": "Date", "as": "date"})
        self.assertEqual(relation["amount"], {"column": "Amount", "as": "number"})
        self.assertEqual(relation["tax_id"], {"column": "Tax ID", "as": "text"})
        self.assertEqual(relation["subtotal"], {"const": None})
        expected = proof.expected_record(relation, OTHER_ROWS[1])
        self.assertEqual((expected["client"], expected["date"], expected["amount"]),
                         ("Panadería Ñandú", "2026-10-03", 1787.26))

    def test_extra_rows_skip_empty_ones_and_prefer_different_cell_types(self):
        plan, _ = plan_and_code()
        rows = [ROW] + OTHER_ROWS
        picked = proof.pick_extra_rows(plan, rows, 2)
        self.assertEqual(sorted(picked), [1, 2])  # row 3 has an empty Amount

    def test_dates_survive_the_proof_file(self):
        self.assertEqual(proof.decode_row(proof.encode_row(ROW)), ROW)

    def test_summary_counts_skips_and_totals(self):
        plan, _ = plan_and_code()
        s = build.summary(plan, list(enumerate(OTHER_ROWS)))
        self.assertEqual(s["send"], 2)
        self.assertEqual(s["skipped"], [(2, "empty Amount")])
        self.assertEqual(s["totals"], {"Amount (PEN)": round(2697.0 + 1787.26, 2)})

    def test_golden_rule_nothing_unverified_enters_the_workshop(self):
        plan, code = plan_and_code()
        with self.assertRaises(build.BuildError):
            build.save_tool(plan, code, build.Verification(ok=False, attempts=3))


class AntiCheatTests(unittest.TestCase):
    def setUp(self):
        self.texts, self.numbers = codegen.data_values([ROW] + OTHER_ROWS, {"Amount (PEN)": "4,216.96"})
        self.block = {"type": "fill_field", "column": "Client"}

    def test_generated_style_code_passes(self):
        plan, code = plan_and_code()
        for b in plan["blocks"]:
            codegen.check_anti_cheat(code[b["id"]], b, self.texts, self.numbers)

    def test_hard_coded_values_are_rejected(self):
        for src in ['def run(page, row, ctx):\n    page.fill("#client", "Paper Moth Bakery")',
                    'def run(page, row, ctx):\n    page.fill("#amount", "4,216.96")',
                    'def run(page, row, ctx):\n    x = 2697.0',
                    'def run(page, row, ctx):\n    page.fill("#client", "panadería ñandú")',
                    'def run(page, row, ctx):\n    page.fill("#client", "Someone")']:  # not from row[...]
            with self.assertRaises(codegen.UnsafeCode, msg=src):
                codegen.check_anti_cheat(src, self.block, self.texts, self.numbers)

    def test_timeouts_are_not_data(self):
        codegen.check_anti_cheat('def run(page, row, ctx):\n    page.wait_for_timeout(5000)',
                                 {"type": "wait"}, self.texts, self.numbers)


class VerifyTests(unittest.TestCase):
    """Equivalence verification on the real portal app (sandbox), demo + extra rows."""

    @classmethod
    def setUpClass(cls):
        SandboxRunTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        SandboxRunTests.tearDownClass.__func__(cls)

    def cases(self, plan):
        return build.cases_for(plan, [ROW] + OTHER_ROWS, ROW, USER_RECORD, extra=2)

    def test_correct_machine_passes_on_demo_and_extra_rows(self):
        plan, code = plan_and_code()
        plan["target"] = {"url": self.url}
        problems, stamp = build.verify(plan, code, self.cases(plan))
        self.assertEqual(problems, [])
        # the row with text date + text amount is the more different one, so it goes first
        self.assertEqual([c["case"] for c in stamp["cases"]], ["your demo", "extra row 3", "extra row 2"])
        matched, total = build.stamp_counts(stamp)
        self.assertEqual((matched, total), (33, 33))

    def test_wrong_date_format_is_caught_in_its_block(self):
        plan, code = plan_and_code()
        plan["target"] = {"url": self.url}
        code["b4"] = code["b4"].replace("%d/%m/%Y", "%Y-%m-%d")
        problems, _ = build.verify(plan, code, self.cases(plan))
        self.assertEqual({b for b, _ in problems}, {"b4"})
        self.assertEqual(len(problems), 3)  # the demo and both extra rows

    def test_empty_cell_row_is_skipped_and_the_run_goes_on(self):
        plan, code = plan_and_code()
        events = []
        with sandbox.PortalSandbox(self.url) as sb:
            results = runner.run_rows(plan, code, list(enumerate([OTHER_ROWS[2], OTHER_ROWS[0]])), sb.url,
                                      on_event=events.append)
            stored = sb.records()
        self.assertEqual(results[0].skipped, "empty Amount")
        self.assertTrue(results[1].ok)
        self.assertEqual(len(stored), 1)
        self.assertIn({"event": "row_skipped", "row": 0, "reason": "empty Amount"}, events)
        self.assertEqual(events[0]["level"], "page")


if __name__ == "__main__":
    unittest.main()
