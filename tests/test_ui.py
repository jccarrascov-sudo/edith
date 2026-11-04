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
from PySide6.QtWidgets import QApplication  # noqa: E402

from edith.ui import agent, hotkey, stats, theme  # noqa: E402
from edith.workshop import build  # noqa: E402

_app = QGuiApplication.instance() or QApplication(["test", "-platform", "offscreen"])


class PaletteTests(unittest.TestCase):
    def test_both_themes_have_the_requested_colors(self):
        self.assertEqual((theme.LIGHT.bg, theme.LIGHT.shadow_light), ("#E6E9EF", "#FFFFFF"))
        self.assertEqual((theme.DARK.bg, theme.DARK.shadow_light, theme.DARK.shadow_dark),
                         ("#23262B", "#2D3036", "#191B1F"))
        self.assertEqual((theme.LIGHT.text, theme.DARK.text), ("#1A1B1E", "#F2F3F5"))
        self.assertEqual((theme.LIGHT.accent, theme.DARK.accent), ("#5B7CFA", "#7AA7FF"))
        self.assertEqual(theme.DARK.muted.upper(), theme.over("#F2F3F5", "#23262B", 0.60).upper())

    def test_every_text_color_reaches_4_5_to_1(self):
        for pal in (theme.LIGHT, theme.DARK):
            for name in ("text", "muted", "accent_text"):
                self.assertGreaterEqual(theme.contrast(getattr(pal, name), pal.bg), 4.5, (pal.bg, name))

    def test_the_bundled_font_is_the_only_one(self):
        from edith.ui import fonts

        self.assertTrue(fonts.load())
        f = fonts.font(fonts.BIG_PX, fonts.SEMIBOLD)
        self.assertEqual((f.family(), f.pixelSize(), f.weight()), ("Source Serif 4", 28, 600))
        src = "".join(p.read_text(encoding="utf-8") for p in Path(fonts.__file__).parent.glob("*.py"))
        self.assertNotIn("Segoe", src)
        self.assertTrue((Path(fonts.__file__).parent / "fonts" / "OFL.txt").exists())

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


class RepeatDetectionTests(unittest.TestCase):
    def test_asking_to_repeat_an_action_is_a_task(self):
        for q in ("repite lo que hice", "hazlo otra vez", "vuelve a hacerlo", "haz lo mismo",
                  "¿puedes hacerlo otra vez?", "do that again", "repeat what I just did", "can you do it again?",
                  "redo it", "realiza lo que hice hace un momento"):
            self.assertTrue(agent.is_task(q), q)

    def test_questions_about_it_are_not(self):
        for q in ("¿qué hice hace un momento?", "what did I do?", "haz un resumen de esto",
                  "do you remember what the teacher said about it?", "explícame esto", "¿cómo repito esto?",
                  "pon esto en mis apuntes"):
            self.assertFalse(agent.is_task(q), q)


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


class HotkeyTests(unittest.TestCase):
    def test_combinations_are_parsed_and_normalized(self):
        self.assertEqual(hotkey.normalize("ctrl+alt+e"), "Ctrl+Alt+E")
        self.assertEqual(hotkey.normalize("Alt + Ctrl + F5"), "Ctrl+Alt+F5")
        self.assertEqual(hotkey.parse("Ctrl+Alt+E"), (0x0002 | 0x0001, ord("E")))
        for bad in ("E", "Ctrl", "Ctrl+", "Ctrl+Alt", "Hyper+E", "Ctrl+Enter", "Ctrl+F25"):
            self.assertIsNone(hotkey.normalize(bad), bad)

    def test_key_presses_need_a_modifier_and_a_key(self):
        from PySide6.QtCore import Qt

        both = Qt.ControlModifier | Qt.AltModifier
        self.assertEqual(hotkey.from_key_event(Qt.Key_K, both), "Ctrl+Alt+K")
        self.assertEqual(hotkey.from_key_event(Qt.Key_F9, Qt.MetaModifier), "Win+F9")
        self.assertIsNone(hotkey.from_key_event(Qt.Key_Control, Qt.ControlModifier))  # keep waiting
        self.assertEqual(hotkey.from_key_event(Qt.Key_K, Qt.NoModifier), "")             # no modifier
        self.assertEqual(hotkey.from_key_event(Qt.Key_Return, both), "")                 # unsupported key

    def test_a_taken_combination_is_refused_and_the_old_one_kept(self):
        owner, other = hotkey.GlobalHotkeys(), hotkey.GlobalHotkeys()
        try:
            self.assertTrue(owner.set("open", "Ctrl+Alt+Shift+F23"))
            self.assertTrue(other.set("open", "Ctrl+Alt+Shift+F22"))
            self.assertFalse(other.set("open", "Ctrl+Alt+Shift+F23"))   # another "app" has it
            self.assertEqual(other.current["open"], "Ctrl+Alt+Shift+F22")
            other.clear("open")
            self.assertTrue(owner.set("open", "Ctrl+Alt+Shift+F22"))    # freed, so it can be taken
        finally:
            owner.close()
            other.close()


class PanelTests(unittest.TestCase):
    def setUp(self):
        from edith.config import load_config
        from edith.ui import panel

        self.panel_mod = panel
        panel.STATE_FILE.unlink(missing_ok=True)
        # A real Edith may be running and own Ctrl+Alt+E: the tests use a combination of their own.
        for target in (hotkey, panel):
            p = mock.patch.object(target, "DEFAULT_OPEN", "Ctrl+Alt+Shift+F17")
            p.start()
            self.addCleanup(p.stop)
        self.hotkeys = hotkey.GlobalHotkeys()
        with mock.patch.object(theme, "windows_is_light", return_value=True):
            self.theme = theme.Theme()
        self.edith = panel.Edith(self.theme, agent.Agent(load_config()),
                                 panel.Actions(toggle_audio=None, toggle_memory=lambda: False,
                                               open_activity=lambda _p: None, quit=lambda: None),
                                 self.hotkeys)

    def tearDown(self):
        self.hotkeys.close()
        self.edith.deleteLater()

    def press(self, key, mods):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        QApplication.sendEvent(self.edith.field, QKeyEvent(QEvent.KeyPress, key, mods))

    def test_dot_and_circle_share_the_widget_center(self):
        ring, dot = self.edith.dot_geometry()
        widget_center = QRectF(self.edith.rect()).center()
        self.assertEqual(ring.center(), widget_center)
        self.assertEqual(dot, widget_center)
        self.assertEqual(ring.width(), self.panel_mod.DOT)
        m = self.panel_mod.M_DOT
        self.assertEqual((ring.left(), ring.top()), (m, m))                    # same margin all round
        self.assertEqual(self.edith.width() - ring.right(), m)
        from edith.ui.theme import glow_extent, shadow_extent
        self.assertGreaterEqual(m, glow_extent(self.panel_mod.DOT_GLOW))       # the glow is never cut
        self.assertGreaterEqual(self.panel_mod.M, shadow_extent(self.panel_mod.PANEL_DEPTH))

    def test_panel_opens_centered_on_the_dot_and_stays_on_screen(self):
        from PySide6.QtCore import QPoint

        e, P = self.edith, self.panel_mod
        screen = e.screen().availableGeometry()
        e.dot_pos = QPoint(screen.center().x() - 38, screen.center().y() - 38)
        e.show()
        e.expand(activate=False)
        e.content.layout().activate()
        _app.processEvents()
        g = e.geometry()
        dot_cx = e.dot_pos.x() + (P.DOT + 2 * P.M_DOT) / 2
        self.assertLessEqual(abs(g.x() + g.width() / 2 - dot_cx), 1)
        field = e.field.geometry()
        self.assertEqual(field.left() - P.M, field.top() - P.M)               # equal margins left/top
        self.assertEqual(g.width() - P.M - field.right() - 1, field.left() - P.M)   # and right
        e.collapse()
        e.dot_pos = QPoint(screen.right() - 30, screen.bottom() - 30)         # at the corner
        e.expand(activate=False)
        _app.processEvents()
        g = e.geometry().adjusted(P.M, P.M, -P.M, -P.M)                       # the panel without its shadow
        self.assertTrue(screen.contains(g), (screen, g))
        e.collapse()

    def test_paused_memory_dims_the_dot(self):
        self.assertEqual(self.edith._dot_target(), theme.LIGHT.idle)
        self.edith.set_memory_on(False)
        self.assertEqual(self.edith._dot_target(), theme.LIGHT.paused)
        self.assertNotEqual(theme.LIGHT.paused, theme.LIGHT.idle)

    def test_default_shortcut_is_registered(self):
        self.assertEqual(self.hotkeys.current.get("open"), hotkey.DEFAULT_OPEN)
        self.assertIn(hotkey.DEFAULT_OPEN, self.edith.btn_hotkey.text())

    def test_new_shortcut_is_captured_saved_and_applied(self):
        from PySide6.QtCore import Qt

        self.edith._start_capture()
        self.assertIn("Esc", self.edith.status.text())
        self.press(Qt.Key_Control, Qt.ControlModifier)          # modifier alone: still waiting
        self.assertTrue(self.edith.capturing)
        self.press(Qt.Key_K, Qt.NoModifier)                      # no modifier: refused, still waiting
        self.assertTrue(self.edith.capturing)
        self.press(Qt.Key_F21, Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)
        self.assertFalse(self.edith.capturing)
        self.assertEqual(self.hotkeys.current["open"], "Ctrl+Alt+Shift+F21")
        saved = json.loads(self.panel_mod.STATE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["hotkey"], "Ctrl+Alt+Shift+F21")
        self.edith._start_capture()
        self.edith._restore_default()
        self.assertEqual(self.hotkeys.current["open"], hotkey.DEFAULT_OPEN)

    def test_taken_shortcut_keeps_the_old_one_and_esc_cancels(self):
        from PySide6.QtCore import Qt

        other = hotkey.GlobalHotkeys()
        try:
            self.assertTrue(other.set("x", "Ctrl+Alt+Shift+F20"))
            self.edith._start_capture()
            self.press(Qt.Key_F20, Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)
            self.assertEqual(self.hotkeys.current["open"], hotkey.DEFAULT_OPEN)
            self.assertIn(hotkey.DEFAULT_OPEN, self.edith.status.text())
            self.edith._start_capture()
            self.press(Qt.Key_Escape, Qt.NoModifier)
            self.assertFalse(self.edith.capturing)
            self.assertEqual(self.hotkeys.current["open"], hotkey.DEFAULT_OPEN)
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()


class TypingOverAPreviewTests(unittest.TestCase):
    """Seen live Nov 4: with a preview waiting, "corrige: ..." + Enter did nothing; it was lost."""

    def test_corrige_over_a_waiting_preview_corrects_that_preview(self):
        import threading
        import time
        from types import SimpleNamespace

        from edith.config import load_config

        a = agent.Agent(load_config())
        made = Path(tempfile.mkdtemp()) / "Payment reminder - Golden Spoon Supplies.docx"
        made.write_bytes(b"x")
        p = SimpleNamespace(recipe=SimpleNamespace(id="r1", kind="excel_to_word", level=1),
                            outputs=[SimpleNamespace(key="Golden Spoon Supplies", made=made)])
        a.recipe_runner = SimpleNamespace(prepare=lambda m: p)
        sent = []
        a.submit = lambda request: sent.append(request)
        t = threading.Thread(target=a._guarded, args=(lambda _r: a._recipe(SimpleNamespace()), "haz lo mismo"))
        t.start()
        end = time.monotonic() + 5
        while not a.awaiting and time.monotonic() < end:
            time.sleep(0.02)
        self.assertTrue(a.awaiting)
        a.revise("corrige: el monto va con S/ en vez de USD")
        t.join(5)
        self.assertEqual(sent, ["corrige: el monto va con S/ en vez de USD"])       # it goes as a request
        self.assertEqual(a._last_run, ("r1", "Golden Spoon Supplies", str(made)))    # about THIS preview
        self.assertIsNone(a._then)

    def test_enter_with_nothing_new_still_applies(self):
        from edith.config import load_config

        a = agent.Agent(load_config())
        a.awaiting = True
        a.decide(True)
        self.assertTrue(a._confirmed)
        self.assertIsNone(a._then)


class AccessibleNamesTests(unittest.TestCase):
    def test_the_drawn_options_button_has_a_name(self):
        from edith.ui import panel

        with mock.patch.object(theme, "windows_is_light", return_value=True):
            th = theme.Theme()
        b = panel.SoftButton(th, lambda *_a: None, name="Opciones")
        self.assertEqual(b.accessibleName(), "Opciones")


class FieldOverAPreviewTests(PanelTests):
    """Seen live Nov 4: the field stayed read-only while a preview waited, so "corrige: ..." never
    reached it and Enter applied the letter. The real panel, not only the agent."""

    def test_the_field_is_writable_while_a_preview_waits_and_new_text_is_a_request(self):
        from types import SimpleNamespace

        a = self.edith.agent
        a.busy = True
        a.busy_changed.emit(True)
        _app.processEvents()
        self.assertTrue(self.edith.field.isReadOnly())             # working: nothing to type into
        a.awaiting = True
        self.edith._update_dot()                                   # what the preview signal now triggers
        self.assertFalse(self.edith.field.isReadOnly())            # waiting for Enter: writable
        sent = []
        a.revise = lambda text: sent.append(text)
        self.edith._last_request = "haz lo mismo para Golden Spoon Supplies"
        self.edith.field.setText("corrige: el monto va con S/ en vez de USD")
        self.edith._enter()
        self.assertEqual(sent, ["corrige: el monto va con S/ en vez de USD"])

    def test_the_preview_signals_re_evaluate_the_field(self):
        a = self.edith.agent
        a.busy, a.awaiting = True, True
        self.edith.field.setReadOnly(True)
        a.result_preview.emit(None)
        _app.processEvents()
        self.assertFalse(self.edith.field.isReadOnly())
