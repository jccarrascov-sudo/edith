"""Core behaviour tests with a fake screen and a fake clock (no real capture needed).

Run: .venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

TMP = tempfile.mkdtemp(prefix="edith-test-")
os.environ["EDITH_HOME"] = TMP  # must be set before importing edith.config

from edith import config  # noqa: E402
from edith.brain import ask, tokens  # noqa: E402
from edith.memory import audio, capture  # noqa: E402
from edith.memory.timeline import Timeline  # noqa: E402


class FakeShot:
    def __init__(self, pixels: np.ndarray):
        self.size = (pixels.shape[1], pixels.shape[0])
        bgra = np.dstack([pixels, np.full(pixels.shape[:2], 255, np.uint8)])
        self.bgra = bgra.tobytes()


class FakeScreen:
    def __init__(self):
        self.pixels = np.zeros((720, 1280, 3), np.uint8)

    def grab(self, monitor):
        return FakeShot(self.pixels)


class FakeClock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def fresh_config(**overrides) -> config.Config:
    cfg = config.load_config(Path(TMP) / "missing.toml")
    cfg.exclude = {"titles": ["keepass", "bank"], "processes": ["1password.exe"]}
    for section, values in overrides.items():
        getattr(cfg, section).update(values)
    for folder in (cfg.frames_dir, cfg.audio_dir):
        for f in folder.iterdir():
            f.unlink()
    cfg.timeline_path.unlink(missing_ok=True)
    return cfg


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.cfg = fresh_config()
        self.timeline = Timeline(self.cfg)
        self.screen = FakeScreen()
        self.clock = FakeClock()
        self.window = ("Notes - Notepad", "notepad.exe")
        self.cap = capture.ScreenCapture(self.cfg, self.timeline, threading.Event())
        patches = [
            mock.patch.object(capture, "foreground_window", lambda: self.window),
            mock.patch.object(capture.time, "time", self.clock),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_for(self, seconds: float, step: float = 2.0, on_tick=None):
        for i in range(int(seconds / step)):
            if on_tick:
                on_tick(i)
            self.cap.tick(self.screen, {})
            self.clock.now += step

    def frames(self):
        return [e for e in self.timeline.read(since=0) if e["type"] == "frame"]

    def test_static_screen_saves_only_every_30s(self):
        self.run_for(300)  # 5 minutes of a frozen screen
        stamps = [e["ts"] for e in self.frames()]
        self.assertEqual(len(stamps), 10)  # first frame + one every 30 s
        self.assertTrue(all(b - a >= 30 for a, b in zip(stamps, stamps[1:])))
        self.assertEqual(len(list(self.cfg.frames_dir.iterdir())), 10)

    def test_small_noise_is_ignored_but_real_changes_are_saved(self):
        rng = np.random.default_rng(0)

        def tick(i):
            if i % 5 == 4:  # a real change: a big bright window appears/moves
                self.screen.pixels[:] = 0
                self.screen.pixels[100:600, (i * 37) % 600:(i * 37) % 600 + 600] = 220
            else:  # a blinking cursor / clock: a few pixels
                self.screen.pixels[10:14, 10:20] = rng.integers(0, 255)

        self.run_for(40, on_tick=tick)
        scores = [e["score"] for e in self.frames()[1:]]
        self.assertGreater(len(scores), 0)
        self.assertTrue(all(s >= self.cfg.capture["change_threshold"] for s in scores))
        self.assertLessEqual(len(self.frames()), 1 + 4)

    def test_excluded_window_never_reaches_buffer(self):
        self.window = ("KeePass - secrets.kdbx", "KeePassXC.exe")
        self.run_for(60, on_tick=lambda i: self.screen.pixels.fill(i * 10 % 255))
        self.window = ("Online banking - Chrome", "chrome.exe")
        self.run_for(60, on_tick=lambda i: self.screen.pixels.fill(i * 10 % 255))
        self.window = ("Vault", "1Password.exe")
        self.run_for(60, on_tick=lambda i: self.screen.pixels.fill(i * 10 % 255))
        self.assertEqual(list(self.cfg.frames_dir.iterdir()), [])
        self.assertFalse(self.timeline.path.exists() and self.timeline.path.read_text().strip())
        self.assertEqual(self.cap.excluded_ticks, 90)

    def test_window_switching_to_excluded_mid_grab_is_dropped(self):
        calls = iter([("Notes", "notepad.exe"), ("KeePass", "keepass.exe")])
        with mock.patch.object(capture, "foreground_window", lambda: next(calls)):
            self.cap.tick(self.screen, {})
        self.assertEqual(list(self.cfg.frames_dir.iterdir()), [])


class FakeTranscriber:
    def __init__(self):
        self.calls = 0

    def transcribe(self, samples):
        self.calls += 1
        return [(0.5, 2.0, "hello from the video")], "en"


class AudioPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.cfg = fresh_config()
        self.timeline = Timeline(self.cfg)
        self.gate = capture.PrivacyGate(margin_s=2.0)
        self.whisper = FakeTranscriber()
        self.rec = audio.AudioRecorder(self.cfg, self.timeline, threading.Event(), self.gate,
                                       transcriber=self.whisper)
        self.loud = (0.1 * np.sin(np.arange(16000 * 15) / 5)).astype(np.float32)

    def test_loud_block_is_transcribed_and_saved(self):
        self.rec._process(time.time() - 15, self.loud)
        self.assertEqual(self.whisper.calls, 1)
        self.assertEqual(len(list(self.cfg.audio_dir.iterdir())), 1)
        self.assertEqual(self.timeline.read(since=0)[0]["text"], "hello from the video")

    def test_silent_block_is_not_transcribed(self):
        self.rec._process(time.time() - 15, np.zeros(16000 * 15, np.float32))
        self.assertEqual(self.whisper.calls, 0)
        self.assertEqual(list(self.cfg.audio_dir.iterdir()), [])

    def test_block_overlapping_excluded_window_is_dropped(self):
        start = time.time() - 15
        self.gate.mark()  # an excluded window was in front during this block
        self.rec._process(start, self.loud)
        self.assertEqual(self.whisper.calls, 0)
        self.assertEqual(list(self.cfg.audio_dir.iterdir()), [])
        self.assertFalse(self.timeline.path.exists())
        self.assertEqual(self.rec.private_blocks, 1)


class RetentionTests(unittest.TestCase):
    def test_prune_removes_old_events_and_files(self):
        cfg = fresh_config()
        tl = Timeline(cfg)
        now = time.time()
        old, new = now - 11 * 60, now - 60
        for ts, name in ((old, "old.jpg"), (new, "new.jpg")):
            path = cfg.frames_dir / name
            path.write_bytes(b"x")
            os.utime(path, (ts, ts))
            tl.append({"type": "frame", "ts": ts, "path": f"frames/{name}", "score": 1,
                       "window": "w", "process": "p"})
        tl.append({"type": "transcript", "ts": old, "start": old, "end": old + 10, "text": "a"})
        # A segment that started 11 min ago but ended within the window is still kept.
        tl.append({"type": "transcript", "ts": old, "start": old, "end": now - 30, "text": "b"})
        wav = cfg.audio_dir / "old.wav"
        wav.write_bytes(b"x")
        os.utime(wav, (old, old))

        tl.prune()
        kept = tl.read(since=0)
        self.assertEqual([e.get("text", e.get("path")) for e in kept], ["b", "frames/new.jpg"])
        self.assertEqual(sorted(f.name for f in cfg.frames_dir.iterdir()), ["new.jpg"])
        self.assertEqual(list(cfg.audio_dir.iterdir()), [])


class TokenHelperTests(unittest.TestCase):
    def test_history_scales_newest_first_then_decay(self):
        self.assertEqual(tokens.history_scales(5, [1.0, 0.5, 0.25]), [0.25, 0.25, 0.25, 0.5, 1.0])
        self.assertEqual(tokens.history_scales(1, [1.0, 0.5]), [1.0])

    def test_scale_is_uniform(self):
        from PIL import Image
        img = Image.new("RGB", (1280, 720))
        self.assertEqual(tokens.scale_image(img, 0.5).size, (640, 360))
        self.assertEqual(tokens.scale_image(img, 1.0).size, (1280, 720))

    def test_crop_keeps_margin_and_stays_inside(self):
        from PIL import Image
        img = Image.new("RGB", (1280, 720))
        crop, box = tokens.crop_with_margin(img, (1200, 10, 1270, 40), margin=48)
        self.assertEqual(box[2], 1280)
        self.assertEqual(box[1], 0)
        self.assertGreaterEqual(crop.width, 160)

    def test_changed_box_finds_only_the_changed_area(self):
        from PIL import Image, ImageDraw
        a = Image.new("RGB", (1280, 720), "white")
        b = a.copy()
        ImageDraw.Draw(b).rectangle((600, 300, 700, 360), fill="black")
        box = tokens.changed_box(a, b)
        self.assertTrue(box[0] <= 600 and box[1] <= 300 and box[2] >= 700 and box[3] >= 360)
        self.assertLess(tokens.box_area_ratio(box, b.size), 0.05)
        self.assertIsNone(tokens.changed_box(a, a.copy()))

    def test_prefer_text_only_when_cheaper(self):
        small_form = "\n".join(f'[{i}] edit "Field {i}" = "" @10,{i * 30},200,24' for i in range(6))
        big_tree = "\n".join(f'[{i}] button "Tab number {i} with a long title" @{i},0,100,30' for i in range(400))
        self.assertTrue(tokens.prefer_text(small_form, (1280, 720)))
        self.assertFalse(tokens.prefer_text(big_tree, (1280, 720)))
        self.assertFalse(tokens.prefer_text(small_form, (1280, 720), needs_pixels=True))


class SelectFramesTests(unittest.TestCase):
    def test_spread_over_time_plus_recent(self):
        frames = [{"path": str(i), "ts": float(i * 10), "score": 0.02 + (i % 7) / 100}
                  for i in range(60)]
        picked = ask.select_frames(frames, max_n=12, recent_n=3)
        self.assertEqual(len(picked), 12)
        self.assertEqual([f["path"] for f in picked[-3:]], ["57", "58", "59"])
        stamps = [f["ts"] for f in picked]
        self.assertEqual(stamps, sorted(stamps))
        self.assertLess(stamps[0], 570 / 9)  # the first time slice is represented
        self.assertEqual(len({f["path"] for f in picked}), 12)

    def test_transcript_segments_are_merged(self):
        segs = [{"start": t, "end": t + 3, "text": str(t)} for t in range(0, 60, 4)]
        segs.append({"start": 100, "end": 102, "text": "later"})
        merged = ask.merge_transcripts(segs)
        self.assertEqual(len(merged), 4)
        self.assertTrue(all(m["end"] - m["start"] <= 30 for m in merged))
        self.assertEqual(merged[-1]["text"], "later")
        self.assertEqual(" ".join(m["text"] for m in merged).split(), [s["text"] for s in segs])

    def test_few_frames_are_all_sent(self):
        frames = [{"path": str(i), "ts": float(i), "score": 0.1} for i in range(5)]
        self.assertEqual(ask.select_frames(frames, 12, 3), frames)


if __name__ == "__main__":
    unittest.main()
