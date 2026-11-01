"""What the user did, without ever touching the keyboard: clicks and text-field values.

ClickWatcher: a low-level MOUSE hook (WH_MOUSE_LL). It only sees button presses and their
    screen position. The UIA element under each click is resolved on a separate thread
    (the hook callback must return fast), then logged as
    {"type": "click", "ts", "x", "y", "button", "window", "process", "element"}.
FieldWatcher: polls the focused UI Automation element. When an edit/combo field's value
    (ValuePattern) settles or focus leaves it, it logs
    {"type": "field", "ts", "value", "window", "process", "element"}.
    Elements with IsPassword are never read. There is no keyboard hook of any kind: the
    value is what the field shows, read the same way a screen reader does.
Nothing is recorded while an excluded window is in front.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes
from typing import Callable

from ..config import Config
from .capture import PrivacyGate
from .timeline import Timeline
from .winutil import foreground_window

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
BUTTONS = {0x0201: "left", 0x0204: "right", 0x0207: "middle"}  # WM_*BUTTONDOWN
LLMHF_INJECTED = 0x01


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_ssize_t
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def element_info(el) -> dict:
    """The parts of a uia.Element worth keeping in the timeline (value only if not a password)."""
    info = {"kind": el.kind, "name": el.name, "rect": list(el.rect)}
    if el.is_password:
        info["password"] = True
    elif el.value:
        info["value"] = el.value
    return info


class ClickWatcher:
    def __init__(self, cfg: Config, timeline: Timeline, stop: threading.Event,
                 gate: PrivacyGate | None = None, on_click: Callable[[float], None] | None = None):
        self.cfg = cfg
        self.timeline = timeline
        self.stop = stop
        self.gate = gate
        self.on_click = on_click
        self.clicks = 0
        self.private_clicks = 0
        self.injected_clicks = 0
        self._queue: queue.Queue[tuple[float, int, int, str]] = queue.Queue()
        self._hook_thread_id = 0
        self._proc = HOOKPROC(self._callback)  # keep a reference: ctypes must not free it

    def _callback(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and wparam in BUTTONS:
            data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            # Clicks injected by software (e.g. Pantallita's own fallback mode) are not the user's.
            if data.flags & LLMHF_INJECTED:
                self.injected_clicks += 1
            else:
                self._queue.put((time.time(), data.pt.x, data.pt.y, BUTTONS[wparam]))
        return _user32.CallNextHookEx(None, code, wparam, lparam)

    def run(self) -> None:
        """Hook thread + resolver thread; returns when `stop` is set."""
        resolver = threading.Thread(target=self._resolve_loop, name="click-resolver", daemon=True)
        resolver.start()
        self._hook_thread_id = _kernel32.GetCurrentThreadId()
        hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
        if not hook:
            log.error("Could not install the mouse hook (error %s); clicks will not be recorded",
                      ctypes.get_last_error())
            resolver.join()
            return
        threading.Thread(target=self._quit_on_stop, name="click-stopper", daemon=True).start()
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            _user32.UnhookWindowsHookEx(hook)
            resolver.join(timeout=5)

    def _quit_on_stop(self) -> None:
        self.stop.wait()
        _user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)

    def _resolve_loop(self) -> None:
        import uiautomation as auto

        from . import uia

        with auto.UIAutomationInitializerInThread():
            while not self.stop.is_set():
                try:
                    ts, x, y, button = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self.record(ts, x, y, button, uia.element_at)
                except Exception:
                    log.exception("Could not record a click")

    def record(self, ts: float, x: int, y: int, button: str, element_at) -> None:
        title, process = foreground_window()
        if self.cfg.is_excluded(title, process):
            self.private_clicks += 1
            if self.gate:
                self.gate.mark()
            return
        if self.on_click:
            self.on_click(ts)
        el = element_at(x, y)
        event = {"type": "click", "ts": ts, "x": x, "y": y, "button": button,
                 "window": title, "process": process}
        if el is not None:
            event["element"] = element_info(el)
        self.timeline.append(event)
        self.clicks += 1


class FieldWatcher:
    """Logs the value of the focused text field once it settles (or when focus leaves it)."""

    FIELD_KINDS = {"edit", "combo"}

    def __init__(self, cfg: Config, timeline: Timeline, stop: threading.Event,
                 poll_s: float = 0.5, settle_s: float = 1.5):
        self.cfg = cfg
        self.timeline = timeline
        self.stop = stop
        self.poll_s = poll_s
        self.settle_s = settle_s
        self.changes = 0
        self._current = None        # (key, element info, window, process)
        self._logged: dict = {}     # key -> last value written to the timeline
        self._pending: tuple[str, float] | None = None  # (value, first seen)

    def run(self) -> None:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread():
            while not self.stop.wait(self.poll_s):
                try:
                    self.tick(self._read_focused(auto), time.time())
                except Exception:
                    log.debug("Field poll failed", exc_info=True)
            self.flush(time.time())

    def _read_focused(self, auto):
        """(key, element info, value, window, process) for the focused field, or None."""
        from . import uia

        title, process = foreground_window()
        if self.cfg.is_excluded(title, process):
            return "excluded"
        control = auto.GetFocusedControl()
        if control is None:
            return None
        kind = uia.INTERACTIVE.get(control.ControlTypeName)
        if kind not in self.FIELD_KINDS:
            return None
        if control.GetPropertyValue(auto.PropertyId.IsPasswordProperty):
            return None
        value = uia._value(control)
        if value is None:
            return None
        rect = uia._rect(control)
        name = (control.Name or "").strip().replace("\n", " ")[:80]
        # Title and position change (page loads, scrolling); the field itself does not.
        key = (process, kind, name, control.AutomationId or "")
        return key, {"kind": kind, "name": name, "automation_id": control.AutomationId or "",
                     "rect": list(rect)}, value, title, process

    def tick(self, focused, now: float) -> None:
        if focused == "excluded":
            # Drop whatever was pending: it may have been typed right before switching.
            self._current, self._pending = None, None
            return
        key = focused[0] if focused else None
        if self._current and self._current[0] != key:
            self.flush(now)
        if not focused:
            self._current = None
            return
        key, info, value, title, process = focused
        self._current = (key, info, title, process)
        # A field's first settled value is logged too (if not empty): browsers expose their
        # UIA tree lazily, so the first time a field is seen it may already be filled in.
        if value == self._logged.get(key, ""):
            self._pending = None
        elif not self._pending or self._pending[0] != value:
            self._pending = (value, now)
        elif now - self._pending[1] >= self.settle_s:
            self.flush(now)

    def flush(self, now: float) -> None:
        if not self._current or not self._pending:
            return
        key, info, title, process = self._current
        value = self._pending[0]
        self._pending = None
        if self._logged.get(key) == value:
            return
        self._logged[key] = value
        self.timeline.append({"type": "field", "ts": now, "value": value, "window": title,
                              "process": process, "element": info})
        self.changes += 1
