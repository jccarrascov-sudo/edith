"""What a level-4 or level-5 program may do to an app: the `ui` object it receives.

The program itself can import nothing but pure modules; this object is its only way to the
app, and it only reaches the windows of one process on Edith's hidden desktop.
- Level 4, FormUI: controls by their accessible name, through UI Automation patterns
  (ValuePattern to write and read, InvokePattern to press). No mouse, no keyboard, no focus.
- Level 5, VisionUI: points found on a screenshot by SAM 3.1 when the recipe was compiled;
  acting through window messages at that point (a click, characters). No mouse, no focus.
Also `python -m edith.recipes.uihelp capture <pid> <out.png>`: the app's client area as an
image (run on the hidden desktop, where the app is).
"""

from __future__ import annotations

import sys
import time


class Submitted(BaseException):
    """A dry run reached the submit button: the program is stopped there, nothing is saved.
    (BaseException: a program's own `except Exception` cannot swallow it and go on.)"""


class FormUI:
    def __init__(self, pid: int, timeout_s: float = 15, dry: dict | None = None):
        import uiautomation as auto

        self._dry = dry                    # {"submit": name, "out": path}: record instead of pressing

        self._auto = auto
        self._win = auto.WindowControl(searchDepth=1, ProcessId=pid)
        if not self._win.Exists(timeout_s, 0.2):
            raise RuntimeError("the app's window did not appear")
        self._cache: dict[tuple[str, str], object] = {}
        self._index()

    def _index(self) -> None:
        """One walk of the window's controls (each search by name would walk it again)."""
        for c, _depth in self._auto.WalkControl(self._win, maxDepth=4):
            if c.Name and c.ControlTypeName in ("EditControl", "ButtonControl", "ComboBoxControl", "TextControl"):
                self._cache.setdefault((c.ControlTypeName, c.Name), c)

    def _find(self, name: str, kinds: tuple[str, ...]):
        for kind in kinds:
            if (kind, name) in self._cache:
                return self._cache[(kind, name)]
        self._index()                                   # the window may have changed
        for kind in kinds:
            if (kind, name) in self._cache:
                return self._cache[(kind, name)]
        for kind in kinds:
            c = getattr(self._win, kind)(Name=name, searchDepth=4)
            if c.Exists(3, 0.1):
                return c
        raise LookupError(f'no control named "{name}"; there are: {", ".join(self.names())}')

    def names(self) -> list[str]:
        out = []
        for c, _depth in self._auto.WalkControl(self._win, maxDepth=4):
            if c.ControlTypeName in ("EditControl", "ButtonControl", "ComboBoxControl", "CheckBoxControl") and c.Name:
                out.append(c.Name)
        return out

    def set(self, name: str, value) -> None:
        """Write a field (its whole value) and check it reads back."""
        c = self._find(name, ("EditControl", "ComboBoxControl"))
        text = "" if value is None else str(value)
        c.GetValuePattern().SetValue(text)
        if (c.GetValuePattern().Value or "") != text:
            raise RuntimeError(f'"{name}" did not take the value')

    def read(self, name: str) -> str:
        return self._find(name, ("EditControl", "ComboBoxControl", "TextControl")).GetValuePattern().Value or ""

    def invoke(self, name: str, wait_s: float = 0.5) -> None:
        """Press a button (in a dry run, the submit button is not pressed: the fields are read)."""
        if self._dry and name == self._dry["submit"]:
            values = {n: (c.GetValuePattern().Value or "") for (k, n), c in self._cache.items()
                      if k in ("EditControl", "ComboBoxControl")}
            _record(self._dry["out"], name, values)
            raise Submitted()
        self._find(name, ("ButtonControl",)).GetInvokePattern().Invoke()
        time.sleep(wait_s)


class VisionUI:
    """Points are client coordinates of the app's main window (from the compile screenshot)."""

    def __init__(self, pid: int, timeout_s: float = 15, dry: dict | None = None, points: dict | None = None):
        import win32gui
        import win32process

        self._dry = dry
        self._points = points or {}        # name -> [x, y], from the compile screenshot

        end = time.monotonic() + timeout_s
        self._hwnd = 0
        while not self._hwnd and time.monotonic() < end:
            found = []

            def visit(h, _):
                if win32gui.IsWindowVisible(h) and win32process.GetWindowThreadProcessId(h)[1] == pid \
                        and win32gui.GetWindowText(h):
                    l, t, r, b = win32gui.GetWindowRect(h)
                    found.append(((r - l) * (b - t), h))
                return True
            win32gui.EnumWindows(visit, None)
            self._hwnd = max(found)[1] if found else 0     # the main window, not a helper one
            if not self._hwnd:
                time.sleep(0.2)
        if not self._hwnd:
            raise RuntimeError("the app's window did not appear")

    def _child(self, x: int, y: int) -> tuple[int, int, int]:
        import win32gui

        child = win32gui.ChildWindowFromPointEx(self._hwnd, (int(x), int(y)), 0x0001 | 0x0002)  # skip hidden/disabled
        child = child or self._hwnd
        sx, sy = win32gui.ClientToScreen(self._hwnd, (int(x), int(y)))
        cx, cy = win32gui.ScreenToClient(child, (sx, sy))
        return child, cx, cy

    def click(self, x: int, y: int, wait_s: float = 0.5) -> None:
        import win32api
        import win32con
        import win32gui

        child, cx, cy = self._child(x, y)
        if self._dry and _text(child) == self._dry["submit"]:
            # Each value is recorded under the label the APP shows next to its box when there is one,
            # not under the name vision gave the point: otherwise two swapped points would type
            # crossed and read back crossed, and the dry run would pass.
            values = {}
            for name, (px, py) in self._points.items():
                c, _x, _y = self._child(px, py)
                if c != self._hwnd and win32gui.GetClassName(c).lower().find("edit") >= 0:
                    values[self._label_of(c) or name] = _text(c)
            _record(self._dry["out"], self._dry["submit"], values)
            raise Submitted()
        lp = (cy << 16) | (cx & 0xFFFF)
        win32api.SendMessage(child, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp)
        win32api.SendMessage(child, win32con.WM_LBUTTONUP, 0, lp)
        time.sleep(wait_s)

    def _label_of(self, box: int) -> str:
        """The text of the nearest captioned control left of `box` on the same row, "" if none."""
        import win32gui

        l, t, r, b = win32gui.GetWindowRect(box)
        mid = (t + b) // 2
        best: list[tuple[int, str]] = []

        def visit(h, _):
            if h == box or not win32gui.IsWindowVisible(h) or "edit" in win32gui.GetClassName(h).lower():
                return True
            hl, ht, hr, hb = win32gui.GetWindowRect(h)
            if hr <= l + 2 and ht <= mid <= hb:
                text = _text(h).strip().rstrip(":").strip()
                if text:
                    best.append((hr, text))
            return True
        try:
            win32gui.EnumChildWindows(self._hwnd, visit, None)
        except Exception:
            return ""
        return max(best)[1] if best else ""

    def type(self, x: int, y: int, text) -> None:
        """Replace the text of the field at (x, y), character by character."""
        import win32api
        import win32con

        child, _cx, _cy = self._child(x, y)
        import ctypes

        send = ctypes.windll.user32.SendMessageW         # the wide version: "Ñandú" arrives intact
        want = "" if text is None else str(text)
        for _attempt in range(2):                        # read back; once more if it did not take
            send(child, win32con.WM_SETTEXT, 0, ctypes.c_wchar_p(""))
            for ch in want:
                send(child, win32con.WM_CHAR, ord(ch), 0)
            if _text(child) == want:
                return
        raise RuntimeError(f"typing at ({x}, {y}) left {_text(child)!r} instead of {want!r}")


def _text(hwnd: int) -> str:
    """A control's text, also in another process (GetWindowText only reads captions there)."""
    import win32con
    import win32gui

    n = win32gui.SendMessage(hwnd, win32con.WM_GETTEXTLENGTH, 0, 0)
    buf = win32gui.PyMakeBuffer((n + 1) * 2)
    win32gui.SendMessage(hwnd, win32con.WM_GETTEXT, n + 1, buf)
    return buf.tobytes()[:n * 2].decode("utf-16-le", errors="replace")


def _record(out: str, button: str, values: dict) -> None:
    import json

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"button": button, "values": values}, f, ensure_ascii=False)


def make(level: int, pid: int, dry: dict | None = None, points: dict | None = None):
    return FormUI(pid, dry=dry) if level == 4 else VisionUI(pid, dry=dry, points=points)


def capture(pid: int, out: str) -> None:
    """The client area of the app's main window as PNG (PrintWindow: works on a hidden desktop)."""
    import win32gui
    import win32ui
    from PIL import Image

    ui = VisionUI(pid)
    l, t, r, b = win32gui.GetClientRect(ui._hwnd)
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(ui._hwnd)
    src = win32ui.CreateDCFromHandle(hdc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    import ctypes

    ctypes.windll.user32.PrintWindow(ui._hwnd, mem.GetSafeHdc(), 1)      # PW_CLIENTONLY
    info = bmp.GetInfo()
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
    img.save(out)
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(ui._hwnd, hdc)
    win32gui.DeleteObject(bmp.GetHandle())


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "capture":
    capture(int(sys.argv[2]), sys.argv[3])
