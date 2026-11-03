"""Screen text: what the foreground window says, read through UI Automation (no model, no OCR).

After each significant screen change the capture wakes this watcher, which reads the visible
text of the window in front and logs it when it changed:
    {"type": "text", "ts", "window", "process", "text"}
This text is the cheap, first source for questions and background notes; screenshots are only
used when a question needs to see something. Excluded apps and Edith's own windows are never
read, and neither are password fields."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

from ..config import Config
from .capture import PrivacyGate
from .timeline import Timeline
from .winutil import foreground_hwnd, foreground_window

log = logging.getLogger(__name__)


class TextWatcher:
    def __init__(self, cfg: Config, timeline: Timeline, stop: threading.Event, gate: PrivacyGate,
                 fallback_s: float = 15.0):
        self.cfg, self.timeline, self.stop, self.gate = cfg, timeline, stop, gate
        self.fallback_s = fallback_s
        self.wake = threading.Event()
        self._last: dict[tuple[str, str], str] = {}   # (window, process) -> hash of the last text logged
        self.logged = 0

    def poke(self) -> None:
        """The screen changed: read the window's text soon."""
        self.wake.set()

    def run(self) -> None:
        import uiautomation as auto

        threading.Thread(target=lambda: (self.stop.wait(), self.wake.set()), daemon=True).start()
        with auto.UIAutomationInitializerInThread():
            while not self.stop.is_set():
                self.wake.wait(self.fallback_s)
                self.wake.clear()
                if self.stop.is_set():
                    break
                try:
                    self.tick()
                except Exception:
                    log.debug("Screen text read failed", exc_info=True)

    def tick(self) -> None:
        from . import uia

        start = time.time()
        hwnd = foreground_hwnd()
        title, process = foreground_window()
        if not hwnd or self.cfg.is_excluded(title, process) or _own_window(hwnd):
            return
        text = uia.visible_text(hwnd)
        # The window may have changed (or an excluded one appeared) while reading.
        if (title, process) != foreground_window() or self.gate.blocked(start):
            return
        self.record(start, title, process, text)

    def record(self, ts: float, title: str, process: str, text: str) -> bool:
        if not text.strip():
            return False
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        key = (title, process)
        if self._last.get(key) == digest:
            return False
        self._last[key] = digest
        self.timeline.append({"type": "text", "ts": ts, "window": title, "process": process, "text": text})
        self.logged += 1
        return True


def _own_window(hwnd: int) -> bool:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value == os.getpid()
