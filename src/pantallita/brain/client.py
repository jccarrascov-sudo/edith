"""The single gateway to the Meta Model API: model guard, session cost meter and budget brake.

Every paid call goes through `MetaClient`, which
  1. refuses any `-contributor` model variant,
  2. refuses to call once the session cost passed the configured limit (default $5),
  3. records the real usage of each call in state/costs.jsonl (local, git-ignored).

A session ends after `budget.session_idle_hours` without calls, or with `pantallita cost --new`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

from ..config import ROOT, Config, load_api_settings

log = logging.getLogger(__name__)

# USD, from https://dev.meta.ai/docs/pricing-rate-limits (checked 2026-10-31).
TOKEN_PRICES = {"muse-spark-1.3": {"input": 1.25e-6, "cached": 0.15e-6, "output": 4.25e-6}}
PER_IMAGE_PRICES = {"sam-3.1": 2.50 / 1000, "muse-image-1.0": 0.01}

STATE_DIR = ROOT / "state"
LEDGER = STATE_DIR / "costs.jsonl"
SESSION = STATE_DIR / "session.json"


class BudgetExceeded(RuntimeError):
    pass


def check_model(model: str) -> None:
    if "-contributor" in model:
        raise ValueError(f"Model {model!r} is not allowed (no -contributor variants).")


def token_cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    p = TOKEN_PRICES.get(model)
    if p is None:
        raise KeyError(f"No price for {model!r}; add it to TOKEN_PRICES before using it.")
    uncached = max(0, input_tokens - cached_tokens)  # cached tokens are a subset of input tokens
    return uncached * p["input"] + cached_tokens * p["cached"] + output_tokens * p["output"]


@dataclass
class Session:
    id: str
    started: float
    last_call: float
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    cost: float = 0.0
    by_model: dict = field(default_factory=dict)


class CostMeter:
    def __init__(self, limit_usd: float, idle_hours: float, state_dir: Path = STATE_DIR):
        self.limit = limit_usd
        self.idle_s = idle_hours * 3600
        self.ledger = state_dir / "costs.jsonl"
        self.session_path = state_dir / "session.json"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.session = self._load()

    def _load(self) -> Session:
        now = time.time()
        if self.session_path.exists():
            try:
                s = Session(**json.loads(self.session_path.read_text(encoding="utf-8")))
                if now - s.last_call < self.idle_s:
                    return s
            except (json.JSONDecodeError, TypeError):
                pass
        return self.new_session()

    def new_session(self) -> Session:
        now = time.time()
        self.session = Session(id=uuid.uuid4().hex[:8], started=now, last_call=now)
        self._save()
        return self.session

    def _save(self) -> None:
        self.session_path.write_text(json.dumps(self.session.__dict__, indent=1), encoding="utf-8")

    def check(self) -> None:
        if self.session.cost >= self.limit:
            raise BudgetExceeded(
                f"Session cost ${self.session.cost:.4f} reached the ${self.limit:.2f} limit. "
                "Raise budget.session_limit_usd in config.toml or start a new session "
                "(python -m pantallita cost --new).")

    def record(self, model: str, purpose: str, input_tokens: int = 0, cached_tokens: int = 0,
               output_tokens: int = 0, images: int = 0) -> float:
        if model in PER_IMAGE_PRICES:
            cost = images * PER_IMAGE_PRICES[model]
        else:
            cost = token_cost(model, input_tokens, cached_tokens, output_tokens)
        s = self.session
        s.calls += 1
        s.input_tokens += input_tokens
        s.cached_tokens += cached_tokens
        s.output_tokens += output_tokens
        s.images += images
        s.cost += cost
        s.last_call = time.time()
        s.by_model[model] = round(s.by_model.get(model, 0.0) + cost, 6)
        self._save()
        with open(self.ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": s.last_call, "session": s.id, "model": model, "purpose": purpose,
                                "input": input_tokens, "cached": cached_tokens, "output": output_tokens,
                                "images": images, "cost": round(cost, 6)}) + "\n")
        return cost

    def summary(self) -> str:
        s = self.session
        return (f"session {s.id}: {s.calls} calls, ${s.cost:.4f} of ${self.limit:.2f} "
                f"(in {s.input_tokens:,} / cached {s.cached_tokens:,} / out {s.output_tokens:,} tokens, "
                f"{s.images} images)")


class MetaClient:
    def __init__(self, cfg: Config):
        api = load_api_settings()
        check_model(api.model)
        self.model = api.model
        self.openai = OpenAI(api_key=api.api_key, base_url=api.base_url)
        self.meter = CostMeter(float(cfg.budget["session_limit_usd"]), float(cfg.budget["session_idle_hours"]))

    def chat(self, purpose: str, model: str | None = None, **kwargs):
        """chat.completions.create with the guard, the brake and cost accounting."""
        model = model or self.model
        check_model(model)
        self.meter.check()
        response = self.openai.chat.completions.create(model=model, **kwargs)
        u = response.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        cost = self.meter.record(model, purpose, u.prompt_tokens, cached, u.completion_tokens)
        response.pantallita_cost = cost
        return response

    def segment(self, purpose: str, image_data_url: str, prompt: str, model: str = "sam-3.1") -> list[dict]:
        """SAM: stream one image segmentation; returns the raw Responses events (see vision/sam.py)."""
        check_model(model)
        self.meter.check()
        content = [{"type": "input_text", "text": prompt},
                   {"type": "input_image", "image_url": image_data_url}]
        with self.openai.responses.stream(
                model=model, input=[{"type": "message", "role": "user", "content": content}]) as stream:
            events = [event.model_dump(warnings=False) for event in stream]
        self.meter.record(model, purpose, images=1)
        return events

    def generate_image(self, purpose: str, prompt: str, model: str = "muse-image-1.0", **kwargs):
        check_model(model)
        self.meter.check()
        response = self.openai.images.generate(model=model, prompt=prompt, n=1, **kwargs)
        self.meter.record(model, purpose, images=len(response.data))
        return response
