"""Append-only event log (buffer/timeline.jsonl) with a rolling retention window.

Event shapes:
  {"type": "frame", "ts", "path", "score", "window", "process"}
  {"type": "window", "ts", "window", "process"}           # foreground window changed
  {"type": "transcript", "ts", "start", "end", "text", "lang"}

`ts` is a Unix timestamp (float). Paths are relative to buffer/.
The file is shared between processes (`run` writes, `ask`/`status` may prune), so every
read-modify-write goes through a lock file locked with msvcrt.
"""

from __future__ import annotations

import contextlib
import json
import logging
import msvcrt
import os
import threading
import time
from pathlib import Path

from ..config import Config

log = logging.getLogger(__name__)


class Timeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.path = cfg.timeline_path
        self._lock_path = cfg.buffer_dir / "timeline.lock"
        self._thread_lock = threading.Lock()

    @contextlib.contextmanager
    def _locked(self):
        with self._thread_lock, open(self._lock_path, "a+b") as lock_file:
            # LK_LOCK retries for ~10 s before raising; keep looping so callers never lose data.
            while True:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

    def append(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False)
        with self._locked(), open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read_unlocked(self) -> list[dict]:
        if not self.path.exists():
            return []
        events = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Skipping corrupt timeline line")
        return events

    def read(self, since: float | None = None) -> list[dict]:
        """Events newer than `since` (defaults to the retention cutoff), oldest first."""
        cutoff = self.cutoff() if since is None else since
        with self._locked():
            events = self._read_unlocked()
        return sorted((e for e in events if _event_end(e) >= cutoff), key=lambda e: e["ts"])

    def cutoff(self) -> float:
        return time.time() - self.cfg.retention_s

    def prune(self) -> int:
        """Drop events, frames and audio older than the retention window. Returns files deleted."""
        cutoff = self.cutoff()
        with self._locked():
            events = self._read_unlocked()
            kept = [e for e in events if _event_end(e) >= cutoff]
            if len(kept) != len(events):
                tmp = self.path.with_suffix(".jsonl.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in kept)
                os.replace(tmp, self.path)
        deleted = 0
        for folder in (self.cfg.frames_dir, self.cfg.audio_dir):
            for file in folder.iterdir():
                with contextlib.suppress(FileNotFoundError, PermissionError):
                    if file.stat().st_mtime < cutoff:
                        file.unlink()
                        deleted += 1
        return deleted

    def buffer_path(self, relative: str) -> Path:
        return self.cfg.buffer_dir / relative


def _event_end(event: dict) -> float:
    return event.get("end", event["ts"])
