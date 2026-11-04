"""Where an edit goes, and how it is written and undone, in any app (UI Automation + clipboard).

The target is the text field the user was in when they opened Edith (Ctrl+Alt+E, or a click
on the dot, which does not take the focus). Its current text is read through UI Automation
(TextPattern or ValuePattern, like a screen reader), never a password field, never an
excluded app.

Writing pastes the new text the way a user would: the window is brought back, the caret goes
to the end (append) or everything is selected (replace), and Ctrl+V is sent. The user's
clipboard is saved first and restored after. The result is read back to check the text is
really there. Undo is the app's own Ctrl+Z, checked the same way, and refused if the user
changed the text after Edith's edit (it would undo the user's work instead).

Everything here runs on the UI thread (the clipboard belongs to it)."""

from __future__ import annotations

import ctypes
import logging
import os
import time
from ctypes import wintypes
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_u = ctypes.windll.user32
_u.GetForegroundWindow.restype = wintypes.HWND
_u.IsWindow.argtypes = [wintypes.HWND]
_u.SetForegroundWindow.argtypes = [wintypes.HWND]

VK_CONTROL = 0x11
VK_A, VK_V, VK_Y, VK_Z, VK_END = 0x41, 0x56, 0x59, 0x5A, 0x23
TEXT_KINDS = {"EditControl", "DocumentControl"}


@dataclass
class Target:
    hwnd: int
    title: str
    process: str
    kind: str                      # EditControl | DocumentControl
    name: str
    rect: tuple[int, int, int, int]
    text: str                      # its text when Edith was opened
    control: object = field(repr=False, default=None)


@dataclass
class Applied:
    target: Target
    before: str
    after: str
    ts: float


def normalize(text: str) -> str:
    """Line endings and trailing spaces differ between apps (Notepad uses \\r); an empty Chromium
    field reads as U+FFFC (the object replacement character)."""
    text = text.replace("\ufffc", "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def same_text(written: str, field: str) -> bool:
    """Is `written` in the field? Compared without line breaks and spacing: a contenteditable
    (Gmail's body) turns each pasted line into a paragraph, so the field reads blank lines more."""
    return " ".join(normalize(written).split()) in " ".join(normalize(field).split())


def read_text(control) -> str | None:
    import uiautomation as auto

    try:
        if control.GetPropertyValue(auto.PropertyId.IsPasswordProperty):
            return None
        tp = control.GetPattern(auto.PatternId.TextPattern)
        if tp:
            text = tp.DocumentRange.GetText(-1) or ""
        else:
            vp = control.GetPattern(auto.PatternId.ValuePattern)
            if not vp:
                return None
            text = vp.Value or ""
        # An empty contenteditable (Gmail's message body) reads as its own label.
        name = getattr(control, "Name", "")
        name = name.strip() if isinstance(name, str) else ""
        return "" if name and normalize(text) == name else text
    except Exception:
        log.debug("could not read the target's text", exc_info=True)
        return None


def capture(is_excluded) -> Target | None:
    """The text field in front right now (call it before Edith takes the focus)."""
    import uiautomation as auto

    from ..memory.winutil import foreground_window

    hwnd = _u.GetForegroundWindow() or 0
    title, process = foreground_window()
    if not hwnd or is_excluded(title, process):
        return None
    pid = wintypes.DWORD()
    _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return None
    control = None
    for attempt in range(2):
        # Chromium builds its accessibility tree on the first UIA query: the first answer can be
        # a bare pane, the next one the real field.
        try:
            control = auto.GetFocusedControl()
        except Exception:
            control = None
        if control is not None and control.ProcessId == pid.value and control.ControlTypeName in TEXT_KINDS:
            break
        control = None
        if attempt == 0:
            time.sleep(0.25)
    if control is None:
        return None
    text = read_text(control)
    if text is None:
        return None
    r = control.BoundingRectangle
    return Target(hwnd, title, process, control.ControlTypeName, (control.Name or "").strip()[:80],
                  (r.left, r.top, r.right, r.bottom), text, control)


# --- keys and clipboard -------------------------------------------------------------------------

def _keys(*combo: int) -> None:
    """Send a shortcut. The keyboard's state is never read (Edith never reads the keyboard);
    edits run after the user pressed Enter in the panel, so no modifier is held."""
    for vk in combo:
        _u.keybd_event(vk, 0, 0, 0)
    for vk in reversed(combo):
        _u.keybd_event(vk, 0, 2, 0)


def _pump(seconds: float) -> None:
    from PySide6.QtCore import QCoreApplication

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.02)


class _Clipboard:
    """Saves every format of the user's clipboard and puts it back afterwards."""

    def __enter__(self):
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QGuiApplication

        self.cb = QGuiApplication.clipboard()
        self.saved = QMimeData()
        src = self.cb.mimeData()
        for fmt in (src.formats() if src else []):
            self.saved.setData(fmt, src.data(fmt))
        return self

    def put(self, text: str) -> None:
        self.cb.setText(text)

    def __exit__(self, *exc):
        self.cb.setMimeData(self.saved)


class Typist:
    def __init__(self):
        self.last: Applied | None = None

    def _bring_back(self, t: Target) -> bool:
        """The target window in front again, with the focus in its field."""
        if not _u.IsWindow(t.hwnd):
            return False
        _u.SetForegroundWindow(t.hwnd)
        _pump(0.15)
        try:
            t.control.SetFocus()
        except Exception:
            pass
        _pump(0.1)
        return (_u.GetForegroundWindow() or 0) == t.hwnd

    def apply(self, t: Target, mode: str, text: str) -> tuple[bool, str]:
        """Write `text` into the target. Returns (ok, reason when not ok)."""
        before = read_text(t.control)
        if before is None:
            return False, "unreadable"
        if not self._bring_back(t):
            return False, "window_gone"
        paste = text
        if mode == "append" and normalize(before):
            paste = ("\n" if not before.endswith(("\n", "\r")) else "") + text
        with _Clipboard() as cb:
            cb.put(paste)
            _pump(0.05)
            _keys(VK_CONTROL, VK_A if mode == "replace" else VK_END)
            _pump(0.05)
            _keys(VK_CONTROL, VK_V)
            _pump(0.5)                                   # the app reads the clipboard now
            after = read_text(t.control)
        if after is None or not same_text(text, after):
            return False, "not_written"
        self.last = Applied(t, before, after, time.time())
        return True, ""

    def undo(self) -> tuple[bool, str]:
        a = self.last
        if a is None:
            return False, "nothing"
        now = read_text(a.target.control)
        if now is None or not _u.IsWindow(a.target.hwnd):
            return False, "window_gone"
        if normalize(now) != normalize(a.after):
            return False, "changed_since"
        if not self._bring_back(a.target):
            return False, "window_gone"
        pressed = 0
        for _ in range(3):                               # a paste is one undo step in most apps
            _keys(VK_CONTROL, VK_Z)
            pressed += 1
            _pump(0.35)
            now = read_text(a.target.control) or ""
            if normalize(now) == normalize(a.before):
                self.last = None
                return True, ""
            n, b, f = normalize(now), normalize(a.before), normalize(a.after)
            if n == f or (n.startswith(b) and f.startswith(n)):
                continue                                 # not yet, or only part of the paste: again
            break                                        # the app undid something of the user's
        for _ in range(pressed):                         # put back whatever the app undid
            _keys(VK_CONTROL, VK_Y)
            _pump(0.2)
        return False, "not_undone"
