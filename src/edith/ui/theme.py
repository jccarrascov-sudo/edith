"""Colors and soft-UI relief. Every color lives in a palette; there is a light and a dark one.

The palette follows Windows' app theme (AppsUseLightTheme) live: a change fades every color
from the old palette to the new one in THEME_FADE_MS. Relief is only two soft shadows, light
up-left and dark down-right (raised), or the same two inside the shape (inset, i.e. pressed)."""

from __future__ import annotations

from dataclasses import dataclass, fields

from PySide6.QtCore import QObject, QRectF, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath

THEME_FADE_MS = 250
ACCENT = "#5B7CFA"   # the only accent color, in both themes


@dataclass(frozen=True)
class Palette:
    bg: str            # background, panel and buttons all share it
    shadow_light: str  # up-left
    shadow_dark: str   # down-right
    text: str
    muted: str         # secondary text, the agent's status line
    idle: str          # the dot when nothing is happening
    accent: str = ACCENT
    shadow_alpha: float = 0.55   # how strong both shadows are


LIGHT = Palette(bg="#E6E9EF", shadow_light="#FFFFFF", shadow_dark="#A3B1C6",
                text="#3B4048", muted="#8A919C", idle="#A9AFB9", shadow_alpha=0.6)
DARK = Palette(bg="#23262B", shadow_light="#2D3036", shadow_dark="#191B1F",
               text="#D4D7DD", muted="#7C828C", idle="#555B64", shadow_alpha=0.9)


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


def raised(p: QPainter, rect: QRectF, radius: float, pal: Palette, depth: float = 5) -> None:
    """A shape lifted off the background: light shadow up-left, dark shadow down-right."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    blur = int(depth * 2.2)
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
