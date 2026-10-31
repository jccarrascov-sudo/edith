"""Build a compact multimodal context from the buffer and ask Muse Spark about it."""

from __future__ import annotations

import base64
import io
import logging
import sys
import time
from datetime import datetime

from PIL import Image

from .config import Config, load_api_settings
from .timeline import Timeline

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are pantallita, an on-screen companion with a short memory of the user's computer.
You receive what happened during the last {minutes} minutes:
- a window history (which app/window was in front, with local times),
- a transcript of the system audio, i.e. what came out of the speakers (videos, calls,
  music). It is NOT the user's microphone,
- screenshots, each labeled with its local time and window.

Answer the user's question using only this context.
- Always say roughly when the things you mention happened, relative to now, and where,
  e.g. "~4 min ago, in Chrome (YouTube), ...".
- Transcripts are automatic and may contain errors; screenshots may be downscaled.
- If the context does not contain the answer, say so briefly instead of guessing.
- Reply in the same language as the question. Be concise."""


def hhmmss(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def ago(ts: float, now: float) -> str:
    seconds = max(0, now - ts)
    return f"~{int(seconds)} s ago" if seconds < 60 else f"~{round(seconds / 60)} min ago"


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


def encode_frame(path, width: int) -> str:
    with Image.open(path) as img:
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=75)
    return base64.b64encode(out.getvalue()).decode("ascii")


def window_history(events: list[dict], now: float, limit: int = 60) -> list[str]:
    lines, last = [], None
    for e in events:
        key = (e["window"], e["process"])
        if key == last:
            continue
        last = key
        lines.append(f'- {hhmmss(e["ts"])} ({ago(e["ts"], now)}) {e["process"] or "?"}: "{e["window"]}"')
    return lines[-limit:]


def build_messages(cfg: Config, timeline: Timeline, question: str) -> tuple[list[dict], dict]:
    now = time.time()
    events = timeline.read()
    windows = [e for e in events if e["type"] == "window"]
    transcripts = [e for e in events if e["type"] == "transcript"]
    frames = [e for e in events if e["type"] == "frame" and timeline.buffer_path(e["path"]).exists()]
    picked = select_frames(frames, cfg.ask["max_frames"], cfg.ask["recent_frames"])

    text = [f"Current local time: {hhmmss(now)}.", "", "Window history (oldest first):"]
    text += window_history(windows, now) or ["(none)"]
    text += ["", "System audio transcript (oldest first):"]
    text += [f'[{hhmmss(t["start"])}-{hhmmss(t["end"])}, {ago(t["start"], now)}] {t["text"]}'
             for t in transcripts] or ["(no speech heard)"]
    text += ["", f"{len(picked)} screenshots follow, oldest first."]

    content: list[dict] = [{"type": "text", "text": "\n".join(text)}]
    image_bytes = 0
    for f in picked:
        content.append({"type": "text", "text": (
            f'Screenshot at {hhmmss(f["ts"])} ({ago(f["ts"], now)}), '
            f'{f["process"] or "?"}: "{f["window"]}"')})
        b64 = encode_frame(timeline.buffer_path(f["path"]), cfg.ask["image_width"])
        image_bytes += len(b64)
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    content.append({"type": "text", "text": f"Question: {question}"})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(minutes=cfg.retention["minutes"])},
        {"role": "user", "content": content},
    ]
    stats = {"images": len(picked), "frames_available": len(frames),
             "transcript_segments": len(transcripts), "window_changes": len(windows),
             "image_kb": image_bytes // 1024, "context_text": "\n".join(text)}
    return messages, stats


def ask(cfg: Config, question: str, dry_run: bool = False) -> str:
    timeline = Timeline(cfg)
    messages, stats = build_messages(cfg, timeline, question)
    summary = (f"[pantallita] sending {stats['images']} images ({stats['image_kb']} KB base64, "
               f"{stats['frames_available']} frames in buffer), {stats['transcript_segments']} "
               f"transcript segments, {stats['window_changes']} window events")
    print(summary, file=sys.stderr)
    if dry_run:
        print(stats["context_text"])
        return ""

    from openai import OpenAI

    api = load_api_settings()
    client = OpenAI(api_key=api.api_key, base_url=api.base_url)
    t0 = time.monotonic()
    response = client.chat.completions.create(
        model=api.model, messages=messages, max_tokens=cfg.ask["max_output_tokens"])
    answer = response.choices[0].message.content or ""
    print(answer)

    usage = response.usage
    if usage:
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None) if details else None
        print(f"[pantallita] {api.model}: {usage.prompt_tokens} input tokens, "
              f"{usage.completion_tokens} output tokens"
              + (f" ({reasoning} reasoning)" if reasoning else "")
              + f", {stats['images']} images, {time.monotonic() - t0:.1f}s", file=sys.stderr)
    return answer
