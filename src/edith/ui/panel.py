"""Edith on screen: a small soft dot that opens into a minimal panel.

At rest: a 40 px raised dot, always on top, draggable, with no text or icon. Its center is
the only status indicator: gray when idle, a dimmer gray while the memory is paused, the
accent while thinking or acting (a soft fade, no animation). A global shortcut (default
Ctrl+Alt+E, changeable in the options) opens and closes the panel from any app. A click opens the panel: one text field and the answer under it. The audio
and options buttons fade in only while the mouse is over the panel. Esc or a click elsewhere
closes it back into the dot. Rule: what is not needed right now is not shown."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..config import ROOT
from .agent import Agent
from .hotkey import DEFAULT_OPEN, GlobalHotkeys, from_key_event

DEFAULT_PRESENTATION = "Ctrl+Alt+P"
from .fonts import SMALL_PX, TEXT_PX, font
from .theme import Theme, glow, glow_extent, inset, raised, shadow_extent

log = logging.getLogger(__name__)

DOT = 40              # the dot's diameter
DOT_GLOW = 6          # the closed dot's centered shadow
PANEL_DEPTH = 6       # the open panel's shadows: offset and blur
M_DOT = int(glow_extent(DOT_GLOW)) + 2           # transparent margin around the dot: its glow fits
M = int(shadow_extent(PANEL_DEPTH)) + 2          # ...around the panel: offset + blur fit on every side
PAD = 24              # the panel's inner margin, the same on all four sides
TEXT_INSET = 14       # text inside the field and every line under it start on the same axis
PANEL_W = 380
RADIUS = 22
FADE_MS = 180
ANSWER_MAX_H = 420
STATE_FILE = ROOT / "state" / "ui.json"   # dot position and shortcut


@dataclass
class Actions:
    """What the hover buttons do (provided by the app, which owns the memory threads)."""
    toggle_audio: Callable[[], bool] | None   # returns: listening now? None = no audio button
    toggle_memory: Callable[[], bool]          # returns: recording now?
    open_activity: Callable[["Edith"], None]
    quit: Callable[[], None]
    delete_everything: Callable[[], int] | None = None   # returns files deleted


class SoftButton(QAbstractButton):
    """A quiet round button inside the text field: just its glyph, gray, or in the accent while
    on; pressing it presses a small round hollow into the field."""

    def __init__(self, theme: Theme, glyph: Callable[[QPainter, QRectF, QColor], None], checkable=False):
        super().__init__()
        self.theme, self.glyph = theme, glyph
        self.setCheckable(checkable)
        self.setFixedSize(QSize(30, 30))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        theme.changed.connect(self.update)

    def paintEvent(self, _event) -> None:
        pal = self.theme.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(3, 3, 24, 24)
        if self.isDown():
            inset(p, r, 12, pal, depth=1.5)
        self.glyph(p, r, QColor(pal.accent_text if self.isChecked() else pal.muted))


def _reading_html(markdown: str) -> str:
    """Markdown typeset for reading: every line break kept, generous line height (Qt's 125 %
    on top of the font's own leading reads like 140 %)."""
    import re

    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setMarkdown(markdown.replace("\n", "  \n"))
    body = doc.toHtml()
    body = re.sub(r"<body[^>]*>", "<body>", body[body.find("<body"):])   # the label's font, not the document's
    body = re.sub(r'<p style="', '<p style="line-height:125%; ', body)
    return re.sub(r"<li style=\"", '<li style="line-height:125%; ', body)


def _thumbnail(path: str, w: int = 72, h: int = 42) -> QPixmap:
    """A small screenshot with rounded corners, for "Based on: ..."."""
    src = QPixmap(path)
    if src.isNull():
        return QPixmap()
    src = src.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), 6, 6)
    p.setClipPath(clip)
    p.drawPixmap(0, 0, src, (src.width() - w) // 2, (src.height() - h) // 2, w, h)
    p.end()
    return out


def bars_glyph(p: QPainter, r: QRectF, color: QColor) -> None:
    """Audio: three short vertical bars."""
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    for dx, h in ((-5, 6), (0, 12), (5, 8)):
        p.drawRoundedRect(QRectF(r.center().x() + dx - 1.25, r.center().y() - h / 2, 2.5, h), 1.25, 1.25)


def dots_glyph(p: QPainter, r: QRectF, color: QColor) -> None:
    """Options: three small dots."""
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    for dx in (-5, 0, 5):
        p.drawEllipse(QPointF(r.center().x() + dx, r.center().y()), 1.6, 1.6)


class Edith(QWidget):
    def __init__(self, theme: Theme, agent: Agent, actions: Actions, hotkeys: GlobalHotkeys | None = None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.theme, self.agent, self.actions = theme, agent, actions
        self.lang = agent.lang
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Edith")
        self.expanded = False
        self.state = self._load_state()
        self.dot_pos = self._initial_position()
        self.hotkeys = hotkeys
        self.capturing = False   # waiting for a new shortcut to be pressed
        self._moments: list[dict] = []
        self._footnote = ""
        self.presentation = bool(self.state.get("presentation", False))
        agent.router.local_only = bool(self.state.get("local_only", False))
        self._press: QPoint | None = None
        self._dragged = False
        self.memory_on = True

        pal = theme.palette
        self.dot_color = QColor(pal.idle)
        self._dot_fade = QVariantAnimation(self, duration=250)
        self._dot_fade.valueChanged.connect(self._set_dot_color)
        self._fade_in = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=FADE_MS)
        self._fade_in.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))

        self._build()
        theme.changed.connect(self._restyle)
        agent.busy_changed.connect(lambda _busy: self._update_dot())
        agent.status.connect(self._show_status)
        agent.reply.connect(self._show_reply)
        agent.footnote.connect(self._show_footnote)
        agent.preview.connect(self._show_preview)
        agent.undoable.connect(self._show_undo)
        if hotkeys:
            hotkeys.pressed.connect(self._hotkey)
            wanted = self.state.get("hotkey", DEFAULT_OPEN)
            if not hotkeys.set("open", wanted) and wanted != DEFAULT_OPEN:
                hotkeys.set("open", DEFAULT_OPEN)
            hotkeys.set("presentation", self.state.get("presentation_hotkey", DEFAULT_PRESENTATION))
        self._label_hotkey()
        self._label_local()
        self._label_presentation()
        self._restyle()
        self._collapse_now()

    # --- construction ---------------------------------------------------------------------------
    def _build(self) -> None:
        self.content = QWidget(self)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(M + PAD, M + PAD, M + PAD, M + PAD)
        outer.setSpacing(12)

        self.field = QLineEdit()
        self.field.setFont(font(TEXT_PX))
        self.field.setPlaceholderText(self.agent.t("placeholder"))
        self.field.setFrame(False)
        self.field.setFixedHeight(40)
        self.field.setTextMargins(TEXT_INSET, 0, TEXT_INSET, 0)
        self.field.returnPressed.connect(self._enter)
        self.field.installEventFilter(self)   # catches the new shortcut while capturing
        outer.addWidget(self.field)

        # Everything under the field starts where the field's text starts.
        self.body = QWidget()
        inner = QVBoxLayout(self.body)
        inner.setContentsMargins(TEXT_INSET, 0, TEXT_INSET, 0)
        inner.setSpacing(10)
        outer.addWidget(self.body)

        self.status = QLabel()
        self.status.setFont(font(SMALL_PX))
        self.status.setWordWrap(True)
        inner.addWidget(self.status)

        self.answer = QLabel()
        self.answer.setFont(font(TEXT_PX))
        self.answer.setWordWrap(True)
        self.answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.answer.setTextFormat(Qt.RichText)   # answers arrive as Markdown, shown as typeset HTML
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.answer)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner.addWidget(self.scroll)

        # What was sent to the cloud, or "Answered locally": small and gray, only after an answer.
        self.foot = QLabel()
        self.foot.setFont(font(SMALL_PX))
        self.foot.setWordWrap(True)
        inner.addWidget(self.foot)

        # Key moments (at most 3, newest first, one line each, the time in gray). Shown only while
        # there is no answer on screen; a click asks about that moment. Never a pop-up.
        self.moments = QLabel()
        self.moments.setFont(font(SMALL_PX))
        self.moments.setTextFormat(Qt.RichText)
        self.moments.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.moments.linkActivated.connect(self._ask_moment)
        inner.addWidget(self.moments)

        # Under an edit's preview: "Based on: [thumbnail] source · 4 min ago".
        self.based = QWidget()
        based = QHBoxLayout(self.based)
        based.setContentsMargins(0, 2, 0, 0)
        based.setSpacing(10)
        self.thumb = QLabel()
        self.based_text = QLabel()
        self.based_text.setFont(font(SMALL_PX))
        self.based_text.setWordWrap(True)
        based.addWidget(self.thumb)
        based.addWidget(self.based_text, 1)
        self.based.hide()
        inner.addWidget(self.based)

        # Always there while Edith's last edit can be undone.
        self.btn_undo = self._text_button(self.agent.t("undo_label"), lambda: self.agent.submit("undo"))
        self.btn_undo.hide()
        inner.addWidget(self.btn_undo, 0, Qt.AlignLeft)

        # Options (shown by the options button): two short lines of gray text buttons, so they
        # fit in both languages. Activity, pause, quit; then the global shortcut.
        self.menu = QWidget()
        menu = QVBoxLayout(self.menu)
        menu.setContentsMargins(0, 0, 0, 0)
        menu.setSpacing(8)
        es = self.lang == "es"
        self.btn_activity = self._text_button("Actividad" if es else "Activity",
                                              lambda: self.actions.open_activity(self))
        self.btn_memory = self._text_button("", self._toggle_memory)
        self.btn_quit = self._text_button("Salir" if es else "Quit", self.actions.quit)
        self.btn_hotkey = self._text_button("", self._start_capture)
        self.btn_default = self._text_button(self.agent.t("hotkey_default"), self._restore_default)
        self.btn_default.hide()   # only while a new shortcut is being captured
        self.btn_local = self._text_button("", self._toggle_local)
        self.btn_presentation = self._text_button("", self.toggle_presentation)
        self.btn_delete = self._text_button(self.agent.t("delete_all"), self._delete_everything)
        self.btn_delete.setVisible(self.actions.delete_everything is not None)
        self._delete_armed = False
        self.text_buttons = (self.btn_activity, self.btn_memory, self.btn_quit, self.btn_hotkey,
                             self.btn_default, self.btn_local, self.btn_presentation, self.btn_delete)
        groups = ((self.btn_activity, self.btn_memory, self.btn_local, self.btn_presentation),
                  ((self.btn_hotkey, self.btn_default),),
                  (self.btn_delete, self.btn_quit))
        for n, group in enumerate(groups):
            if n:
                menu.addSpacing(10)                 # groups are separated by space, never by lines
            for item in group:
                row = QHBoxLayout()
                row.setSpacing(16)
                for b in (item if isinstance(item, tuple) else (item,)):
                    row.addWidget(b)
                row.addStretch(1)
                menu.addLayout(row)
        self.menu.hide()
        inner.addWidget(self.menu)

        # Audio and options live inside the field's right end, like the icons of a search field,
        # and fade in only while the mouse is over the panel.
        self.tools = QWidget(self.field)
        row = QHBoxLayout(self.tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.btn_audio = None
        if self.actions.toggle_audio:
            self.btn_audio = SoftButton(self.theme, bars_glyph, checkable=True)
            self.btn_audio.setChecked(True)
            self.btn_audio.setToolTip("Audio")
            self.btn_audio.clicked.connect(lambda: self.btn_audio.setChecked(self.actions.toggle_audio()))
            row.addWidget(self.btn_audio)
        self.btn_options = SoftButton(self.theme, dots_glyph, checkable=True)
        self.btn_options.toggled.connect(self._toggle_menu)
        row.addWidget(self.btn_options)
        self.tools.adjustSize()
        self.field.setTextMargins(TEXT_INSET, 0, self.tools.width() + 8, 0)
        self.hover_fx = QGraphicsOpacityEffect(self.tools, opacity=0.0)
        self.tools.setGraphicsEffect(self.hover_fx)
        self._hover_fade = QVariantAnimation(self, duration=150)
        self._hover_fade.valueChanged.connect(lambda v: self.hover_fx.setOpacity(float(v)))
        self._label_memory()

    def _text_button(self, label: str, on_click) -> QPushButton:
        b = QPushButton(label)
        b.setFlat(True)
        b.setFont(font(SMALL_PX))
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)   # the text field keeps the focus
        b.clicked.connect(on_click)
        return b

    # --- look -----------------------------------------------------------------------------------
    def _restyle(self) -> None:
        pal = self.theme.palette
        self.field.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; color: {pal.text};"
                                 f" selection-background-color: {pal.accent_text}; selection-color: {pal.bg}; }}")
        self.status.setStyleSheet(f"color: {pal.muted}; background: transparent;")
        self.answer.setStyleSheet(f"color: {pal.text}; background: transparent;")
        self.foot.setStyleSheet(f"color: {pal.muted}; background: transparent;")
        self.based_text.setStyleSheet(f"color: {pal.muted}; background: transparent;")
        self.btn_undo.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {pal.muted};"
                                    f" padding: 0; }} QPushButton:hover {{ color: {pal.accent_text}; }}")
        self.moments.setStyleSheet("background: transparent;")
        self._render_moments()
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {pal.idle}; border-radius: 3px; min-height: 24px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page"
            " { background: none; height: 0; }")
        for b in self.text_buttons:
            b.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {pal.muted};"
                            f" padding: 0; }} QPushButton:hover {{ color: {pal.text}; }}")
        # Placeholder color follows the theme too.
        field_pal = self.field.palette()
        field_pal.setColor(field_pal.ColorRole.PlaceholderText, QColor(pal.muted))
        self.field.setPalette(field_pal)
        if not self._dot_fade.state() == QVariantAnimation.Running:
            self.dot_color = QColor(self._dot_target())
        self.update()

    def _dot_target(self) -> str:
        pal = self.theme.palette
        if self.agent.busy:
            return pal.accent
        return pal.idle if self.memory_on else pal.paused

    def _update_dot(self) -> None:
        self._dot_fade.stop()
        self._dot_fade.setStartValue(QColor(self.dot_color))
        self._dot_fade.setEndValue(QColor(self._dot_target()))
        self._dot_fade.start()
        self.field.setReadOnly(self.agent.busy and not self.agent.awaiting)

    def _set_dot_color(self, color) -> None:
        self.dot_color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        pal = self.theme.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.expanded:
            ring, center = self.dot_geometry()
            glow(p, ring, DOT / 2, pal, spread=DOT_GLOW)
            p.setPen(Qt.NoPen)
            p.setBrush(self.dot_color)
            p.drawEllipse(center, 5, 5)
            return
        raised(p, QRectF(M, M, self.width() - 2 * M, self.height() - 2 * M), RADIUS, pal, depth=PANEL_DEPTH)
        inset(p, QRectF(self.field.geometry()), 16, pal, depth=2.5)

    def dot_geometry(self) -> tuple[QRectF, QPointF]:
        """The raised circle and the center of the status dot. Both come from the widget's own
        rectangle (equal margins all round, so the soft shadows are never cut), so the dot and
        the circle share one geometric center: the widget's."""
        ring = QRectF(self.rect()).adjusted(M_DOT, M_DOT, -M_DOT, -M_DOT)
        return ring, ring.center()

    # --- open / close ---------------------------------------------------------------------------
    def _collapse_now(self) -> None:
        self.expanded = False
        self._no_activate(True)
        self.content.hide()
        self.btn_options.setChecked(False)
        self.setFixedSize(DOT + 2 * M_DOT, DOT + 2 * M_DOT)
        self.move(self.dot_pos)
        self.update()

    def collapse(self) -> None:
        if not self.expanded:
            return
        self._collapse_now()
        self._fade(start=0.4)

    def expand(self, activate: bool = True) -> None:
        """Open the panel. `activate=False` shows it without taking the focus from the app the
        user is in (used when Edith needs a confirmation while the panel was closed)."""
        if self.expanded:
            return
        self.expanded = True
        self._no_activate(False)
        self.content.show()
        self.hover_fx.setOpacity(0.0)
        self._load_moments()
        self._fit()
        self._fade()
        self.raise_()
        if activate:
            self.activateWindow()
            self.field.setFocus()
            self.field.selectAll()

    def _hotkey(self, name: str) -> None:
        if name == "open":
            self.toggle()
        elif name == "presentation":
            self.toggle_presentation()

    def _capture_target(self) -> None:
        """Remember the text field in front before Edith takes the focus (for edits)."""
        if self.agent.busy:
            return
        from ..edit.target import capture

        try:
            self.agent.target = capture(self.agent.cfg.is_excluded)
        except Exception:
            log.debug("could not capture the target field", exc_info=True)
            self.agent.target = None

    def panel_origin(self, w: int, h: int) -> tuple[int, int]:
        """Where the open panel goes: centered on the dot, with the text field where the dot was
        (so it opens from the dot's center), moved back inside the screen near an edge. The
        transparent shadow margin may stay off screen; the panel itself never does."""
        cx = self.dot_pos.x() + (DOT + 2 * M_DOT) / 2
        cy = self.dot_pos.y() + (DOT + 2 * M_DOT) / 2
        x = round(cx - w / 2)
        y = round(cy - (M + PAD + self.field.height() / 2))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            x = min(max(screen.left() - M, x), screen.right() + 1 + M - w)
            y = min(max(screen.top() - M, y), screen.bottom() + 1 + M - h)
        return x, y

    def toggle(self) -> None:
        """The global shortcut: open (with the cursor in the field) or close."""
        if self.expanded and self.isActiveWindow():
            self.collapse()
            return
        if not self.expanded:
            self._capture_target()
        self.expand()
        self.raise_()
        self.activateWindow()
        self.field.setFocus()
        self.field.selectAll()

    def _fade(self, start: float = 0.0) -> None:
        self._fade_in.stop()
        self._fade_in.setStartValue(start)
        self._fade_in.start()

    def _fit(self) -> None:
        """Size the panel to its content and keep it on screen, opening from the dot."""
        if not self.expanded:
            return
        w = PANEL_W + 2 * M
        inner = PANEL_W - 2 * PAD - 2 * TEXT_INSET
        has_answer = bool(self.answer.text())
        self.scroll.setVisible(has_answer)
        if has_answer:
            self.scroll.setFixedHeight(min(ANSWER_MAX_H, self.answer.heightForWidth(inner - 10) + 14))
        self.status.setVisible(bool(self.status.text()))
        self.foot.setVisible(has_answer and bool(self.foot.text()))
        self.moments.setVisible(not has_answer and not self.agent.busy and bool(self._moments))
        lay = self.content.layout()
        self.body.layout().invalidate()            # sizes cached before a widget was shown or hidden
        lay.invalidate()
        # Field + body + equal margins, measured on the body itself: the outer layout may keep a
        # height cached from before a widget under the field was shown, hidden or resized.
        bw = PANEL_W - 2 * PAD
        body_h = max(self.body.heightForWidth(bw), self.body.minimumSizeHint().height())
        h = 2 * (M + PAD) + self.field.height() + lay.spacing() + body_h
        self.content.setGeometry(0, 0, w, h)
        self.setFixedSize(w, h)
        x, y = self.panel_origin(w, h)
        self.move(x, y)
        self.update()

    # --- agent ----------------------------------------------------------------------------------
    def _enter(self) -> None:
        typed = self.field.text().strip()
        if self.agent.awaiting:
            if not typed or typed == getattr(self, "_last_request", None):
                self.agent.decide(True)
            return
        if typed and not self.agent.busy:
            self._last_request = typed
            self.answer.setText("")
            self.foot.setText("")
            self.agent.submit(typed)
            self.field.selectAll()

    def _show_status(self, line: str) -> None:
        self.status.setText(line)
        self._fit()

    def _show_footnote(self, line: str) -> None:
        """What was sent and its cost: kept in the ledger and the Activity view, shown under the
        answer only with [ui] show_details = true. The panel stays quiet."""
        self._footnote = line if self.agent.cfg.ui.get("show_details") else ""
        self.foot.setText(self._footnote)
        self._fit()

    # --- edit from memory -----------------------------------------------------------------------
    def _show_preview(self, p) -> None:
        """An edit to confirm: new text in the accent, removed text struck through in gray."""
        from ..brain.edit import preview_html

        if p is None:
            self.based.hide()
            self.foot.setText(self._footnote)
            self._fit()
            return
        pal = self.theme.palette
        old = self.agent.target.text if self.agent.target else ""
        self.answer.setText(f'<div style="line-height:125%;">{preview_html(old, p, pal.accent_text, pal.muted, pal.text)}</div>')
        self.thumb.setPixmap(_thumbnail(p.frame) if p.frame else QPixmap())
        self.thumb.setVisible(bool(p.frame))
        self.based_text.setText(self.agent.t("based_on", source=p.source or "memory", ago=self._ago(p.source_ts)))
        self.based.show()
        self.foot.setText(self.agent.t("edit_hint"))
        if not self.expanded:
            self.expand(activate=False)
        self._fit()

    def _ago(self, ts: float | None) -> str:
        import time as _time

        if ts is None:
            return ""
        s = max(0, int(_time.time() - ts))
        return self.agent.t("ago_s", n=s) if s < 60 else self.agent.t("ago_min", n=round(s / 60))

    def _show_undo(self, on: bool) -> None:
        self.btn_undo.setVisible(on)
        self._fit()

    def _delete_everything(self) -> None:
        """Two clicks: the first asks, the second deletes (the first is forgotten after 4 s)."""
        from PySide6.QtCore import QTimer

        if not self._delete_armed:
            self._delete_armed = True
            self.btn_delete.setText(self.agent.t("delete_confirm"))
            QTimer.singleShot(4000, self._disarm_delete)
            return
        self._disarm_delete()
        n = self.actions.delete_everything()
        self.agent.typist.last = None
        self.btn_undo.hide()
        self._moments = []
        self._render_moments()
        self.answer.setText("")
        self._show_status(self.agent.t("deleted", n=n))

    def _disarm_delete(self) -> None:
        self._delete_armed = False
        self.btn_delete.setText(self.agent.t("delete_all"))

    def toggle_presentation(self) -> None:
        self.presentation = not self.presentation
        self.state["presentation"] = self.presentation
        self._save_state()
        self._label_presentation()

    def _label_presentation(self) -> None:
        self.btn_presentation.setText(self.agent.t("presentation_on" if self.presentation else "presentation_off"))

    # --- key moments ----------------------------------------------------------------------------
    def _load_moments(self) -> None:
        from ..brain.notes import NoteBook

        try:
            self._moments = NoteBook(self.agent.cfg).moments(limit=3)
        except Exception:
            log.debug("could not read the notes", exc_info=True)
            self._moments = []
        self._render_moments()

    def _render_moments(self) -> None:
        from datetime import datetime
        from html import escape

        from PySide6.QtGui import QFontMetrics

        pal = self.theme.palette
        metrics = QFontMetrics(self.moments.font())
        width = (PANEL_W - 2 * PAD - 2 * TEXT_INSET - metrics.horizontalAdvance("00:00")
                 - 3 * metrics.horizontalAdvance(" ") - 6)
        rows = []
        for i, m in enumerate(self._moments):
            when = datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
            line = metrics.elidedText(m["text"], Qt.ElideRight, width)
            rows.append(f'<tr><td style="padding:3px 0;"><a href="{i}" style="color:{pal.text}; text-decoration:none;">'
                        f'{escape(line)}</a></td><td align="right" style="color:{pal.muted}; padding:3px 0;">{when}</td></tr>')
        self.moments.setText(f'<table width="100%" cellspacing="0" cellpadding="0">{"".join(rows)}</table>')

    def _ask_moment(self, index: str) -> None:
        from datetime import datetime

        m = self._moments[int(index)]
        question = self.agent.t("about_moment", time=datetime.fromtimestamp(m["ts"]).strftime("%H:%M"), text=m["text"])
        self.field.setText(question)
        self._enter()

    def _show_reply(self, reply: str) -> None:
        self.answer.setText(_reading_html(reply))
        self.scroll.verticalScrollBar().setValue(0)
        if self.agent.awaiting and not self.expanded:
            self.expand(activate=False)  # a confirmation is needed: it must be seen
        self._fit()

    # --- options --------------------------------------------------------------------------------
    def _toggle_menu(self, on: bool) -> None:
        self.menu.setVisible(on)
        self._fit()

    def _toggle_local(self) -> None:
        self.agent.router.local_only = not self.agent.router.local_only
        self.state["local_only"] = self.agent.router.local_only
        self._save_state()
        self._label_local()

    def _label_local(self) -> None:
        self.btn_local.setText(self.agent.t("local_only_on" if self.agent.router.local_only else "local_only_off"))

    def _toggle_memory(self) -> None:
        self.set_memory_on(self.actions.toggle_memory())

    def set_memory_on(self, on: bool) -> None:
        self.memory_on = on
        self._label_memory()
        self._update_dot()

    def _label_memory(self) -> None:
        es = self.lang == "es"
        self.btn_memory.setText(("Pausar memoria" if es else "Pause memory") if self.memory_on
                                else ("Reanudar memoria" if es else "Resume memory"))

    # --- global shortcut -------------------------------------------------------------------------
    def _label_hotkey(self) -> None:
        combo = self.hotkeys.current.get("open", "-") if self.hotkeys else "-"
        self.btn_hotkey.setText(self.agent.t("hotkey_label", combo=combo))
        self.btn_hotkey.setEnabled(self.hotkeys is not None)

    def _start_capture(self) -> None:
        self.capturing = True
        self.btn_default.show()
        self.field.setFocus()
        self._show_status(self.agent.t("hotkey_press"))

    def _end_capture(self, line: str = "") -> None:
        self.capturing = False
        self.btn_default.hide()
        self._label_hotkey()
        self._show_status(line)

    def _apply_hotkey(self, combo: str) -> None:
        old = self.hotkeys.current.get("open", "-")
        if self.hotkeys.set("open", combo):
            self.state["hotkey"] = self.hotkeys.current["open"]
            self._save_state()
            self._end_capture(self.agent.t("hotkey_saved", combo=self.hotkeys.current["open"]))
        else:
            self._end_capture(self.agent.t("hotkey_taken", combo=combo, old=old))

    def _restore_default(self) -> None:
        self._apply_hotkey(DEFAULT_OPEN)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.field and event.type() == QEvent.Resize:
            self.tools.move(self.field.width() - self.tools.width() - 6,
                            (self.field.height() - self.tools.height()) // 2)
        if self.capturing and obj is self.field and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._end_capture()
                return True
            combo = from_key_event(event.key(), event.modifiers())
            if combo is None:
                return True                      # only modifiers so far: keep waiting
            if not combo:
                self._show_status(self.agent.t("hotkey_need_mod"))
                return True
            self._apply_hotkey(combo)
            return True
        return super().eventFilter(obj, event)

    # --- events ---------------------------------------------------------------------------------
    def enterEvent(self, _event) -> None:
        if self.expanded:
            self._hover(1.0)

    def leaveEvent(self, _event) -> None:
        if self.expanded and not self.btn_options.isChecked():
            self._hover(0.0)

    def _hover(self, to: float) -> None:
        self._hover_fade.stop()
        self._hover_fade.setStartValue(self.hover_fx.opacity())
        self._hover_fade.setEndValue(to)
        self._hover_fade.start()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            if self.agent.awaiting:
                self.agent.decide(False)
            else:
                self.collapse()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        # A click anywhere else deactivates the window: close back into the dot.
        if event.type() == event.Type.ActivationChange and not self.isActiveWindow() and self.expanded:
            if self.capturing:
                self._end_capture()
            self.collapse()
        super().changeEvent(event)

    def _no_activate(self, on: bool) -> None:
        """While closed, a click on the dot must not take the focus: the field the user is in is
        read first (the target of an edit), then the panel opens and takes the focus."""
        import ctypes

        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, -20)                      # GWL_EXSTYLE
        new = (style | 0x08000000) if on else (style & ~0x08000000)  # WS_EX_NOACTIVATE
        if new != style:
            user32.SetWindowLongW(hwnd, -20, new)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self.expanded:
            self._capture_target()
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint() - self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event) -> None:
        if self._press is None:
            return
        to = event.globalPosition().toPoint() - self._press
        if not self._dragged and (to - self.pos()).manhattanLength() < 4:
            return
        self._dragged = True
        self.move(to)

    def mouseReleaseEvent(self, event) -> None:
        if self._press is None:
            return
        self._press = None
        if self._dragged:
            self.dot_pos = self.pos()
            self.state.update(x=self.dot_pos.x(), y=self.dot_pos.y())
            self._save_state()
        elif not self.expanded:
            self.expand()

    # --- saved state -----------------------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _initial_position(self) -> QPoint:
        try:
            return QPoint(int(self.state["x"]), int(self.state["y"]))
        except (KeyError, TypeError, ValueError):
            from PySide6.QtGui import QGuiApplication
            g = QGuiApplication.primaryScreen().availableGeometry()
            return QPoint(g.right() - DOT - 2 * M_DOT - 12, g.top() + int(g.height() * 0.62))

    def _save_state(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(self.state), encoding="utf-8")
        except OSError:
            log.debug("could not save the panel's state", exc_info=True)
