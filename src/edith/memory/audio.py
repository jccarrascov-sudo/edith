"""System audio (WASAPI loopback) -> ~15 s blocks -> faster-whisper transcripts."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio

from ..config import Config
from .capture import PrivacyGate
from .timeline import Timeline

log = logging.getLogger(__name__)

WHISPER_RATE = 16_000
GAP_S = 0.2  # loopback delivers nothing while silent; gaps longer than this are filled with zeros


def _add_cuda_dll_dirs() -> None:
    """CTranslate2 on Windows needs the cuBLAS/cuDNN DLLs from the nvidia-* wheels on the path."""
    try:
        import nvidia  # namespace package installed by nvidia-cublas-cu12 / nvidia-cudnn-cu12
    except ImportError:
        return
    for base in nvidia.__path__:
        for bin_dir in Path(base).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    """Loads whisper lazily, so VRAM is only used once there is something to transcribe."""

    def __init__(self, cfg: Config):
        self.a = cfg.audio
        self.model = None
        self.device = None

    def _load(self, device: str) -> None:
        _add_cuda_dll_dirs()
        from faster_whisper import WhisperModel

        compute_type = self.a["compute_type"]
        t0 = time.monotonic()
        try:  # avoid a Hugging Face round-trip on every start once the model is cached
            self.model = WhisperModel(self.a["model"], device=device, compute_type=compute_type,
                                      local_files_only=True)
        except Exception:
            self.model = WhisperModel(self.a["model"], device=device, compute_type=compute_type)
        self.device = device
        log.info("Loaded whisper %s on %s (%s) in %.1fs", self.a["model"], device, compute_type,
                 time.monotonic() - t0)

    def transcribe(self, audio: np.ndarray) -> tuple[list[tuple[float, float, str]], str]:
        if self.model is None:
            try:
                self._load(self.a["device"])
            except Exception as exc:
                log.warning("Whisper on %s failed (%s); falling back to CPU", self.a["device"], exc)
                self._load("cpu")
        try:
            return self._run(audio)
        except Exception as exc:
            if self.device == "cpu":
                raise
            log.warning("Transcription on %s failed (%s); falling back to CPU", self.device, exc)
            self._load("cpu")
            return self._run(audio)

    def _run(self, audio: np.ndarray) -> tuple[list[tuple[float, float, str]], str]:
        segments, info = self.model.transcribe(
            audio, beam_size=1, vad_filter=True, condition_on_previous_text=False)
        out = [(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
        return out, info.language


def to_mono_16k(samples: np.ndarray, channels: int, rate: int) -> np.ndarray:
    mono = samples.reshape(-1, channels).mean(axis=1) if channels > 1 else samples
    if rate == WHISPER_RATE or mono.size == 0:
        return mono.astype(np.float32)
    n_out = int(mono.size * WHISPER_RATE / rate)
    x_out = np.linspace(0, mono.size - 1, n_out)
    return np.interp(x_out, np.arange(mono.size), mono).astype(np.float32)


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


class AudioRecorder:
    def __init__(self, cfg: Config, timeline: Timeline, stop: threading.Event,
                 gate: PrivacyGate, transcriber: Transcriber | None = None):
        self.cfg = cfg
        self.a = cfg.audio
        self.timeline = timeline
        self.stop = stop
        self.gate = gate
        self.transcriber = transcriber or Transcriber(cfg)
        self.chunks: queue.Queue[tuple[float, bytes, int]] = queue.Queue()
        self.blocks = self.silent_blocks = self.private_blocks = self.segments = 0
        self.listening = True   # the panel's audio button: off = blocks are dropped unheard

    # --- device handling -------------------------------------------------
    @staticmethod
    def default_loopback(p: pyaudio.PyAudio) -> dict:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for loopback in p.get_loopback_device_info_generator():
            if speakers["name"] in loopback["name"]:
                return loopback
        raise RuntimeError(f"No loopback device for {speakers['name']!r}")

    def _callback(self, in_data, frame_count, time_info, status):
        self.chunks.put((time.time(), in_data, frame_count))
        return (None, pyaudio.paContinue)

    # --- main loop -------------------------------------------------------
    def run(self) -> None:
        while not self.stop.is_set():
            p = pyaudio.PyAudio()
            try:
                self._record(p)
            except Exception:
                log.exception("Audio capture failed; retrying in 5 s")
                self.stop.wait(5)
            finally:
                p.terminate()

    def _record(self, p: pyaudio.PyAudio) -> None:
        device = self.default_loopback(p)
        channels = int(device["maxInputChannels"])
        rate = int(device["defaultSampleRate"])
        self.chunks = queue.Queue()
        stream = p.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                        input_device_index=device["index"], frames_per_buffer=rate // 10,
                        stream_callback=self._callback)
        log.info("Recording loopback of %r (%d ch, %d Hz)", device["name"], channels, rate)
        try:
            self._consume(p, device, channels, rate)
        finally:
            stream.stop_stream()
            stream.close()

    def _consume(self, p: pyaudio.PyAudio, device: dict, channels: int, rate: int) -> None:
        block_s = float(self.a["block_s"])
        block_start = time.time()
        pos_time = block_start  # wall-clock time of the next sample in `buf`
        buf: list[np.ndarray] = []
        buffered = 0  # samples (per channel) in buf
        pending = None  # a chunk that already belongs to the next block

        while True:
            deadline = block_start + block_s
            # Drain by chunk timestamps, not by wall clock, so a slow transcription
            # (e.g. the first model load) never shifts queued audio.
            while not self.stop.is_set():
                if pending is None:
                    try:
                        pending = self.chunks.get(timeout=0.5)
                    except queue.Empty:
                        if time.time() >= deadline:
                            break
                        continue
                arrival, data, frames = pending
                chunk_start = arrival - frames / rate
                if chunk_start >= deadline:
                    break
                pending = None
                if chunk_start - pos_time > GAP_S:
                    gap = int((chunk_start - pos_time) * rate)
                    buf.append(np.zeros(gap * channels, dtype=np.float32))
                    buffered += gap
                    pos_time += gap / rate
                buf.append(np.frombuffer(data, dtype=np.float32))
                buffered += frames
                pos_time += frames / rate
            if self.stop.is_set():
                return

            # Pad trailing silence up to the block boundary, then cut exactly one block.
            if deadline - pos_time > GAP_S:
                pad = int((deadline - pos_time) * rate)
                buf.append(np.zeros(pad * channels, dtype=np.float32))
                buffered += pad
                pos_time += pad / rate
            samples = np.concatenate(buf) if buf else np.zeros(0, dtype=np.float32)
            cut = min(int(block_s * rate), buffered) * channels
            block, rest = samples[:cut], samples[cut:]
            buf, buffered = ([rest] if rest.size else []), rest.size // channels

            self._process(block_start, to_mono_16k(block, channels, rate))
            block_start = deadline

            try:
                if self.default_loopback(p)["name"] != device["name"]:
                    log.info("Default output device changed; reopening")
                    return
            except Exception:
                return

    def _process(self, block_start: float, audio: np.ndarray) -> None:
        self.blocks += 1
        if not self.listening:
            return
        if self.gate.blocked(block_start):
            self.private_blocks += 1
            log.debug("Dropped audio block: an excluded window was active")
            return
        level = rms(audio)
        if level < self.a["silence_rms"]:
            self.silent_blocks += 1
            log.debug("Silent block (rms %.4f)", level)
            return
        if self.a["keep_wav"]:
            self._save_wav(block_start, audio)
        t0 = time.monotonic()
        segments, lang = self.transcriber.transcribe(audio)
        for start, end, text in segments:
            self.timeline.append({"type": "transcript", "ts": block_start + start,
                                  "start": block_start + start, "end": block_start + end,
                                  "text": text, "lang": lang})
        self.segments += len(segments)
        log.info("Transcribed %d segment(s) in %.1fs: %s", len(segments), time.monotonic() - t0,
                 " ".join(s[2] for s in segments)[:120])

    def _save_wav(self, block_start: float, audio: np.ndarray) -> None:
        name = datetime.fromtimestamp(block_start).strftime("%Y%m%d-%H%M%S") + ".wav"
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        with wave.open(str(self.cfg.audio_dir / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(WHISPER_RATE)
            w.writeframes(pcm.tobytes())
