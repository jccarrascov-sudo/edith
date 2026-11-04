"""Edith remembers the conversation and the person."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))

from edith import forget  # noqa: E402
from edith.brain import answer, companion, notes  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.ui.agent import text as ui_text  # noqa: E402


def cfg_in(tmp: Path):
    cfg = load_config()
    cfg.root = tmp
    for d in (cfg.frames_dir, cfg.audio_dir):
        d.mkdir(parents=True, exist_ok=True)
    cfg.companion = {**cfg.companion, "learn": False}
    return cfg


def echo_client(replies):
    """A fake cloud client that streams the given replies in order and keeps the messages."""
    it = iter(replies)
    fake = mock.Mock()

    def stream(purpose, on_token=None, **kw):
        text = next(it)
        if on_token:
            on_token(text)
        return text, None, 0.001

    fake.chat_stream.side_effect = stream
    return fake


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in(Path(tempfile.mkdtemp()))
        self.r = answer.Router(self.cfg, None, lambda k, **kw: ui_text("es", k, **kw), notes=notes.NoteBook(self.cfg))

    def test_remember_and_forget_on_request(self):
        self.assertEqual(self.r.respond("recuerda que me llamo Camila").text, "Lo recordaré.")
        self.assertEqual(self.r.respond("Remember that I prefer short answers").text, "Lo recordaré.")
        self.assertEqual(self.r.respond("recuerda que me llamo Camila").text, "Eso ya lo sabía.")
        self.assertEqual(len(self.r.profile.facts()), 2)
        self.assertEqual(self.r.respond("olvida que prefiero respuestas cortas").text, "No tenía eso guardado.")
        self.assertEqual(self.r.respond("forget that I prefer short answers").text, "Listo, lo olvidé.")
        self.assertEqual(self.r.profile.facts(), ["me llamo Camila."])

    def test_questions_about_the_past_are_not_saved_as_facts(self):
        for q in ("remember what I asked you yesterday about recursion?", "remember when I opened the PDF",
                  "recuerda lo que vimos en clase", "¿Recuerdas que tenía examen?", "olvida cuando abrí eso"):
            self.assertIsNone(companion.to_remember(q) or companion.to_forget(q), q)
        self.assertEqual(companion.to_remember("Recuerda que el examen es el viernes"), "el examen es el viernes")
        self.assertEqual(companion.to_remember("acuérdate de comprar pan"), "comprar pan")

    def test_the_footer_says_the_conversation_went_too(self):
        fake = echo_client(["Hola.", "Bien."])
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            first = self.r.respond("hola")
            second = self.r.respond("¿cómo estás?")
        self.assertIn("solo tu pregunta", first.footnote)
        self.assertIn("nuestra conversación", second.footnote)

    def test_the_thread_and_the_person_go_with_the_next_question(self):
        self.r.profile.add("Se llama Camila", "told")
        fake = echo_client(["La recursión es una función que se llama a sí misma.", "Porque divide el problema."])
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            self.r.respond("¿qué es la recursión?")
            self.r.respond("¿y por qué sirve?")
        sent = fake.chat_stream.call_args_list[1].kwargs["messages"]
        roles = [m["role"] for m in sent]
        self.assertEqual(roles, ["system", "system", "user", "assistant", "user"])
        self.assertIn("Se llama Camila", sent[1]["content"])
        self.assertEqual(sent[2]["content"], "¿qué es la recursión?")
        self.assertEqual(sent[3]["content"], "La recursión es una función que se llama a sí misma.")
        self.assertEqual(sent[4]["content"], "¿y por qué sirve?")

    def test_old_conversations_are_found_when_asked_about(self):
        conv = self.r.conversation
        conv.path.parent.mkdir(parents=True, exist_ok=True)
        old = time.time() - 3 * 86400
        conv.path.write_text(json.dumps({"ts": old, "q": "¿qué libro me recomiendas de algoritmos?",
                                         "a": "Introduction to Algorithms de Cormen.", "kind": "answer"},
                                        ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(conv.recent(), [])                        # not part of the current thread
        fake = echo_client(["Te recomendé el de Cormen."])
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            self.r.respond("¿te acuerdas qué libro de algoritmos me recomendaste?")
        about = fake.chat_stream.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Cormen", about)

    def test_learning_saves_only_new_facts(self):
        p = self.r.profile
        p.add("Estudia en la PUCP", "told")
        seen = []
        gen = lambda s, u: seen.append(u) or '["Estudia en la PUCP", "Tiene examen de Estadística en diciembre"]'  # noqa: E731
        n = companion.learn(p, gen, "tengo examen de estadística en diciembre")
        self.assertEqual(n, 1)
        self.assertEqual(len(p.facts()), 2)
        self.assertEqual(seen, ["tengo examen de estadística en diciembre"])     # only the person's words
        self.assertEqual(companion.learn(p, lambda s, u: self.fail("no model for this"), "¿qué es la recursión?"), 0)

    def test_delete_everything_forgets_the_person_and_the_conversations(self):
        self.r.respond("recuerda que me llamo Camila")
        cfg = load_config()                                         # delete_everything works on the real home
        companion.Profile(cfg).add("Le gusta el café", "told")
        companion.Conversation(cfg).add("hola", "hola")
        forget.delete_everything(cfg)
        self.assertEqual(companion.Profile(cfg).facts(), [])
        self.assertEqual(companion.Conversation(cfg).read(), [])


if __name__ == "__main__":
    unittest.main()
