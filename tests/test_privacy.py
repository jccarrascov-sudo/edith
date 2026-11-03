"""Phase 5 (final version): delete everything, rotating logs, no crash, onboarding."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from edith import forget  # noqa: E402
from edith.config import ROOT, load_config  # noqa: E402
from edith.memory.archive import DayArchive  # noqa: E402
from edith.memory.timeline import Timeline  # noqa: E402

_app = QGuiApplication.instance() or QApplication(["test", "-platform", "offscreen"])


class DeleteTests(unittest.TestCase):
    def test_everything_of_the_memory_goes_machines_and_costs_stay(self):
        cfg = load_config()
        Timeline(cfg).append({"type": "window", "ts": time.time(), "window": "x", "process": "y"})
        (cfg.frames_dir / "a.jpg").write_bytes(b"jpg")
        (cfg.audio_dir / "a.wav").write_bytes(b"wav")
        DayArchive(cfg).add([{"ts": time.time(), "kind": "heard", "where": "audio", "text": "secret"}])
        cfg.notes_path.write_text('{"type": "moment", "ts": 1, "kind": "exam", "text": "x"}\n', encoding="utf-8")
        plans = ROOT / "state" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "p.json").write_text("{}", encoding="utf-8")
        tools = ROOT / "tools" / "m"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "plan.json").write_text("{}", encoding="utf-8")
        costs = ROOT / "state" / "costs.jsonl"
        costs.write_text('{"ts": 1, "cost": 0.1}\n', encoding="utf-8")

        n = forget.delete_everything(cfg)
        self.assertGreaterEqual(n, 6)
        self.assertFalse(cfg.timeline_path.exists())
        self.assertEqual(list(cfg.frames_dir.iterdir()) + list(cfg.audio_dir.iterdir()), [])
        self.assertEqual(DayArchive(cfg).read(0), [])
        self.assertFalse(cfg.notes_path.exists())
        self.assertFalse(any(plans.iterdir()))
        self.assertTrue((tools / "plan.json").exists())
        self.assertTrue(costs.exists())


class LogTests(unittest.TestCase):
    def setUp(self):
        self.hook, self.thook = sys.excepthook, __import__("threading").excepthook
        self.handlers = list(logging.getLogger("edith").handlers)

    def tearDown(self):
        sys.excepthook = self.hook
        __import__("threading").excepthook = self.thook
        for h in logging.getLogger("edith").handlers:
            if h not in self.handlers:
                h.close()
        logging.getLogger("edith").handlers = self.handlers

    def test_rotating_log_and_errors_are_logged_not_fatal(self):
        forget.setup_logs()
        handler = logging.getLogger("edith").handlers[-1]
        self.assertEqual((handler.maxBytes, handler.backupCount), (1_000_000, 3))
        try:
            raise ValueError("boom")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        handler.flush()
        text = (ROOT / "state" / "logs" / "edith.log").read_text(encoding="utf-8")
        self.assertIn("Unexpected error", text)
        self.assertIn("ValueError: boom", text)


class NoConsoleTests(unittest.TestCase):
    def test_the_windowless_launcher_starts_without_stdout_and_stderr(self):
        from edith.__main__ import main

        handlers = list(logging.getLogger("edith").handlers)
        try:
            with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
                main(["status"])                         # edith-ui.exe has no console at all
        finally:
            for h in logging.getLogger("edith").handlers:
                if h not in handlers:
                    h.close()
            logging.getLogger("edith").handlers = handlers
            sys.excepthook = sys.__excepthook__


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        from edith.ui import agent, hotkey, panel, theme
        from edith.ui.onboarding import Onboarding

        panel.STATE_FILE.unlink(missing_ok=True)
        # A real Edith may be running and own Ctrl+Alt+E: the tests use a combination of their own.
        for target in (hotkey, panel):
            p = mock.patch.object(target, "DEFAULT_OPEN", "Ctrl+Alt+Shift+F17")
            p.start()
            self.addCleanup(p.stop)
        self.hotkeys = hotkey.GlobalHotkeys()
        with mock.patch.object(theme, "windows_is_light", return_value=True):
            th = theme.Theme()
        self.deleted = []
        self.panel = panel.Edith(th, agent.Agent(load_config()),
                                 panel.Actions(None, lambda: True, lambda _p: None, lambda: None,
                                               delete_everything=lambda: self.deleted.append(1) or 7),
                                 self.hotkeys)
        self.onb = Onboarding(self.panel)
        self.panel_mod = panel

    def tearDown(self):
        self.hotkeys.close()

    def key(self, k, mods=Qt.NoModifier):
        QApplication.sendEvent(self.onb, QKeyEvent(QEvent.KeyPress, k, mods))

    def test_three_pages_then_never_again(self):
        self.assertIn("10 minutes", self.onb.body.text())
        self.key(Qt.Key_Return)
        self.assertIn("Your screen never leaves your PC. Only what you ask goes to the cloud.", self.onb.body.text())
        self.key(Qt.Key_Return)
        self.assertIn("Ctrl+Alt+Shift+F17", self.onb.body.text())
        self.assertFalse(self.onb.change.isHidden())
        self.key(Qt.Key_Return)
        saved = json.loads(self.panel_mod.STATE_FILE.read_text(encoding="utf-8"))
        self.assertTrue(saved["onboarded"])

    def test_esc_skips_and_the_shortcut_can_be_changed_there(self):
        self.onb.page = 2
        self.onb._start_capture()
        self.key(Qt.Key_F19, Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)
        self.assertEqual(self.hotkeys.current["open"], "Ctrl+Alt+Shift+F19")
        self.assertIn("Ctrl+Alt+Shift+F19", self.panel.btn_hotkey.text())
        self.key(Qt.Key_Escape)
        self.assertTrue(json.loads(self.panel_mod.STATE_FILE.read_text(encoding="utf-8"))["onboarded"])

    def test_delete_everything_needs_two_clicks(self):
        self.panel._delete_everything()
        self.assertEqual(self.deleted, [])
        self.assertIn("again", self.panel.btn_delete.text())
        self.panel._delete_everything()
        self.assertEqual(self.deleted, [1])
        self.assertIn("7 files", self.panel.status.text())


if __name__ == "__main__":
    unittest.main()
