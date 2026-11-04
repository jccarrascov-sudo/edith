"""Records the demo video's clips, one scene at a time -> demo-clips/clip_N.mp4 (+ README.txt).

    .venv\\Scripts\\python scripts\\record_demo.py [--only 1,2,3] [--lang es|en]

It drives the REAL screen (mouse and keyboard at a person's pace): do not touch the PC while it
runs; move the mouse to a screen corner to abort (pyautogui fail-safe). It only uses the repo's
fictitious test files and a fresh Edith home (no personal memory, no old recipes), and it never
types into a window it did not open: every keystroke is preceded by a check of the window in
front. Before recording: Do Not Disturb on, desktop icons hidden, windows minimized, Windows in
dark mode; afterwards everything is put back as it was.

Scenes (real-life office cases, fictitious data):
  1 (5 s)      the clean desktop with Edith's dot at rest
  2 (30-60 s)  one payment reminder written by hand in Word from row 1 of the invoices sheet
  3 (20-40 s)  Ctrl+Alt+R -> Edith compiles what was just done -> "Aprendí: ..."
  4 (10-15 s)  "haz lo mismo para <another client, another month>" -> preview -> Enter -> done
  5 (20-30 s)  another client -> "corrige: el monto va con S/ en vez de USD" -> "Aprendí: ..." ->
               the next client already comes with S/ (the lesson applies by itself)
  6 (15-20 s)  a class video in full screen; Edith is asked from the shortcut; the result appears
               without leaving the video
  7 (5-10 s)   Edith's Activity view
Learning takes Muse Spark 35-75 s, so scene 3 can run longer than 40 s: cut it in editing.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "demo"))
OUT = ROOT / "demo-clips"
CASE = ROOT / "demo" / "data" / "Cobranzas noviembre"           # the "office folder" of the video (test files only)
PY = ROOT / ".venv" / "Scripts" / "pythonw.exe"
user32 = ctypes.windll.user32


class SceneError(Exception):
    pass


# --- tools ------------------------------------------------------------------------------------------

def ffmpeg(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    pkgs = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
    for p in pkgs.glob(f"Gyan.FFmpeg*/ffmpeg-*/bin/{name}.exe"):
        return str(p)
    raise SystemExit(f"{name} not found: winget install Gyan.FFmpeg")


class Recorder:
    def __init__(self, path: Path):
        self.path = path
        self.p = subprocess.Popen(
            [ffmpeg("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "gdigrab", "-framerate", "30",
             "-offset_x", "0", "-offset_y", "0", "-video_size", "1920x1080", "-draw_mouse", "1", "-i", "desktop",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(path)],
            stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        self.t0 = time.monotonic()
        time.sleep(0.8)                                   # gdigrab is running

    def stop(self, min_s: float) -> float:
        left = min_s - (time.monotonic() - self.t0)
        if left > 0:
            time.sleep(left)
        try:
            self.p.communicate(b"q", timeout=20)
        except subprocess.TimeoutExpired:
            self.p.kill()
        return time.monotonic() - self.t0


# --- windows and input ------------------------------------------------------------------------------

def fg() -> tuple[int, str, int]:
    h = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return h, buf.value, pid.value


def guard(title_has: tuple[str, ...] = (), pid: int | None = None, wait: float = 8.0) -> None:
    """Never type into a window this script did not open: wait for the expected one in front."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        _h, title, p = fg()
        if (pid is not None and p == pid) or (title_has and any(t.lower() in title.lower() for t in title_has)):
            return
        time.sleep(0.2)
    raise SceneError(f"the window in front is {fg()[1]!r}, not {title_has or pid}: no keystrokes sent")


class _KI(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _IN(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KI), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _unicode_char(ch: str) -> None:
    for flags in (0x0004, 0x0004 | 0x0002):           # KEYEVENTF_UNICODE, then key up
        i = _IN(type=1)
        i.ki = _KI(0, ord(ch), flags, 0, 0)
        user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(i))


def type_human(text: str, check=None) -> None:
    """Type like a person: 45-130 ms per key, longer after spaces and punctuation. Any character
    (ñ, á, «») goes as Unicode. `check` runs every few keys so a focus change stops the typing."""
    for i, ch in enumerate(text):
        if check and i % 6 == 0:
            check()
        _unicode_char(ch)
        time.sleep(random.uniform(0.045, 0.13) + (0.12 if ch in " ,.:;" else 0))


def key(*keys: str) -> None:
    import pyautogui

    pyautogui.hotkey(*keys, interval=0.05)


def move_human(x: int, y: int) -> None:
    import pyautogui

    pyautogui.moveTo(x, y, duration=random.uniform(0.5, 0.9), tween=pyautogui.easeInOutQuad)


def windows_of(pid: int) -> list[int]:
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(h):
            out.append(h)
        return True
    user32.EnumWindows(visit, 0)
    return out


class _PE(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def children(pid: int) -> list[int]:
    """The processes started by `pid` (Toolhelp32 snapshot)."""
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    out = []
    try:
        e = _PE(dwSize=ctypes.sizeof(_PE))
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.th32ParentProcessID == pid:
                out.append(e.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def find_window(part: str, wait: float = 20) -> int:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(h, _):
            n = user32.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(h, buf, n + 1)
            if user32.IsWindowVisible(h) and part.lower() in buf.value.lower():
                found.append(h)
            return True
        user32.EnumWindows(visit, 0)
        if found:
            return found[0]
        time.sleep(0.5)
    raise SceneError(f"no window with {part!r} in its title")


def place(h: int, x: int, y: int, w: int, hgt: int) -> None:
    user32.ShowWindow(h, 9)                               # SW_RESTORE
    user32.MoveWindow(h, x, y, w, hgt, True)


def front(h: int) -> None:
    user32.ShowWindow(h, 9)
    key("alt")                                            # lets SetForegroundWindow work from a script
    user32.SetForegroundWindow(h)
    time.sleep(0.6)


# --- Edith ------------------------------------------------------------------------------------------

class Edith:
    """A fresh Edith (its own home: no personal memory, no old recipes) started by this script."""

    def __init__(self, lang: str):
        from dotenv import dotenv_values

        self.home = Path(tempfile.mkdtemp(prefix="edith-video-"))
        (self.home / "state").mkdir()
        (self.home / "state" / "ui.json").write_text(json.dumps(
            {"x": 1812, "y": 900, "onboarded": True, "presentation": False}), encoding="utf-8")
        cfg = (ROOT / "config.toml").read_text(encoding="utf-8").replace('language = "en"', f'language = "{lang}"')
        (self.home / "config.toml").write_text(cfg, encoding="utf-8")
        env = dict(os.environ, EDITH_HOME=str(self.home))
        env.update({k: v for k, v in dotenv_values(ROOT / ".env").items() if v})   # the key stays in memory only
        self.p = subprocess.Popen([str(PY), "-c", "from edith.ui.app import launch; launch()"], env=env,
                                  cwd=str(ROOT))
        # The venv's pythonw.exe is a launcher: Edith's windows belong to the Python it starts.
        self.pid = 0
        end = time.monotonic() + 60
        while time.monotonic() < end and not self.pid:
            self.pid = next((p for p in [self.p.pid, *children(self.p.pid)] if windows_of(p)), 0)
            time.sleep(0.5)
        if not self.pid:
            raise SystemExit("Edith did not start")
        time.sleep(4)

    def text(self) -> str:
        import uiautomation as auto

        parts = []
        for h in windows_of(self.pid):
            try:
                root = auto.ControlFromHandle(h)
                for c, _d in auto.WalkControl(root, includeTop=True, maxDepth=12):
                    if c.Name:
                        parts.append(c.Name)
                    try:
                        v = c.GetPattern(auto.PatternId.ValuePattern)
                        if v and v.Value:
                            parts.append(v.Value)
                    except Exception:
                        pass
            except Exception:
                continue
        return "\n".join(parts)

    def wait_for(self, *needles: str, timeout: float = 120, pattern: str | None = None) -> str:
        import re

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            t = self.text()
            if pattern and re.search(pattern, t):
                return pattern
            for n in needles:
                if n in t:
                    return n
            time.sleep(0.7)
        raise SceneError(f"Edith never showed {needles or pattern}")

    def panel_open(self) -> bool:
        """The panel is out (wider than the dot and its glow) and in front."""
        for h in windows_of(self.pid):
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            if r.right - r.left > 250 and fg()[2] == self.pid:
                return True
        return False

    def open_panel(self) -> None:
        # The shortcut toggles: press it until the panel is out and in front (seen live: one press
        # closed a panel still open from the scene before, and the request went nowhere).
        for _ in range(4):
            if self.panel_open():
                break
            key("ctrl", "alt", "e")
            time.sleep(0.9)
        if not self.panel_open():
            raise SceneError("Edith's panel did not open")
        guard(pid=self.pid)
        time.sleep(0.4)

    def lessons(self) -> int:
        f = self.home / "state" / "lessons.jsonl"
        return len(f.read_text(encoding="utf-8").splitlines()) if f.exists() else 0

    def ask(self, request: str) -> None:
        self.open_panel()
        type_human(request, check=lambda: guard(pid=self.pid, wait=2))
        time.sleep(0.4)
        guard(pid=self.pid)
        key("enter")

    def press(self, k: str) -> None:
        guard(pid=self.pid)
        key(k)

    def close(self) -> None:
        """End this script's Edith and everything it started (its llama-server too)."""
        tree, todo = [], [self.p.pid]
        while todo:
            pid = todo.pop()
            tree.append(pid)
            todo += children(pid)
        k32 = ctypes.windll.kernel32
        for pid in reversed(tree):                        # children first
            h = k32.OpenProcess(0x0001, False, pid)       # PROCESS_TERMINATE
            if h:
                k32.TerminateProcess(h, 0)
                k32.CloseHandle(h)
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            pass


# --- the desktop: put in shape, then back as it was ----------------------------------------------------

NOTIF = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
THEME = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _get(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _set(path: str, name: str, value) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        if value is None:
            try:
                winreg.DeleteValue(k, name)
            except OSError:
                pass
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


def _desktop_view() -> tuple[int, int]:
    defview = 0
    progman = user32.FindWindowW("Progman", None)
    defview = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)
    if not defview:                                       # the wallpaper slideshow moves it to a WorkerW
        w = 0
        while not defview:
            w = user32.FindWindowExW(0, w, "WorkerW", None)
            if not w:
                break
            defview = user32.FindWindowExW(w, 0, "SHELLDLL_DefView", None)
    icons = user32.FindWindowExW(defview, 0, "SysListView32", None) if defview else 0
    return defview, icons


def _icons(show: bool) -> None:
    defview, icons = _desktop_view()
    if defview and bool(user32.IsWindowVisible(icons)) != show:
        user32.SendMessageW(defview, 0x0111, 0x7402, 0)  # WM_COMMAND "show desktop icons" toggle


class Desktop:
    def __enter__(self):
        import win32com.client

        self.toasts = _get(NOTIF, "NOC_GLOBAL_SETTING_TOASTS_ENABLED")
        self.light = _get(THEME, "AppsUseLightTheme")
        _defview, icons = _desktop_view()
        self.icons_shown = bool(icons and user32.IsWindowVisible(icons))
        _set(NOTIF, "NOC_GLOBAL_SETTING_TOASTS_ENABLED", 0)          # Do Not Disturb (no toasts)
        _set(THEME, "AppsUseLightTheme", 0)                          # dark: Edith follows it live
        _icons(False)
        # A plain background while recording (the wallpaper may be personal). fWinIni=2: not saved,
        # so Windows' own setting is untouched and comes back even if this script is stopped.
        buf = ctypes.create_unicode_buffer(520)
        user32.SystemParametersInfoW(0x0073, 520, buf, 0)            # SPI_GETDESKWALLPAPER
        self.wallpaper = buf.value
        user32.SystemParametersInfoW(0x0014, 0, "", 2)               # SPI_SETDESKWALLPAPER: none
        self.shell = win32com.client.Dispatch("Shell.Application")
        self.shell.MinimizeAll()
        time.sleep(1.5)
        return self

    def __exit__(self, *exc):
        user32.SystemParametersInfoW(0x0014, 0, self.wallpaper, 2)
        _set(NOTIF, "NOC_GLOBAL_SETTING_TOASTS_ENABLED", self.toasts)
        _set(THEME, "AppsUseLightTheme", self.light)
        _icons(self.icons_shown)
        try:
            self.shell.UndoMinimizeALL()
        except Exception:
            pass


# --- the case -------------------------------------------------------------------------------------------

def _modern(path: Path) -> None:
    """Save the template in Word's current format (a hidden Word of its own, this file only), so
    Word neither says "Modo de compatibilidad" nor asks to upgrade it when it is saved."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    w = win32com.client.DispatchEx("Word.Application")
    w.Visible = False
    w.DisplayAlerts = 0
    try:
        d = w.Documents.Open(str(path))
        d.Convert()
        d.Save()
        d.Close(0)
    finally:
        w.Quit()


def prepare_case() -> dict:
    import make_letters
    from edith.recipes.docs import sheet_rows

    folder = CASE
    for i in range(2, 9):                                  # a folder still held by Word: use the next one
        if not folder.exists():
            break
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            break
        folder = CASE.with_name(f"{CASE.name} {i}")
    files = make_letters.make(folder)
    _modern(files["template"])
    _h, rows = sheet_rows(files["sheet"])
    by = {str(r["Client"]).strip(): r for r in rows}
    first = rows[0]
    month = str(first["Date"])[:7]
    other = next(r for r in rows[1:] if str(r["Date"])[:7] != month)        # another client, another month
    return {**files, "row": first, "rows": by, "other": str(other["Client"]).strip()}


def class_video() -> Path:
    """A one-minute class slide (Microeconomics), made here: nothing downloaded."""
    path = OUT / "_clase.mp4"
    if path.exists():
        return path
    font = "C\\:/Windows/Fonts/segoeui.ttf"
    bold = "C\\:/Windows/Fonts/segoeuib.ttf"
    # (text, bold, size, color, y): each line goes through a text file, so ":" and "%" stay text
    lines = [("Microeconomía II  ·  Clase 9", True, 58, "0xE8ECF4", 150),
             ("Universidad  ·  Semestre 2026-II", False, 30, "0x9AA6BF", 232),
             ("Elasticidad precio de la demanda", True, 44, "0xE8ECF4", 380),
             ("Ed = (% cambio en Qd) / (% cambio en P)", False, 38, "0xE8ECF4", 490),
             ("|Ed| > 1  elástica          |Ed| < 1  inelástica", False, 38, "0xE8ECF4", 600),
             ("Ejemplo: café en Lima, el precio sube 10 % y la cantidad baja 15 %  ->  Ed = -1.5", False, 34,
              "0xE8ECF4", 710)]
    tmp = Path(tempfile.mkdtemp(prefix="edith-clase-"))
    draws = []
    for i, (text, is_bold, size, color, y) in enumerate(lines):
        f = tmp / f"l{i}.txt"
        f.write_text(text, encoding="utf-8")
        fp = str(f).replace("\\", "/").replace(":", "\\:")
        draws.append(f"drawtext=fontfile='{bold if is_bold else font}':textfile='{fp}':expansion=none:"
                     f"fontcolor={color}:fontsize={size}:x=140:y={y}")
    subprocess.run([ffmpeg("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x1B2338:s=1920x1080:d=120:r=30", "-vf", ",".join(draws), "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


# --- scenes -------------------------------------------------------------------------------------------

def scene_1(ctx):
    ctx["desktop"].shell.MinimizeAll()                     # anything that came back since
    time.sleep(1.5)


def _word_notice(owner_has: str) -> int:
    """A "Microsoft Word" message box owned by this script's document window, else 0."""
    found = []

    def title(h):
        n = user32.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        return b.value

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        if user32.IsWindowVisible(h) and title(h) == "Microsoft Word" and owner_has in title(user32.GetWindow(h, 4)):
            found.append(h)
        return True
    user32.EnumWindows(visit, 0)
    return found[0] if found else 0


def scene_2(ctx):
    """One letter by hand: Excel on the left, the Word template on the right, the five blanks typed."""
    import make_letters
    import pythoncom
    import win32com.client

    os.startfile(str(ctx["sheet"]))
    xl = find_window("invoices", 40)
    place(xl, 0, 0, 960, 1032)
    os.startfile(str(ctx["template"]))
    wd = find_window("Payment reminder", 40)
    place(wd, 960, 0, 960, 1032)
    front(wd)
    time.sleep(1.5)
    pythoncom.CoInitialize()
    doc = win32com.client.GetObject(str(ctx["template"]))   # this test document only
    ctx["doc"] = doc
    r = ctx["row"]
    values = [("CLIENT", str(r["Client"]).strip()), ("DATE", make_letters.when(r["Date"])),
              ("AMOUNT", make_letters.amount(r["Amount"])), ("DESCRIPTION", str(r["Description"]).strip()),
              ("TAX ID", str(r["Tax ID"]).strip())]
    for blank, value in values:
        rng = doc.Content
        # positional: pywin32 ignores keyword arguments to COM methods. MatchCase, so "Tax ID on file" stays.
        if not rng.Find.Execute(blank, True, False, False, False, False, True, 0):
            raise SceneError(f"the blank {blank} is not in the template")
        guard(("Payment reminder",))
        rng.Select()                                       # like a double click on the blank
        time.sleep(random.uniform(0.6, 1.1))
        type_human(value, check=lambda: guard(("Payment reminder",), wait=2))
        time.sleep(random.uniform(0.5, 0.9))
    guard(("Payment reminder",))
    key("f12")                                             # Save As, like a person
    guard(("Guardar como", "Save As"), wait=15)
    time.sleep(0.8)
    result = ctx["template"].parent / f"Payment reminder - {str(r['Client']).strip()}.docx"
    key("ctrl", "a")
    type_human(str(result), check=lambda: guard(("Guardar como", "Save As"), wait=2))
    time.sleep(0.5)
    key("enter")
    end = time.monotonic() + 25
    while not result.exists() and time.monotonic() < end:
        notice = _word_notice("Payment reminder")          # "the document will be upgraded": Aceptar
        if notice:
            front(notice)
            if fg()[0] == notice:
                key("enter")
        time.sleep(0.5)
    if not result.exists():
        raise SceneError("the letter was not saved")
    ctx["result"] = result
    time.sleep(2)


def scene_3(ctx):
    edith = ctx["edith"]
    key("ctrl", "alt", "r")
    edith.wait_for("Aprendí", "Learned", timeout=150)
    time.sleep(3)


def _preview_then(edith, request: str, then: str = "enter") -> None:
    edith.ask(request)
    edith.wait_for("Hecho en", "Done in", timeout=60)
    time.sleep(2.5)                                        # let the viewer read the preview
    edith.press(then)
    time.sleep(2)


def scene_4(ctx):
    _preview_then(ctx["edith"], f"haz lo mismo para {ctx['other']}")
    out = ctx["template"].parent / f"Payment reminder - {ctx['other']}.docx"
    end = time.monotonic() + 15
    while not out.exists() and time.monotonic() < end:
        time.sleep(0.3)
    if not out.exists():
        raise SceneError(f"{out.name} was not written")
    time.sleep(1.5)


def scene_5(ctx):
    edith = ctx["edith"]
    # While a preview waits, Enter applies it (seen live: the "corrige" typed then never went out).
    # So like a person: keep the letter, notice the currency, then correct it.
    _preview_then(edith, "haz lo mismo para Golden Spoon Supplies")
    before = edith.lessons()
    edith.ask("corrige: el monto va con S/ en vez de USD")
    end = time.monotonic() + 90                            # the lesson is in Edith's lessons file
    while edith.lessons() <= before and time.monotonic() < end:
        time.sleep(0.7)
    if edith.lessons() <= before:
        raise SceneError("no lesson was learned")
    edith.wait_for("Hecho en", "Done in", timeout=60)      # the corrected preview
    time.sleep(3)
    edith.press("enter")                                    # keep the corrected letter
    time.sleep(1.5)
    edith.ask("ahora para Panadería Ñandú")
    edith.wait_for("Hecho en", "Done in", timeout=60)
    time.sleep(3.5)                                        # S/ is already there: the lesson applied itself
    edith.press("esc")
    time.sleep(1)


def scene_6(ctx):
    edith = ctx["edith"]
    player = subprocess.Popen([ffmpeg("ffplay"), "-hide_banner", "-loglevel", "error", "-fs", "-an", "-loop", "0",
                               str(class_video())], creationflags=subprocess.CREATE_NO_WINDOW)
    ctx["player"] = player
    try:
        time.sleep(4)
        _preview_then(edith, "y para Café Señorial también")
        time.sleep(3)
    finally:
        player.terminate()


def scene_7(ctx):
    import uiautomation as auto

    edith = ctx["edith"]
    edith.open_panel()
    hs = windows_of(edith.pid)
    rects = []
    for h in hs:
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        rects.append(((r.right - r.left) * (r.bottom - r.top), r))
    panel = max(rects, key=lambda a: a[0])[1]
    move_human((panel.left + panel.right) // 2, (panel.top + panel.bottom) // 2)   # the options appear on hover
    time.sleep(1.2)
    # "···" (options) is a drawn button with no name: the rightmost button on the text field's row.
    field, buttons = None, []
    for h in windows_of(edith.pid):
        for c, _d in auto.WalkControl(auto.ControlFromHandle(h), includeTop=True, maxDepth=12):
            r = c.BoundingRectangle
            if r.width() <= 0:
                continue
            if c.ControlTypeName == "EditControl" and field is None:
                field = r
            elif c.ControlTypeName == "ButtonControl":
                buttons.append(r)
    if field is None:
        raise SceneError("the panel's text field was not found")
    mid = (field.top + field.bottom) // 2
    row = [b for b in buttons if b.top - 12 <= mid <= b.bottom + 12 and b.left >= field.right - 4]
    if not row:
        raise SceneError("the options button was not found")
    dots = max(row, key=lambda b: b.right)
    import pyautogui

    move_human((dots.left + dots.right) // 2, (dots.top + dots.bottom) // 2)
    time.sleep(0.3)
    pyautogui.click()
    time.sleep(1.0)
    button = None
    for h in windows_of(edith.pid):
        for c, _d in auto.WalkControl(auto.ControlFromHandle(h), includeTop=True, maxDepth=12):
            if c.Name in ("Actividad", "Activity"):
                button = c
                break
    if button is None:
        raise SceneError("the Activity button was not found")
    rc = button.BoundingRectangle
    move_human((rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2)
    time.sleep(0.4)
    pyautogui.click()
    time.sleep(6)


SCENES = {1: (scene_1, 5, "clean desktop, Edith at rest"),
          2: (scene_2, 30, "one payment reminder written by hand"),
          3: (scene_3, 20, "Ctrl+Alt+R: Edith learns it"),
          4: (scene_4, 10, "another client, another month: preview, Enter, done"),
          5: (scene_5, 20, "corrige: S/ instead of USD; the lesson applies by itself"),
          6: (scene_6, 15, "asked from a full-screen class video"),
          7: (scene_7, 5, "the Activity view")}
NEEDS = {3: 2, 4: 3, 5: 3, 6: 3}                           # a scene needs the one before to have worked


# --- checks -------------------------------------------------------------------------------------------

def probe(path: Path) -> tuple[float, str]:
    """(duration in s, "" or why it is not usable): exists, has a length, a middle frame not flat."""
    from PIL import Image, ImageStat

    if not path.exists() or path.stat().st_size < 10_000:
        return 0.0, "missing or empty"
    r = subprocess.run([ffmpeg("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    dur = float(json.loads(r.stdout or "{}").get("format", {}).get("duration", 0) or 0)
    frame = path.with_suffix(".mid.png")
    subprocess.run([ffmpeg("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{dur / 2:.2f}", "-i", str(path),
                    "-frames:v", "1", str(frame)])
    if not frame.exists():
        return dur, "no frame could be read"
    spread = max(ImageStat.Stat(Image.open(frame).convert("RGB")).stddev)
    frame.unlink()
    return dur, "" if spread > 4 else "the middle frame is a flat color (black?)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="1,2,3,4,5,6,7")
    ap.add_argument("--lang", default="es")
    ap.add_argument("--prep", default="", help="scenes to run first WITHOUT recording (e.g. 2,3 for the recipe)")
    args = ap.parse_args()
    import pyautogui

    pyautogui.FAILSAFE = True
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT.mkdir(exist_ok=True)
    wanted = [int(x) for x in args.only.split(",")]
    ok, failed, notes = {}, {}, {}
    ctx = prepare_case()
    class_video()
    print("Recording starts in 5 s. Do not touch the mouse or keyboard; a screen corner aborts.")
    time.sleep(5)
    with Desktop() as desk:
        ctx["desktop"] = desk
        ctx["edith"] = Edith(args.lang)
        try:
            for n in [int(x) for x in args.prep.split(",") if x.strip()]:
                SCENES[n][0](ctx)                          # not recorded: only what later scenes need
                ok.setdefault(n, 0.0)
                time.sleep(2)
            for n in wanted:
                fn, min_s, what = SCENES[n]
                if n in NEEDS and NEEDS[n] in wanted and NEEDS[n] not in ok:
                    failed[n] = f"skipped: scene {NEEDS[n]} did not work"
                    continue
                for attempt in range(3):                  # the scene, and up to 2 repeats
                    path = OUT / f"clip_{n}.mp4"
                    rec = Recorder(path)
                    try:
                        fn(ctx)
                        secs = rec.stop(min_s)
                        ok[n] = secs
                        failed.pop(n, None)
                        print(f"clip_{n}: OK {secs:.1f} s ({what})")
                        break
                    except pyautogui.FailSafeException:
                        rec.stop(0)
                        raise
                    except Exception as exc:
                        rec.stop(0)
                        failed[n] = f"attempt {attempt + 1}: {type(exc).__name__}: {exc}"
                        print(f"clip_{n}: FAILED {failed[n]}")
                        if n == 2:                         # start the hand-made letter over from the template
                            _close_case(ctx)
                            ctx.update(prepare_case())
                        if n == 3:
                            break                          # "learn" again only from a fresh scene 2
                        key("esc")
                        time.sleep(2)
                time.sleep(2)                              # a clean cut between scenes
        finally:
            ctx["edith"].close()
            _close_case(ctx)
    report(wanted, ok, failed)
    return 0 if not failed else 1


def _close_case(ctx) -> None:
    """Close only the test document and sheet this script opened (never saving them)."""
    for _ in range(3):                                     # a notice of ours would block Word's COM
        notice = _word_notice("Payment reminder")
        if not notice:
            break
        user32.PostMessageW(notice, 0x0010, 0, 0)         # WM_CLOSE = Cancel
        time.sleep(1)
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        for p in (ctx.get("result"), ctx.get("template")):
            if p:
                try:
                    win32com.client.GetObject(str(p)).Close(0)
                except Exception:
                    pass
        try:
            win32com.client.GetObject(str(ctx["sheet"])).Close(False)
        except Exception:
            pass
    except Exception:
        pass


def report(wanted, ok, failed) -> None:
    lines = ["Edith demo clips (recorded by scripts/record_demo.py, 1920x1080, 30 fps, no audio)", ""]
    for n in wanted:
        path = OUT / f"clip_{n}.mp4"
        dur, why = probe(path) if n in ok else (0.0, "")
        state = "OK" if n in ok and not why else ("CHECK: " + why if n in ok else "FAILED: " + failed.get(n, "?"))
        lines.append(f"clip_{n}.mp4  {dur:5.1f} s  {SCENES[n][2]}  -> {state}")
    lines += ["", "Scene 3 includes Muse Spark compiling the recipe (35-75 s): cut the wait in editing.",
              "The files of the case are fictitious (demo/data/Cobranzas, made by demo/make_letters.py)."]
    (OUT / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
