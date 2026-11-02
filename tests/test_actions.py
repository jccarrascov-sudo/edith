"""Clicks, text-field values and click-triggered capture bursts (no real hooks or UIA)."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("PANTALLITA_HOME", tempfile.mkdtemp(prefix="pantallita-test-"))

from pantallita import config  # noqa: E402
from pantallita.brain import ask  # noqa: E402
from pantallita.memory import actions, capture, uia  # noqa: E402
from pantallita.memory.timeline import Timeline  # noqa: E402


def fresh_config() -> config.Config:
    cfg = config.load_config(Path(config.ROOT) / "missing.toml")
    cfg.exclude = {"titles": ["bank"], "processes": ["1password.exe"]}
    cfg.timeline_path.unlink(missing_ok=True)
    return cfg


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = fresh_config()
        self.timeline = Timeline(self.cfg)
        self.window = ("Invoice Portal - Edge", "msedge.exe")
        for p in (mock.patch.object(actions, "foreground_window", lambda: self.window),
                  mock.patch.object(actions, "window_at", lambda x, y: (1, *self.window)),
                  mock.patch.object(actions, "browser_url", lambda *a: None)):
            p.start()
            self.addCleanup(p.stop)

    def events(self, kind):
        return [e for e in self.timeline.read(since=0) if e["type"] == kind]


class ClickTests(Base):
    def setUp(self):
        super().setUp()
        self.bursts = []
        self.watcher = actions.ClickWatcher(self.cfg, self.timeline, threading.Event(),
                                            on_click=self.bursts.append)

    def test_click_is_logged_with_its_element(self):
        el = uia.Element("button", "Save", None, (10, 20, 110, 50), False)
        self.watcher.record(1000.0, 60, 35, "left", lambda x, y: el)
        [click] = self.events("click")
        self.assertEqual((click["x"], click["y"], click["button"]), (60, 35, "left"))
        self.assertEqual(click["element"], {"kind": "button", "name": "Save", "rect": [10, 20, 110, 50]})
        self.assertEqual(click["process"], "msedge.exe")
        self.assertEqual(self.bursts, [1000.0])

    def test_password_element_value_is_never_stored(self):
        el = uia.Element("edit", "Password", "hunter2", (0, 0, 100, 20), True)
        self.watcher.record(1000.0, 5, 5, "left", lambda x, y: el)
        [click] = self.events("click")
        self.assertTrue(click["element"]["password"])
        self.assertNotIn("hunter2", str(click))

    def test_click_in_excluded_window_is_dropped(self):
        self.window = ("My Bank - Online banking", "msedge.exe")
        self.watcher.record(1000.0, 5, 5, "left", lambda x, y: self.fail("must not inspect the element"))
        self.assertEqual(self.events("click"), [])
        self.assertEqual(self.bursts, [])
        self.assertEqual(self.watcher.private_clicks, 1)


def focused(name, value, kind="edit", title="Invoice Portal - Edge", process="msedge.exe"):
    key = (process, kind, name, "")
    return key, {"kind": kind, "name": name, "automation_id": "", "rect": [0, 0, 100, 20]}, value, title, process


class FieldTests(Base):
    def setUp(self):
        super().setUp()
        self.watcher = actions.FieldWatcher(self.cfg, self.timeline, threading.Event(), settle_s=1.5)

    def test_value_is_logged_once_it_settles(self):
        w, t = self.watcher, 0.0
        for value in ["", "A", "AC", "ACME", "ACME SAC"]:  # the field as it is seen while typing
            w.tick(focused("Client", value), t)
            t += 0.5
        self.assertEqual(self.events("field"), [])  # still changing
        for _ in range(4):
            w.tick(focused("Client", "ACME SAC"), t)
            t += 0.5
        [field] = self.events("field")
        self.assertEqual((field["value"], field["element"]["name"]), ("ACME SAC", "Client"))

    def test_leaving_the_field_logs_its_final_value(self):
        w = self.watcher
        w.tick(focused("Amount", ""), 0.0)
        w.tick(focused("Amount", "1250.00"), 0.5)
        w.tick(focused("Date", ""), 1.0)  # focus moves on before the value settled
        self.assertEqual([e["value"] for e in self.events("field")], ["1250.00"])

    def test_unchanged_field_is_logged_once_and_empty_never(self):
        w = self.watcher
        for visit in range(3):  # focus the same pre-filled field three times
            for t in range(6):
                w.tick(focused("Client", "already there"), visit * 10 + t * 0.5)
            w.tick(None, visit * 10 + 4)
        for t in range(6):
            w.tick(focused("Notes", ""), 40 + t * 0.5)
        w.tick(None, 44)
        self.assertEqual([e["value"] for e in self.events("field")], ["already there"])

    def test_same_field_after_page_title_change_is_not_duplicated(self):
        w = self.watcher
        w.tick(focused("Date", "2026-10-15", title="New invoice"), 0.0)
        w.tick(focused("Date", "2026-10-15", title="New invoice"), 2.0)
        w.tick(None, 2.5)
        w.tick(focused("Date", "2026-10-15", title="Invoice Portal"), 3.0)
        w.tick(focused("Date", "2026-10-15", title="Invoice Portal"), 5.0)
        w.tick(None, 5.5)
        self.assertEqual(len(self.events("field")), 1)

    def test_switching_to_excluded_app_drops_the_pending_value(self):
        w = self.watcher
        w.tick(focused("Client", ""), 0.0)
        w.tick(focused("Client", "secret"), 0.5)
        w.tick("excluded", 1.0)
        w.tick(None, 5.0)
        self.assertEqual(self.events("field"), [])

    def test_password_fields_are_not_read(self):
        class FakeControl:
            ControlTypeName = "EditControl"
            Name, AutomationId = "Password", "pw"

            def GetPropertyValue(self, prop):
                return True  # IsPassword

            def GetPattern(self, _):
                raise AssertionError("the value of a password field must never be read")

        fake_auto = mock.Mock(GetFocusedControl=lambda: FakeControl())
        self.assertIsNone(self.watcher._read_focused(fake_auto))


class NoKeyboardTests(unittest.TestCase):
    def test_no_keyboard_hook_or_key_polling_anywhere(self):
        src = Path(actions.__file__).resolve().parents[1]
        forbidden = ["WH_KEYBOARD", "GetKeyboardState", "GetKeyState(", "pynput", "keyboard.", "keylog"]
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for word in forbidden:
                self.assertNotIn(word, text, f"{word} found in {path.name}")

    def test_the_only_key_state_read_is_the_launch_modifier(self):
        """GetAsyncKeyState may appear only in ui/launch.py, only for VK_CONTROL (0x11)."""
        import re
        src = Path(actions.__file__).resolve().parents[1]
        for path in src.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            calls = re.findall(r"GetAsyncKeyState\(([^)]*)\)", text)
            if path.name != "launch.py" or path.parent.name != "ui":
                self.assertEqual(calls, [], f"key state read in {path}")
                continue
            self.assertEqual(calls, ["VK_CONTROL"])
            self.assertIn("VK_CONTROL = 0x11", text)


class UrlTests(unittest.TestCase):
    def test_address_bar_text_becomes_a_url(self):
        self.assertEqual(uia.normalize_url("127.0.0.1:8765/"), "http://127.0.0.1:8765/")
        self.assertEqual(uia.normalize_url("example.com/a?b=1"), "https://example.com/a?b=1")
        self.assertEqual(uia.normalize_url("http://localhost:8765"), "http://localhost:8765")
        for text in ("127.0.0.1:8765/", "localhost:8000", "dev.meta.ai/docs", "https://x.org"):
            self.assertRegex(text, uia._URLISH)
        for text in ("Andes Test Supply SAC", "1250.50", "10/09/2026", "Search Google or type a URL"):
            self.assertNotRegex(text, uia._URLISH)


class BurstTests(unittest.TestCase):
    def test_click_switches_capture_to_fast_interval_for_a_while(self):
        cfg = fresh_config()
        cap = capture.ScreenCapture(cfg, Timeline(cfg), threading.Event())
        with mock.patch.object(capture.time, "time", lambda: 500.0):
            self.assertEqual(cap.interval(), 2.0)
            cap.burst(499.0)
            self.assertEqual(cap.interval(), 0.5)
        with mock.patch.object(capture.time, "time", lambda: 502.5):
            self.assertEqual(cap.interval(), 2.0)


class AskActionsTests(unittest.TestCase):
    def test_actions_are_one_short_line_each(self):
        events = [
            {"type": "click", "ts": 100.0, "x": 1, "y": 2, "button": "left", "process": "msedge.exe",
             "element": {"kind": "button", "name": "Save"}},
            {"type": "field", "ts": 101.0, "value": "ACME", "process": "msedge.exe",
             "element": {"kind": "edit", "name": "Client"}},
        ]
        lines = ask.action_lines(events, now=160.0)
        self.assertIn('left click on button "Save" in msedge.exe', lines[0])
        self.assertIn('field edit "Client" now = "ACME"', lines[1])


if __name__ == "__main__":
    unittest.main()
