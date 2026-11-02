"""The repetition detector: the same form filled 3 times in a few minutes -> "Want me to do it?".

A pattern is (app, site, the set of fields filled). If the user says no, that pattern is
remembered in state/dismissed.json and never offered again."""

from __future__ import annotations

import json
import urllib.parse

from ..config import ROOT
from ..learn import segment as seg_mod

DISMISSED = ROOT / "state" / "dismissed.json"
TIMES = 3


def pattern_key(seg: seg_mod.Segment) -> str:
    u = urllib.parse.urlsplit(seg.url or "")
    return f"{seg.process.lower()}|{u.netloc.lower()}|{'+'.join(sorted(seg.final_values))}"


def dismissed() -> set[str]:
    try:
        return set(json.loads(DISMISSED.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def dismiss(key: str) -> None:
    keys = dismissed() | {key}
    DISMISSED.parent.mkdir(parents=True, exist_ok=True)
    DISMISSED.write_text(json.dumps(sorted(keys), indent=1), encoding="utf-8")


def repeated(events: list[dict], already_asked: set[str] | None = None) -> tuple[str, seg_mod.Segment] | None:
    """(pattern, latest episode) if some form was filled TIMES times and the user has not
    said no to it (nor been asked already in this session)."""
    counts: dict[str, list[seg_mod.Segment]] = {}
    for seg in seg_mod.find_demos(events):
        counts.setdefault(pattern_key(seg), []).append(seg)
    skip = dismissed() | (already_asked or set())
    for key, segs in counts.items():
        if len(segs) >= TIMES and key not in skip:
            return key, max(segs, key=lambda s: s.end)
    return None
