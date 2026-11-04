"""Noticing a task done twice: Word documents from one template, a desktop form filled twice."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))

import make_letters  # noqa: E402
from edith.config import load_config  # noqa: E402
from edith.recipes import detect, docs  # noqa: E402
from edith.recipes.store import RecipeStore  # noqa: E402


def field(ts, name, value, process="northwind.exe"):
    return {"type": "field", "ts": ts, "value": value, "window": "Northwind Desktop", "process": process,
            "element": {"name": name, "kind": "edit"}}


def click(ts, name, process="northwind.exe"):
    return {"type": "click", "ts": ts, "x": 1, "y": 1, "button": "left", "window": "Northwind Desktop",
            "process": process, "element": {"name": name, "kind": "button"}}


class DetectTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.files = make_letters.make(self.dir, hand=True)
        old = time.time() - 3600
        os.utime(self.files["template"], (old, old))
        _h, rows = docs.sheet_rows(self.files["sheet"])
        self.second = make_letters.hand_letter(rows[4], self.dir)
        cfg = load_config()
        cfg.root = Path(tempfile.mkdtemp())
        self.det = detect.Detector(cfg, RecipeStore(cfg))

    def recent(self):
        now = time.time()
        return [(self.files["template"], now - 600), (self.files["result"], now - 300), (self.second, now - 10),
                (self.files["sheet"], now - 650)]

    def test_two_letters_from_one_template_are_noticed(self):
        s = self.det.check(self.recent(), events=[])
        self.assertEqual((s.kind, s.template, s.name), ("excel_to_word", self.files["template"], "Payment reminder"))
        self.assertIn(s.result, (self.files["result"], self.second))

    def test_one_letter_is_not_enough_and_no_is_remembered(self):
        now = time.time()
        self.assertIsNone(self.det.check([(self.files["template"], now - 600), (self.files["result"], now)], events=[]))
        s = self.det.check(self.recent(), events=[])
        self.det.decline(s)
        self.assertIsNone(self.det.check(self.recent(), events=[]))

    def test_a_form_filled_twice_is_noticed(self):
        t = time.time() - 300
        events = [field(t, "Client", "A"), field(t + 2, "Amount", "1"), click(t + 4, "Save"),
                  field(t + 60, "Client", "B"), field(t + 62, "Amount", "2"), click(t + 64, "Save")]
        s = self.det.check([], events=events)
        self.assertEqual((s.kind, s.process, s.fields), ("excel_to_form", "northwind.exe", ("Amount", "Client")))
        self.assertIsNone(self.det.check([], events=events[:3]))


if __name__ == "__main__":
    unittest.main()
