"""Background notes by the local model: key moments and block summaries (local text only).

Key moments: a slide change, dates/deadlines/tasks mentioned, explicit instructions ("this is
in the exam"). A cheap filter with no model picks candidate lines from new transcript and
screen text; only then does Muse Glimmer (low priority, after any question) turn them into
one-line moments. A slide change needs no model: the new slide's first line. Without the
local model the filter's own lines are kept. Block summaries: every `summary_minutes`, two or
three sentences about the block, used by later questions instead of the raw memory.

Stored in buffer/notes.jsonl (git-ignored, deleted with the memory, kept `keep_hours`):
    {"type": "moment", "ts", "kind": "slide|deadline|task|exam", "text"}
    {"type": "summary", "ts", "start", "end", "text"}
No pop-ups: the panel shows at most three moments when it is opened."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable

from ..config import Config
from ..memory.timeline import Timeline
from .ask import hhmmss, merge_transcripts, screen_text_lines, window_history
from .local import LocalModel, LocalUnavailable

log = logging.getLogger(__name__)

KINDS = ("slide", "deadline", "task", "exam")
SLIDES = re.compile(r"powerpnt|powerpoint|presentaci[oó]n|presentation|slides|keynote|impress|\.pptx?\b|\.pdf\b",
                    re.IGNORECASE)
_TEST = r"(exam|examen|quiz|test|prueba|parcial|final)"
EXAM = re.compile(r"\b(in|on|en|for|para) (the|el|la|this|este|esta) " + _TEST + r"\b|"
                  r"\b" + _TEST + r"\b.{0,40}\b(will|covers?|includes?|va a|incluye|cubre|entra|viene)\b|"
                  r"\b(important|importante|remember|recuerden|no olviden|don'?t forget)\b", re.IGNORECASE)
DEADLINE = re.compile(r"\b(deadline|due|submit|turn in|hand in|entrega[rn]?|plazo|fecha l[ií]mite|vence|"
                      r"hasta el|before|antes del?)\b", re.IGNORECASE)
TASK = re.compile(r"\b(homework|assignment|task|to-?do|project|tarea|trabajo|pendiente|proyecto|ejercicios?)\b",
                  re.IGNORECASE)
DATE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                  r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|tomorrow|ma[nñ]ana|next week|"
                  r"pr[oó]xima semana|january|february|march|april|may|june|july|august|september|october|"
                  r"november|december|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|"
                  r"noviembre|diciembre)\b", re.IGNORECASE)


def kind_of(line: str, spoken: bool) -> str | None:
    """The moment a line may hold, by the filter alone. Screen text needs more than a date
    (inboxes and calendars are full of dates); what is said in a class with a date counts."""
    if EXAM.search(line):
        return "exam"
    if DEADLINE.search(line) and (DATE.search(line) or spoken):
        return "deadline"
    if TASK.search(line) and (DATE.search(line) or spoken):
        return "task"
    if spoken and DATE.search(line) and len(line) > 25:
        return "deadline"
    return None


class NoteBook:
    def __init__(self, cfg: Config, path: Path | None = None):
        self.path = path or cfg.notes_path
        self.keep_s = float(cfg.notes["keep_hours"]) * 3600
        self._lock = threading.Lock()

    def read(self, since: float = 0.0) -> list[dict]:
        out = []
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            n = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if n.get("ts", 0) >= since:
                            out.append(n)
            except FileNotFoundError:
                pass
        return sorted(out, key=lambda n: n["ts"])

    def add(self, note: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(note, ensure_ascii=False) + "\n")

    def moments(self, limit: int = 3, within_s: float = 3600) -> list[dict]:
        """The newest key moments (newest first) of the last hour."""
        recent = [n for n in self.read(time.time() - within_s) if n["type"] == "moment"]
        return list(reversed(recent))[:limit]

    def today(self) -> list[dict]:
        return self.read(time.time() - self.keep_s)

    def prune(self) -> None:
        keep = [n for n in self.read() if n["ts"] >= time.time() - self.keep_s]
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(n, ensure_ascii=False) + "\n" for n in keep)
            tmp.replace(self.path)

    def is_new(self, kind: str, text: str, ts: float, window_s: float = 600) -> bool:
        key = re.sub(r"\W+", " ", text.lower()).strip()[:40]
        return not any(n["type"] == "moment" and n["kind"] == kind and abs(n["ts"] - ts) < window_s
                       and re.sub(r"\W+", " ", n["text"].lower()).strip()[:40] == key
                       for n in self.read(ts - window_s))


MOMENT_PROMPT = """You find key moments in what a student or worker saw and heard. Lines come with times.
A key moment is: a date or deadline for a delivery, a task that was assigned, or an explicit
instruction that something matters ("this is in the exam", "remember this").
Reply with a JSON array only, one object per real key moment:
[{"t": "HH:MM:SS", "kind": "deadline|task|exam", "text": "at most 12 words, in the language of the line"}]
Reply [] if there is none."""

SUMMARY_PROMPT = """Summarize this block of time in at most 50 words: what the user did, saw and
heard, with the apps and the important facts (names, numbers, dates). No times, no lists.
Use the language of most of the content. Plain text."""


class NotesWorker:
    """Runs in the background while the memory records; see the module docstring."""

    def __init__(self, cfg: Config, timeline: Timeline, local: LocalModel, stop: threading.Event,
                 on_change: Callable[[], None] | None = None, book: NoteBook | None = None):
        self.cfg, self.timeline, self.local, self.stop = cfg, timeline, local, stop
        self.book = book or NoteBook(cfg)
        self.on_change = on_change or (lambda: None)
        self.last_check = time.time()
        self.last_summary = time.time()
        self._read_lines: dict[tuple[str, str], set[str]] = {}

    def run(self) -> None:
        while not self.stop.wait(float(self.cfg.notes["check_s"])):
            try:
                self.tick(time.time())
            except Exception:
                log.exception("Notes tick failed")

    def tick(self, now: float) -> None:
        events = self.timeline.read(since=self.last_check)
        self.last_check = now
        added = self.find_moments(events)
        if now - self.last_summary >= 60 * float(self.cfg.notes["summary_minutes"]):
            added += self.summarize(self.last_summary, now)
            self.last_summary = now
            self.book.prune()
        if added:
            self.on_change()

    # --- key moments ----------------------------------------------------------------------------
    def candidates(self, events: list[dict]) -> tuple[list[dict], list[tuple[float, str, str]]]:
        """(slide moments, candidate lines as (time, line, filter kind))."""
        slides, lines = [], []
        for e in events:
            if e["type"] == "transcript":
                k = kind_of(e["text"], spoken=True)
                if k:
                    lines.append((e["start"], e["text"].strip(), k))
            elif e["type"] == "text":
                key = (e["window"], e["process"])
                old = self._read_lines.get(key)
                current = [ln for ln in e["text"].splitlines() if ln.strip()]
                self._read_lines[key] = set(current)
                new = current if old is None else [ln for ln in current if ln not in old]
                if old is not None and new and SLIDES.search(f'{e["process"]} {e["window"]}'):
                    slides.append({"type": "moment", "ts": e["ts"], "kind": "slide", "text": new[0][:90]})
                for ln in new:
                    k = kind_of(ln, spoken=False)
                    if k:
                        lines.append((e["ts"], ln.strip()[:200], k))
        return slides, lines

    def find_moments(self, events: list[dict]) -> int:
        slides, lines = self.candidates(events)
        moments = list(slides)
        if lines:
            moments += self._classify(lines)
        added = 0
        for m in moments:
            if m["kind"] in KINDS and m["text"] and self.book.is_new(m["kind"], m["text"], m["ts"]):
                self.book.add(m)
                added += 1
        return added

    def _classify(self, lines: list[tuple[float, str, str]]) -> list[dict]:
        fallback = [{"type": "moment", "ts": ts, "kind": k, "text": _short(ln)} for ts, ln, k in lines]
        if not self.local.installed:
            return fallback
        text = "\n".join(f"[{hhmmss(ts)}] {ln}" for ts, ln, _ in lines[-20:])
        try:
            reply = self.local.generate(MOMENT_PROMPT, text, max_tokens=300, background=True)
        except LocalUnavailable:
            return fallback
        by_time = {hhmmss(ts): ts for ts, _, _ in lines}
        out = []
        for item in _json_list(reply):
            if not isinstance(item, dict) or item.get("kind") not in KINDS:
                continue
            ts = by_time.get(str(item.get("t")), lines[-1][0])
            out.append({"type": "moment", "ts": ts, "kind": item["kind"], "text": _short(str(item.get("text", "")))})
        return out

    # --- summaries ------------------------------------------------------------------------------
    def summarize(self, start: float, end: float) -> int:
        if not self.local.installed:
            return 0
        events = [e for e in self.timeline.read(since=start) if e["ts"] <= end]
        if not any(e["type"] in ("text", "transcript", "field") for e in events):
            return 0
        now = time.time()
        parts = ["Windows:"] + window_history([e for e in events if e["type"] == "window"], now, limit=20)
        speech = merge_transcripts([e for e in events if e["type"] == "transcript"])
        if speech:
            parts += ["Heard:"] + [f'[{hhmmss(t["start"])}] {t["text"]}' for t in speech]
        parts += ["Screen text:"] + screen_text_lines([e for e in events if e["type"] == "text"], now, max_chars=3500)
        try:
            text = self.local.generate(SUMMARY_PROMPT, "\n".join(parts)[-6000:], max_tokens=120, background=True)
        except LocalUnavailable:
            return 0
        text = _whole_sentences(text)
        if not text:
            return 0
        self.book.add({"type": "summary", "ts": end, "start": start, "end": end, "text": text})
        return 1


def _whole_sentences(text: str) -> str:
    """The model may stop mid-sentence at its token limit: keep only whole sentences."""
    text = text.strip()
    if not text or text[-1] in ".!?…\"”)":
        return text
    cut = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
    return text[:cut + 1] if cut > 0 else text


def _short(text: str, words: int = 14) -> str:
    w = text.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def _json_list(reply: str) -> list:
    m = re.search(r"\[.*\]", reply, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
