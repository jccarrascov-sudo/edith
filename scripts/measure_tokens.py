"""Measure the input tokens saved by each optimization, on real data, with the official counter.

Uses POST /v1/responses/input_tokens (counts only, generates nothing).
  1-2-4: the current buffer (run `pantallita run` for a few minutes first).
  2-3:   a live screenshot + UI Automation tree of the current foreground window.

Run: .venv\\Scripts\\python scripts\\measure_tokens.py
Nothing is saved to disk; screenshots only go to the token counter.
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
import urllib.request

import mss
from PIL import Image

from pantallita.brain import tokens
from pantallita.memory import uia
from pantallita.brain.ask import ContextOptions, build_messages
from pantallita.memory.capture import primary_monitor
from pantallita.config import load_api_settings, load_config
from pantallita.memory.timeline import Timeline
from pantallita.memory.winutil import foreground_window

API = load_api_settings()


def count(content: list[dict], system: str | None = None) -> int:
    """Input tokens of a request whose user turn is `content` (chat-style parts)."""
    def part(p: dict) -> dict:
        if p["type"] == "text":
            return {"type": "input_text", "text": p["text"]}
        return {"type": "input_image", "image_url": p["image_url"]["url"]}

    messages = [{"role": "user", "content": [part(p) for p in content]}]
    if system:
        messages.insert(0, {"role": "system", "content": [{"type": "input_text", "text": system}]})
    body = json.dumps({"model": API.model, "input": messages}).encode()
    req = urllib.request.Request(f"{API.base_url}/responses/input_tokens", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {API.api_key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["input_tokens"]


def count_messages(messages: list[dict]) -> int:
    return count(messages[1]["content"], system=messages[0]["content"])


def image_part(img: Image.Image) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{tokens.jpeg_b64(img)}"}}


def row(name: str, value: int, base: int | None = None) -> str:
    saving = f"{100 * (1 - value / base):.0f}%" if base else "-"
    return f"| {name} | {value:,} | {saving} |"


def measure_ask(cfg) -> list[str]:
    timeline = Timeline(cfg)
    q = "What was I doing in the last minutes?"
    variants = [
        ("Before: raw events, 12 frames @1024 px (old default)", 1024,
         ContextOptions(history_scales=None, diff_crops=False, compact_text=False)),
        ("Text as diffs: merged transcript + window changes only", 1024,
         ContextOptions(history_scales=None, diff_crops=False, compact_text=True)),
        ("Same, every frame at full 1280 px (no decay)", 1280,
         ContextOptions(history_scales=None, diff_crops=False, compact_text=True)),
        ("+ Decreasing resolution 100% / 75% / 50%", 1280,
         ContextOptions(history_scales=[1.0, 0.75, 0.5], diff_crops=False, compact_text=True)),
        ("+ Decreasing resolution 100% / 50% / 25%", 1280,
         ContextOptions(history_scales=[1.0, 0.5, 0.25], diff_crops=False, compact_text=True)),
        ("+ Visual history as diffs (changed-region crops), 100/75/50", 1280,
         ContextOptions(history_scales=[1.0, 0.75, 0.5], diff_crops=True, compact_text=True)),
    ]
    lines = ["| `ask` context (same buffer, same question) | Input tokens | Saving vs. first row |",
             "|---|---:|---:|"]
    base = None
    for name, width, opts in variants:
        cfg.ask["image_width"] = width
        messages, stats = build_messages(cfg, timeline, q, opts)
        n = count_messages(messages)
        base = base or n
        lines.append(row(f"{name} ({stats['images']} images)", n, base))
    lines.append(f"\nBuffer: {stats['frames_available']} frames, {stats['transcript_segments']} transcript "
                 f"segments, {stats['window_changes']} window events.")
    return lines


def live_screen() -> tuple[Image.Image, int, str]:
    title, process = foreground_window()
    if load_config().is_excluded(title, process):
        sys.exit("Foreground window is excluded; switch to another window and retry.")
    with mss.MSS() as sct:
        shot = sct.grab(primary_monitor(sct))
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), \
        ctypes.windll.user32.GetForegroundWindow(), process


def measure_live() -> list[str]:
    screen, hwnd, process = live_screen()
    empty = count([{"type": "text", "text": "Screen:"}])
    lines = [f"\nLive window: `{process}` on a {screen.width}x{screen.height} screen. "
             f"Image rows exclude the fixed request overhead ({empty} tokens).", ""]

    # 2. Crop of a known element vs. the whole screen.
    uia.window_elements(hwnd)  # first query wakes up Chromium's accessibility tree
    elements = [e for e in uia.window_elements(hwnd) if e.kind in ("edit", "button", "link", "combo")]
    full = count([{"type": "text", "text": "Screen:"}, image_part(tokens.fit_width(screen, 1280))]) - empty
    lines += ["| Known element (UIA) | Input tokens | Saving |", "|---|---:|---:|",
              row("Whole screen @1280 px", full)]
    if elements:
        target = max(elements, key=lambda e: (e.kind == "edit", (e.rect[2] - e.rect[0]) * (e.rect[3] - e.rect[1])))
        crop, box = tokens.crop_with_margin(screen, target.rect, margin=48)
        n = count([{"type": "text", "text": "Screen:"}, image_part(crop)]) - empty
        lines.append(row(f"Crop of one `{target.kind}` + 48 px margin ({crop.width}x{crop.height}, full detail)", n, full))
        masked = screen.copy()
        masked.paste((0, 0, 0), (0, 0, masked.width, masked.height))
        masked.paste(crop, box[:2])
        n = count([{"type": "text", "text": "Screen:"}, image_part(tokens.fit_width(masked, 1280))]) - empty
        lines.append(row("Same element, rest of the screen masked black (SimpAgent-style)", n, full))

    # 3. Text (compressed UIA tree) vs. image of the same window.
    wr = uia.auto.ControlFromHandle(hwnd).BoundingRectangle
    window_img = screen.crop((max(0, wr.left), max(0, wr.top), min(screen.width, wr.right), min(screen.height, wr.bottom)))
    img_n = count([{"type": "text", "text": "Screen:"}, image_part(tokens.fit_width(window_img, 1280))]) - empty
    compressed = uia.compressed_tree(hwnd)
    raw = uia.raw_tree(hwnd)
    comp_n = count([{"type": "text", "text": "Screen:\n" + compressed}]) - empty
    raw_n = count([{"type": "text", "text": "Screen:\n" + raw}]) - empty
    lines += ["", "| Same window as... | Input tokens | Saving vs. raw tree |", "|---|---:|---:|",
              row(f"Raw linearized UIA tree ({len(raw.splitlines())} nodes)", raw_n),
              row(f"Screenshot of the window @1280 px", img_n, raw_n),
              row(f"Compressed UIA tree ({len(compressed.splitlines()) - 1} visible interactive/label nodes)", comp_n, raw_n)]
    return lines


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    t0 = time.monotonic()
    lines = measure_ask(cfg) + measure_live()
    print("\n".join(lines))
    print(f"\n(measured {time.strftime('%Y-%m-%d %H:%M')}, model {API.model}, {time.monotonic() - t0:.0f}s)")


if __name__ == "__main__":
    main()
