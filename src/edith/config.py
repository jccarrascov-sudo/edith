"""Configuration: config.toml (non-secret settings) + .env (API credentials)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# src/edith/config.py -> repo root. EDITH_HOME moves the data folder (buffer/, state/, tools/);
# PANTALLITA_HOME is still read, from before the app was renamed.
ROOT = Path(os.environ.get("EDITH_HOME") or os.environ.get("PANTALLITA_HOME")
            or Path(__file__).resolve().parents[2])

DEFAULTS: dict[str, dict] = {
    "capture": {
        "interval_s": 2.0,
        "change_threshold": 0.02,
        "max_idle_s": 30,
        "jpeg_quality": 70,
        "max_width": 1280,
        "burst_interval_s": 0.5,
        "burst_s": 3.0,
    },
    "audio": {
        "enabled": True,
        "block_s": 15,
        "silence_rms": 0.003,
        "model": "small",
        "device": "cuda",
        "compute_type": "int8",
        "keep_wav": True,
    },
    "retention": {"minutes": 10, "prune_every_s": 5},
    "ask": {
        "max_frames": 12,
        "recent_frames": 3,
        "image_width": 1280,
        "history_scales": [1.0, 0.75, 0.5],
        "diff_crops": True,
        "diff_crop_max_area": 0.6,
        "max_output_tokens": 4000,
        "reasoning_effort": "low",
    },
    "budget": {"session_limit_usd": 5.0, "session_idle_hours": 3, "monthly_limit_usd": 10.0,
               "warn_at": 0.8, "input_per_m": 1.25, "cached_per_m": 0.15, "output_per_m": 4.25},
    "cloud": {"max_images": 3, "image_width": 1024, "older_image_width": 640, "confirm_images": False,
              "timeout_s": 90, "max_output_tokens": 2000},
    "local": {"enabled": True, "server": "", "model": "", "mmproj": "", "file": "Muse-Glimmer-30B-UD-Q2_K_XL.gguf",
              "gpu_layers": 6, "context": 8192, "port": 0, "threads": 0, "timeout_s": 120,
              "load_timeout_s": 300},
    "notes": {"enabled": True, "summary_minutes": 10, "check_s": 30, "keep_hours": 24,
              "important_keep_days": 365},
    "archive": {"keep_hours": 24, "describe_every_s": 300, "describe_below_chars": 150,
                "max_chars_cloud": 4000, "max_chars_local": 2500},
    "ui": {"language": "en"},
    "network": {"allow": ["127.0.0.1", "localhost"]},
    "exclude": {"titles": [], "processes": []},
}


@dataclass
class Config:
    capture: dict
    audio: dict
    retention: dict
    ask: dict
    budget: dict
    cloud: dict
    local: dict
    notes: dict
    archive: dict
    ui: dict
    network: dict
    exclude: dict
    root: Path

    @property
    def buffer_dir(self) -> Path:
        return self.root / "buffer"

    @property
    def frames_dir(self) -> Path:
        return self.buffer_dir / "frames"

    @property
    def audio_dir(self) -> Path:
        return self.buffer_dir / "audio"

    @property
    def notes_path(self) -> Path:
        """Background notes (block summaries, key moments): local text, kept keep_hours."""
        return self.buffer_dir / "notes.jsonl"

    @property
    def timeline_path(self) -> Path:
        return self.buffer_dir / "timeline.jsonl"

    @property
    def retention_s(self) -> float:
        return float(self.retention["minutes"]) * 60

    def is_excluded(self, title: str, process: str) -> bool:
        t, p = title.lower(), process.lower()
        if any(word.lower() in t for word in self.exclude["titles"]):
            return True
        return any(name.lower() == p for name in self.exclude["processes"])


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config.toml"
    user: dict = {}
    if path.exists():
        user = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    merged = {section: {**values, **user.get(section, {})} for section, values in DEFAULTS.items()}
    cfg = Config(**merged, root=ROOT)
    for d in (cfg.frames_dir, cfg.audio_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


@dataclass
class ApiSettings:
    api_key: str
    base_url: str
    model: str


def load_api_settings() -> ApiSettings:
    load_dotenv(ROOT / ".env")

    def first(*names: str, default: str = "") -> str:
        return next((os.environ[n] for n in names if os.environ.get(n)), default)

    # MODEL_* are the names; META_* are accepted from older .env files.
    key = first("MODEL_API_KEY", "META_API_KEY")
    if not key:
        raise SystemExit(f"No API key found. Put MODEL_API_KEY in {ROOT / '.env'}")
    return ApiSettings(
        api_key=key,
        base_url=first("MODEL_API_BASE_URL", "META_BASE_URL", default="https://api.meta.ai/v1"),
        model=first("MODEL_NAME", "META_MODEL", default="muse-spark-1.3"),
    )
