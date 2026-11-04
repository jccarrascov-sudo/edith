"""A separate Windows desktop where recipes of levels 4 and 5 run, so the user sees nothing.

Windows can hold several desktops in the same window station; only one is shown. Edith creates
"EdithWork" once and starts there both the app a recipe drives and the recipe's own isolated
process (UI Automation only sees the windows of its own desktop). No mouse moves and no focus
is taken on the user's screen. Processes are ended when the run is over.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

NAME = "EdithWork"
_lock = threading.Lock()
_desk = None


def ensure() -> str:
    """The hidden desktop's full name (created on first use; kept while Edith runs)."""
    global _desk
    import win32con
    import win32service

    with _lock:
        if _desk is None:
            try:
                _desk = win32service.OpenDesktop(NAME, 0, False, win32con.GENERIC_ALL)
            except Exception:
                _desk = win32service.CreateDesktop(NAME, 0, win32con.GENERIC_ALL, None)
    return f"WinSta0\\{NAME}"


class Process:
    def __init__(self, handle, pid: int):
        self.handle, self.pid = handle, pid

    def wait(self, timeout_s: float) -> int | None:
        import win32event
        import win32process

        r = win32event.WaitForSingleObject(self.handle, int(timeout_s * 1000))
        if r == win32event.WAIT_TIMEOUT:
            return None
        return win32process.GetExitCodeProcess(self.handle)

    def alive(self) -> bool:
        return self.wait(0) is None

    def kill(self) -> None:
        import win32process

        try:
            win32process.TerminateProcess(self.handle, 1)
        except Exception:
            pass


def start(cmdline: str, cwd: str | None = None, env: dict | None = None) -> Process:
    """Start a process on the hidden desktop (no window ever reaches the user's screen)."""
    import win32con
    import win32process

    si = win32process.STARTUPINFO()
    si.lpDesktop = ensure()
    si.dwFlags = win32con.STARTF_USESHOWWINDOW
    si.wShowWindow = win32con.SW_SHOWNORMAL
    flags = win32con.CREATE_NEW_PROCESS_GROUP | win32process.CREATE_UNICODE_ENVIRONMENT
    hp, ht, pid, _tid = win32process.CreateProcess(None, cmdline, None, None, False, flags, env, cwd, si)
    ht.Close()
    return Process(hp, pid)


def start_app(cmdline: str, wait_title: str = "", timeout_s: float = 20) -> Process:
    """Start an app on the hidden desktop and wait until its window is up."""
    p = start(cmdline)
    if wait_title:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end and not windows_of(p.pid, wait_title):
            time.sleep(0.2)
    return p


def windows_of(pid: int, title_part: str = "") -> list[int]:
    """Top-level windows of `pid` on the hidden desktop."""
    import win32gui
    import win32process
    import win32service

    ensure()
    try:
        desk = win32service.OpenDesktop(NAME, 0, False, 0x0001 | 0x0040)   # READOBJECTS | ENUMERATE
        hwnds = desk.EnumDesktopWindows()
    except Exception:
        log.debug("could not list the hidden desktop's windows", exc_info=True)
        return []
    out = []
    for hwnd in hwnds:
        try:
            if win32process.GetWindowThreadProcessId(hwnd)[1] == pid and                     title_part.lower() in win32gui.GetWindowText(hwnd).lower():
                out.append(hwnd)
        except Exception:
            continue
    return out
