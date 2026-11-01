"""Build a compact multimodal context from the buffer and ask Muse Spark about it."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from . import tokens
from ..config import Config
from ..memory.timeline import Timeline

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


@dataclass
class ContextOptions:
    """Which token optimizations to apply (all on by default; flags exist to measure each one)."""
    history_scales: list[float] | None = None  # None = every frame at image_width
    diff_crops: bool = True                    # older frames: only the region that changed
    compact_text: bool = True                  # merged transcript + window changes only

    @classmethod
    def from_config(cls, cfg: Config) -> "ContextOptions":
        return cls(history_scales=list(cfg.ask["history_scales"]), diff_crops=cfg.ask["diff_crops"])


def frame_parts(cfg: Config, timeline: Timeline, picked: list[dict], now: float,
                opts: ContextOptions) -> tuple[list[dict], int]:
    """Content parts for the picked frames (oldest first) and the total base64 size."""
    images = []
    for f in picked:
        with Image.open(timeline.buffer_path(f["path"])) as img:
            images.append(tokens.fit_width(img.convert("RGB"), cfg.ask["image_width"]))
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
            label += " [current screen, full resolution]"
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


def build_messages(cfg: Config, timeline: Timeline, question: str,
                   opts: ContextOptions | None = None) -> tuple[list[dict], dict]:
    opts = opts or ContextOptions.from_config(cfg)
    now = time.time()
    events = timeline.read()
    windows = [e for e in events if e["type"] == "window"]
    transcripts = [e for e in events if e["type"] == "transcript"]
    frames = [e for e in events if e["type"] == "frame" and timeline.buffer_path(e["path"]).exists()]
    picked = select_frames(frames, cfg.ask["max_frames"], cfg.ask["recent_frames"])

    text = [f"Current local time: {hhmmss(now)}.", "", "Window history (oldest first):"]
    if opts.compact_text:
        text += window_history(windows, now) or ["(none)"]
    else:
        text += [f'- {hhmmss(e["ts"])} ({ago(e["ts"], now)}) {e["process"] or "?"}: "{e["window"]}"'
                 for e in windows] or ["(none)"]
    text += ["", "System audio transcript (oldest first):"]
    text += [f'[{hhmmss(t["start"])}-{hhmmss(t["end"])}, {ago(t["start"], now)}] {t["text"]}'
             for t in (merge_transcripts(transcripts) if opts.compact_text else transcripts)]         or ["(no speech heard)"]
    text += ["", f"{len(picked)} screenshots follow, oldest first."]

    content: list[dict] = [{"type": "text", "text": "\n".join(text)}]
    parts, image_bytes = frame_parts(cfg, timeline, picked, now, opts)
    content += parts
    content.append({"type": "text", "text": f"Question: {question}"})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(minutes=cfg.retention["minutes"])},
        {"role": "user", "content": content},
    ]
    stats = {"images": sum(p["type"] == "image_url" for p in content), "frames_available": len(frames),
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

    from .client import MetaClient

    client = MetaClient(cfg)
    t0 = time.monotonic()
    response = client.chat(
        "ask", messages=messages, max_tokens=cfg.ask["max_output_tokens"],
        reasoning_effort=cfg.ask["reasoning_effort"])
    answer = response.choices[0].message.content or ""
    print(answer)

    usage = response.usage
    if usage:
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None) if details else None
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        print(f"[pantallita] {client.model}: {usage.prompt_tokens} input tokens ({cached} cached), "
              f"{usage.completion_tokens} output tokens"
              + (f" ({reasoning} reasoning)" if reasoning else "")
              + f", {stats['images']} images, {time.monotonic() - t0:.1f}s, "
              f"${response.pantallita_cost:.4f}", file=sys.stderr)
        print(f"[pantallita] {client.meter.summary()}", file=sys.stderr)
    return answer
