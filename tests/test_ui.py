"""Companion logic that does not need a screen: repetition, shelf time, sounds, machine lookup."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault("PANTALLITA_HOME", tempfile.mkdtemp(prefix="pantallita-test-"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "demo"))
import make_invoices  # noqa: E402
from test_learn import demo_events  # noqa: E402

from pantallita.ui import flow, repeat, shelf, sounds  # noqa: E402
from pantallita.workshop import build  # noqa: E402


class RepetitionTests(unittest.TestCase):
    def setUp(self):
        repeat.DISMISSED.unlink(missing_ok=True)
        rows = make_invoices.generate()
        self.events = demo_events(rows[0]) + demo_events(rows[1], 60) + demo_events(rows[2], 120)

    def test_three_times_is_offered_twice_is_not(self):
        found = repeat.repeated(self.events)
        self.assertIsNotNone(found)
        key, seg = found
        self.assertIn("chrome.exe|127.0.0.1:8765|", key)
        self.assertGreater(seg.start, demo_events(make_invoices.generate()[0], 119)[0]["ts"] - 1)  # the latest
        two = [e for e in self.events if e["ts"] < self.events[0]["ts"] + 100]
        self.assertIsNone(repeat.repeated(two))

    def test_no_means_never_again(self):
        key, _ = repeat.repeated(self.events)
        repeat.dismiss(key)
        self.assertIsNone(repeat.repeated(self.events))
        self.assertIn(key, json.loads(repeat.DISMISSED.read_text(encoding="utf-8")))

    def test_asked_once_per_session(self):
        key, _ = repeat.repeated(self.events)
        self.assertIsNone(repeat.repeated(self.events, already_asked={key}))


class ShelfTests(unittest.TestCase):
    def test_time_saved_counts_ok_rows_minus_run_time(self):
        d = Path(tempfile.mkdtemp())
        (d / "runs").mkdir()
        (d / "plan.json").write_text(json.dumps({"name": "M", "blocks": [], "seconds_per_row_by_hand": 24}))
        events = [{"event": "row_out", "ok": True}] * 10 + [{"event": "row_out", "ok": False},
                                                             {"event": "run_end", "seconds": 5}]
        (d / "runs" / "run-1.jsonl").write_text("\n".join(json.dumps(e) for e in events))
        saved, rows = shelf.time_saved(d)
        self.assertEqual((saved, rows), (10 * 24 - 5, 10))
        self.assertEqual(shelf.fmt(saved), "4 min")

    def test_rename_keeps_everything_else(self):
        d = Path(tempfile.mkdtemp())
        (d / "plan.json").write_text(json.dumps({"name": "Invoice Runner", "intent": "x", "blocks": []}))
        shelf.rename(d, "Factura Express")
        plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual((plan["name"], plan["intent"]), ("Factura Express", "x"))


class SoundTests(unittest.TestCase):
    def test_sounds_are_valid_short_wavs(self):
        for name in ("click", "ding", "sparks", "whoosh", "stamp"):
            with wave.open(io.BytesIO(sounds.make(name))) as w:
                self.assertEqual((w.getnchannels(), w.getframerate()), (1, sounds.RATE))
                self.assertLess(w.getnframes() / w.getframerate(), 1.0)


class MachineLookupTests(unittest.TestCase):
    def test_machine_is_found_by_workbook_and_site(self):
        tools = Path(tempfile.mkdtemp())
        d = tools / "invoice-runner"
        d.mkdir()
        (d / "plan.json").write_text(json.dumps({"name": "Invoice Runner", "blocks": [],
                                                 "source": {"path": r"C:\x\demo\data\invoices.xlsx"},
                                                 "target": {"url": "http://127.0.0.1:8765"}}))
        with mock.patch.object(build, "TOOLS_DIR", tools):
            self.assertEqual(flow.find_machine(Path(r"D:\other\INVOICES.xlsx"), "http://127.0.0.1:8765/?saved=3"), d)
            self.assertIsNone(flow.find_machine(Path("invoices.xlsx"), "http://127.0.0.1:9000/"))
            self.assertIsNone(flow.find_machine(Path("payroll.xlsx"), "http://127.0.0.1:8765/"))

    def test_origin(self):
        self.assertEqual(flow.origin("http://127.0.0.1:8765/?saved=1"), "http://127.0.0.1:8765")
        self.assertEqual(flow.origin(None), "")


class LassoTests(unittest.TestCase):
    def test_a_lasso_outside_rows_gives_nothing(self):
        from pantallita.ui import launch

        fake_excel = mock.Mock()
        fake_excel.ActiveWindow.RangeFromPoint.return_value = None  # not over a cell
        with mock.patch("comtypes.client.GetActiveObject", return_value=fake_excel):
            self.assertIsNone(launch.excel_selection([(10, 10), (20, 30)]))
        self.assertIsNone(launch.excel_selection([]))


if __name__ == "__main__":
    unittest.main()
