"""Primary-monitor capture with cheap change detection."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import mss
import numpy as np
from PIL import Image

from ..config import Config
from .timeline import Timeline
from .winutil import foreground_window

log = logging.getLogger(__name__)

THUMB_SIZE = (64, 36)


def thumbnail(img: Image.Image) -> np.ndarray:
    """64x36 grayscale in [0, 1], used only to measure how much the screen changed."""
    small = img.reduce(8) if img.width >= 1024 else img
    return np.asarray(small.convert("L").resize(THUMB_SIZE, Image.BILINEAR), dtype=np.float32) / 255.0


def change_score(a: np.ndarray | None, b: np.ndarray) -> float:
    return 1.0 if a is None else float(np.mean(np.abs(a - b)))


def primary_monitor(sct: mss.base.MSSBase) -> dict:
    # On Windows the primary monitor is the one whose top-left corner is the origin.
    for mon in sct.monitors[1:]:
        if mon["left"] == 0 and mon["top"] == 0:
            return mon
    return sct.monitors[1]


class PrivacyGate:
    """Remembers when an excluded window was last in front, so audio from that time is dropped too."""

    def __init__(self, margin_s: float):
        self.margin_s = margin_s
        self.last_excluded = 0.0

    def mark(self) -> None:
        self.last_excluded = time.time()

    def blocked(self, start: float) -> bool:
        """True if an excluded window was active at any point since `start` (minus one capture tick)."""
        return self.last_excluded >= start - self.margin_s


class ScreenCapture:
    def __init__(self, cfg: Config, timeline: Timeline, stop: threading.Event,
                 gate: PrivacyGate | None = None):
        self.cfg = cfg
        self.gate = gate or PrivacyGate(cfg.capture["interval_s"])
        self.c = cfg.capture
        self.timeline = timeline
        self.stop = stop
        self.last_thumb: np.ndarray | None = None
        self.last_saved = 0.0
        self.last_window: tuple[str, str] | None = None
        self.saved = 0
        self.excluded_ticks = 0

    def run(self) -> None:
        with mss.MSS() as sct:
            monitor = primary_monitor(sct)
            log.info("Capturing primary monitor %sx%s every %ss",
                     monitor["width"], monitor["height"], self.c["interval_s"])
            while not self.stop.is_set():
                started = time.monotonic()
                try:
                    self.tick(sct, monitor)
                except Exception:
                    log.exception("Capture tick failed")
                self.stop.wait(max(0.0, self.c["interval_s"] - (time.monotonic() - started)))

    def tick(self, sct: mss.base.MSSBase, monitor: dict) -> None:
        title, process = foreground_window()
        if self.cfg.is_excluded(title, process):
            self.excluded_ticks += 1
            self.gate.mark()
            return

        shot = sct.grab(monitor)
        # The window may have switched while grabbing; check again before keeping anything.
        title_after, process_after = foreground_window()
        if self.cfg.is_excluded(title_after, process_after):
            self.excluded_ticks += 1
            self.gate.mark()
            return

        now = time.time()
        if (title_after, process_after) != self.last_window:
            self.last_window = (title_after, process_after)
            self.timeline.append({"type": "window", "ts": now,
                                  "window": title_after, "process": process_after})

        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        thumb = thumbnail(img)
        score = change_score(self.last_thumb, thumb)
        idle = now - self.last_saved >= self.c["max_idle_s"]
        if score < self.c["change_threshold"] and not idle:
            return

        if img.width > self.c["max_width"]:
            img = img.resize((self.c["max_width"], round(img.height * self.c["max_width"] / img.width)),
                             Image.LANCZOS)
        name = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S-%f")[:-3] + ".jpg"
        img.save(self.cfg.frames_dir / name, "JPEG", quality=self.c["jpeg_quality"])
        self.timeline.append({
            "type": "frame", "ts": now, "path": f"frames/{name}", "score": round(score, 4),
            "window": title_after, "process": process_after,
        })
        self.last_thumb = thumb
        self.last_saved = now
        self.saved += 1
        log.debug("Saved %s (score %.3f%s)", name, score, ", idle" if idle and score < self.c["change_threshold"] else "")
