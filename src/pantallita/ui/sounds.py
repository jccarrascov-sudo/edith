"""Tiny sounds synthesized with NumPy at runtime (no audio files): click, ding, sparks, whoosh."""

from __future__ import annotations

import io
import threading
import wave
import winsound

import numpy as np

RATE = 22050
_cache: dict[str, bytes] = {}
enabled = True


def _wav(samples: np.ndarray) -> bytes:
    samples = np.clip(samples, -1, 1)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((samples * 32000 * 0.5).astype(np.int16).tobytes())
    return out.getvalue()


def _env(n: int, attack: float = 0.005, decay: float = 6.0) -> np.ndarray:
    t = np.arange(n) / RATE
    a = np.minimum(1.0, t / attack)
    return a * np.exp(-decay * t)


def _tone(freq: float, seconds: float, decay: float = 6.0) -> np.ndarray:
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    return np.sin(2 * np.pi * freq * t) * _env(n, decay=decay)


def make(name: str) -> bytes:
    rng = np.random.default_rng(7)
    if name == "click":
        s = _tone(1800, 0.04, 80) * 0.6
    elif name == "ding":  # verified: two bright notes
        a, b = _tone(1318.5, 0.5, 5), _tone(1975.5, 0.6, 4)
        offset = int(RATE * 0.09)
        s = np.zeros(offset + len(b))
        s[: len(a)] += a
        s[offset:] += b
    elif name == "sparks":  # a failing block
        n = int(RATE * 0.35)
        s = rng.uniform(-1, 1, n) * (rng.random(n) > 0.93) * _env(n, decay=7)
    elif name == "whoosh":  # cards flying
        n = int(RATE * 0.35)
        noise = rng.uniform(-1, 1, n)
        k = np.linspace(0.02, 0.4, n)
        smooth = np.zeros(n)
        for i in range(1, n):
            smooth[i] = smooth[i - 1] + k[i] * (noise[i] - smooth[i - 1])
        s = smooth * np.sin(np.linspace(0, np.pi, n)) * 1.6
    elif name == "stamp":
        s = _tone(220, 0.12, 30) + rng.uniform(-0.3, 0.3, int(RATE * 0.12)) * _env(int(RATE * 0.12), decay=40)
    else:
        s = np.zeros(10)
    return _wav(s)


def play(name: str) -> None:
    """Fire and forget (winsound cannot play from memory asynchronously, so a thread waits)."""
    if not enabled:
        return
    data = _cache.get(name) or _cache.setdefault(name, make(name))
    threading.Thread(target=winsound.PlaySound, args=(data, winsound.SND_MEMORY), daemon=True).start()
