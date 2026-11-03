"""UI logic that does not need a screen: palettes, task detection, machine lookup, activity, sparklines."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("EDITH_HOME", tempfile.mkdtemp(prefix="edith-test-"))

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402

from edith.ui import agent, stats, theme  # noqa: E402
from edith.workshop import build  # noqa: E402

_app = QGuiApplication.instance() or QGuiApplication(["test", "-platform", "offscreen"])


class PaletteTests(unittest.TestCase):
    def test_both_themes_have_the_requested_colors(self):
        self.assertEqual((theme.LIGHT.bg, theme.LIGHT.shadow_light), ("#E6E9EF", "#FFFFFF"))
        self.assertEqual((theme.DARK.bg, theme.DARK.shadow_light, theme.DARK.shadow_dark),
                         ("#23262B", "#2D3036", "#191B1F"))
        self.assertEqual(theme.LIGHT.accent, theme.DARK.accent)  # one accent only

    def test_blend_goes_from_one_palette_to_the_other(self):
        self.assertEqual(QColor(theme.blend(theme.LIGHT, theme.DARK, 0).bg), QColor(theme.LIGHT.bg))
        self.assertEqual(QColor(theme.blend(theme.LIGHT, theme.DARK, 1).bg), QColor(theme.DARK.bg))
        mid = QColor(theme.blend(theme.LIGHT, theme.DARK, 0.5).bg)
        self.assertTrue(QColor(theme.DARK.bg).red() < mid.red() < QColor(theme.LIGHT.bg).red())

    def test_windows_theme_change_fades_to_the_other_palette(self):
        from PySide6.QtCore import QEventLoop, QTimer

        with mock.patch.object(theme, "windows_is_light", return_value=True):
            t = theme.Theme()
        self.assertEqual(t.palette, theme.LIGHT)
        steps = []
        t.changed.connect(lambda: steps.append(t.palette.bg))
        with mock.patch.object(theme, "windows_is_light", return_value=False):
            t.refresh()
        loop = QEventLoop()
        QTimer.singleShot(theme.THEME_FADE_MS + 150, loop.quit)
        loop.exec()
        self.assertGreater(len(steps), 2)                       # a fade, not a jump
        self.assertEqual(QColor(t.palette.bg), QColor(theme.DARK.bg))

    def test_fade_is_within_the_requested_range(self):
        self.assertTrue(200 <= theme.THEME_FADE_MS <= 300)


class TaskDetectionTests(unittest.TestCase):
    def test_tasks_go_to_agent_mode(self):
        for request in ("do the rest", "Do it", "haz el resto", "hazlo", "do the same with the others",
                        "llena las demás facturas", "what I did 5 minutes ago, do it with the rest"):
            self.assertTrue(agent.is_task(request), request)

    def test_questions_go_to_ask(self):
        for question in ("what was the video about?", "¿qué error salió en la terminal?",
                         "what did the rest of the email say?", "resume lo que vi"):
            self.assertFalse(agent.is_task(question), question)

    def test_texts_exist_in_both_languages(self):
        self.assertEqual(set(agent.TEXT["en"]), set(agent.TEXT["es"]))
        self.assertIn("Escribiendo", agent.text("es", "writing", i=1, n=3, where="portal", level="red"))


class MachineLookupTests(unittest.TestCase):
    def test_machine_is_found_by_workbook_and_site(self):
        tools = Path(tempfile.mkdtemp())
        d = tools / "invoice-runner"
        d.mkdir()
        (d / "plan.json").write_text(json.dumps({"name": "Invoice Runner", "blocks": [],
                                                 "source": {"path": r"C:\x\demo\data\invoices.xlsx"},
                                                 "target": {"url": "http://127.0.0.1:8765"}}))
        with mock.patch.object(build, "TOOLS_DIR", tools):
            self.assertEqual(agent.find_machine(Path(r"D:\other\INVOICES.xlsx"), "http://127.0.0.1:8765/?saved=3"), d)
            self.assertIsNone(agent.find_machine(Path("invoices.xlsx"), "http://127.0.0.1:9000/"))
            self.assertIsNone(agent.find_machine(Path("payroll.xlsx"), "http://127.0.0.1:8765/"))

    def test_origin(self):
        self.assertEqual(agent.origin("http://127.0.0.1:8765/?saved=1"), "http://127.0.0.1:8765")
        self.assertEqual(agent.origin(None), "")


class ActivityTests(unittest.TestCase):
    def test_events_are_counted_per_bin_and_old_ones_dropped(self):
        now = 10_000.0
        events = [{"ts": now - 5}, {"ts": now - 10}, {"ts": now - 45}, {"ts": now - 700}]
        bins = stats.activity_bins(events, now, minutes=10, bin_s=30)
        self.assertEqual(len(bins), 20)
        self.assertEqual(bins[-1], (now, 2))
        self.assertEqual(bins[-2][1], 1)
        self.assertEqual(sum(c for _, c in bins), 3)

    def test_ram_is_read(self):
        self.assertGreater(stats.ram_used_gb(), 0)


class SparklineTests(unittest.TestCase):
    def test_points_span_the_rect(self):
        r = QRectF(10, 20, 100, 40)
        pts = stats.spark_points([1, 3, 2], r)
        self.assertEqual([(p.x(), p.y()) for p in pts], [(10, 60), (60, 20), (110, 40)])
        self.assertEqual(len(stats.spark_points([5, 5], r)), 2)  # flat line: no division by zero
        self.assertEqual(stats.spark_points([], r), [])


class RenameTests(unittest.TestCase):
    def test_old_home_variable_is_still_honoured(self):
        old = tempfile.mkdtemp(prefix="old-home-")
        env = {k: v for k, v in os.environ.items() if k not in ("EDITH_HOME", "PANTALLITA_HOME")}
        env["PANTALLITA_HOME"] = old
        out = subprocess.run([sys.executable, "-c", "from edith.config import ROOT; print(ROOT)"],
                             capture_output=True, text=True, env=env, check=True)
        self.assertEqual(Path(out.stdout.strip()), Path(old))


if __name__ == "__main__":
    unittest.main()
