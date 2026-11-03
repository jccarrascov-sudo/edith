"""Presentation mode: a soft outline around the field Edith is about to write into.

Off by default (Ctrl+Alt+P or the options turn it on), for demos and screen recordings. It is
the only thing Edith ever draws over other apps: a click-through, always-on-top window that
never takes the focus, a 2 px accent line with a faint glow, shown while an edit is previewed
and applied, then gone."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .theme import Theme

PAD = 10        # room for the glow around the field
RADIUS = 12


class Outline(QWidget):
    def __init__(self, theme: Theme):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.theme = theme
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=220)
        self._fade.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))

    def show_at(self, rect: tuple[int, int, int, int]) -> None:
        """`rect` in physical screen pixels (left, top, right, bottom), as UI Automation gives it."""
        left, top, right, bottom = rect
        screen = QGuiApplication.screenAt(QRect(left, top, 1, 1).center()) or QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        x, y = int(left / dpr) - PAD, int(top / dpr) - PAD
        w, h = int((right - left) / dpr) + 2 * PAD, int((bottom - top) / dpr) + 2 * PAD
        if w <= 2 * PAD or h <= 2 * PAD:
            return
        self.setGeometry(x, y, w, h)
        self.setWindowOpacity(0.0)
        self.show()
        self._fade.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(PAD, PAD, -PAD, -PAD)
        accent = QColor(self.theme.palette.accent)
        for i in range(6, 0, -1):                      # faint glow, fading outwards
            glow = QColor(accent)
            glow.setAlphaF(0.05)
            p.setPen(QPen(glow, i * 1.6))
            p.drawRoundedRect(r, RADIUS, RADIUS)
        accent.setAlphaF(0.9)
        p.setPen(QPen(accent, 2))
        p.drawRoundedRect(r, RADIUS, RADIUS)
