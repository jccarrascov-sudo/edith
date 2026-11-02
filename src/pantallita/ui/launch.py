"""The launch gesture: lasso rows in Excel with the modifier held, then throw them on a window.

1. Hold Ctrl and drag over Excel: the drag is kept from Excel (it would select cells) and
   drawn as a lasso. A Ctrl+click without dragging is handed back to Excel untouched.
2. Releasing turns the lassoed rows into a deck of cards stuck to the cursor.
3. A click on any window throws the deck there; a right click cancels.

Privacy: the only keyboard information ever read is whether Ctrl is down, and only at the
instant the mouse button goes down over Excel (`ctrl_held`). No key is recorded or logged,
and there is no keyboard hook. The row data come from Excel itself (COM), never from pixels.
"""

from __future__ import annotations

import ctypes
import logging
import math
import threading
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..memory.actions import (BUTTONS, HOOKPROC, LLMHF_INJECTED, MSLLHOOKSTRUCT, WH_MOUSE_LL, WM_QUIT,
                              _kernel32, _user32)
from ..memory.winutil import window_at

log = logging.getLogger(__name__)

VK_CONTROL = 0x11
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP = 0x200, 0x201, 0x202, 0x204, 0x205
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
DRAG_PX = 8
SOURCE_PROCESSES = {"excel.exe"}

_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short


def ctrl_held() -> bool:
    """Is the launch modifier down right now? The ONLY keyboard state Pantallita reads."""
    return bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)


def click_at_cursor() -> None:
    """Give a Ctrl+click back to the app (it was held while we checked for a drag)."""
    _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


class LaunchHook(QObject):
    """Mouse-only hook running in its own thread; talks to the UI through Qt signals
    (queued across threads). Coordinates are physical screen pixels."""

    lasso_started = Signal(int, int)
    lasso_moved = Signal(int, int)
    lasso_finished = Signal(list)        # [(x, y), ...]
    deck_moved = Signal(int, int)
    thrown = Signal(int, int)            # deck dropped at (x, y)
    cancelled = Signal()

    IDLE, PRESSING, LASSO, DECK = range(4)

    def __init__(self):
        super().__init__()
        self.mode = self.IDLE
        self.start = (0, 0)
        self.points: list[tuple[int, int]] = []
        self.enabled = True
        self._proc = HOOKPROC(self._callback)
        self._thread_id = 0
        self._thread: threading.Thread | None = None

    # --- lifecycle --------------------------------------------------------------------------
    def start_thread(self) -> None:
        self._thread = threading.Thread(target=self._run, name="launch-hook", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
        if not hook:
            log.error("launch hook failed (%s)", ctypes.get_last_error())
            return
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            _user32.UnhookWindowsHookEx(hook)

    def arm_deck(self) -> None:
        """The lasso became a deck: the next click throws it."""
        self.mode = self.DECK

    def reset(self) -> None:
        self.mode = self.IDLE
        self.points = []

    # --- the hook (must return fast) ----------------------------------------------------------
    def _callback(self, code: int, wparam: int, lparam: int) -> int:
        if code < 0 or not self.enabled:
            return _user32.CallNextHookEx(None, code, wparam, lparam)
        data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if data.flags & LLMHF_INJECTED:
            return _user32.CallNextHookEx(None, code, wparam, lparam)
        x, y = data.pt.x, data.pt.y
        swallow = False
        try:
            swallow = self._handle(wparam, x, y)
        except Exception:
            log.exception("launch hook")
            self.reset()
        return 1 if swallow else _user32.CallNextHookEx(None, code, wparam, lparam)

    def _handle(self, msg: int, x: int, y: int) -> bool:
        if self.mode == self.IDLE:
            if msg == WM_LBUTTONDOWN and ctrl_held():
                _hwnd, _title, process = window_at(x, y)
                if process.lower() in SOURCE_PROCESSES:
                    self.mode, self.start, self.points = self.PRESSING, (x, y), [(x, y)]
                    return True
            return False
        if self.mode in (self.PRESSING, self.LASSO):
            if msg == WM_MOUSEMOVE:
                if self.mode == self.PRESSING and math.hypot(x - self.start[0], y - self.start[1]) > DRAG_PX:
                    self.mode = self.LASSO
                    self.lasso_started.emit(*self.start)
                if self.mode == self.LASSO:
                    self.points.append((x, y))
                    self.lasso_moved.emit(x, y)
                return False  # never block the cursor itself
            if msg == WM_LBUTTONUP:
                if self.mode == self.PRESSING:  # a plain Ctrl+click: hand it back to Excel
                    self.reset()
                    threading.Timer(0.01, click_at_cursor).start()
                else:
                    points, self.points = self.points, []
                    self.mode = self.IDLE
                    self.lasso_finished.emit(points)
                return True
            return False
        if self.mode == self.DECK:
            if msg == WM_MOUSEMOVE:
                self.deck_moved.emit(x, y)
                return False
            if msg == WM_LBUTTONDOWN:
                return True
            if msg == WM_LBUTTONUP:
                self.mode = self.IDLE
                self.thrown.emit(x, y)
                return True
            if msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
                if msg == WM_RBUTTONUP:
                    self.mode = self.IDLE
                    self.cancelled.emit()
                return True
        return False


# --- Excel: which rows the lasso covers, read through COM -----------------------------------

@dataclass
class Selection:
    workbook: Path
    sheet: str
    headers: list[str]
    first_row: int                         # Excel row numbers (1-based, header is row 1)
    last_row: int
    rows: list[dict] = field(default_factory=list)   # header -> value, from Excel itself
    row_rects: list[tuple[int, int, int, int]] = field(default_factory=list)  # screen px per row

    @property
    def indexes(self) -> list[int]:
        """0-based data-row indexes (as the workshop counts them: row 2 is index 0)."""
        return list(range(self.first_row - 2, self.last_row - 1))


def excel_selection(points: list[tuple[int, int]]) -> Selection | None:
    """The Excel rows under the lasso, with their values and on-screen rectangles."""
    import comtypes.client
    import comtypes.client.dynamic

    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    excel = comtypes.client.GetActiveObject("Excel.Application")
    window = excel.ActiveWindow
    sheet = excel.ActiveSheet
    cx = (min(xs) + max(xs)) // 2

    def row_at(y: int) -> int | None:
        cell = window.RangeFromPoint(cx, y)  # a bare IDispatch: wrap it to read .Row
        if cell is None:
            return None
        try:
            return int(comtypes.client.dynamic.Dispatch(cell).Row)
        except Exception:
            return None

    top, bottom = row_at(min(ys) + 2), row_at(max(ys) - 2)
    if top is None or bottom is None:
        return None
    top = max(top, 2)  # row 1 holds the headers
    if bottom < top:
        return None
    used_cols = int(sheet.UsedRange.Columns.Count)
    to_px = sheet_to_screen(excel)
    def row_values(r: int) -> list:
        # Range.Value is a parameterized COM property: call it; one call per row.
        values = sheet.Range(sheet.Cells(r, 1), sheet.Cells(r, used_cols)).Value()
        return list(values[0]) if used_cols > 1 else [values]

    headers = [str(v).strip() if v not in (None, "") else f"col{i + 1}" for i, v in enumerate(row_values(1))]
    rows, rects = [], []
    for r in range(top, bottom + 1):
        values = row_values(r)
        if all(v is None or v == "" for v in values):
            continue
        rows.append(dict(zip(headers, [_clean(v) for v in values])))
        rng = sheet.Range(sheet.Cells(r, 1), sheet.Cells(r, used_cols))
        rects.append(to_px(rng.Left, rng.Top, rng.Left + rng.Width, rng.Top + rng.Height))
    if not rows:
        return None
    try:  # show the user what was taken
        sheet.Range(f"{top}:{bottom}").Select()
    except Exception:
        pass
    return Selection(Path(str(excel.ActiveWorkbook.FullName)), str(sheet.Name), headers, top, bottom, rows, rects)


def sheet_to_screen(excel):
    """Sheet points -> screen pixels for the active window, scrolled to the top-left visible cell.

    PointsToScreenPixels(0) gives the grid origin on screen, but it does not scale the points
    themselves; measured on this machine: pixel = origin + points * dpi/72 * zoom/100."""
    window = excel.ActiveWindow
    ox, oy = int(window.PointsToScreenPixelsX(0)), int(window.PointsToScreenPixelsY(0))
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(int(excel.Hwnd)) or 96
    except Exception:
        dpi = 96
    scale = dpi / 72 * float(window.Zoom) / 100
    visible = window.VisibleRange
    sx, sy = float(visible.Left), float(visible.Top)  # what is scrolled out of view

    def to_px(left, top, right, bottom) -> tuple[int, int, int, int]:
        return (int(ox + (left - sx) * scale), int(oy + (top - sy) * scale),
                int(ox + (right - sx) * scale), int(oy + (bottom - sy) * scale))
    return to_px


def _clean(value):
    """COM values to plain Python: dates come as datetime (possibly tz-aware), empty as None."""
    import datetime as dt

    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if value == "":
        return None
    return value
