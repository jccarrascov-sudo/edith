"""The activity view, apart from the panel: RAM, VRAM and memory activity in the last minutes.

Each card is the important number, large, and under it a sparkline (one accent line and a
faint fill) for the trend. No axes, no grid, no legend; the exact value of a point appears
only in a tooltip under the mouse. A card with no data is not shown."""

from __future__ import annotations

import ctypes
import subprocess
import threading
import time
from collections import deque
from ctypes import wintypes

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QToolTip, QWidget

from ..config import Config
from .theme import Theme, raised

SAMPLE_S = 5
MARGIN = 18          # room for the soft shadows around each card
CARD_W, CARD_H = 196, 132


# --- data ---------------------------------------------------------------------------------------

class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def ram_used_gb() -> float:
    status = _MemoryStatus(dwLength=ctypes.sizeof(_MemoryStatus))
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return (status.ullTotalPhys - status.ullAvailPhys) / 2**30


def vram_used_gb() -> float | None:
    """GPU memory in use, from nvidia-smi; None if there is no NVIDIA GPU or driver tool."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return float(out.stdout.split()[0]) / 1024 if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def activity_bins(events: list[dict], now: float, minutes: float = 10, bin_s: float = 30) -> list[tuple[float, int]]:
    """(bin end time, events recorded in it) over the last `minutes`, oldest first."""
    n = int(minutes * 60 // bin_s)
    start = now - n * bin_s
    counts = [0] * n
    for e in events:
        i = int((e.get("ts", 0) - start) // bin_s)
        if 0 <= i < n:
            counts[i] += 1
    return [(start + (i + 1) * bin_s, c) for i, c in enumerate(counts)]


class Sampler:
    """RAM and VRAM every SAMPLE_S seconds for the retention window, on a background thread."""

    def __init__(self, cfg: Config, stop: threading.Event):
        size = int(cfg.retention_s // SAMPLE_S)
        self.ram: deque[tuple[float, float]] = deque(maxlen=size)
        self.vram: deque[tuple[float, float]] = deque(maxlen=size)
        self.stop = stop
        threading.Thread(target=self._run, name="sampler", daemon=True).start()

    def _run(self) -> None:
        has_gpu = True
        while True:
            now = time.time()
            self.ram.append((now, ram_used_gb()))
            if has_gpu:
                v = vram_used_gb()
                if v is None:
                    has_gpu = False  # no GPU tool: stop asking, the card stays hidden
                else:
                    self.vram.append((now, v))
            if self.stop.wait(SAMPLE_S):
                return


# --- drawing ------------------------------------------------------------------------------------

def spark_points(values: list[float], rect: QRectF) -> list[QPointF]:
    """Values spread over the rect's width; the line uses the full height (trend, not scale)."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = rect.width() / max(1, len(values) - 1)
    return [QPointF(rect.left() + i * step, rect.bottom() - (v - lo) / span * rect.height())
            for i, v in enumerate(values)]


class Card(QWidget):
    def __init__(self, theme: Theme, label: str, fmt, tip):
        super().__init__()
        self.theme, self.label, self.fmt, self.tip = theme, label, fmt, tip
        self.series: list[tuple[float, float]] = []
        self.setFixedSize(CARD_W + 2 * MARGIN, CARD_H + 2 * MARGIN)
        self.setMouseTracking(True)
        theme.changed.connect(self.update)

    def set_series(self, series: list[tuple[float, float]]) -> None:
        self.series = series
        self.setVisible(bool(series))
        self.update()

    def _line_rect(self) -> QRectF:
        return QRectF(MARGIN + 18, MARGIN + 76, CARD_W - 36, CARD_H - 94)

    def paintEvent(self, _event) -> None:
        pal = self.theme.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        raised(p, QRectF(MARGIN, MARGIN, CARD_W, CARD_H), 20, pal, depth=6)
        p.setPen(QColor(pal.muted))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRectF(MARGIN + 18, MARGIN + 14, CARD_W - 36, 18), Qt.AlignLeft, self.label)
        if not self.series:
            return
        p.setPen(QColor(pal.text))
        big = QFont("Segoe UI Variable Display", 24)
        big.setWeight(QFont.Light)
        p.setFont(big)
        p.drawText(QRectF(MARGIN + 18, MARGIN + 30, CARD_W - 36, 40), Qt.AlignLeft | Qt.AlignVCenter,
                   self.fmt(self.series[-1][1]))
        pts = spark_points([v for _, v in self.series], self._line_rect())
        if len(pts) < 2:
            return
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        area = QPainterPath(line)
        r = self._line_rect()
        area.lineTo(pts[-1].x(), r.bottom())
        area.lineTo(pts[0].x(), r.bottom())
        area.closeSubpath()
        fill = QLinearGradient(0, r.top(), 0, r.bottom())
        top, bottom = QColor(pal.accent), QColor(pal.accent)
        top.setAlphaF(0.13)
        bottom.setAlphaF(0.0)
        fill.setColorAt(0, top)
        fill.setColorAt(1, bottom)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPath(area)
        p.setPen(QPen(QColor(pal.accent), 1.75, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)

    def mouseMoveEvent(self, event) -> None:
        r = self._line_rect()
        if not self.series or not r.adjusted(-6, -12, 6, 6).contains(event.position()):
            QToolTip.hideText()
            return
        i = round((event.position().x() - r.left()) / max(1.0, r.width()) * (len(self.series) - 1))
        ts, value = self.series[max(0, min(len(self.series) - 1, i))]
        QToolTip.showText(event.globalPosition().toPoint(), self.tip(ts, value), self)


class StatsView(QWidget):
    """A separate floating window with one card per series that has data. Esc or clicking
    elsewhere closes it."""

    def __init__(self, cfg: Config, theme: Theme, sampler: Sampler, lang: str):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.cfg, self.theme, self.sampler = cfg, theme, sampler
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Edith")
        es = lang == "es"

        def ago(ts: float) -> str:
            s = max(0, int(time.time() - ts))
            return (f"hace {s} s" if es else f"{s} s ago") if s < 60 else \
                   (f"hace {s // 60} min" if es else f"{s // 60} min ago")

        self.ram = Card(theme, "RAM", lambda v: f"{v:.1f} GB", lambda ts, v: f"{v:.2f} GB · {ago(ts)}")
        self.vram = Card(theme, "VRAM", lambda v: f"{v:.1f} GB", lambda ts, v: f"{v:.2f} GB · {ago(ts)}")
        self.activity = Card(theme, "Actividad · 10 min" if es else "Activity · 10 min",
                             lambda v: f"{sum(c for _, c in self.activity.series):.0f}",
                             lambda ts, v: (f"{v:.0f} eventos · {ago(ts)}" if es else f"{v:.0f} events · {ago(ts)}"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        for card in (self.activity, self.ram, self.vram):
            layout.addWidget(card)
        self.timer = QTimer(self, interval=SAMPLE_S * 1000, timeout=self.refresh)
        theme.changed.connect(self.update)

    def refresh(self) -> None:
        from ..memory.timeline import Timeline

        events = Timeline(self.cfg).read()
        bins = activity_bins(events, time.time(), self.cfg.retention["minutes"])
        self.activity.set_series([(ts, float(c)) for ts, c in bins] if any(c for _, c in bins) else [])
        self.ram.set_series(list(self.sampler.ram))
        self.vram.set_series(list(self.sampler.vram))
        self.adjustSize()

    def open_near(self, anchor) -> None:
        self.refresh()
        self.timer.start()
        screen = (self.screen() or anchor.screen()).availableGeometry()
        x = min(max(screen.left(), anchor.x()), screen.right() - self.width())
        y = anchor.y() + anchor.height() if anchor.y() + anchor.height() + self.height() < screen.bottom() \
            else anchor.y() - self.height()
        self.move(x, max(screen.top(), y))
        self.show()
        self.activateWindow()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        raised(p, QRectF(self.rect()).adjusted(8, 8, -8, -8), 24, self.theme.palette, depth=4)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()

    def changeEvent(self, event) -> None:
        if event.type() == event.Type.ActivationChange and not self.isActiveWindow():
            self.close()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        self.timer.stop()
        super().closeEvent(event)
