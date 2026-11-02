"""The machine, visible: its real blocks in a row, the engine (compile level), cards going in
and out, and the verification stamps. Click a block to see its actual code."""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from . import art

BLOCK_W, BLOCK_H, GAP = 78, 46, 8


class MachineWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.t0 = time.monotonic()
        self.plan: dict | None = None
        self.code: dict = {}
        self.block_state: dict[str, str] = {}
        self.level = "page"
        self.caption = ""
        self.rows_total = 0
        self.rows_done = 0
        self.rows_skipped = 0
        self.rows_failed = 0
        self.card_in: tuple[float, str] | None = None
        self.card_out: tuple[float, bool] | None = None
        self.stamp_cases: list[dict] = []
        self.stamp_at = 0.0
        self.code_for: str | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)

    # --- layout ---------------------------------------------------------------------------------
    def blocks(self) -> list[dict]:
        return self.plan["blocks"] if self.plan else []

    def block_rect(self, i: int) -> QRectF:
        return QRectF(70 + i * (BLOCK_W + GAP), 44, BLOCK_W, BLOCK_H)

    def place(self) -> None:
        n = max(1, len(self.blocks()))
        w = 70 + n * (BLOCK_W + GAP) + 150
        h = 150 + (190 if self.code_for else 0)
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(w, h)
        self.move(avail.right() - w - 20, avail.bottom() - h - 150)

    # --- state from the flow ------------------------------------------------------------------
    def show_machine(self, plan: dict, code: dict) -> None:
        self.plan, self.code = plan, code or self.code
        self.block_state = {b["id"]: ("building" if not code else "idle") for b in plan["blocks"]}
        self.caption = plan["name"]
        self.code_for = None
        self.place()
        self.show()
        self.timer.start(33)

    def set_stamps(self, cases: list[dict]) -> None:
        self.stamp_cases = cases
        self.stamp_at = time.monotonic()

    def on_event(self, e: dict) -> None:
        kind = e.get("event")
        if kind == "run_start":
            self.rows_total, self.rows_done, self.rows_skipped, self.rows_failed = e.get("rows", 0), 0, 0, 0
            self.level = e.get("level", self.level)
            self.block_state = {b["id"]: "idle" for b in self.blocks()}
        elif kind == "row_in":
            self.card_in = (time.monotonic(), str(e.get("row", "")))
            self.block_state = {b["id"]: "idle" for b in self.blocks()}
        elif kind == "block_done":
            self.block_state[e["block"]] = "done"
            nxt = self.next_block(e["block"])
            if nxt:
                self.block_state[nxt] = "active"
        elif kind == "block_error" and e.get("block"):
            self.block_state[e["block"]] = "error"
        elif kind == "row_out":
            self.card_out = (time.monotonic(), bool(e.get("ok")))
            self.rows_done += bool(e.get("ok"))
            self.rows_failed += not e.get("ok")
        elif kind == "row_skipped":
            self.rows_skipped += 1
            self.card_out = (time.monotonic(), False)

    def next_block(self, block_id: str) -> str | None:
        ids = [b["id"] for b in self.blocks()]
        i = ids.index(block_id) if block_id in ids else -1
        return ids[i + 1] if 0 <= i < len(ids) - 1 else None

    def finish(self, text: str) -> None:
        self.caption = text
        QTimer.singleShot(9000, self.close_machine)

    def close_machine(self) -> None:
        self.timer.stop()
        self.hide()

    # --- drawing ----------------------------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        if not self.plan:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = time.monotonic() - self.t0
        w = self.width()
        bench = QRectF(6, 6, w - 12, 138)
        p.setPen(art.pen(art.INK, 2))
        p.setBrush(QColor(255, 253, 248, 235))
        p.drawRoundedRect(bench, 16, 16)
        p.setPen(art.INK)
        p.setFont(art.font(10, bold=True))
        p.drawText(QRectF(20, 12, w - 200, 24), Qt.AlignLeft | Qt.AlignVCenter, self.caption)
        if self.rows_total:
            p.setFont(art.font(9))
            p.drawText(QRectF(w - 220, 12, 200, 24), Qt.AlignRight | Qt.AlignVCenter,
                       f"{self.rows_done}/{self.rows_total} done"
                       + (f" · {self.rows_skipped} skipped" if self.rows_skipped else "")
                       + (f" · {self.rows_failed} failed" if self.rows_failed else ""))
        art.draw_engine(p, QRectF(18, 50, 36, 36), self.level, t)
        # the conveyor belt under the blocks
        n = len(self.blocks())
        belt_y = 44 + BLOCK_H + 10
        p.setPen(art.pen(art.INK, 2))
        p.drawLine(QPointF(64, belt_y), QPointF(70 + n * (BLOCK_W + GAP), belt_y))
        for i in range(int((n * (BLOCK_W + GAP)) / 18)):
            x = 70 + ((i * 18 + t * 40) % (n * (BLOCK_W + GAP)))
            p.drawLine(QPointF(x, belt_y), QPointF(x - 5, belt_y + 5))
        for i, b in enumerate(self.blocks()):
            art.draw_block(p, self.block_rect(i), b["type"], b.get("title", b["type"]),
                           self.block_state.get(b["id"], "idle"), t)
        # a card riding into the machine / out of it
        if self.card_in and time.monotonic() - self.card_in[0] < 0.5:
            k = (time.monotonic() - self.card_in[0]) / 0.5
            art.draw_card(p, QRectF(10 + 50 * k, 100, 60, 34), ["row " + self.card_in[1]], tilt=-5, opacity=1 - k * 0.5)
        if self.card_out and time.monotonic() - self.card_out[0] < 0.6:
            k = (time.monotonic() - self.card_out[0]) / 0.6
            x = 70 + n * (BLOCK_W + GAP) + 10 + 40 * k
            art.draw_card(p, QRectF(x, 98 + (30 * k if not self.card_out[1] else 0), 60, 34), ["ok" if self.card_out[1] else "skip"],
                          tilt=8, accent=art.TEAL if self.card_out[1] else art.CORAL, opacity=1 - k * 0.4)
        # stamps: one pair per verified row
        sx = 70 + n * (BLOCK_W + GAP) + 50
        for j, case in enumerate(self.stamp_cases[:3]):
            marks = [c["match"] for c in case.get("fields", []) + case.get("record", [])]
            art.draw_stamp(p, QPointF(sx + 24 + j * 6, 62 + j * 26), 21, marks, "✓" if all(marks) and marks else "",
                           offset=9, t=time.monotonic() - self.stamp_at)
        if self.code_for:
            self.draw_code(p, w)
        p.end()

    def draw_code(self, p: QPainter, w: int) -> None:
        box = QRectF(6, 150, w - 12, 182)
        block = next((b for b in self.blocks() if b["id"] == self.code_for), {})
        p.setPen(art.pen(art.INK, 2))
        p.setBrush(QColor("#24303d"))
        p.drawRoundedRect(box, 12, 12)
        p.setPen(QColor("#8fd3c7"))
        p.setFont(art.font(8.5, bold=True))
        p.drawText(box.adjusted(14, 8, -14, 0), Qt.AlignLeft | Qt.AlignTop,
                   f'{block.get("id")} · {block.get("type")} — {block.get("contract", "")}')
        mono = QFont("Consolas")
        mono.setPointSizeF(8.5)
        p.setFont(mono)
        p.setPen(QColor("#fff6e5"))
        p.drawText(box.adjusted(14, 30, -14, -8), Qt.AlignLeft | Qt.AlignTop,
                   self.code.get(self.code_for, "(still being built)"))

    def mousePressEvent(self, event) -> None:
        for i, b in enumerate(self.blocks()):
            if self.block_rect(i).contains(event.position()):
                self.code_for = None if self.code_for == b["id"] else b["id"]
                self.place()
                self.update()
                return
