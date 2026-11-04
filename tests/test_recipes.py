"""Recipes: the sandbox, finding the demo row, matching requests, verification, apply/undo."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))

import make_letters  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.recipes import docs, learn, run, sandbox  # noqa: E402
from edith.recipes.store import RecipeStore  # noqa: E402

# A correct level-1 program for the demo letter, written by hand for the tests (the app's
# programs are written by Muse Spark).
GOOD = '''
import docx
import openpyxl
from datetime import datetime
from pathlib import Path

def run(sheet, template, key, out_dir):
    ws = openpyxl.load_workbook(sheet, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h) for h in rows[0]]
    row = None
    for r in rows[1:]:
        d = dict(zip(head, r))
        if str(d.get("Client") or "").strip().lower() == key.strip().lower():
            row = d
    def amount(v):
        if v in (None, ""):
            return ""
        if isinstance(v, str):
            v = float(v.split(",")[0] + v.split(",")[1]) if "," in v else float(v)
        return f"{float(v):,.2f}"
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
    values = {"CLIENT": str(row["Client"]).strip(), "TAX ID": str(row["Tax ID"] or "").strip(),
              "DATE": when(row["Date"]), "AMOUNT": amount(row["Amount"]),
              "DESCRIPTION": str(row["Description"] or "").strip()}
    d = docx.Document(template)
    for p in d.paragraphs:
        for r in p.runs:
            for k in ("TAX ID", "CLIENT", "DATE", "AMOUNT", "DESCRIPTION"):
                if k in r.text:
                    r.text = r.text.replace(k, values[k])
    out = Path(out_dir) / ("Payment reminder - " + values["CLIENT"] + ".docx")
    d.save(str(out))
    return str(out)
'''


def program_doing(body: str) -> str:
    return "def run(sheet, template, key, out_dir):\n" + "".join(f"    {line}\n" for line in body.splitlines())


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.files = make_letters.make(self.tmp / "user", hand=True)
        self.args = (str(self.files["sheet"]), str(self.files["template"]), "Paper Moth Bakery", str(self.tmp / "out"))

    def test_the_whitelist_rejects_dangerous_code_before_running(self):
        for bad in ("import os\ndef run(sheet, template, key, out_dir):\n    os.system('calc')",
                    "import subprocess\ndef run(sheet, template, key, out_dir):\n    pass",
                    program_doing("eval('1')"), program_doing("x = ().__class__"),
                    program_doing("from pathlib import Path\nPath(sheet).unlink()"),
                    "def run(a, b):\n    pass", "print('top level')\ndef run(sheet, template, key, out_dir):\n    pass"):
            with self.assertRaises(sandbox.UnsafeProgram, msg=bad):
                sandbox.check(bad, 1)
        sandbox.check(GOOD, 1)

    def test_the_process_cannot_write_or_read_outside_or_use_the_network(self):
        victim = self.tmp / "victim.txt"
        outside = Path(os.environ.get("USERPROFILE", self.tmp)) / "NTUSER.DAT"
        for body, why in ((f"open({str(victim)!r}, 'w').write('x')", "writing outside"),
                          (f"open({str(outside)!r}, 'rb').read(1)", "reading outside"),
                          ("import pathlib\npathlib.Path(sheet).write_text('x')", "writing outside"),
                          (f"from pathlib import Path\nPath(sheet).rename({str(self.tmp / 'moved.xlsx')!r})", "rename")):
            out = sandbox.run(program_doing(body), 1, *self.args)
            self.assertFalse(out.ok, body)
            self.assertIn("not allowed", out.error, body)
        self.assertFalse(victim.exists())
        self.assertTrue(self.files["sheet"].exists())

    def test_a_good_program_makes_the_users_document(self):
        out = sandbox.run(GOOD, 1, *self.args)
        self.assertTrue(out.ok, out.error)
        self.assertEqual(docs.compare(self.files["result"], out.out), [])
        self.assertLess(out.seconds, 3)

    def test_the_emergency_stop_kills_a_running_program(self):
        import threading
        import time

        slow = program_doing("while True:\n    pass")
        box = []
        t = threading.Thread(target=lambda: box.append(sandbox.run(slow, 1, *self.args)))
        t.start()
        time.sleep(1.5)
        self.assertEqual(sandbox.stop_all(), 1)
        t.join(10)
        self.assertFalse(box[0].ok)


class LearnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.files = make_letters.make(self.tmp / "user", hand=True)
        self.demo = learn.Demo(self.files["sheet"], self.files["template"], self.files["result"], 80)

    def test_the_demo_row_and_key_are_found_without_a_model(self):
        _h, rows, row, key = learn.demo_row(self.files["sheet"], self.files["result"])
        self.assertEqual(key, "Client")
        self.assertEqual(row["Client"], "Paper Moth Bakery")

    def test_find_demo_from_recent_files(self):
        import time

        now = time.time()
        recent = [(self.files["template"], now - 90), (self.files["sheet"], now - 120), (self.files["result"], now - 5)]
        d = learn.find_demo(30, recent)
        self.assertEqual((d.sheet, d.template, d.result), (self.files["sheet"], self.files["template"], self.files["result"]))

    def test_hard_coded_values_are_caught(self):
        cheat = GOOD.replace('str(row["Client"]).strip()', '"Paper Moth Bakery"')
        _h, rows, row, key = learn.demo_row(self.files["sheet"], self.files["result"])
        c = learn.verify(cheat, 1, self.demo, rows, row, key, self.tmp / "w")
        self.assertFalse(c.ok)
        self.assertIn("literals", c.problems[0])

    def test_compile_verifies_corrects_and_saves(self):
        cfg = load_config()
        store = RecipeStore(cfg)
        replies = iter(["NAME: Payment reminder\nSTEPS:\n- a\n```python\ndef run(sheet, template, key, out_dir):\n"
                        "    return None\n```", "NAME: Payment reminder\nSTEPS:\n- Find the client\n```python\n" + GOOD + "```"])
        seen = []
        r = learn.compile_demo(cfg, self.demo, ask=lambda s, u: seen.append(u) or next(replies), store=store)
        self.assertEqual((r.level, r.verified["attempts"], r.key_column), (1, 2, "Client"))
        self.assertIn("did not pass the verification", seen[1])
        self.assertTrue((store.proof(r) / self.files["result"].name).exists())

    def test_no_level_passes_raises(self):
        bad = "NAME: x\nSTEPS:\n- a\n```python\ndef run(sheet, template, key, out_dir):\n    return None\n```"
        with self.assertRaises(learn.CannotLearn):
            learn.compile_demo(load_config(), self.demo, ask=lambda s, u: bad, levels=(1,), store=RecipeStore(load_config()))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.files = make_letters.make(self.tmp / "user", hand=True)
        cfg = load_config()
        cfg.root = self.tmp / "home"
        self.store = RecipeStore(cfg)
        demo = learn.Demo(self.files["sheet"], self.files["template"], self.files["result"], 80)
        self.recipe = learn.compile_demo(cfg, demo, ask=lambda s, u: "NAME: Payment reminder\nSTEPS:\n- a\n```python\n"
                                         + GOOD + "```", store=self.store)

    def test_requests_are_matched_without_a_model(self):
        rs = [self.recipe]
        self.assertEqual(run.match("haz lo mismo para Panadería Ñandú", rs).keys, ["Panadería Ñandú"])
        self.assertEqual(run.match("hazlo para Lazy Comet", rs).ambiguous, ["Lazy Comet Coffee Bar", "Lazy Comet Print House"])
        self.assertEqual(run.match("do the same for lazy comet coffee", rs).keys, ["Lazy Comet Coffee Bar"])
        self.assertEqual(sorted(run.match("para Ñandú y Acuña", rs).keys), ["Librería Acuña", "Panadería Ñandú"])
        self.assertEqual(len(run.match("hazlo para todos", rs).keys), 30)
        self.assertIsNone(run.match("¿cuánto pagó Lazy Comet Coffee Bar?", rs))
        self.assertIsNone(run.match("hola edith", rs))

    def test_prepare_apply_undo(self):
        runner = run.Runner(self.store)
        p = runner.prepare(run.match("haz lo mismo para Panadería Ñandú", [self.recipe]))
        final = self.files["result"].parent / "Payment reminder - Panadería Ñandú.docx"
        self.assertFalse(final.exists())                     # the preview touches nothing of the user's
        self.assertIn("Dear Panadería Ñandú,", p.new_text)
        self.assertLess(p.seconds, 3)
        runner.apply(p)
        self.assertTrue(final.exists())
        self.assertEqual(runner.undo(), (True, ""))
        self.assertFalse(final.exists())
        self.assertEqual(runner.undo(), (False, "nothing"))

    def test_undo_restores_an_overwritten_file_and_refuses_after_user_changes(self):
        runner = run.Runner(self.store)
        m = run.match("do the same for Paper Moth Bakery", [self.recipe])
        before = self.files["result"].read_bytes()
        runner.apply(runner.prepare(m))
        self.assertEqual(runner.undo(), (True, ""))
        self.assertEqual(self.files["result"].read_bytes(), before)
        runner.apply(runner.prepare(m))
        self.files["result"].write_bytes(b"changed by the user")
        self.assertEqual(runner.undo(), (False, "changed_since"))

    def test_a_broken_proof_stops_the_run(self):
        (self.store.proof(self.recipe) / self.files["result"].name).unlink()
        with self.assertRaises(RuntimeError):
            run.Runner(self.store).prepare(run.match("para Panadería Ñandú", [self.recipe]))


if __name__ == "__main__":
    unittest.main()
