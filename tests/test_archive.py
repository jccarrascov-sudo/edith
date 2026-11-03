"""The day's text archive: every detail kept as text for the day, searched by questions."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))

from edith.brain import answer, ask, notes  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.memory import archive  # noqa: E402
from edith.memory.timeline import Timeline  # noqa: E402
from edith.ui.agent import text as ui_text  # noqa: E402


def cfg_in(tmp: Path):
    cfg = load_config()
    cfg.root = tmp
    for d in (cfg.frames_dir, cfg.audio_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


class TermTests(unittest.TestCase):
    def test_accents_case_and_numbers(self):
        self.assertIn("nandu", archive.terms("Cliente: Ñandú Servicios"))
        self.assertIn("explic", archive.terms("lo que explicó el profe"))
        t = archive.terms("Amount (PEN): 1,250.50 and RUC 20601234567.")
        self.assertIn("1,250.50", t)
        self.assertIn("125050", t)
        self.assertIn("20601234567", t)
        self.assertNotIn("and", t)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in(Path(tempfile.mkdtemp()))
        self.day = archive.DayArchive(self.cfg)

    def test_archiver_keeps_only_new_lines_and_resumes(self):
        a = archive.Archiver(self.day)
        b0 = time.time() - 1000
        ev = lambda ts, txt: {"type": "text", "ts": ts, "window": "Doc", "process": "word.exe", "text": txt}  # noqa: E731
        a.take([ev(b0 + 100, "Title\nline one"), ev(b0 + 110, "Title\nline one\nline two")])
        a.take([{"type": "transcript", "ts": b0 + 125, "start": b0 + 120, "end": b0 + 124, "text": " the exam is Friday ", "lang": "en"},
                {"type": "field", "ts": b0 + 130, "value": "1,250.50", "window": "Portal", "process": "msedge.exe",
                 "element": {"kind": "edit", "name": "Amount"}}])
        texts = [(r["kind"], r["text"]) for r in self.day.read(0)]
        self.assertEqual(texts, [("screen", "Title\nline one"), ("screen", "line two"),
                                 ("heard", "the exam is Friday"), ("field", "Amount = 1,250.50")])
        a.take([ev(b0 + 100, "old event again")])                       # not newer than the last one: ignored
        self.assertEqual(len(self.day.read(0)), 4)
        b = archive.Archiver(self.day)
        b.resume()
        self.assertEqual(b.last_ts, b0 + 130)

    def test_search_finds_the_detail_with_its_neighbours(self):
        now = time.time()
        recs = [{"ts": now - 7000 + i, "kind": "screen", "where": "chrome.exe: News", "text": f"headline number {i}"}
                for i in range(200)]
        recs.insert(120, {"ts": now - 6880.5, "kind": "screen", "where": "msedge.exe: Invoice Portal",
                          "text": "Client: Ñandú Servicios SAC\nTax ID (RUC): 20601234567"})
        self.day.add(recs)
        hits = self.day.search("what was the RUC of the client Nandu?", max_chars=600)
        self.assertTrue(any("20601234567" in h["text"] for h in hits))
        self.assertLessEqual(len(hits), 3)                          # the hit and its neighbours
        self.assertEqual(hits, sorted(hits, key=lambda h: h["ts"]))
        self.assertEqual(self.day.search("zebra quantum"), [])

    def test_old_days_are_pruned(self):
        old = time.time() - 3 * 86400
        self.day.add([{"ts": old, "kind": "heard", "where": "audio", "text": "old"},
                      {"ts": time.time(), "kind": "heard", "where": "audio", "text": "new"}])
        self.day.prune()
        self.assertEqual([r["text"] for r in self.day.read(0)], ["new"])
        self.assertEqual(len(list(self.day.dir.glob("*.jsonl"))), 1)


class SpanTests(unittest.TestCase):
    now = datetime(2026, 11, 3, 15, 0).timestamp()

    def test_at_a_clock_time(self):
        f = ask.focus("what was I doing at 9:30?", self.now, 600)
        self.assertEqual(f.kind, "at")
        self.assertEqual(datetime.fromtimestamp(f.span[0]).strftime("%H:%M"), "09:25")
        self.assertGreater(f.start, f.end)                          # nothing of it is in the 10-minute buffer

    def test_parts_of_the_day(self):
        f = ask.focus("¿qué dijo el profe esta mañana?", self.now, 600)
        self.assertEqual(f.kind, "part_of_day")
        self.assertEqual(datetime.fromtimestamp(f.span[0]).hour, 5)
        self.assertEqual(datetime.fromtimestamp(f.span[1]).strftime("%H:%M"), "11:59")

    def test_minutes_ago_beyond_the_buffer(self):
        f = ask.focus("what was on screen 40 min ago?", self.now, 600)
        self.assertEqual(f.span, (self.now - 2400 - 75, self.now - 2400 + 75))


class ImportantTests(unittest.TestCase):
    def test_important_moments_are_kept_a_year_the_rest_a_day(self):
        cfg = cfg_in(Path(tempfile.mkdtemp()))
        book = notes.NoteBook(cfg)
        now = time.time()
        book.add({"type": "moment", "ts": now - 200 * 86400, "kind": "deadline", "text": "thesis due", "date": "2027-03-01"})
        book.add({"type": "moment", "ts": now - 400 * 86400, "kind": "exam", "text": "too old"})
        book.add({"type": "moment", "ts": now - 2 * 86400, "kind": "slide", "text": "old slide"})
        book.add({"type": "summary", "ts": now - 2 * 86400, "start": now - 2 * 86400 - 600,
                  "end": now - 2 * 86400, "text": "old summary"})
        book.add({"type": "moment", "ts": now - 60, "kind": "slide", "text": "new slide"})
        book.prune()
        self.assertEqual([n["text"] for n in book.read()], ["thesis due", "new slide"])
        self.assertEqual([n["text"] for n in book.for_questions()], ["thesis due", "new slide"])
        lines = ask.note_lines(book.for_questions(), now)
        self.assertIn("(event date 2027-03-01)", lines[0])
        self.assertIn("days ago", lines[0])

    def test_appointments_and_payments_pass_the_filter(self):
        self.assertEqual(notes.kind_of("dentist appointment on Monday 9/11 at 4 pm", spoken=False), "deadline")
        self.assertEqual(notes.kind_of("la cuota de la universidad vence el 15/11", spoken=False), "deadline")


class RouterArchiveTests(unittest.TestCase):
    def test_only_older_matching_lines_are_sent_and_counted(self):
        cfg = cfg_in(Path(tempfile.mkdtemp()))
        now = time.time()
        day = archive.DayArchive(cfg)
        day.add([{"ts": now - 5000, "kind": "heard", "where": "audio", "text": "the final exam is on Friday November 13"},
                 {"ts": now - 4000, "kind": "screen", "where": "chrome.exe: Recipes", "text": "pancakes with honey"},
                 {"ts": now - 60, "kind": "heard", "where": "audio", "text": "exam room is B204"}])  # still in the buffer
        Timeline(cfg).append({"type": "transcript", "ts": now - 60, "start": now - 60, "end": now - 55,
                              "text": "exam room is B204", "lang": "en"})
        fake = mock.Mock()
        fake.chat_stream.return_value = ("Friday.", None, 0.001)
        r = answer.Router(cfg, None, lambda k, **kw: ui_text("en", k, **kw), notes=notes.NoteBook(cfg))
        with mock.patch("edith.brain.client.MetaClient", return_value=fake):
            reply = r.respond("when is the exam?")
        sent = fake.chat_stream.call_args.kwargs["messages"][1]["content"][0]["text"]
        self.assertIn("the final exam is on Friday November 13", sent)
        self.assertNotIn("pancakes", sent)
        self.assertEqual(sent.count("exam room is B204"), 1)        # from the buffer, not twice
        self.assertIn("1 line from earlier today", reply.footnote)


if __name__ == "__main__":
    unittest.main()
