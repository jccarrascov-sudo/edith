"""The one typeface of the interface: Source Serif 4 (Adobe, SIL Open Font License 1.1),
bundled in ui/fonts/ (with OFL.txt) and loaded from there, never from the internet.

Weights: 400 for text, 600 for titles and large numbers. Sizes in pixels: 14 panel text,
12 secondary text, 28 large numbers (20 for the onboarding titles)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

FAMILY = "Source Serif 4"
TEXT_PX, SMALL_PX, TITLE_PX, BIG_PX = 14, 12, 20, 28
REGULAR, SEMIBOLD = 400, 600
_DIR = Path(__file__).with_name("fonts")
_loaded = False


def load() -> bool:
    """Register the bundled files with Qt (once). True if the family is available."""
    global _loaded
    if not _loaded:
        for f in sorted(_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(f))
        _loaded = True
    return FAMILY in QFontDatabase.families()


def font(px: int = TEXT_PX, weight: int = REGULAR) -> QFont:
    load()
    f = QFont(FAMILY)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    f.setHintingPreference(QFont.PreferNoHinting)
    return f
