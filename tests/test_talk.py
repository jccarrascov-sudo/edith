"""Conversation: small talk and general questions are answered naturally, with no memory sent."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))

from edith.brain import answer, notes, talk  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.memory.timeline import Timeline  # noqa: E402
from edith.ui.agent import text as ui_text  # noqa: E402


class KindTests(unittest.TestCase):
    def test_memory_small_talk_and_unsure(self):
        for q in ("what did the teacher say about the exam?", "what was on my screen 5 min ago?",
                  "¿qué dijo el profe?", "resume lo que vi esta mañana", "did I send the invoice?"):
            self.assertEqual(talk.kind(q), "memory", q)
        for q in ("hola", "Hi!", "gracias!", "¿cómo estás?", "who are you?", "buenos días Edith"):
            self.assertEqual(talk.kind(q), "chat", q)
        for q in ("what is recursion?", "¿qué hago antes de un examen?", "write me a haiku about rain"):
            self.assertEqual(talk.kind(q), "unsure", q)
        for q in ("what does this error mean?", "¿qué significa este error?", "explícame esto"):
            self.assertEqual(talk.kind(q), "screen", q)
        for q in ("¿cómo automatizo esto?", "help me automate these invoices", "esto es muy repetitivo"):
            self.assertEqual(talk.kind(q), "automate", q)

    def test_the_questions_of_nov_4_go_to_the_memory(self):
        # Asked live on Nov 4; they went to the chat, which said it kept no screenshots.
        for q in ("pero tu no tomas caputras?", "pero tú no tomas capturas?", "10 minutos ?? de memoria no era",
                  "no viste como borre este nuevo word?", "¿te acuerdas de lo que había en el Word?",
                  "do you keep screenshots?", "what was there before I deleted it?"):
            self.assertEqual(talk.kind(q), "memory", q)
        for q in ("¿qué es la memoria RAM?", "what is a memory leak?"):
            self.assertNotEqual(talk.kind(q), "memory", q)

    def test_a_denial_of_the_memory_is_recognised(self):
        for a in ("No, yo solo veo lo que tienes en pantalla en el momento, no guardo capturas.",
                  "No, solo veo lo que tienes en este momento en pantalla, no guardo lo de antes.",
                  "Tienes razón, así debería ser, pero en esta conversación no me llegó ese recuerdo y no puedo verlo.",
                  "I don't keep screenshots, I only see what is on your screen."):
            self.assertTrue(talk.DENIES_MEMORY.search(a), a)
        self.assertFalse(talk.DENIES_MEMORY.search("Sí, guardo los últimos 10 minutos en tu PC."))

    def test_the_restore_requests_of_nov_4_are_edits(self):
        from edith.brain.edit import is_edit

        for r in ("regresalo a como estaba", "escribelo n mi word vacio", "escríbelo en mi Word vacío",
                  "pégalo aquí", "put it back as it was", "restore the text"):
            self.assertTrue(is_edit(r), r)
        for r in ("¿qué pasa aquí?", "hola", "what is recursion?"):
            self.assertFalse(is_edit(r), r)

    def test_the_hidden_token_is_never_shown(self):
        shown = []
        g = talk.Gate(shown.append)
        for piece in ("[[", "MEM", "ORY]]"):
            g(piece)
        self.assertTrue(g.escalated)
        self.assertEqual(shown, [])
        shown.clear()
        g = talk.Gate(shown.append)
        for piece in ("Hi", " there", "!"):
            g(piece)
        self.assertEqual("".join(shown), "Hi there!")
        self.assertFalse(g.escalated)


class ChatRoutingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.cfg.root = Path(tempfile.mkdtemp())
        for d in (self.cfg.frames_dir, self.cfg.audio_dir):
            d.mkdir(parents=True, exist_ok=True)
        Timeline(self.cfg).append({"type": "transcript", "ts": time.time() - 30, "start": time.time() - 30,
                                   "end": time.time() - 25, "text": "secret screen words", "lang": "en"})

    def router(self):
        return answer.Router(self.cfg, None, lambda k, **kw: ui_text("en", k, **kw), notes=notes.NoteBook(self.cfg))

    def test_small_talk_sends_only_the_question(self):
        fake = mock.Mock()
        fake.chat_stream.side_effect = lambda purpose, on_token=None, **kw: (on_token("Hi! How can I help?") or
                                                                               ("Hi! How can I help?", None, 0.0001))
        shown = []
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            reply = self.router().respond("hola", on_token=shown.append)
        sent = fake.chat_stream.call_args.kwargs["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "user"])
        self.assertEqual(sent[1]["content"], "hola")
        self.assertNotIn("secret screen words", str(sent))
        self.assertEqual(reply.text, "Hi! How can I help?")
        self.assertEqual("".join(shown), "Hi! How can I help?")

    def test_unsure_question_escalates_to_the_memory_when_the_model_asks(self):
        replies = iter([(talk.NEED_MEMORY, None, 0.0001), ("At 10:41 they said Friday.", None, 0.002)])

        def stream(purpose, on_token=None, **kw):
            text, usage, cost = next(replies)
            if on_token:
                on_token(text)
            return text, usage, cost

        fake = mock.Mock()
        fake.chat_stream.side_effect = stream
        shown = []
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            reply = self.router().respond("and the exam, when is it?", on_token=shown.append)
        self.assertEqual(fake.chat_stream.call_count, 2)
        second = str(fake.chat_stream.call_args_list[1].kwargs["messages"])
        self.assertIn("secret screen words", second)                 # now with the memory
        self.assertEqual(reply.text, "At 10:41 they said Friday.")
        self.assertNotIn(talk.NEED_MEMORY, "".join(shown))


    def test_a_chat_answer_denying_the_memory_is_replaced_by_the_memory(self):
        replies = iter([("No, solo veo lo que tienes en este momento en pantalla, no guardo lo de antes.", None, 0.0001),
                        ("Sí: a las 13:51 tenías el ensayo abierto.", None, 0.002)])

        def stream(purpose, on_token=None, **kw):
            text, usage, cost = next(replies)
            if on_token:
                on_token(text)
            return text, usage, cost

        fake = mock.Mock()
        fake.chat_stream.side_effect = stream
        shown = []
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            reply = self.router().respond("¿y eso es todo lo que ves?", on_token=shown.append)
        self.assertEqual(fake.chat_stream.call_count, 2)
        self.assertIn("secret screen words", str(fake.chat_stream.call_args_list[1].kwargs["messages"]))
        self.assertEqual(reply.text, "Sí: a las 13:51 tenías el ensayo abierto.")
        self.assertIn(talk.RESET, shown)                               # the panel cleared the denial
        self.assertNotIn("no guardo", "".join(shown[shown.index(talk.RESET):]))

    def test_the_chat_prompt_says_edith_has_a_memory(self):
        p = talk.CHAT_PROMPT.format(token=talk.NEED_MEMORY, minutes=10)
        self.assertIn("You DO have a memory", p)
        self.assertIn("10 minutes", p)


class ScreenAndGuideTests(ChatRoutingTests):
    def test_a_doubt_about_the_screen_gets_the_screen_as_it_is_now(self):
        from PIL import Image

        Image.new("RGB", (320, 180), "white").save(self.cfg.frames_dir / "now.jpg")
        tl = Timeline(self.cfg)
        tl.append({"type": "frame", "ts": time.time() - 100, "path": "frames/now.jpg", "score": 0.1,
                   "window": "Terminal", "process": "wt.exe", "screen": [320, 180]})
        tl.append({"type": "text", "ts": time.time() - 100, "window": "Terminal", "process": "wt.exe",
                   "text": "KeyError: 'amount'"})
        fake = mock.Mock()
        fake.chat_stream.return_value = ("A missing column.", None, 0.002)
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            self.router().respond("what does this error mean?")
        content = fake.chat_stream.call_args.kwargs["messages"][1]["content"]
        self.assertTrue(any(part.get("type") == "image_url" for part in content))   # the newest frame
        self.assertIn("KeyError: 'amount'", content[0]["text"])                    # and the window's text

    def test_automation_questions_get_the_guide(self):
        fake = mock.Mock()
        fake.chat_stream.return_value = ("Fill one by hand...", None, 0.002)
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            self.router().respond("how can I automate this?")
        self.assertEqual(fake.chat_stream.call_args.kwargs["messages"][0]["content"], talk.GUIDE_PROMPT)


if __name__ == "__main__":
    unittest.main()
