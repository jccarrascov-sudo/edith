"""Lessons: the preview edit applied to the document, learning with verification, discard."""

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
from edith.recipes import docs, learn, lessons, run  # noqa: E402
from edith.recipes.store import RecipeStore  # noqa: E402
from test_recipes import GOOD  # noqa: E402

KIND = GOOD.replace('    d.save(str(out))', '''    for p in d.paragraphs:
        for r in p.runs:
            if r.text == "Best regards,":
                r.text = "Kind regards,"
    d.add_paragraph("Please pay within 5 days.")
    d.save(str(out))''')
SOLES = KIND.replace('    d.save(str(out))', '''    for p in d.paragraphs:
        for r in p.runs:
            if "USD " in r.text:
                r.text = r.text.replace("USD ", "S/ ")
    d.save(str(out))''')


def reply(program: str, lesson: str) -> str:
    return f"LESSON: {lesson}\n```python\n{program}```"


class ApplyEditTests(unittest.TestCase):
    def setUp(self):
        self.files = make_letters.make(Path(tempfile.mkdtemp()), hand=True)
        self.paras = docs.shape(self.files["result"]).paragraphs

    def edit(self, text: str) -> docs.DocShape:
        return docs.shape(lessons.apply_edit(self.files["result"], text, Path(tempfile.mkdtemp()) / "x.docx"))

    def test_changed_removed_and_added_paragraphs(self):
        text = "\n".join(self.paras).replace("Best regards,", "Kind regards,")
        text = text.replace("If you have already paid, please ignore this message.\n", "") + "\nPlease pay within 5 days."
        s = self.edit(text)
        self.assertIn("Kind regards,", s.paragraphs)
        self.assertNotIn("If you have already paid, please ignore this message.", s.paragraphs)
        self.assertEqual(s.paragraphs[-1], "Please pay within 5 days.")

    def test_formatting_stays_where_it_was(self):
        s = self.edit("\n".join(self.paras).replace("Dear Paper Moth Bakery,", "Dear Paper Moth Bakery team,"))
        self.assertIn("Dear Paper Moth Bakery team,", s.paragraphs)
        self.assertEqual(s.bold, ["Payment reminder", "Paper Moth Bakery"])     # "team" is not bold

    def test_correct_is_recognized(self):
        self.assertEqual(lessons.CORRECT.match("corrige: el monto va con S/").group("what"), "el monto va con S/")
        self.assertTrue(lessons.CORRECT.match("Correct, sign with Kind regards"))
        self.assertIsNone(lessons.CORRECT.match("¿qué corrige este botón?"))


class LearnLessonTests(unittest.TestCase):
    def setUp(self):
        self.files = make_letters.make(Path(tempfile.mkdtemp()), hand=True)
        cfg = load_config()
        cfg.root = Path(tempfile.mkdtemp())
        self.cfg = cfg
        self.store = RecipeStore(cfg)
        demo = learn.Demo(self.files["sheet"], self.files["template"], self.files["result"], 60)
        self.r = learn.compile_demo(cfg, demo, ask=lambda s, u: "NAME: Payment reminder\nSTEPS:\n- a\n```python\n" + GOOD
                                    + "```", store=self.store)
        self.runner = run.Runner(self.store)

    def preview(self, request: str):
        return self.runner.prepare(run.match(request, [self.store.all()[0]]))

    def test_a_preview_edit_becomes_a_lesson_applied_on_the_next_run(self):
        p = self.preview("para Librería Acuña")
        made = p.outputs[0].made
        text = "\n".join(docs.shape(made).paragraphs).replace("Best regards,", "Kind regards,") + "\nPlease pay within 5 days."
        corrected = lessons.apply_edit(made, text, made.parent / "c" / made.name)
        replies = iter([reply(GOOD, "x"), reply(KIND, "Despedirse con Kind regards")])   # 1st does not match
        seen = []
        les = lessons.learn(self.cfg, self.store, self.r, "Librería Acuña", made, corrected=corrected,
                            ask=lambda s, u: seen.append(u) or next(replies))
        self.assertEqual(les.text, "Despedirse con Kind regards")
        self.assertIn("did not pass the check", seen[1])
        nxt = docs.shape(self.preview("para Taller Ñuñoa").outputs[0].made).paragraphs
        self.assertIn("Kind regards,", nxt)
        self.assertIn("Please pay within 5 days.", nxt)
        self.assertEqual(self.store.all()[0].demo_key, "Librería Acuña")      # the corrected letter is the proof

    def test_an_instruction_must_change_something(self):
        made = self.preview("para Librería Acuña").outputs[0].made
        les = lessons.learn(self.cfg, self.store, self.r, "Librería Acuña", made, instruction="nada",
                            ask=lambda s, u: reply(GOOD, "x"))
        self.assertIsNone(les)
        self.assertEqual(lessons.LessonStore(self.store).all(), [])

    def test_discard_puts_the_previous_program_back(self):
        made = self.preview("para Librería Acuña").outputs[0].made
        a = lessons.learn(self.cfg, self.store, self.r, "Librería Acuña", made, instruction="Kind regards",
                          ask=lambda s, u: reply(KIND, "Kind regards"))
        r = self.store.all()[0]
        made = self.preview("para Librería Acuña").outputs[0].made
        b = lessons.learn(self.cfg, self.store, r, "Librería Acuña", made, instruction="S/",
                          ask=lambda s, u: reply(SOLES, "Soles"))
        ls = lessons.LessonStore(self.store)
        self.assertEqual(ls.discard(b.id), 1)
        txt = docs.shape(self.preview("para Café Señorial").outputs[0].made).text()
        self.assertIn("USD", txt)
        self.assertIn("Kind regards,", txt)
        self.assertEqual(ls.discard(a.id), 1)
        self.assertIn("Best regards,", docs.shape(self.preview("para Café Señorial").outputs[0].made).text())
        self.assertEqual(ls.all(), [])


if __name__ == "__main__":
    unittest.main()
