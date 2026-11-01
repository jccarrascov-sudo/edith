"""Configuration: config.toml (non-secret settings) + .env (API credentials)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# src/pantallita/config.py -> repo root
ROOT = Path(os.environ.get("PANTALLITA_HOME", Path(__file__).resolve().parents[2]))

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
    "budget": {"session_limit_usd": 5.0, "session_idle_hours": 3},
    "exclude": {"titles": [], "processes": []},
}


@dataclass
class Config:
    capture: dict
    audio: dict
    retention: dict
    ask: dict
    budget: dict
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

    key = first("META_API_KEY", "MODEL_API_KEY", "OPENAI_API_KEY")
    if not key:
        raise SystemExit(f"No API key found. Put META_API_KEY in {ROOT / '.env'}")
    return ApiSettings(
        api_key=key,
        base_url=first("META_BASE_URL", "MODEL_API_BASE_URL", "OPENAI_BASE_URL",
                       default="https://api.meta.ai/v1"),
        model=first("META_MODEL", "MODEL_NAME", default="muse-spark-1.3"),
    )
