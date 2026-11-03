"""Summary of what is currently in buffer/."""

from __future__ import annotations

import time
from collections import Counter

from .config import Config
from .memory.timeline import Timeline


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def status(cfg: Config) -> None:
    timeline = Timeline(cfg)
    events = timeline.read(since=0)
    counts = Counter(e["type"] for e in events)
    now = time.time()

    print(f"Buffer: {cfg.buffer_dir}  (retention {cfg.retention['minutes']} min)")
    total = 0
    for name, folder in (("frames", cfg.frames_dir), ("audio", cfg.audio_dir)):
        files = [f for f in folder.iterdir() if f.is_file()]
        size = sum(f.stat().st_size for f in files)
        total += size
        oldest = min((f.stat().st_mtime for f in files), default=None)
        age = f", oldest {int(now - oldest)} s ago" if oldest else ""
        print(f"  {name:<9} {len(files):>4} files  {_size(size):>9}{age}")
    tl_size = timeline.path.stat().st_size if timeline.path.exists() else 0
    total += tl_size
    print(f"  timeline  {len(events):>4} events {_size(tl_size):>9}  "
          f"(frame {counts['frame']}, click {counts['click']}, field {counts['field']}, "
          f"transcript {counts['transcript']}, window {counts['window']})")
    if events:
        print(f"  oldest event {int(now - events[0]['ts'])} s ago, newest {int(now - events[-1]['ts'])} s ago")
    print(f"  total {_size(total)}")
