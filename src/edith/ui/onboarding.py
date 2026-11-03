"""First run: three short screens, then never again. What Edith does; the privacy sentence;
the shortcut, which can be changed right there. Enter goes on, Esc skips."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .hotkey import from_key_event
from .theme import raised

W, H, M = 460, 280, 18


class Onboarding(QWidget):
    finished = Signal()

    def __init__(self, panel):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        from .panel import font

        self.panel, self.theme, self.t = panel, panel.theme, panel.agent.t
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Edith")
        self.setFixedSize(W + 2 * M, H + 2 * M)
        self.page = 0
        self.capturing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(M + 34, M + 30, M + 34, M + 22)
        lay.setSpacing(12)
        self.title = QLabel()
        self.title.setFont(font(15, light=True))
        self.body = QLabel()
        self.body.setFont(font(10.5))
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.PlainText)
        lay.addWidget(self.title)
        lay.addWidget(self.body, 1)
        row = QHBoxLayout()
        self.change = QPushButton()
        self.next = QPushButton()
        for b in (self.change, self.next):
            b.setFlat(True)
            b.setFont(font(10))
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        self.change.clicked.connect(self._start_capture)
        self.next.clicked.connect(self._next)
        row.addWidget(self.change)
        row.addStretch(1)
        row.addWidget(self.next)
        lay.addLayout(row)
        self._fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=220)
        self._fade.valueChanged.connect(lambda v: self.setWindowOpacity(float(v)))
        self.theme.changed.connect(self._restyle)
        self._restyle()
        self._show_page()

    def open(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._fade.start()

    # --- pages ----------------------------------------------------------------------------------
    def _show_page(self, line: str = "") -> None:
        p = self.page + 1
        combo = self.panel.hotkeys.current.get("open", "-") if self.panel.hotkeys else "-"
        self.title.setText(self.t(f"onb{p}_title"))
        body = self.t(f"onb{p}_body", combo=combo)
        self.body.setText(body + (f"\n\n{line}" if line else ""))
        self.change.setVisible(self.page == 2 and self.panel.hotkeys is not None)
        self.change.setText(self.t("hotkey_press") if self.capturing else self.t("onb_change"))
        self.next.setText(self.t("onb_start") if self.page == 2 else self.t("onb_next"))
        self.update()

    def _next(self) -> None:
        if self.page < 2:
            self.page += 1
            self._show_page()
        else:
            self._finish()

    def _finish(self) -> None:
        self.panel.state["onboarded"] = True
        self.panel._save_state()
        self.close()
        self.finished.emit()

    def _start_capture(self) -> None:
        self.capturing = True
        self._show_page()

    def keyPressEvent(self, event) -> None:
        if self.capturing:
            if event.key() == Qt.Key_Escape:
                self.capturing = False
                self._show_page()
                return
            combo = from_key_event(event.key(), event.modifiers())
            if combo is None:
                return
            if not combo:
                self._show_page(self.t("hotkey_need_mod"))
                return
            self.capturing = False
            old = self.panel.hotkeys.current.get("open", "-")
            if self.panel.hotkeys.set("open", combo):
                self.panel.state["hotkey"] = self.panel.hotkeys.current["open"]
                self.panel._save_state()
                self.panel._label_hotkey()
                self._show_page(self.t("hotkey_saved", combo=self.panel.hotkeys.current["open"]))
            else:
                self._show_page(self.t("hotkey_taken", combo=combo, old=old))
            return
        if event.key() == Qt.Key_Escape:
            self._finish()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._next()

    # --- look -----------------------------------------------------------------------------------
    def _restyle(self) -> None:
        pal = self.theme.palette
        self.title.setStyleSheet(f"color: {pal.text}; background: transparent;")
        self.body.setStyleSheet(f"color: {pal.muted}; background: transparent;")
        for b, color in ((self.change, pal.muted), (self.next, pal.accent)):
            b.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {color}; padding: 0; }}"
                            f" QPushButton:hover {{ color: {pal.text}; }}")
        self.update()

    def paintEvent(self, _event) -> None:
        pal = self.theme.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        raised(p, QRectF(M, M, W, H), 24, pal, depth=6)
        p.setPen(Qt.NoPen)
        for i in range(3):                              # where we are: three small dots
            p.setBrush(QColor(pal.accent if i == self.page else pal.idle))
            p.drawEllipse(QPointF(M + W / 2 + (i - 1) * 14, M + H - 18), 3, 3)
