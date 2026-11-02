"""The workshop shelf: every machine the inventor built, with its (editable) name and the time it
gave back, plus the total. Shown when the inventor goes to his workshop."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QInputDialog, QWidget

from ..workshop import build
from . import art

ROW_H = 46


def time_saved(tool_dir: Path) -> tuple[float, int]:
    """(seconds given back, rows done) over all runs: rows x the demo's time per row, minus run time."""
    plan = json.loads((tool_dir / "plan.json").read_text(encoding="utf-8"))
    per_row = float(plan.get("seconds_per_row_by_hand", 0))
    saved, rows = 0.0, 0
    for log in (tool_dir / "runs").glob("*.jsonl") if (tool_dir / "runs").exists() else []:
        ok, seconds = 0, 0.0
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "row_out" and e.get("ok"):
                ok += 1
            elif e.get("event") == "run_end":
                seconds = float(e.get("seconds", 0))
        rows += ok
        saved += max(0.0, ok * per_row - seconds)
    return saved, rows


def machines() -> list[dict]:
    out = []
    if not build.TOOLS_DIR.exists():
        return out
    for d in sorted(build.TOOLS_DIR.iterdir()):
        if not (d / "plan.json").exists():
            continue
        plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
        saved, rows = time_saved(d)
        out.append({"dir": d, "name": plan["name"], "blocks": [b["type"] for b in plan["blocks"]],
                    "saved": saved, "rows": rows, "proof": (d / "proof" / "expected.json").exists()})
    return out


def rename(tool_dir: Path, name: str) -> None:
    path = tool_dir / "plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["name"] = name
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")


def fmt(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    if seconds >= 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds:.0f} s"


class Shelf(QWidget):
    renamed = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.items: list[dict] = []
        self.t0 = time.monotonic()

    def open(self) -> None:
        self.items = machines()
        h = 70 + max(1, len(self.items)) * ROW_H + 34
        w = 330
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(avail.right() - w - 16, avail.bottom() - h - 96, w, h)
        self.show()
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(art.pen(art.INK, 2))
        p.setBrush(QColor(255, 253, 248, 240))
        p.drawRoundedRect(QRectF(4, 4, w - 8, h - 8), 16, 16)
        p.setPen(art.INK)
        p.setFont(art.font(11, bold=True))
        p.drawText(QRectF(18, 12, w - 36, 24), Qt.AlignLeft | Qt.AlignVCenter, "Workshop")
        total = sum(m["saved"] for m in self.items)
        p.setFont(art.font(9))
        p.drawText(QRectF(18, 12, w - 36, 24), Qt.AlignRight | Qt.AlignVCenter, f"time given back: {fmt(total)}")
        if not self.items:
            p.drawText(QRectF(18, 50, w - 36, ROW_H), Qt.AlignCenter, "No machines yet. Show me one!")
        for i, m in enumerate(self.items):
            y = 50 + i * ROW_H
            # the plank
            p.setPen(Qt.NoPen)
            p.setBrush(art.MUSTARD_DARK)
            p.drawRoundedRect(QRectF(14, y + ROW_H - 10, w - 28, 6), 3, 3)
            # a mini machine: one tiny block per real block
            for k, bt in enumerate(m["blocks"][:9]):
                art.draw_block(p, QRectF(18 + k * 11, y + 14, 10, 20), bt, "")
            p.setPen(art.INK)
            p.setFont(art.font(9, bold=True))
            p.drawText(QRectF(124, y + 6, w - 140, 18), Qt.AlignLeft | Qt.AlignVCenter, m["name"])
            p.setFont(art.font(8))
            p.drawText(QRectF(124, y + 22, w - 140, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{fmt(m['saved'])} saved · {m['rows']} rows" + ("" if m["proof"] else " · no proof"))
        p.setFont(art.font(7.5))
        p.setPen(QColor(36, 48, 61, 150))
        p.drawText(QRectF(18, h - 30, w - 36, 18), Qt.AlignCenter, "click a name to rename it")
        p.end()

    def mousePressEvent(self, event) -> None:
        i = int((event.position().y() - 50) // ROW_H)
        if 0 <= i < len(self.items) and event.position().x() > 120:
            m = self.items[i]
            name, ok = QInputDialog.getText(self, "Rename machine", "Name:", text=m["name"])
            if ok and name.strip():
                rename(m["dir"], name.strip()[:40])
                self.items = machines()
                self.update()
                self.renamed.emit()
