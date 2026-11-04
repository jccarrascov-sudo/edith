"""Edith never promises an action it can't do; writing an email goes to edit from memory."""

from __future__ import annotations

import unittest

from edith.brain import ask, edit, talk
from edith.ui.agent import BARE_ORDER, is_task, text


class HonestTests(unittest.TestCase):
    def test_every_prompt_that_talks_to_the_user_says_it_cannot_act(self):
        for prompt in (ask.SYSTEM_PROMPT, talk.CHAT_PROMPT, talk.GUIDE_PROMPT):
            self.assertIn("Never say you did it, are doing it or will do it", prompt)

    def test_writing_an_email_is_an_edit(self):
        for r in ("madnale un correo a juan diciendlo que esto es una prueba y mandale un saludo",
                  "mándale un correo a Juan diciéndole que es una prueba", "envíale un mensaje a Ana",
                  "send Juan an email saying it's a test", "write an email to Ana about the exam"):
            self.assertTrue(edit.is_edit(r), r)
            self.assertFalse(is_task(r), r)
        for r in ("¿le mandé un correo a Juan?", "recibí un correo de Juan", "did I send the email to Ana?"):
            self.assertFalse(edit.is_edit(r), r)

    def test_bare_orders_get_an_honest_reply(self):
        for r in ("pero hazlo....", "si, hazlo", "envíalo", "just do it", "ok, send it"):
            self.assertTrue(BARE_ORDER.match(r), r)
        for r in ("haz el resto", "hazlo otra vez con la fila 3", "do it again"):
            self.assertFalse(BARE_ORDER.match(r), r)
        self.assertIn("Enter", text("es", "cant_act"))
        self.assertIn("haz el resto", text("es", "cant_act"))

    def test_dictated_content_needs_no_memory_source(self):
        p = edit.parse('{"mode": "append", "text": "Hola Juan,\\n\\nEsto es una prueba. ¡Saludos!", '
                       '"source_time": null, "source": "your request"}', 0)
        self.assertIsInstance(p, edit.Proposal)
        self.assertIsNone(p.source_ts)


class ComposeBodyTests(unittest.TestCase):
    """Gmail's message body is a contenteditable: found live in a test Chromium, Nov 3."""

    def test_pasted_lines_read_back_as_paragraphs_still_count_as_written(self):
        from edith.edit.target import same_text

        self.assertTrue(same_text("Hola Juan,\n\nEs una prueba.", "Hola Juan,\n\n\nEs una prueba."))
        self.assertFalse(same_text("Hola Juan, es una prueba.", "Hola Juan,"))

    def test_an_empty_body_that_reads_as_its_label_is_empty(self):
        from unittest import mock

        from edith.edit import target

        tp = mock.Mock()
        tp.DocumentRange.GetText.return_value = "Cuerpo del mensaje"
        control = mock.Mock(Name="Cuerpo del mensaje")
        control.GetPropertyValue.return_value = False
        control.GetPattern.return_value = tp
        self.assertEqual(target.read_text(control), "")
        tp.DocumentRange.GetText.return_value = "Hola Juan"
        self.assertEqual(target.read_text(control), "Hola Juan")


if __name__ == "__main__":
    unittest.main()
