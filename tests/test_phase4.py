"""Phase 4 (final version): edit from memory. No real window is touched and no model is called."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))

from edith.brain import answer, edit, notes  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.edit import target  # noqa: E402
from edith.ui import agent as agent_mod  # noqa: E402
from edith.ui.agent import text as ui_text  # noqa: E402


class IntentTests(unittest.TestCase):
    def test_edit_requests(self):
        for r in ("put in my notes what the teacher explained", "reply to this email with the data I saw earlier",
                  "add the exam date here", "pasa a mis apuntes lo que explicó el profe",
                  "responde este correo con los datos que vi hace rato", "contesta con el monto"):
            self.assertTrue(edit.is_edit(r), r)

    def test_questions_and_tasks_are_not_edits(self):
        for r in ("what was the answer to it?", "when is the exam?", "¿qué dijo el profe?", "do the rest",
                  "what did they put in the notes"):
            self.assertFalse(edit.is_edit(r), r)
        self.assertTrue(agent_mod.is_task("do the rest"))
        self.assertTrue(agent_mod.UNDO.search("undo"))
        self.assertTrue(agent_mod.UNDO.search("deshazlo"))
        self.assertFalse(agent_mod.UNDO.search("what did I undo?"))


class ParseTests(unittest.TestCase):
    now = datetime(2026, 11, 3, 10, 45).timestamp()

    def test_proposal(self):
        p = edit.parse('{"mode": "append", "text": "- Exam: Friday 13", "source_time": "10:41:07", '
                       '"source": "class audio"}', self.now)
        self.assertEqual((p.mode, p.text, p.source), ("append", "- Exam: Friday 13", "class audio"))
        self.assertEqual(datetime.fromtimestamp(p.source_ts).strftime("%H:%M:%S"), "10:41:07")

    def test_fenced_json_and_unknown_mode(self):
        p = edit.parse('```json\n{"mode": "rewrite", "text": "x"}\n```', self.now)
        self.assertEqual(p.mode, "append")
        self.assertIsNone(p.source_ts)

    def test_no_proposal(self):
        self.assertEqual(edit.parse('{"error": "no email data in memory"}', self.now), "no email data in memory")
        self.assertEqual(edit.parse("sure, here you go", self.now), "no_json")
        self.assertEqual(edit.parse('{"mode": "append", "text": "  "}', self.now), "empty")

    def test_source_frame_is_the_one_on_screen_then(self):
        frames = [{"ts": 100.0, "path": "a"}, {"ts": 200.0, "path": "b"}, {"ts": 300.0, "path": "c"}]
        self.assertEqual(edit.source_frame(frames, 250.0)["path"], "b")
        self.assertEqual(edit.source_frame(frames, 50.0)["path"], "a")
        self.assertEqual(edit.source_frame(frames, None)["path"], "c")
        self.assertIsNone(edit.source_frame([], 1.0))


class PreviewTests(unittest.TestCase):
    def test_append_shows_the_new_text_in_the_accent(self):
        p = edit.Proposal("append", "- Exam <Friday>", "", None)
        html = edit.preview_html("Week 9\n- Recursion", p, "#5B7CFA", "#888888", "#222222")
        self.assertIn('color:#5B7CFA;">- Exam &lt;Friday&gt;', html)
        self.assertIn("- Recursion", html)
        self.assertNotIn("line-through", html)

    def test_replace_strikes_removed_words_in_gray(self):
        p = edit.Proposal("replace", "The exam is on Friday", "", None)
        html = edit.preview_html("The exam is on Monday", p, "#ACC", "#GRY", "#TXT")
        self.assertIn('color:#GRY; text-decoration:line-through;">Monday', html)
        self.assertIn('color:#ACC;">Friday', html)
        self.assertIn('color:#TXT;">The', html)


class FakeControl:
    """Just enough of a uiautomation control: a ValuePattern over a string."""

    def __init__(self, text):
        self.text = text

    def GetPropertyValue(self, _prop):
        return False

    def GetPattern(self, pattern):
        import uiautomation as auto

        if pattern == auto.PatternId.ValuePattern:
            return mock.Mock(Value=self.text)
        return None


class TypistTests(unittest.TestCase):
    def target_with(self, ctrl):
        return target.Target(1, "Notes", "notepad.exe", "DocumentControl", "", (0, 0, 10, 10), ctrl.text, ctrl)

    def test_normalize(self):
        self.assertEqual(target.normalize("a\r\nb \rc￼"), "a\nb\nc")
        self.assertEqual(target.normalize("￼"), "")

    def test_nothing_to_undo(self):
        self.assertEqual(target.Typist().undo(), (False, "nothing"))

    def test_undo_is_refused_when_the_user_changed_the_text_since(self):
        ctrl = FakeControl("notes\nEdith's line\nuser's own line")
        typist = target.Typist()
        typist.last = target.Applied(self.target_with(ctrl), "notes", "notes\nEdith's line", time.time())
        with mock.patch.object(target, "_keys") as keys, mock.patch.object(target._u, "IsWindow", return_value=True):
            self.assertEqual(typist.undo(), (False, "changed_since"))
        keys.assert_not_called()                                  # not a single key was sent
        self.assertIsNotNone(typist.last)

    def test_apply_refuses_when_the_window_cannot_be_brought_back(self):
        ctrl = FakeControl("notes")
        with mock.patch.object(target.Typist, "_bring_back", return_value=False), \
                mock.patch.object(target, "_keys") as keys:
            self.assertEqual(target.Typist().apply(self.target_with(ctrl), "append", "x"), (False, "window_gone"))
        keys.assert_not_called()


class RouterEditTests(unittest.TestCase):
    def test_the_edit_prompt_and_the_field_reach_the_model(self):
        cfg = load_config()
        tmp = Path(tempfile.mkdtemp())
        cfg.root = tmp
        for d in (cfg.frames_dir, cfg.audio_dir):
            d.mkdir(parents=True, exist_ok=True)
        calls = []

        class Local:
            installed = True

            def ensure_started(self):
                pass

            def generate(self, system, user, **kw):
                calls.append((system, user))
                return '{"mode": "append", "text": "- Exam: Friday"}'

        r = answer.Router(cfg, Local(), lambda k, **kw: ui_text("en", k, **kw), notes=notes.NoteBook(cfg))
        r.local_only = True
        block = edit.target_block("Notes", "notepad.exe", "DocumentControl", "Text editor", "Week 9")
        reply = r.respond("put the exam in my notes", system=edit.EDIT_PROMPT, extra=block)
        self.assertEqual(calls[0][0], edit.EDIT_PROMPT)
        self.assertIn("Week 9", calls[0][1])
        self.assertLess(calls[0][1].index("Week 9"), calls[0][1].index("put the exam in my notes"))
        self.assertEqual(edit.parse(reply.text, time.time()).text, "- Exam: Friday")
        r.respond("when?")                                        # the next answer uses the normal prompt again
        self.assertNotEqual(calls[1][0], edit.EDIT_PROMPT)


if __name__ == "__main__":
    unittest.main()
