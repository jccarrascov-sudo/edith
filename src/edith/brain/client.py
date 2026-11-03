"""The single gateway to the Meta Model API: model guard, session cost meter and budget brake.

Every paid call goes through `MetaClient`, which
  1. refuses any `-contributor` model variant,
  2. refuses to call once the session cost passed the configured limit (default $5),
  3. records the real usage of each call in state/costs.jsonl (local, git-ignored).
Local answers are recorded in the same ledger with a cost of $0, so the metrics view can show
latency for both. `Ledger` sums it per day and per month for the monthly limit.

A session ends after `budget.session_idle_hours` without calls, or with `edith cost --new`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
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


def prices_from(cfg: Config) -> dict:
    """Muse Spark's prices from config.toml ([budget] *_per_m, USD per million tokens)."""
    b = cfg.budget
    spark = {"input": b["input_per_m"] / 1e6, "cached": b["cached_per_m"] / 1e6, "output": b["output_per_m"] / 1e6}
    return {m: spark for m in TOKEN_PRICES}


def token_cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int,
               prices: dict | None = None) -> float:
    p = (prices or TOKEN_PRICES).get(model)
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
    def __init__(self, limit_usd: float, idle_hours: float, state_dir: Path = STATE_DIR,
                 prices: dict | None = None):
        self.limit = limit_usd
        self.prices = prices
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
                "(python -m edith cost --new).")

    def record(self, model: str, purpose: str, input_tokens: int = 0, cached_tokens: int = 0,
               output_tokens: int = 0, images: int = 0, latency_s: float | None = None) -> float:
        if model in PER_IMAGE_PRICES:
            cost = images * PER_IMAGE_PRICES[model]
        else:
            cost = token_cost(model, input_tokens, cached_tokens, output_tokens, self.prices)
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
        append_ledger(self.ledger, {"ts": s.last_call, "session": s.id, "model": model, "purpose": purpose,
                                     "input": input_tokens, "cached": cached_tokens, "output": output_tokens,
                                     "images": images, "cost": round(cost, 6),
                                     **({"latency_s": round(latency_s, 2)} if latency_s is not None else {})})
        return cost

    def summary(self) -> str:
        s = self.session
        return (f"session {s.id}: {s.calls} calls, ${s.cost:.4f} of ${self.limit:.2f} "
                f"(in {s.input_tokens:,} / cached {s.cached_tokens:,} / out {s.output_tokens:,} tokens, "
                f"{s.images} images)")


def append_ledger(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class Ledger:
    """Sums of state/costs.jsonl: today, this month, and a daily series for the metrics view."""

    def __init__(self, path: Path = LEDGER):
        self.path = path

    def entries(self, since: float = 0.0) -> list[dict]:
        out = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("ts", 0) >= since:
                        out.append(e)
        except FileNotFoundError:
            pass
        return out

    @staticmethod
    def month_start(now: float) -> float:
        d = datetime.fromtimestamp(now)
        return datetime(d.year, d.month, 1).timestamp()

    def month_total(self, now: float | None = None) -> float:
        now = now or time.time()
        return sum(e.get("cost", 0.0) for e in self.entries(self.month_start(now)) if e["ts"] <= now)

    def daily(self, days: int = 30, now: float | None = None) -> list[tuple[float, float]]:
        """(midnight of each day, cost that day), oldest first, days with no calls included."""
        now = now or time.time()
        d = datetime.fromtimestamp(now)
        today = datetime(d.year, d.month, d.day).timestamp()
        starts = [today - 86400 * i for i in range(days - 1, -1, -1)]
        sums = dict.fromkeys(starts, 0.0)
        for e in self.entries(starts[0]):
            if e["ts"] <= now:
                sums[max(s for s in starts if s <= e["ts"])] += e.get("cost", 0.0)
        return list(sums.items())

    def latencies(self, limit: int = 40) -> list[tuple[float, float]]:
        """(time, seconds) of the last answers, cloud and local."""
        return [(e["ts"], e["latency_s"]) for e in self.entries() if "latency_s" in e][-limit:]


class MetaClient:
    def __init__(self, cfg: Config):
        api = load_api_settings()
        check_model(api.model)
        self.model = api.model
        self.openai = OpenAI(api_key=api.api_key, base_url=api.base_url,
                             timeout=float(cfg.cloud["timeout_s"]), max_retries=1)
        self.meter = CostMeter(float(cfg.budget["session_limit_usd"]), float(cfg.budget["session_idle_hours"]),
                               prices=prices_from(cfg))

    def chat(self, purpose: str, model: str | None = None, **kwargs):
        """chat.completions.create with the guard, the brake and cost accounting."""
        model = model or self.model
        check_model(model)
        self.meter.check()
        response = self.openai.chat.completions.create(model=model, **kwargs)
        u = response.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        cost = self.meter.record(model, purpose, u.prompt_tokens, cached, u.completion_tokens)
        response.edith_cost = cost
        return response

    def chat_stream(self, purpose: str, on_token=None, model: str | None = None, **kwargs):
        """Streamed chat: text pieces go to on_token as they arrive. Returns (text, usage, cost)."""
        model = model or self.model
        check_model(model)
        self.meter.check()
        t0 = time.monotonic()
        parts, usage = [], None
        for chunk in self.openai.chat.completions.create(model=model, stream=True,
                                                          stream_options={"include_usage": True}, **kwargs):
            if chunk.usage:
                usage = chunk.usage
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                parts.append(delta.content)
                if on_token:
                    on_token(delta.content)
        cost = 0.0
        if usage:
            cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
            cost = self.meter.record(model, purpose, usage.prompt_tokens, cached, usage.completion_tokens,
                                     latency_s=time.monotonic() - t0)
        return "".join(parts), usage, cost

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
