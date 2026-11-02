"""The stage: a full-screen, click-through layer for the lasso, the deck of cards, their flight
to the inventor and the ghost fan. It is only shown while something happens on it.

The ghost fan's OK / cancel lives in `FanBar`, a small window that takes focus while the fan
is open, so Enter and Esc go to Pantallita's own window (nothing else is read)."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import art


def card_lines(row: dict) -> list[str]:
    values = [v for v in row.values() if v not in (None, "")]
    texts = [v.strftime("%d/%m/%Y") if hasattr(v, "strftime") else str(v) for v in values]
    return [texts[0] if texts else "", " · ".join(texts[1:3]), " · ".join(texts[3:5])]


class Stage(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        screen = QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        self.dpr = screen.devicePixelRatio()
        self.t0 = time.monotonic()
        self.lasso: list[QPointF] = []
        self.cards: list[list[str]] = []
        self.card_images: list[QImage | None] = []
        self.deck_at: QPointF | None = None
        self.deck_drawn = QPointF()
        self.flight: tuple[float, QPointF, QPointF] | None = None
        self.fan_images: list[QImage] = []
        self.fan_rect: QRectF | None = None
        self.fan_opened = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def to_logical(self, x: int, y: int) -> QPointF:
        return QPointF(x / self.dpr - self.geometry().left(), y / self.dpr - self.geometry().top())

    def activate(self) -> None:
        if not self.isVisible():
            self.show()
        if not self.timer.isActive():
            self.timer.start(33)

    def idle_check(self) -> None:
        if not (self.lasso or self.deck_at or self.flight or self.fan_images):
            self.timer.stop()
            self.hide()

    # --- lasso and deck ---------------------------------------------------------------------
    def start_lasso(self, x: int, y: int) -> None:
        self.lasso = [self.to_logical(x, y)]
        self.activate()

    def extend_lasso(self, x: int, y: int) -> None:
        self.lasso.append(self.to_logical(x, y))

    def end_lasso(self) -> None:
        self.lasso = []
        self.idle_check()

    def make_deck(self, rows: list[dict], x: int, y: int) -> None:
        self.cards = [card_lines(r) for r in rows]
        self.card_images = [None] * len(rows)
        self.deck_at = self.to_logical(x, y)
        self.deck_drawn = QPointF(self.deck_at)
        self.activate()

    def set_card_images(self, images: list[QImage]) -> None:
        self.card_images = images[: len(self.cards)]

    def move_deck(self, x: int, y: int) -> None:
        if self.deck_at is not None:
            self.deck_at = self.to_logical(x, y)

    def throw_deck(self, to_logical: QPointF) -> None:
        """The deck flies from where it was dropped into the inventor's hands."""
        if self.deck_at is None:
            return
        self.flight = (time.monotonic(), QPointF(self.deck_drawn), to_logical)
        self.deck_at = None

    def drop_deck(self) -> None:
        self.deck_at = None
        self.cards = []
        self.idle_check()

    # --- ghost fan ------------------------------------------------------------------------------
    def open_fan(self, pngs: list[bytes], target_rect_px: tuple[int, int, int, int]) -> None:
        self.fan_images = []
        for data in pngs:
            img = QImage()
            img.loadFromData(data, "PNG")
            self.fan_images.append(img)
        l, t, r, b = target_rect_px
        a, c = self.to_logical(l, t), self.to_logical(r, b)
        self.fan_rect = QRectF(a, c)
        self.fan_opened = time.monotonic()
        self.activate()

    def close_fan(self) -> None:
        self.fan_images = []
        self.fan_rect = None
        self.idle_check()

    # --- animation --------------------------------------------------------------------------------
    def tick(self) -> None:
        if self.deck_at is not None:  # the deck trails the cursor a little
            self.deck_drawn += (self.deck_at - self.deck_drawn) * 0.35
        if self.flight and time.monotonic() - self.flight[0] > 0.7:
            self.flight = None
            self.cards = []
        self.update()
        self.idle_check()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = time.monotonic() - self.t0
        if len(self.lasso) > 1:
            path = QPainterPath(self.lasso[0])
            for pt in self.lasso[1:]:
                path.lineTo(pt)
            p.setBrush(QColor(42, 157, 143, 40))
            pen = art.pen(art.TEAL, 2.5)
            pen.setStyle(Qt.DashLine)
            pen.setDashOffset(-t * 20)
            p.setPen(pen)
            p.drawPath(path)
        if self.cards and self.deck_at is not None:
            self.draw_deck(p, self.deck_drawn + QPointF(18, 18), t, 1.0)
        if self.flight:
            start, a, b = self.flight
            k = min(1.0, (time.monotonic() - start) / 0.7)
            pos = a + (b - a) * k - QPointF(0, math.sin(k * math.pi) * 140)
            self.draw_deck(p, pos, t, 1.0 - 0.6 * k)
        if self.fan_images and self.fan_rect is not None:
            self.draw_fan(p)
        p.end()

    def draw_deck(self, p: QPainter, at: QPointF, t: float, scale: float) -> None:
        n = len(self.cards)
        shown = list(range(n))[-6:]
        for k, i in enumerate(shown):
            off = (len(shown) - 1 - k)
            rect = QRectF(at.x() + off * 5, at.y() + off * 4, 170 * scale, 50 * scale)
            img = self.card_images[i] if i < len(self.card_images) else None
            art.draw_card(p, rect, self.cards[i], tilt=-6 + k * 2.2 + math.sin(t * 3 + k) * 1.2, image=img)
        if n > 1:
            badge = QPointF(at.x() + 172 * scale, at.y() - 2)
            p.setPen(art.pen(art.INK, 2))
            p.setBrush(art.CORAL)
            p.drawEllipse(badge, 13, 13)
            p.setPen(art.PAPER)
            p.setFont(art.font(8.5, bold=True))
            p.drawText(QRectF(badge.x() - 13, badge.y() - 13, 26, 26), Qt.AlignCenter, str(n))

    def draw_fan(self, p: QPainter) -> None:
        k = min(1.0, (time.monotonic() - self.fan_opened) / 0.5)
        r = self.fan_rect
        n = len(self.fan_images)
        center = QPointF(r.center().x(), r.top() + r.height() * 0.55)
        width = min(400.0, r.width() / max(2.0, 0.75 * n + 0.6))
        for i, img in enumerate(self.fan_images):
            spread = (i - (n - 1) / 2) * k
            h = width * img.height() / max(1, img.width())
            p.save()
            p.translate(center + QPointF(spread * width * 0.72, abs(spread) * 22))
            p.rotate(spread * 9)
            p.setOpacity(0.86)
            frame = QRectF(-width / 2 - 8, -h / 2 - 8, width + 16, h + 16)
            p.setPen(art.pen(art.INK, 2))
            p.setBrush(art.PAPER)
            p.drawRoundedRect(frame, 10, 10)
            p.drawImage(QRectF(-width / 2, -h / 2, width, h), img)
            p.restore()


class FanBar(QWidget):
    """'Send N rows? total ...' with Send (Enter) and Cancel (Esc). Takes focus while open."""

    decided = Signal(bool)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        box = QWidget(self)
        box.setObjectName("box")
        box.setStyleSheet(
            "#box { background: #fffdf8; border: 2px solid #24303d; border-radius: 14px; }"
            "QLabel { color: #24303d; font: 10.5pt 'Segoe UI'; }"
            "QPushButton { font: bold 10pt 'Segoe UI'; border: 2px solid #24303d; border-radius: 9px; padding: 7px 16px; }"
            "#send { background: #2a9d8f; color: #fffdf8; } #cancel { background: #fff6e5; color: #24303d; }")
        self.label = QLabel()
        self.label.setWordWrap(True)
        send = self.send_button = QPushButton("Send  ⏎")
        send.setObjectName("send")
        cancel = self.cancel_button = QPushButton("Cancel  Esc")
        cancel.setObjectName("cancel")
        send.clicked.connect(lambda: self.finish(True))
        cancel.clicked.connect(lambda: self.finish(False))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(send)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(18, 14, 18, 14)
        inner.addWidget(self.label)
        inner.addLayout(buttons)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        self.resize(460, 130)

    def open(self, text: str, ok: str = "Send  ⏎", cancel: str = "Cancel  Esc") -> None:
        self.label.setText(text)
        self.send_button.setText(ok)
        self.cancel_button.setText(cancel)
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.move(avail.center().x() - self.width() // 2, avail.bottom() - self.height() - 24)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def finish(self, ok: bool) -> None:
        self.hide()
        self.decided.emit(ok)

    def keyPressEvent(self, event) -> None:  # keys typed into THIS window only
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(True)
        elif event.key() == Qt.Key_Escape:
            self.finish(False)
