"""The local model: Muse Glimmer 30B (Meta, Apache-2.0) through llama.cpp's llama-server.

Light usage only. Screen text comes from UI Automation, so Glimmer is used for short jobs:
background notes (block summaries, key moments), describing at most a couple of small
images, and answering when the cloud is not used (local only, no internet, API error or
budget reached). Nothing it sees leaves the PC.

Measured on an RTX 5050 8 GB (see BUILD_LOG.md): it is a dense model, so VRAM is set by the
number of GPU layers (default 6, about 2 GB). It always reasons first unless the prompt opens
its answer channel directly, which is what `generate` does (a one-sentence answer in ~8 s
instead of ~45 s). Low process priority halved its speed, so the server runs at normal
priority, and Edith's own background jobs wait while a question is being answered.

A watchdog restarts the server if it dies or stops answering; Edith itself never closes."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from ..config import ROOT, Config

log = logging.getLogger(__name__)

MODEL_NAME = "muse-glimmer-30b"
SYSTEM = ("<|start|>system<|message|>{system}\n\nReasoning strength: low.\n\n"
          '# Valid recipients: "self", "user".<|eot|>')


def free_port() -> int:
    """A port nobody listens on, so a stale server from another run is never mistaken for ours."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _KillWithParent:
    """A Windows job object: the server dies when Edith's process ends, even if Edith crashes."""

    def __init__(self):
        k = ctypes.windll.kernel32
        k.CreateJobObjectW.restype = ctypes.c_void_p
        self.job = k.CreateJobObjectW(None, None)

        class Limits(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("flags", ctypes.c_uint32),
                        ("pad", ctypes.c_byte * 140)]   # rest of JOBOBJECT_EXTENDED_LIMIT_INFORMATION

        info = Limits()
        info.flags = 0x2000                             # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.ok = bool(self.job) and bool(k.SetInformationJobObject(self.job, 9, ctypes.byref(info), 144))

    def add(self, proc: subprocess.Popen) -> None:
        if self.ok:
            k = ctypes.windll.kernel32
            k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            if not k.AssignProcessToJobObject(self.job, int(proc._handle)):
                log.warning("Could not tie llama-server to Edith's process")


class LocalUnavailable(RuntimeError):
    """The local model is not installed, not ready, or did not answer in time."""


def _clean(text: str) -> str:
    """User and screen text must not be able to open or close the model's own channels."""
    return text.replace("<|", "< |")


def answer_prompt(system: str, user: str) -> str:
    """A prompt that starts Glimmer's answer channel directly, so it answers without reasoning."""
    return (SYSTEM.format(system=_clean(system)) + "<|start|>user<|message|>" + _clean(user)
            + "<|eot|><|start|>assistant to=user<|message|>")


def find_server(cfg: Config) -> Path | None:
    if cfg.local.get("server"):
        p = Path(cfg.local["server"])
        return p if p.exists() else None
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    hits = sorted(base.glob("ggml.llamacpp_*/llama-server.exe")) if base.exists() else []
    return hits[0] if hits else None


def find_model(cfg: Config) -> tuple[Path, Path | None] | None:
    """(model .gguf, vision projector .gguf or None)."""
    if cfg.local.get("model"):
        m = Path(cfg.local["model"])
        proj = Path(cfg.local["mmproj"]) if cfg.local.get("mmproj") else None
        return (m, proj if proj and proj.exists() else None) if m.exists() else None
    hub = Path.home() / ".cache" / "huggingface" / "hub" / "models--unsloth--Muse-Glimmer-30B-GGUF" / "snapshots"
    for snap in sorted(hub.glob("*")) if hub.exists() else []:
        m = snap / cfg.local["file"]
        if m.exists():
            proj = snap / "mmproj-kquant.gguf"
            return m, (proj if proj.exists() else None)
    return None


class LocalModel:
    def __init__(self, cfg: Config, on_notice: Callable[[str], None] | None = None):
        self.cfg = cfg
        self.c = cfg.local
        self.port = int(self.c["port"])     # 0 = any free port (each start picks a new one)
        self.base = ""
        self._job = _KillWithParent()
        self.on_notice = on_notice or (lambda _key: None)
        self.proc: subprocess.Popen | None = None
        self.ready = threading.Event()
        self.restarts = 0
        self._started = False
        self._stop = threading.Event()
        self._lock = threading.Lock()          # one generation at a time
        self._foreground = 0                   # questions waiting or running: background jobs yield
        self._fg_lock = threading.Lock()
        self.server = find_server(cfg)
        self.files = find_model(cfg)
        self.log_path = ROOT / "state" / "logs" / "llama-server.log"

    # --- lifecycle ------------------------------------------------------------------------------
    @property
    def installed(self) -> bool:
        return bool(self.c["enabled"] and self.server and self.files)

    def start(self) -> None:
        """Start the server and warm it up in the background; then keep watching it."""
        if not self.installed:
            log.info("Local model not available (enabled=%s, server=%s, model=%s)",
                     self.c["enabled"], self.server, self.files)
            return
        self._started = True
        threading.Thread(target=self._watch, name="local-model", daemon=True).start()

    def ensure_started(self) -> None:
        """Start the server now if nobody started it yet (the `edith ask` command)."""
        if not self._started:
            self.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()

    def _spawn(self) -> None:
        model, proj = self.files
        port = self.port or free_port()
        self.base = f"http://127.0.0.1:{port}"
        args = [str(self.server), "-m", str(model), "-ngl", str(self.c["gpu_layers"]),
                "-c", str(self.c["context"]), "-np", "1", "--host", "127.0.0.1", "--port", str(port),
                "--jinja", "--no-webui"]
        if proj:
            args += ["--mmproj", str(proj), "--no-mmproj-offload"]   # vision encoder on the CPU (VRAM)
        if self.c.get("threads"):
            args += ["-t", str(self.c["threads"])]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        out = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._job.add(self.proc)
        log.info("llama-server started (pid %s, %s GPU layers)", self.proc.pid, self.c["gpu_layers"])

    def _kill(self) -> None:
        self.ready.clear()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None

    def _healthy(self) -> bool:
        if not self._alive():
            return False
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=5) as r:
                return json.loads(r.read()).get("status") == "ok"
        except Exception:
            return False

    def _boot(self) -> bool:
        self._spawn()
        deadline = time.monotonic() + float(self.c["load_timeout_s"])
        while time.monotonic() < deadline and not self._stop.is_set():
            if self.proc.poll() is not None:
                log.error("llama-server exited while loading (code %s); see %s", self.proc.returncode, self.log_path)
                return False
            if self._healthy():
                try:   # warm-up: the first request pays for loading weights into memory
                    self._stream(answer_prompt("You are Edith.", "Say ok."), 4, None, 120)
                except Exception:
                    log.warning("Warm-up failed", exc_info=True)
                self.ready.set()
                log.info("Local model ready")
                return True
            time.sleep(1)
        log.error("llama-server did not become ready in time")
        return False

    def _watch(self) -> None:
        failures, first = 0, True
        while not self._stop.is_set():
            if not self.ready.is_set():
                if not first:
                    self.restarts += 1
                    self.on_notice("local_restarting")
                ok = self._boot()
                if not ok:
                    self._kill()
                    if self._stop.wait(min(60, 10 * (self.restarts + 1))):
                        break
                    first = False
                    continue
                if not first:
                    self.on_notice("local_restarted")
                first, failures = False, 0
            if self._stop.wait(2):
                break
            if not self._alive():
                log.error("llama-server died; restarting")
                self._kill()
                continue
            # A long generation keeps the server busy; only an idle server must answer /health.
            if not self._lock.locked():
                failures = 0 if self._healthy() else failures + 1
                if failures >= 3:
                    log.error("llama-server stopped answering; restarting")
                    self._kill()

    def hung(self) -> None:
        """A request timed out: treat the server as hung; the watchdog restarts it."""
        log.error("Local request timed out; restarting the server")
        self._kill()

    # --- generation -----------------------------------------------------------------------------
    def generate(self, system: str, user: str, max_tokens: int = 400,
                 on_token: Callable[[str], None] | None = None, background: bool = False,
                 timeout_s: float | None = None) -> str:
        """Glimmer's answer (streamed to on_token). Background jobs wait while a question runs."""
        return self._run(lambda t: self._stream(answer_prompt(system, user), max_tokens, on_token, t),
                         background, timeout_s)

    def describe(self, image_b64: str, instruction: str, max_tokens: int = 500,
                 background: bool = True, timeout_s: float | None = None) -> str:
        """A short text description of one JPEG image (slow on the CPU: background only)."""
        def call(t: float) -> str:
            body = {"messages": [{"role": "user", "content": [
                        {"type": "text", "text": _clean(instruction)},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}}]}],
                    "max_tokens": max_tokens, "temperature": 0.2,
                    "chat_template_kwargs": {"reasoning_strength": "low"}}
            d = self._post("/v1/chat/completions", body, t)
            return (d["choices"][0]["message"].get("content") or "").strip()
        return self._run(call, background, timeout_s)

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _run(self, call: Callable[[float], str], background: bool, timeout_s: float | None) -> str:
        if not self.installed:
            raise LocalUnavailable("not installed")
        timeout = float(timeout_s or self.c["timeout_s"])
        if not background:
            with self._fg_lock:
                self._foreground += 1
        try:
            for attempt in range(2):
                if not self._alive():
                    self.ready.clear()                 # dead: the watchdog brings it back
                if not self.ready.wait(timeout if not background else 0.1):
                    raise LocalUnavailable("not ready")
                while background and self._foreground:
                    if self._stop.wait(1):
                        raise LocalUnavailable("stopping")
                if not self._lock.acquire(timeout=timeout):
                    raise LocalUnavailable("busy")
                try:
                    return call(timeout)
                except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
                    if isinstance(exc, TimeoutError) or "timed out" in str(exc):
                        self.hung()
                        raise LocalUnavailable("timed out") from exc
                    if attempt or background:
                        raise LocalUnavailable(str(exc)) from exc
                    log.warning("Local request failed (%s); retrying once", exc)
                    if not self._alive():
                        self.ready.clear()
                    time.sleep(1)
                finally:
                    self._lock.release()
            raise LocalUnavailable("failed")
        finally:
            if not background:
                with self._fg_lock:
                    self._foreground -= 1

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _stream(self, prompt: str, max_tokens: int, on_token, timeout: float) -> str:
        body = {"prompt": prompt, "n_predict": max_tokens, "temperature": 0.2, "stream": True, "cache_prompt": True}
        req = urllib.request.Request(self.base + "/completion", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        deadline = time.monotonic() + timeout
        parts: list[str] = []
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                if time.monotonic() > deadline:
                    raise TimeoutError("local generation timed out")
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                d = json.loads(line[5:])
                if d.get("content"):
                    parts.append(d["content"])
                    if on_token:
                        on_token(d["content"])
                if d.get("stop"):
                    break
        return "".join(parts).strip()
