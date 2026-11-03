"""The day's text archive: every detail in text form, kept `archive.keep_hours` (default 24 h).

Screenshots and audio are deleted after 10 minutes, but their text is not lost: before that,
everything the memory has as text is copied here, one short record per line:
    {"ts", "kind": "screen|heard|field|window|seen", "where": "process: window", "text"}
- screen: lines a window showed (only the lines that are new for that window),
- heard: the audio transcript, field: values text fields settled on,
- window: which window came to the front, seen: a local model's description of a screenshot
  that had little readable text (images, diagrams, video).
No model is needed except for "seen", so nothing important is left out because a model judged
it unimportant. It never holds passwords or excluded apps (they are never captured), stays
on this PC, and only the lines a question needs are sent to the cloud (see `search`).
Stored in buffer/day/YYYY-MM-DD.jsonl (git-ignored)."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..config import Config

log = logging.getLogger(__name__)

STOP = set("""the and for that this with from what was were are you your have has had did does into about
then than they them their there here when where which who how why can could would should will just
el la los las un una unos unas que de del en por para con como pero sus una este esta esto eso ese
esa hay fue era son estan dijo cual cuando donde quien porque mas muy ya lo le les se su al y o a
""".split())


def fold(text: str) -> str:
    """Lowercase without accents, so "Ñandú" matches "nandu" and "explicó" matches "explico"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def terms(text: str) -> list[str]:
    """Search terms: words of 3+ letters (cut to 6, a cheap stem) and every number as written."""
    out = []
    for w in re.findall(r"[\w.,/-]+", fold(text)):
        w = w.strip(".,/-")
        if not w:
            continue
        if any(ch.isdigit() for ch in w):
            out.append(w)
            out.append(re.sub(r"[.,]", "", w))      # 1,250.50 also as 125050
        elif len(w) >= 3 and w not in STOP:
            out.append(w[:6])
    return out


class DayArchive:
    def __init__(self, cfg: Config):
        self.dir = cfg.buffer_dir / "day"
        self.keep_s = float(cfg.archive["keep_hours"]) * 3600
        self._lock = threading.Lock()

    def _file(self, ts: float) -> Path:
        return self.dir / f"{datetime.fromtimestamp(ts):%Y-%m-%d}.jsonl"

    def add(self, records: list[dict]) -> None:
        if not records:
            return
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            by_file: dict[Path, list[dict]] = {}
            for r in records:
                by_file.setdefault(self._file(r["ts"]), []).append(r)
            for path, rs in by_file.items():
                with open(path, "a", encoding="utf-8") as f:
                    f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rs)

    def read(self, since: float = 0.0, until: float | None = None) -> list[dict]:
        until = until or time.time() + 60
        since = max(since, time.time() - self.keep_s)
        out = []
        with self._lock:
            for path in sorted(self.dir.glob("*.jsonl")) if self.dir.exists() else []:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if since <= r["ts"] <= until:
                            out.append(r)
        return sorted(out, key=lambda r: r["ts"])

    def prune(self) -> None:
        """Delete whole days older than keep_hours."""
        cutoff = datetime.fromtimestamp(time.time() - self.keep_s).strftime("%Y-%m-%d")
        with self._lock:
            for path in self.dir.glob("*.jsonl") if self.dir.exists() else []:
                if path.stem < cutoff:
                    path.unlink(missing_ok=True)

    def search(self, question: str, since: float = 0.0, until: float | None = None,
               max_chars: int = 4000, neighbours: int = 1) -> list[dict]:
        """The records that best match the question (term overlap weighted by rarity), each with
        its neighbours for context, oldest first, within max_chars."""
        records = self.read(since, until)
        if not records:
            return []
        q = set(terms(question))
        if not q:
            return []
        docs = [Counter(terms(r["text"] + " " + r.get("where", ""))) for r in records]
        df = Counter(t for d in docs for t in set(d) if t in q)
        n = len(docs)
        scored = []
        for i, d in enumerate(docs):
            s = sum(math.log(1 + n / df[t]) for t in q if d.get(t))
            if s > 0:
                scored.append((s, i))
        picked: set[int] = set()
        size = 0
        for s, i in sorted(scored, reverse=True):
            for j in range(max(0, i - neighbours), min(n, i + neighbours + 1)):
                if j in picked or abs(records[j]["ts"] - records[i]["ts"]) > 90:
                    continue                                # context only from around the same time
                cost = len(records[j]["text"]) + 40
                if size + cost > max_chars:
                    break
                picked.add(j)
                size += cost
            if size >= max_chars:
                break
        return [records[j] for j in sorted(picked)]


class Archiver:
    """Copies the memory's text into the day archive before the 10-minute buffer forgets it."""

    def __init__(self, archive: DayArchive):
        self.archive = archive
        self._seen_lines: dict[str, set[str]] = {}
        self.last_ts = 0.0

    def resume(self) -> None:
        """After a restart, go on from the newest timeline event already archived."""
        recent = self.archive.read(time.time() - 3600)
        self.last_ts = max((r.get("at", r["ts"]) for r in recent), default=0.0)

    def take(self, events: list[dict]) -> list[dict]:
        """Records for the timeline events newer than the last call (and store them)."""
        out = []
        for e in events:
            if e["ts"] <= self.last_ts:
                continue
            where = f'{e.get("process") or "?"}: {e.get("window") or ""}'.strip()
            if e["type"] == "text":
                lines = [ln.strip() for ln in e["text"].splitlines() if ln.strip()]
                seen = self._seen_lines.setdefault(where, set())
                new = [ln for ln in lines if ln not in seen]
                seen.update(new)
                if new:
                    out.append({"at": e["ts"], "ts": e["ts"], "kind": "screen", "where": where, "text": "\n".join(new)})
            elif e["type"] == "transcript":
                out.append({"at": e["ts"], "ts": e["start"], "kind": "heard", "where": "audio", "text": e["text"].strip()})
            elif e["type"] == "field":
                name = (e.get("element") or {}).get("name", "")
                out.append({"at": e["ts"], "ts": e["ts"], "kind": "field", "where": where, "text": f'{name} = {e["value"]}'})
            elif e["type"] == "window":
                out.append({"at": e["ts"], "ts": e["ts"], "kind": "window", "where": where, "text": e.get("window") or ""})
        if events:
            self.last_ts = max(self.last_ts, max(e["ts"] for e in events))
        self.archive.add(out)
        return out
