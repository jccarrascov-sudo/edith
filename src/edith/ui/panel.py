"""Edith on screen: a small soft dot that opens into a minimal panel.

At rest: a 40 px raised dot, always on top, draggable, with no text or icon. Its center is
the only status indicator: gray when idle, the accent while thinking or acting (a soft fade,
no animation). A click opens the panel: one text field and the answer under it. The audio
and options buttons fade in only while the mouse is over the panel. Esc or a click elsewhere
closes it back into the dot. Rule: what is not needed right now is not shown."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..config import ROOT
from .agent import Agent
from .theme import Theme, inset, raised

log = logging.getLogger(__name__)

DOT = 40              # the dot's diameter
M = 18                # transparent margin for the soft shadows
PANEL_W = 380
RADIUS = 22
FADE_MS = 180
ANSWER_MAX_H = 420
POSITION_FILE = ROOT / "state" / "ui.json"


@dataclass
class Actions:
    """What the hover buttons do (provided by the app, which owns the memory threads)."""
    toggle_audio: Callable[[], bool] | None   # returns: listening now? None = no audio button
    toggle_memory: Callable[[], bool]          # returns: recording now?
    open_activity: Callable[["Edith"], None]
    quit: Callable[[], None]


def font(size: float, light: bool = False) -> QFont:
    f = QFont("Segoe UI Variable Text", 1)
    f.setPointSizeF(size)
    if light:
        f.setWeight(QFont.Light)
    return f


class SoftButton(QAbstractButton):
    """A round button of the background's color: raised, or inset while pressed or checked."""

    def __init__(self, theme: Theme, glyph: Callable[[QPainter, QRectF, QColor], None], checkable=False):
        super().__init__()
        self.theme, self.glyph = theme, glyph
        self.setCheckable(checkable)
        self.setFixedSize(QSize(30 + 12, 30 + 12))
        self.setCursor(Qt.PointingHandCursor)
        theme.changed.connect(self.update)

    def paintEvent(self, _event) -> None:
        pal = self.theme.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(6, 6, 30, 30)
        pressed = self.isDown() or self.isChecked()
        (inset if pressed else raised)(p, r, 15, pal, depth=2.5 if pressed else 3)
        self.glyph(p, r, QColor(pal.accent if self.isChecked() else pal.muted))


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
    def __init__(self, theme: Theme, agent: Agent, actions: Actions):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.theme, self.agent, self.actions = theme, agent, actions
        self.lang = agent.lang
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Edith")
        self.expanded = False
        self.dot_pos = self._load_position()
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
        self._restyle()
        self._collapse_now()

    # --- construction ---------------------------------------------------------------------------
    def _build(self) -> None:
        self.content = QWidget(self)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(M + 22, M + 20, M + 22, M + 12)
        outer.setSpacing(10)

        self.field = QLineEdit()
        self.field.setFont(font(10.5))
        self.field.setPlaceholderText(self.agent.t("placeholder"))
        self.field.setFrame(False)
        self.field.setMinimumHeight(30)
        self.field.returnPressed.connect(self._enter)
        outer.addWidget(self.field)
        outer.addSpacing(6)

        self.status = QLabel()
        self.status.setFont(font(9.5))
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self.answer = QLabel()
        self.answer.setFont(font(10.5, light=True))
        self.answer.setWordWrap(True)
        self.answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.answer)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)

        # Hover-only row: options on the right, audio next to it. Space is kept, so the panel
        # does not jump when the mouse comes in; the buttons only fade in.
        self.hover_row = QWidget()
        row = QHBoxLayout(self.hover_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.menu = QWidget()
        menu = QHBoxLayout(self.menu)
        menu.setContentsMargins(0, 0, 0, 0)
        menu.setSpacing(14)
        es = self.lang == "es"
        self.btn_activity = self._text_button("Actividad" if es else "Activity",
                                              lambda: self.actions.open_activity(self))
        self.btn_memory = self._text_button("", self._toggle_memory)
        self.btn_quit = self._text_button("Salir" if es else "Quit", self.actions.quit)
        for b in (self.btn_activity, self.btn_memory, self.btn_quit):
            menu.addWidget(b)
        self.menu.hide()
        row.addWidget(self.menu)
        row.addStretch(1)
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
        self.hover_fx = QGraphicsOpacityEffect(self.hover_row, opacity=0.0)
        self.hover_row.setGraphicsEffect(self.hover_fx)
        self._hover_fade = QVariantAnimation(self, duration=150)
        self._hover_fade.valueChanged.connect(lambda v: self.hover_fx.setOpacity(float(v)))
        outer.addWidget(self.hover_row)
        self._label_memory()

    def _text_button(self, label: str, on_click) -> QPushButton:
        b = QPushButton(label)
        b.setFlat(True)
        b.setFont(font(9.5))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(on_click)
        return b

    # --- look -----------------------------------------------------------------------------------
    def _restyle(self) -> None:
        pal = self.theme.palette
        self.field.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; color: {pal.text};"
                                 f" selection-background-color: {pal.accent}; selection-color: {pal.bg}; }}")
        self.status.setStyleSheet(f"color: {pal.muted}; background: transparent;")
        self.answer.setStyleSheet(f"color: {pal.text}; background: transparent;")
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
            f"QScrollBar::handle:vertical {{ background: {pal.idle}; border-radius: 3px; min-height: 24px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page"
            " { background: none; height: 0; }")
        for b in (self.btn_activity, self.btn_memory, self.btn_quit):
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
        return pal.accent if self.agent.busy else pal.idle

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
            raised(p, QRectF(M, M, DOT, DOT), DOT / 2, pal, depth=4)
            p.setPen(Qt.NoPen)
            p.setBrush(self.dot_color)
            p.drawEllipse(QPointF(M + DOT / 2, M + DOT / 2), 5, 5)
            return
        raised(p, QRectF(M, M, self.width() - 2 * M, self.height() - 2 * M), RADIUS, pal, depth=6)
        g = self.field.geometry()
        inset(p, QRectF(g).adjusted(-14, -6, 14, 6), 16, pal, depth=2.5)

    # --- open / close ---------------------------------------------------------------------------
    def _collapse_now(self) -> None:
        self.expanded = False
        self.content.hide()
        self.btn_options.setChecked(False)
        self.setFixedSize(DOT + 2 * M, DOT + 2 * M)
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
        self.content.show()
        self.hover_fx.setOpacity(0.0)
        self._fit()
        self._fade()
        self.raise_()
        if activate:
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
        inner = w - 2 * (M + 22)
        has_answer = bool(self.answer.text())
        self.scroll.setVisible(has_answer)
        if has_answer:
            self.scroll.setFixedHeight(min(ANSWER_MAX_H, self.answer.heightForWidth(inner - 10) + 14))
        self.status.setVisible(bool(self.status.text()))
        lay = self.content.layout()
        h = lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.sizeHint().height()
        self.content.setGeometry(0, 0, w, h)
        self.setFixedSize(w, h)
        screen = (self.screen().availableGeometry() if self.screen() else None)
        x, y = self.dot_pos.x(), self.dot_pos.y()
        if screen:
            x = min(max(screen.left(), x), screen.right() - w)
            y = min(max(screen.top(), y), screen.bottom() - h)
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
            self.agent.submit(typed)
            self.field.selectAll()

    def _show_status(self, line: str) -> None:
        self.status.setText(line)
        self._fit()

    def _show_reply(self, reply: str) -> None:
        self.answer.setText(reply)
        self.scroll.verticalScrollBar().setValue(0)
        if self.agent.awaiting and not self.expanded:
            self.expand(activate=False)  # a confirmation is needed: it must be seen
        self._fit()

    # --- options --------------------------------------------------------------------------------
    def _toggle_menu(self, on: bool) -> None:
        self.menu.setVisible(on)

    def _toggle_memory(self) -> None:
        self.set_memory_on(self.actions.toggle_memory())

    def set_memory_on(self, on: bool) -> None:
        self.memory_on = on
        self._label_memory()

    def _label_memory(self) -> None:
        es = self.lang == "es"
        self.btn_memory.setText(("Pausar memoria" if es else "Pause memory") if self.memory_on
                                else ("Reanudar memoria" if es else "Resume memory"))

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
            self.collapse()
        super().changeEvent(event)

    def mousePressEvent(self, event) -> None:
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
            self._save_position()
        elif not self.expanded:
            self.expand()

    # --- position -------------------------------------------------------------------------------
    def _load_position(self) -> QPoint:
        try:
            data = json.loads(POSITION_FILE.read_text(encoding="utf-8"))
            return QPoint(int(data["x"]), int(data["y"]))
        except (OSError, ValueError, KeyError, TypeError):
            from PySide6.QtGui import QGuiApplication
            g = QGuiApplication.primaryScreen().availableGeometry()
            return QPoint(g.right() - DOT - 2 * M - 12, g.top() + int(g.height() * 0.62))

    def _save_position(self) -> None:
        try:
            POSITION_FILE.parent.mkdir(parents=True, exist_ok=True)
            POSITION_FILE.write_text(json.dumps({"x": self.dot_pos.x(), "y": self.dot_pos.y()}), encoding="utf-8")
        except OSError:
            log.debug("could not save the dot's position", exc_info=True)
