"""Colors and soft-UI relief. Every color lives in a palette; there is a light and a dark one.

The palette follows Windows' app theme (AppsUseLightTheme) live: a change fades every color
from the old palette to the new one in THEME_FADE_MS. Relief is only two soft shadows, light
up-left and dark down-right (raised), or the same two inside the shape (inset, i.e. pressed)."""

from __future__ import annotations

from dataclasses import dataclass, fields
from functools import lru_cache

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

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
    well: str          # the gently pressed text field
    rim: str           # the hairline of light along a raised shape's top edge
    shadow_alpha: float = 0.3      # the dark shadow (down-right)
    highlight_alpha: float = 0.9   # the light shadow (up-left)
    rim_alpha: float = 0.6
    sheen: float = 0.02            # how much lighter a raised shape is at its top


# Secondary text is the main text at 60 % opacity; in the light theme 60 % gives 4.24:1, under
# the 4.5:1 minimum, so it is 62 % there (4.51:1). The light accent #5B7CFA is 3.0:1 on the
# background: it stays for the dot, lines and outlines, and accent TEXT uses the same hue,
# darker (#2A55F9, 4.6:1). All pairs are checked by tests/test_ui.py.
# Shadows are tinted with the background's own hue (a blue-gray, never a neutral gray), so the
# relief reads as light falling on one material rather than dirt around it.
LIGHT = Palette(bg="#E6E9EF", shadow_light="#FFFFFF", shadow_dark="#8D99B2",
                text="#1A1B1E", muted=over("#1A1B1E", "#E6E9EF", 0.62), idle="#A9AFB9", paused="#CDD2DA",
                accent="#5B7CFA", accent_text="#2A55F9", well="#E0E3EA", rim="#FFFFFF",
                shadow_alpha=0.32, highlight_alpha=0.95, rim_alpha=0.75, sheen=0.025)
DARK = Palette(bg="#23262B", shadow_light="#2D3036", shadow_dark="#191B1F",
               text="#F2F3F5", muted=over("#F2F3F5", "#23262B", 0.60), idle="#555B64", paused="#383C42",
               accent="#7AA7FF", accent_text="#7AA7FF", well="#1F2226", rim="#FFFFFF",
               shadow_alpha=0.7, highlight_alpha=0.45, rim_alpha=0.07, sheen=0.035)


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
# Shadows are real Gaussian blurs (rendered once per size and cached), not stacked outlines: a
# raised shape of depth d casts a dark shadow down-right and a light one up-left, both offset
# 0.7 d and blurred with sigma 1.2 d, so they fade smoothly to nothing about 4.3 d past the
# shape. Windows keep `shadow_extent(d)` of transparent margin on every side, so none is cut.

def _sigma(depth: float) -> float:
    return depth * 1.2


def _offset(depth: float) -> float:
    return depth * 0.7


def shadow_extent(depth: float) -> float:
    """How far a raised shape's shadows reach past it (offset + 3 sigma)."""
    return _offset(depth) + 3 * _sigma(depth)


def glow_extent(spread: float) -> float:
    return 3 * _sigma(spread)


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _box(a, r: int, axis: int):
    """One box blur pass of radius r along an axis (running sums)."""
    import numpy as np

    if r < 1:
        return a
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float32)
    hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
    lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
    return (hi - lo) / (2 * r + 1)


def _gauss(a, sigma: float):
    """A Gaussian blur approximated by three box blurs per axis."""
    r = max(0, int(round((((12 * sigma * sigma / 3) + 1) ** 0.5 - 1) / 2)))
    for axis in (0, 1):
        for _ in range(3):
            a = _box(a, r, axis)
    return a


def _mask(w: int, h: int, pad: int, radius: float):
    """A rounded rectangle's coverage (0..1) in a (h + 2 pad) x (w + 2 pad) array."""
    import numpy as np

    img = QImage(w + 2 * pad, h + 2 * pad, QImage.Format_Grayscale8)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(QRectF(pad, pad, w, h), radius, radius)
    p.end()
    raw = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return raw[:, :img.width()].astype(np.float32) / 255.0


def _image(alpha, color: str):
    """A premultiplied ARGB image of one color with the given coverage."""
    import numpy as np

    c = QColor(color)
    a = np.clip(alpha, 0.0, 1.0)
    out = np.empty(a.shape + (4,), np.uint8)
    out[..., 0] = (a * c.blue()).astype(np.uint8)
    out[..., 1] = (a * c.green()).astype(np.uint8)
    out[..., 2] = (a * c.red()).astype(np.uint8)
    out[..., 3] = (a * 255).astype(np.uint8)
    img = QImage(out.data, a.shape[1], a.shape[0], a.shape[1] * 4, QImage.Format_ARGB32_Premultiplied)
    return img.copy()


@lru_cache(maxsize=96)
def _drop(w: int, h: int, radius: float, sigma: float, color: str, alpha: float) -> tuple[QImage, int]:
    pad = int(3 * sigma) + 2
    return _image(_gauss(_mask(w, h, pad, radius), sigma) * alpha, color), pad


@lru_cache(maxsize=48)
def _inner(w: int, h: int, radius: float, sigma: float, dx: float, dy: float, color: str, alpha: float) -> QImage:
    """The shadow cast inside a hole by its edge, as seen through the hole."""
    import numpy as np

    pad = int(3 * sigma) + 4
    m = _mask(w, h, pad, radius)
    shifted = np.roll(np.roll(m, int(round(dy)), axis=0), int(round(dx)), axis=1)
    shade = _gauss(1.0 - shifted, sigma) * m * alpha
    return _image(shade[pad:pad + h, pad:pad + w], color)


def _blit(p: QPainter, rect: QRectF, img: QImage, pad: int, dx: float = 0, dy: float = 0) -> None:
    p.drawImage(QPointF(rect.x() - pad + dx, rect.y() - pad + dy), img)


def _key(c: str) -> str:
    return QColor(c).name()


def glow(p: QPainter, rect: QRectF, radius: float, pal: Palette, spread: float = 5) -> None:
    """A shape lifted off the background by a centered, symmetric soft shadow (no offset)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    img, pad = _drop(int(rect.width()), int(rect.height()), radius, _sigma(spread),
                     _key(pal.shadow_dark), round(pal.shadow_alpha * 0.9, 3))
    _blit(p, rect, img, pad)
    _body(p, rect, radius, pal)
    p.restore()


def raised(p: QPainter, rect: QRectF, radius: float, pal: Palette, depth: float = 5) -> None:
    """A shape lifted off the background: light shadow up-left, dark shadow down-right, with the
    same offset and blur, then the shape with a hairline of light along its top edge."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    w, h, s, o = int(rect.width()), int(rect.height()), _sigma(depth), _offset(depth)
    light, pad = _drop(w, h, radius, s, _key(pal.shadow_light), round(pal.highlight_alpha, 3))
    dark, _ = _drop(w, h, radius, s, _key(pal.shadow_dark), round(pal.shadow_alpha, 3))
    _blit(p, rect, light, pad, -o, -o)
    _blit(p, rect, dark, pad, o, o)
    _body(p, rect, radius, pal)
    p.restore()


def _body(p: QPainter, rect: QRectF, radius: float, pal: Palette) -> None:
    """The shape itself: the background color, a very soft vertical sheen and a hairline rim
    that is light at the top and fades out towards the bottom."""
    sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    top, bottom = QColor(pal.bg).lighter(100 + int(pal.sheen * 100)), QColor(pal.bg)
    sheen.setColorAt(0, top)
    sheen.setColorAt(1, bottom)
    p.setPen(Qt.NoPen)
    p.setBrush(sheen)
    p.drawRoundedRect(rect, radius, radius)
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    hi = QColor(pal.rim)
    hi.setAlphaF(pal.rim_alpha)
    lo = QColor(pal.rim)
    lo.setAlphaF(0.0)
    rim.setColorAt(0, hi)
    rim.setColorAt(0.5, lo)
    p.setPen(QPen(QBrush(rim), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)


def inset(p: QPainter, rect: QRectF, radius: float, pal: Palette, depth: float = 3) -> None:
    """A shape pressed gently into the background: a slightly deeper tone with a soft inner
    shadow from the top-left edge and a faint light one from the bottom-right."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(pal.well))
    p.drawRoundedRect(rect, radius, radius)
    w, h, s, o = int(rect.width()), int(rect.height()), depth * 0.9, depth * 0.6
    p.drawImage(rect.topLeft(), _inner(w, h, radius, s, o, o, _key(pal.shadow_dark), round(pal.shadow_alpha * 0.9, 3)))
    p.drawImage(rect.topLeft(), _inner(w, h, radius, s, -o, -o, _key(pal.shadow_light), round(pal.highlight_alpha * 0.8, 3)))
    p.restore()
