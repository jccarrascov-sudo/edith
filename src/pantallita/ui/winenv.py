"""Where the mascot can stand and when it should hide (plain Win32 through ctypes)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from ..memory.winutil import foreground_window, process_name

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
CALL_PROCESSES = {"zoom.exe", "teams.exe", "ms-teams.exe", "webex.exe", "ciscowebexstart.exe", "skype.exe"}


def window_rect(hwnd) -> tuple[int, int, int, int]:
    """Visible bounds (without the invisible resize border) in physical pixels."""
    r = wintypes.RECT()
    if _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _cloaked(hwnd) -> bool:
    value = wintypes.DWORD()
    _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


def should_hide(screen: tuple[int, int, int, int]) -> bool:
    """True when something fills the screen (video, game, presentation) or a call app is in front."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or class_name(hwnd) in DESKTOP_CLASSES:
        return False
    _title, process = foreground_window()
    if process.lower() in CALL_PROCESSES:
        return True
    left, top, right, bottom = window_rect(hwnd)
    return left <= screen[0] and top <= screen[1] and right >= screen[2] and bottom >= screen[3]


def window_ledges(screen: tuple[int, int, int, int], own: set[int], min_width: int = 220) -> list[tuple[int, int, int, int]]:
    """Top edges of visible, normal windows a tiny inventor could stand on, front to back:
    (hwnd, y, x_left, x_right), with the parts hidden by windows in front removed roughly
    (an edge is dropped if a window in front covers its middle)."""
    found: list[tuple[int, tuple[int, int, int, int]]] = []

    def visit(hwnd, _lparam):
        if hwnd in own or not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        if _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if class_name(hwnd) in DESKTOP_CLASSES or not _user32.GetWindowTextLengthW(hwnd):
            return True
        found.append((hwnd, window_rect(hwnd)))
        return True

    _user32.EnumWindows(_EnumProc(visit), 0)  # front to back (z-order)
    ledges = []
    for i, (hwnd, (l, t, r, b)) in enumerate(found):
        if r - l < min_width or t <= screen[1] + 40 or t >= screen[3] - 60:
            continue  # too small, maximized (no room above) or too low
        mid = (l + r) // 2
        covered = any(fl <= mid <= fr and ft <= t <= fb for _, (fl, ft, fr, fb) in found[:i])
        if not covered:
            ledges.append((int(hwnd), t, l + 20, r - 20))
    return ledges


def window_process(hwnd) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return process_name(pid.value)
