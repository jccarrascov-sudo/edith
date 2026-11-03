"""Global shortcuts (Windows RegisterHotKey), customizable from the panel.

A combination is a string such as "Ctrl+Alt+E": one or more modifiers (Ctrl, Alt, Shift,
Win) plus exactly one key (a letter, a digit, F1-F24 or Space). Registration happens on a
thread of its own with a Win32 message loop, so a shortcut works whatever app has the focus.
If another app already owns a combination, `set` returns False and the old one stays."""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Qt, Signal

DEFAULT_OPEN = "Ctrl+Alt+E"

MODS = {"Ctrl": 0x0002, "Alt": 0x0001, "Shift": 0x0004, "Win": 0x0008}   # MOD_* flags
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_APP = 0x0312, 0x8000
_QT_MODS = ((Qt.ControlModifier, "Ctrl"), (Qt.AltModifier, "Alt"), (Qt.ShiftModifier, "Shift"),
            (Qt.MetaModifier, "Win"))
_QT_MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr,
                     Qt.Key_Super_L, Qt.Key_Super_R}


def key_vk(key: str) -> int | None:
    """Virtual-key code of the key part of a combination, or None if it is not supported."""
    k = key.strip()
    if len(k) == 1 and (k.isascii() and k.isalnum()):
        return ord(k.upper())
    if k.upper().startswith("F") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        return 0x70 + int(k[1:]) - 1
    if k.lower() == "space":
        return 0x20
    return None


def parse(combo: str) -> tuple[int, int] | None:
    """(modifier flags, virtual key) of a combination, or None if it is not modifier(s) + one key."""
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        flag = next((v for name, v in MODS.items() if name.lower() == p.lower()), None)
        if flag is None:
            return None
        mods |= flag
    vk = key_vk(parts[-1])
    return (mods, vk) if vk is not None else None


def normalize(combo: str) -> str | None:
    """The canonical spelling ("ctrl+alt+e" -> "Ctrl+Alt+E"), or None if it is not valid."""
    parsed = parse(combo)
    if not parsed:
        return None
    mods, vk = parsed
    names = [name for name, flag in MODS.items() if mods & flag]
    if 0x70 <= vk <= 0x87:
        key = f"F{vk - 0x70 + 1}"
    else:
        key = "Space" if vk == 0x20 else chr(vk)
    return "+".join(names + [key])


def from_key_event(key: int, modifiers) -> str | None:
    """A combination from a Qt key press. None while only modifiers are held (keep waiting);
    "" if the key is not supported or no modifier is held."""
    if key in _QT_MODIFIER_KEYS:
        return None
    names = [name for flag, name in _QT_MODS if modifiers & flag]
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        k = chr(key)
    elif Qt.Key_F1 <= key <= Qt.Key_F24:
        k = f"F{key - Qt.Key_F1 + 1}"
    elif key == Qt.Key_Space:
        k = "Space"
    else:
        return ""
    return normalize("+".join(names + [k])) if names else ""


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class GlobalHotkeys(QObject):
    """Named global shortcuts. `pressed(name)` fires on the UI thread."""

    pressed = Signal(str)

    def __init__(self):
        super().__init__()
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self._user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._requests: queue.Queue = queue.Queue()
        self.current: dict[str, str] = {}       # name -> combination
        self._ids: dict[str, int] = {}          # name -> id of its registration
        self._next_id = 1
        self._tid = 0
        ready = threading.Event()
        threading.Thread(target=self._loop, args=(ready,), daemon=True, name="hotkeys").start()
        ready.wait(5)

    def set(self, name: str, combo: str) -> bool:
        """Register `combo` for `name`, replacing its previous one. False (and the previous one
        kept) if the combination is invalid or another app already uses it."""
        combo = normalize(combo) or ""
        if not combo:
            return False
        if self.current.get(name) == combo:
            return True
        return self._call(("set", name, combo))

    def clear(self, name: str) -> None:
        self._call(("clear", name, ""))

    def close(self) -> None:
        if self._tid:
            self._user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT

    def _call(self, request: tuple[str, str, str]) -> bool:
        done = threading.Event()
        box: list[bool] = []
        self._requests.put((request, box, done))
        self._user32.PostThreadMessageW(self._tid, WM_APP, 0, 0)
        done.wait(5)
        return bool(box and box[0])

    # --- the hotkey thread ----------------------------------------------------------------------
    def _loop(self, ready: threading.Event) -> None:
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = _MSG()
        # Make sure the thread has a message queue before anyone posts to it.
        self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        ready.set()
        by_id: dict[int, str] = {}
        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in by_id:
                self.pressed.emit(by_id[msg.wParam])
            elif msg.message == WM_APP:
                while not self._requests.empty():
                    (op, name, combo), box, done = self._requests.get()
                    box.append(self._apply(op, name, combo, by_id))
                    done.set()
        for hid in list(by_id):
            self._user32.UnregisterHotKey(None, hid)

    def _apply(self, op: str, name: str, combo: str, by_id: dict[int, str]) -> bool:
        old = self._ids.get(name)
        if op == "clear":
            if old is not None:
                self._user32.UnregisterHotKey(None, old)
                by_id.pop(old, None)
                self._ids.pop(name, None)
                self.current.pop(name, None)
            return True
        mods, vk = parse(combo)
        hid = self._next_id
        self._next_id += 1
        if not self._user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            return False                      # taken: the old registration stays
        if old is not None:
            self._user32.UnregisterHotKey(None, old)
            by_id.pop(old, None)
        by_id[hid] = name
        self._ids[name] = hid
        self.current[name] = combo
        return True
