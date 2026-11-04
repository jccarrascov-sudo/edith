"""Escalation: a row that fails twice -> Muse Spark replans -> the next level -> a one-line stop."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_letters  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.recipes import docs, escalate, learn, run  # noqa: E402
from edith.recipes.store import RecipeStore  # noqa: E402
from test_recipes import GOOD  # noqa: E402

# The demo program with a bug that only shows on one row (forced, like the demo-check does).
BUGGY = GOOD.replace("    d = docx.Document(template)",
                     "    if values[\"CLIENT\"].startswith(\"Taller\"):\n"
                     "        raise ValueError(\"cannot read this row\")\n"
                     "    d = docx.Document(template)")
LEVEL2 = GOOD.replace("def run(sheet, template, key, out_dir):", "def run(sheet, template, key, out_dir, word):")


def fenced(program: str) -> str:
    return f"NAME: Payment reminder\nSTEPS:\n- a\n```python\n{program}```"


class EscalationTests(unittest.TestCase):
    def setUp(self):
        self.files = make_letters.make(Path(tempfile.mkdtemp()), hand=True)
        cfg = load_config()
        cfg.root = Path(tempfile.mkdtemp())
        self.cfg = cfg
        self.store = RecipeStore(cfg)
        demo = learn.Demo(self.files["sheet"], self.files["template"], self.files["result"], 60)
        self.r = learn.compile_demo(cfg, demo, ask=lambda s, u: fenced(GOOD), store=self.store)
        self.store.save(self.r, BUGGY)                       # the bug appears later, on one row

    def runner(self, ask, levels=(1, 2)):
        rn = run.Runner(self.store)
        statuses = []

        def esc(r, key, error, line):
            old = learn.LEVELS
            learn.LEVELS = levels
            try:
                return escalate.escalate(self.cfg, self.store, r, key, error, line, ask=ask,
                                         on_status=lambda k, kw: statuses.append(k))
            finally:
                learn.LEVELS = old
        rn.escalator = esc
        return rn, statuses

    def test_muse_spark_replans_after_two_failures(self):
        seen = []
        rn, statuses = self.runner(lambda s, u: seen.append(u) or fenced(GOOD))
        p = rn.prepare(run.match("para Taller Ñuñoa", self.store.all()))
        self.assertEqual(p.failed, [])
        self.assertIn("Dear Taller Ñuñoa,", p.new_text)
        self.assertIn("cannot read this row", seen[0])                # it got the real error
        self.assertEqual(statuses, ["escalate_replan"])
        self.assertEqual(self.store.all()[0].level, 1)
        self.assertNotIn("cannot read this row", self.store.program(self.store.all()[0]))

    def test_a_failed_replan_goes_down_to_the_next_level(self):
        # the replan keeps the bug; the level-2 compile (its prompt is about COM) writes a good program
        rn, statuses = self.runner(lambda s, u: fenced(LEVEL2 if "COM" in s else BUGGY))
        p = rn.prepare(run.match("para Taller Ñuñoa", self.store.all()))
        self.assertEqual(p.failed, [])
        self.assertEqual(statuses, ["escalate_replan", "escalate_level"])
        self.assertEqual(self.store.all()[0].level, 2)
        self.assertEqual(p.recipe.level, 2)

    def test_no_level_left_stops_with_one_line(self):
        rn, statuses = self.runner(lambda s, u: fenced(BUGGY), levels=(1,))
        with self.assertRaises(RuntimeError) as ctx:
            rn.prepare(run.match("para Taller Ñuñoa", self.store.all()))
        self.assertIn("Taller Ñuñoa", str(ctx.exception))
        self.assertIn("cannot read this row", str(ctx.exception))
        self.assertEqual(self.store.program(self.store.all()[0]), BUGGY)     # nothing half-changed

    def test_other_rows_never_escalate(self):
        rn, statuses = self.runner(lambda s, u: self.fail("no model for a row that works"))
        p = rn.prepare(run.match("para Panadería Ñandú", self.store.all()))
        self.assertEqual((p.failed, statuses), ([], []))


if __name__ == "__main__":
    unittest.main()
