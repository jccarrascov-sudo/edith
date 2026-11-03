"""Colors and soft-UI relief. Every color lives in a palette; there is a light and a dark one.

The palette follows Windows' app theme (AppsUseLightTheme) live: a change fades every color
from the old palette to the new one in THEME_FADE_MS. Relief is only two soft shadows, light
up-left and dark down-right (raised), or the same two inside the shape (inset, i.e. pressed)."""

from __future__ import annotations

from dataclasses import dataclass, fields

from PySide6.QtCore import QObject, QRectF, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath

THEME_FADE_MS = 250


def over(fg: str, bg: str, alpha: float) -> str:
    """`fg` at `alpha` opacity over `bg`, as a solid color (the panel is one flat color)."""
    a, b = QColor(fg), QColor(bg)
    return QColor.fromRgbF(*(a.getRgbF()[i] * alpha + b.getRgbF()[i] * (1 - alpha) for i in range(3))).name()


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two colors (1 to 21; text needs at least 4.5)."""
    def lum(c: str) -> float:
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in QColor(c).getRgbF()[:3]]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@dataclass(frozen=True)
class Palette:
    bg: str            # background, panel and buttons all share it
    shadow_light: str  # up-left
    shadow_dark: str   # down-right
    text: str
    muted: str         # secondary text, the agent's status line
    idle: str          # the dot when nothing is happening
    paused: str        # the dot while the memory is paused: dimmer than idle
    accent: str        # the one accent: the dot while busy, sparklines, outlines
    accent_text: str   # the accent when it is text (it must reach 4.5:1 on bg)
    shadow_alpha: float = 0.55   # how strong both shadows are


# Secondary text is the main text at 60 % opacity; in the light theme 60 % gives 4.24:1, under
# the 4.5:1 minimum, so it is 62 % there (4.51:1). The light accent #5B7CFA is 3.0:1 on the
# background: it stays for the dot, lines and outlines, and accent TEXT uses the same hue,
# darker (#2A55F9, 4.6:1). All pairs are checked by tests/test_ui.py.
LIGHT = Palette(bg="#E6E9EF", shadow_light="#FFFFFF", shadow_dark="#A3B1C6",
                text="#1A1B1E", muted=over("#1A1B1E", "#E6E9EF", 0.62), idle="#A9AFB9", paused="#CDD2DA",
                accent="#5B7CFA", accent_text="#2A55F9", shadow_alpha=0.6)
DARK = Palette(bg="#23262B", shadow_light="#2D3036", shadow_dark="#191B1F",
               text="#F2F3F5", muted=over("#F2F3F5", "#23262B", 0.60), idle="#555B64", paused="#383C42",
               accent="#7AA7FF", accent_text="#7AA7FF", shadow_alpha=0.75)


def blend(a: Palette, b: Palette, t: float) -> Palette:
    """Every color of `a` moved a fraction t towards `b` (t = 0 gives a, 1 gives b)."""
    out = {}
    for f in fields(Palette):
        x, y = getattr(a, f.name), getattr(b, f.name)
        if isinstance(x, float):
            out[f.name] = x + (y - x) * t
        else:
            ca, cb = QColor(x), QColor(y)
            out[f.name] = QColor.fromRgbF(*(ca.getRgbF()[i] + (cb.getRgbF()[i] - ca.getRgbF()[i]) * t
                                            for i in range(3))).name()
    return Palette(**out)


def windows_is_light() -> bool:
    """Windows' app theme (Settings > Personalization > Colors), read from the registry."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return True


class Theme(QObject):
    """The current palette. `changed` fires on every step of a theme fade."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.light = windows_is_light()
        self.palette = LIGHT if self.light else DARK
        self._from = self._to = self.palette
        self._fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=THEME_FADE_MS)
        self._fade.valueChanged.connect(self._step)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda *_: self.refresh())
        # Qt's signal can miss a change made while the app was busy; the registry is the truth.
        self._poll = QTimer(self, interval=2000, timeout=self.refresh)
        self._poll.start()

    def refresh(self) -> None:
        light = windows_is_light()
        if light == self.light:
            return
        self.light = light
        self._from, self._to = self.palette, (LIGHT if light else DARK)
        self._fade.stop()
        self._fade.start()

    def _step(self, t: float) -> None:
        self.palette = blend(self._from, self._to, float(t))
        self.changed.emit()


# --- relief -------------------------------------------------------------------------------------
# A shadow of depth d is offset d and blurred over int(2.2 d) one-pixel steps of 0.9 px, so it
# reaches d + blur px past its shape: windows keep that much transparent margin on every side.

def blur_of(depth: float) -> int:
    return int(depth * 2.2)


def shadow_extent(depth: float) -> float:
    """How far a raised shape's shadows reach past it (offset + blur)."""
    return depth + (blur_of(depth) - 1) * 0.9


def glow_extent(spread: float) -> float:
    return (blur_of(spread) - 1) * 0.9


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _soft(p: QPainter, rect: QRectF, radius: float, color: str, alpha: float, blur: int) -> None:
    """A blurred rounded shadow: `blur` layers growing 1 px each, so it fades out linearly."""
    c = QColor(color)
    c.setAlphaF(min(1.0, alpha / blur * 1.6))
    p.setPen(QColor(0, 0, 0, 0))
    p.setBrush(c)
    for i in range(blur):
        grow = i * 0.9
        p.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow)


def glow(p: QPainter, rect: QRectF, radius: float, pal: Palette, spread: float = 5) -> None:
    """A shape lifted off the background by a centered, symmetric soft shadow (no offset)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    _soft(p, rect, radius, pal.shadow_dark, pal.shadow_alpha * 0.8, blur_of(spread))
    p.setBrush(QColor(pal.bg))
    p.drawRoundedRect(rect, radius, radius)
    p.restore()


def raised(p: QPainter, rect: QRectF, radius: float, pal: Palette, depth: float = 5) -> None:
    """A shape lifted off the background: light shadow up-left, dark shadow down-right, with the
    same offset and blur."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    blur = blur_of(depth)
    _soft(p, rect.translated(-depth, -depth), radius, pal.shadow_light, pal.shadow_alpha, blur)
    _soft(p, rect.translated(depth, depth), radius, pal.shadow_dark, pal.shadow_alpha, blur)
    p.setBrush(QColor(pal.bg))
    p.drawRoundedRect(rect, radius, radius)
    p.restore()


def inset(p: QPainter, rect: QRectF, radius: float, pal: Palette, depth: float = 3) -> None:
    """A shape pressed into the background: the same two shadows, inside its edges."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    shape = _rounded(rect, radius)
    p.setPen(QColor(0, 0, 0, 0))
    p.setBrush(QColor(pal.bg))
    p.drawPath(shape)
    p.setClipPath(shape)
    big = QPainterPath()
    big.addRect(rect.adjusted(-40, -40, 40, 40))
    blur = int(depth * 2.5)
    for color, sign in ((pal.shadow_dark, 1), (pal.shadow_light, -1)):
        c = QColor(color)
        c.setAlphaF(min(1.0, pal.shadow_alpha / blur * 1.4))
        p.setBrush(c)
        for i in range(blur):
            hole = rect.translated(sign * depth, sign * depth).adjusted(i * 0.8, i * 0.8, -i * 0.8, -i * 0.8)
            p.drawPath(big.subtracted(_rounded(hole, max(0.0, radius - i * 0.8))))
    p.restore()
