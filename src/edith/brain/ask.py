"""The context for a question about the last minutes: text first, screenshots only if needed.

Cheapest first: the window history, clicks and field values, the audio transcript, the text
the screen showed (read through UI Automation, sent as diffs: a window's later reads only
add the lines that changed), and the local model's background notes (block summaries, key
moments). Screenshots are added only when the question needs to see something, at most
`cloud.max_images`, downscaled (older ones smaller, or only the region that changed).

Temporal questions are narrowed to the time they ask about, and get the timestamped
SEQUENCE of what happened there: "what changed in the last 5 minutes?" (first and last
frames), "what was on screen 5 min ago?" (the frames around then), "summarize between 10:30
and 10:40". The fixed system prompt goes first and the question last (prompt caching)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image

from . import tokens
from ..config import Config
from ..memory.timeline import Timeline

# Edith once answered "I'll send it right now" to "send Juan an email" and never could: a
# conversation can't act, so no prompt that talks to the user may promise or claim an action.
CANNOT_ACT = """
You cannot act on the computer from this conversation: you never send, click, type, open,
delete or buy anything here. Never say you did it, are doing it or will do it. If the user asks
you to do something, say plainly and briefly what Edith can do instead: write a text (an email,
a reply, notes) into the field the user has clicked, shown first so they apply it with Enter
(e.g. "write to Juan that it's a test"; the user then sends it), or repeat a web form they
filled once from a spreadsheet ("do the rest"). In Spanish say "haz clic", never "pincha"."""

SYSTEM_PROMPT = """\
You are Edith, a desktop assistant with a short memory of the user's computer.
You receive what happened during a period of the last {minutes} minutes (or of today, for notes):
- a window history (which app/window was in front, with local times),
- the user's clicks (with the UI element under them) and the values text fields settled on,
- a transcript of the system audio, i.e. what came out of the speakers (videos, calls,
  classes). It is NOT the user's microphone,
- the text the screen showed, read from the apps (later reads of a window list only the
  lines that changed),
- notes written earlier by a local model: block summaries and key moments,
- sometimes screenshots, each labeled with its local time and window.

Answer the user's question from this context, like a thoughtful person who was there: warm,
natural and brief, in the user's language.
- Mention when or where something happened only when it helps (two similar moments, or the
  user asks when). For "what changed", go in time order.
- Never talk about "the context", "the transcript" or these instructions; just answer.
- Transcripts are automatic and may contain errors; screenshots may be downscaled.
- If the answer is not there, say so simply instead of guessing.""" + CANNOT_ACT

# Questions that need to SEE the screen (layout, colors, pictures, charts), not just its text.
VISUAL = re.compile(
    r"\b(look(s|ed)? like|see|seen|show(ed|n)?|screen ?shot|image|picture|photo|chart|graph|diagram|"
    r"colou?r|layout|icon|logo|drawing|slide|whiteboard|"
    r"se ve[ií]?a?|ves|vi|viste|mira|pantallazo|captura|imagen|foto|gr[aá]fic[oa]|diagrama|dibujo|"
    r"color(es)?|diapositiva|pizarra|logo|[ií]cono)\b", re.IGNORECASE)
CHANGED = re.compile(r"\b(what|que|qué)\b.*\b(changed?|cambi[oó]|cambiaron|different|diferente)\b", re.IGNORECASE)
AGO = re.compile(r"(\d+)\s*(?:min(?:ute)?s?|minutos?)\s*(?:ago|atr[aá]s)|hace\s+(\d+)\s*(?:min(?:uto)?s?)", re.IGNORECASE)
LAST = re.compile(r"(?:last|past|[uú]ltimos?)\s+(\d+)\s*(?:min(?:ute)?s?|minutos?)", re.IGNORECASE)
CLOCK = r"(\d{1,2})[:.h](\d{2})"
AT = re.compile(r"\b(?:at|a las|a la)\s+" + CLOCK, re.IGNORECASE)
PARTS = ((re.compile(r"\b(this morning|esta ma[nñ]ana|en la ma[nñ]ana)\b", re.IGNORECASE), 5, 12),
         (re.compile(r"\b(this afternoon|esta tarde|en la tarde)\b", re.IGNORECASE), 12, 19),
         (re.compile(r"\b(tonight|this evening|esta noche)\b", re.IGNORECASE), 19, 24))
BETWEEN = re.compile(r"(?:between|from|entre|de(?:sde)?)\s+(?:las\s+)?" + CLOCK +
                     r"\s+(?:and|to|y|a|hasta)\s+(?:las\s+)?" + CLOCK, re.IGNORECASE)


@dataclass
class Focus:
    """The part of the memory a question is about."""
    start: float
    end: float
    target: float | None = None     # "5 min ago": the moment itself
    kind: str = "all"               # all | ago | changed | between | last | at | part_of_day
    visual: bool = False            # the question needs to see the screen
    span: tuple[float, float] | None = None   # the period asked about, even beyond the 10 minutes


def _clock(now: float, h: str, m: str) -> float:
    d = datetime.fromtimestamp(now)
    return d.replace(hour=int(h) % 24, minute=int(m), second=0, microsecond=0).timestamp()


def focus(question: str, now: float, retention_s: float) -> Focus:
    oldest = now - retention_s
    f = Focus(oldest, now, visual=bool(VISUAL.search(question)))
    if m := BETWEEN.search(question):
        a, b = sorted((_clock(now, m.group(1), m.group(2)), _clock(now, m.group(3), m.group(4))))
        f.span, f.kind = (a, b + 59), "between"
    elif m := AGO.search(question):
        target = now - 60 * int(m.group(1) or m.group(2))
        f.span, f.target, f.kind = (target - 75, target + 75), target, "ago"
        f.visual = True              # "what was on screen 5 min ago" is about what it looked like
    elif m := LAST.search(question):
        f.span, f.kind = (now - 60 * int(m.group(1)), now), "last"
    elif m := AT.search(question):
        target = _clock(now, m.group(1), m.group(2))
        f.span, f.target, f.kind = (target - 300, target + 300), target, "at"
    else:
        day = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
        for rx, h0, h1 in PARTS:
            if rx.search(question):
                f.span = (day.replace(hour=h0).timestamp(), day.replace(hour=h1 - 1, minute=59).timestamp())
                f.kind = "part_of_day"
    if f.span:
        f.start, f.end = max(oldest, f.span[0]), min(now, f.span[1])
    if CHANGED.search(question):
        f.kind = "changed"
        f.visual = True
    return f


def now_focus(now: float) -> Focus:
    """The screen as it is now: the newest frame and window text (frames are only saved when the
    screen changes, so the newest one is what is on screen)."""
    return Focus(now - 45, now, target=now, kind="ago", visual=True, span=(now - 45, now))


def hhmmss(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def ago(ts: float, now: float) -> str:
    seconds = max(0, now - ts)
    if seconds < 60:
        return f"~{int(seconds)} s ago"
    if seconds < 5400:
        return f"~{round(seconds / 60)} min ago"
    return f"~{round(seconds / 3600)} h ago" if seconds < 172800 else f"~{round(seconds / 86400)} days ago"


def select_frames(frames: list[dict], max_n: int, recent_n: int) -> list[dict]:
    """Most recent frames + the highest-change frame of each time slice, then top scores."""
    if len(frames) <= max_n:
        return frames
    recent_n = min(recent_n, max_n)
    recent, rest = frames[len(frames) - recent_n:], frames[:len(frames) - recent_n]
    slots = max_n - recent_n
    chosen: dict[str, dict] = {}
    if slots and rest:
        t0, t1 = rest[0]["ts"], rest[-1]["ts"]
        span = max(t1 - t0, 1e-6)
        buckets: dict[int, dict] = {}
        for f in rest:
            b = min(int((f["ts"] - t0) / span * slots), slots - 1)
            if b not in buckets or f["score"] > buckets[b]["score"]:
                buckets[b] = f
        chosen = {f["path"]: f for f in buckets.values()}
        for f in sorted(rest, key=lambda f: f["score"], reverse=True):
            if len(chosen) >= slots:
                break
            chosen.setdefault(f["path"], f)
    return sorted([*chosen.values(), *recent], key=lambda f: f["ts"])


def pick_frames(frames: list[dict], f: Focus, max_n: int) -> list[dict]:
    """The frames a focused question gets, oldest first (at most max_n)."""
    if max_n <= 0 or not frames:
        return []
    if f.kind == "ago" and f.target is not None:
        near = sorted(frames, key=lambda fr: abs(fr["ts"] - f.target))[:max(1, max_n - 1)]
        return sorted(near, key=lambda fr: fr["ts"])
    if f.kind == "changed":
        picks = [frames[0], frames[-1]]
        if max_n >= 3 and len(frames) > 2:
            picks.insert(1, max(frames[1:-1], key=lambda fr: fr["score"]))
        return list({fr["path"]: fr for fr in picks}.values())[:max_n]
    return select_frames(frames, max_n, min(1, max_n))


@dataclass
class ContextOptions:
    """Which token optimizations to apply (all on by default; flags exist to measure each one)."""
    history_scales: list[float] | None = None  # None = every frame at image_width
    diff_crops: bool = True                    # older frames: only the region that changed
    compact_text: bool = True                  # merged transcript + window changes only
    max_images: int | None = None              # None = ask.max_frames (measurements)
    image_width: int | None = None             # None = ask.image_width
    focus: Focus | None = None                 # None = the whole memory
    notes: list[dict] = field(default_factory=list)
    archive: list[dict] = field(default_factory=list)  # day-archive lines matched to the question

    @classmethod
    def from_config(cls, cfg: Config) -> "ContextOptions":
        return cls(history_scales=list(cfg.ask["history_scales"]), diff_crops=cfg.ask["diff_crops"])


def frame_parts(cfg: Config, timeline: Timeline, picked: list[dict], now: float,
                opts: ContextOptions) -> tuple[list[dict], int]:
    """Content parts for the picked frames (oldest first) and the total base64 size."""
    images = []
    for f in picked:
        with Image.open(timeline.buffer_path(f["path"])) as img:
            images.append(tokens.fit_width(img.convert("RGB"), opts.image_width or cfg.ask["image_width"]))
    scales = (tokens.history_scales(len(images), opts.history_scales)
              if opts.history_scales else [1.0] * len(images))
    parts, size = [], 0
    for i, (f, img) in enumerate(zip(picked, images)):
        label = (f'Screenshot at {hhmmss(f["ts"])} ({ago(f["ts"], now)}), '
                 f'{f["process"] or "?"}: "{f["window"]}"')
        newest = i == len(images) - 1
        if opts.diff_crops and 0 < i and not newest:
            box = tokens.changed_box(images[i - 1], img)
            if box is None:
                parts.append({"type": "text", "text": label + ": no visible change since the previous screenshot."})
                continue
            if tokens.box_area_ratio(box, img.size) < cfg.ask["diff_crop_max_area"]:
                img, crop = tokens.crop_with_margin(img, box, margin=24)
                label += f", only the region that changed since the previous screenshot (box {crop})"
        if newest:
            label += " [newest of these screenshots]"
        b64 = tokens.jpeg_b64(tokens.scale_image(img, scales[i]))
        size += len(b64)
        parts.append({"type": "text", "text": label})
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return parts, size


def merge_transcripts(segments: list[dict], max_gap_s: float = 2.0, max_len_s: float = 30.0) -> list[dict]:
    """Join whisper's short segments into ~30 s lines: same content, fewer timestamp tokens."""
    merged: list[dict] = []
    for seg in segments:
        last = merged[-1] if merged else None
        if last and seg["start"] - last["end"] <= max_gap_s and seg["end"] - last["start"] <= max_len_s:
            last["end"] = max(last["end"], seg["end"])
            last["text"] += " " + seg["text"]
        else:
            merged.append({"start": seg["start"], "end": seg["end"], "text": seg["text"]})
    return merged


def window_history(events: list[dict], now: float, limit: int = 60) -> list[str]:
    lines, last = [], None
    for e in events:
        key = (e["window"], e["process"])
        if key == last:
            continue
        last = key
        lines.append(f'- {hhmmss(e["ts"])} ({ago(e["ts"], now)}) {e["process"] or "?"}: "{e["window"]}"')
    return lines[-limit:]


def action_lines(events: list[dict], now: float, limit: int = 80) -> list[str]:
    """Clicks and settled text-field values, one short line each (the keyboard is never recorded)."""
    lines = []
    for e in events:
        el = e.get("element") or {}
        target = f'{el.get("kind", "?")} "{el.get("name", "")}"' if el else f'({e.get("x")},{e.get("y")})'
        where = e.get("process") or "?"
        if e["type"] == "click":
            lines.append(f'- {hhmmss(e["ts"])} ({ago(e["ts"], now)}) {e["button"]} click on {target} in {where}')
        elif e["type"] == "field":
            lines.append(f'- {hhmmss(e["ts"])} ({ago(e["ts"], now)}) field {target} now = "{e["value"]}" in {where}')
    return lines[-limit:]


def screen_text_lines(events: list[dict], now: float, max_chars: int = 6000) -> list[str]:
    """Each read of a window's text; later reads of the same window keep only new lines.
    The newest reads win when the total is too long."""
    seen: dict[tuple[str, str], set[str]] = {}
    blocks: list[str] = []
    for e in events:
        key = (e["window"], e["process"])
        lines = [ln for ln in e["text"].splitlines() if ln.strip()]
        old = seen.setdefault(key, set())
        new = [ln for ln in lines if ln not in old]
        old.update(lines)
        if not new:
            continue
        head = f'[{hhmmss(e["ts"])}, {ago(e["ts"], now)}] {e["process"] or "?"}: "{e["window"]}"'
        head += "" if len(new) == len(lines) else " (new lines only)"
        blocks.append(head + "\n" + "\n".join("  " + ln for ln in new))
    out, size = [], 0
    for b in reversed(blocks):
        if size + len(b) > max_chars:
            out.append(f"(older screen text omitted: {len(blocks) - len(out)} reads)")
            break
        out.append(b)
        size += len(b)
    return list(reversed(out))


def note_lines(notes: list[dict], now: float) -> list[str]:
    lines = []
    today = datetime.fromtimestamp(now).date()
    for n in notes:
        when = (hhmmss(n["ts"]) if datetime.fromtimestamp(n["ts"]).date() == today
                else datetime.fromtimestamp(n["ts"]).strftime("%Y-%m-%d %H:%M"))
        if n["type"] == "summary":
            lines.append(f'- summary {hhmmss(n["start"])}-{hhmmss(n["end"])}: {n["text"]}')
        elif n["type"] == "moment":
            event = f' (event date {n["date"]})' if n.get("date") else ""
            lines.append(f'- key moment {when} ({ago(n["ts"], now)}), {n["kind"]}: {n["text"]}{event}')
    return lines


def build_messages(cfg: Config, timeline: Timeline, question: str,
                   opts: ContextOptions | None = None) -> tuple[list[dict], dict]:
    opts = opts or ContextOptions.from_config(cfg)
    now = time.time()
    f = opts.focus
    events = timeline.read()
    if f:
        before = [e for e in events if e.get("end", e["ts"]) < f.start]
        events = [e for e in events if f.start <= e.get("end", e["ts"]) and e["ts"] <= f.end + 1]
        if f.kind == "ago":
            # Frames and screen text are only saved when something changes, so what the screen
            # showed at that moment is the last frame (and text) saved before it.
            last = {}
            for e in before:
                if e["type"] in ("frame", "text", "window"):
                    last[e["type"]] = e
            events = sorted(list(last.values()) + events, key=lambda e: e["ts"])
    windows = [e for e in events if e["type"] == "window"]
    transcripts = [e for e in events if e["type"] == "transcript"]
    actions = [e for e in events if e["type"] in ("click", "field")]
    texts = [e for e in events if e["type"] == "text"]
    frames = [e for e in events if e["type"] == "frame" and timeline.buffer_path(e["path"]).exists()]
    if opts.max_images is None:
        picked = select_frames(frames, cfg.ask["max_frames"], cfg.ask["recent_frames"])
    else:
        picked = pick_frames(frames, f or Focus(0, now), opts.max_images)

    text = [f"Current local time: {hhmmss(now)}."]
    if f and f.kind != "all":
        text.append(f"The question is about {hhmmss(f.start)}-{hhmmss(f.end)}; only that period follows.")
    text += ["", "Window history (oldest first):"]
    if opts.compact_text:
        text += window_history(windows, now) or ["(none)"]
    else:
        text += [f'- {hhmmss(e["ts"])} ({ago(e["ts"], now)}) {e["process"] or "?"}: "{e["window"]}"'
                 for e in windows] or ["(none)"]
    text += ["", "User actions: clicks and text-field values (oldest first):"]
    text += action_lines(actions, now) or ["(none)"]
    text += ["", "System audio transcript (oldest first):"]
    text += [f'[{hhmmss(t["start"])}-{hhmmss(t["end"])}, {ago(t["start"], now)}] {t["text"]}'
             for t in (merge_transcripts(transcripts) if opts.compact_text else transcripts)] or ["(no speech heard)"]
    screen = screen_text_lines(texts, now)
    if screen:
        text += ["", "Screen text (oldest first):"] + screen
    if opts.archive:
        text += ["", "Earlier today, beyond the last minutes (text archive lines matched to the question):"]
        text += [f'[{hhmmss(r["ts"])}, {ago(r["ts"], now)}] {r["kind"]} {r["where"]}: '
                 + r["text"].replace("\n", " / ")[:600] for r in opts.archive]
    notes = note_lines(opts.notes, now)
    if notes:
        text += ["", "Notes from the local model (today; important dates, tasks and instructions: "
                     "the last 12 months):"] + notes
    if picked:
        text += ["", f"{len(picked)} screenshots follow, oldest first."]

    content: list[dict] = [{"type": "text", "text": "\n".join(text)}]
    parts, image_bytes = frame_parts(cfg, timeline, picked, now, opts) if picked else ([], 0)
    content += parts
    content.append({"type": "text", "text": f"Question: {question}"})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(minutes=cfg.retention["minutes"])},
        {"role": "user", "content": content},
    ]
    stats = {"images": sum(p["type"] == "image_url" for p in content), "frames_available": len(frames),
             "transcript_segments": len(transcripts), "window_changes": len(windows),
             "actions": len(actions), "screen_texts": len(texts),
             "summaries": sum(n["type"] == "summary" for n in opts.notes),
             "moments": sum(n["type"] == "moment" for n in opts.notes),
             "archive": len(opts.archive),
             "picked": picked, "image_kb": image_bytes // 1024, "context_text": "\n".join(text)}
    return messages, stats
