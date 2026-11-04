"""The recipe memory: state/recipes/<id>/recipe.json + program.py (+ the user's demo files).

Persistent, in Edith's own folder, listed in the panel's options where each one can be
deleted. "Delete everything now" keeps recipes, like the verified machines (they are
procedures the user confirmed, not captured screen data)."""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import Config
from .docs import fold


@dataclass
class Recipe:
    id: str
    name: str                     # "Payment reminder from invoices.xlsx"
    kind: str                     # "excel_to_word"
    level: int                    # the official level (1 = direct data, 2 = the app's API)
    steps: list[str]              # what it does, in plain words
    sheet: str                    # the user's files (paths)
    template: str
    result: str                   # the document the user made by hand
    key_column: str               # the column that tells the rows apart (the parameter)
    demo_key: str                 # its value in the user's demonstration
    out_dir: str                  # where new documents go (the user's own folder)
    verified: dict = field(default_factory=dict)   # {"level": 1, "attempts": 2, "rows": [...], "ts": ...}
    manual_s: float = 0.0         # how long the user took by hand
    created: float = 0.0
    runs: list[dict] = field(default_factory=list)  # {"ts", "keys", "seconds", "ok", "applied"}
    extra: dict = field(default_factory=dict)       # forms: window title, fields, submit button, points

    @property
    def folder_name(self) -> str:
        return self.id


class RecipeStore:
    def __init__(self, cfg: Config):
        self.dir = cfg.root / "state" / "recipes"

    def all(self) -> list[Recipe]:
        out = []
        if not self.dir.exists():
            return out
        for f in sorted(self.dir.glob("*/recipe.json")):
            try:
                out.append(Recipe(**json.loads(f.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError):
                continue
        return sorted(out, key=lambda r: r.created)

    def program(self, r: Recipe) -> str:
        return (self.dir / r.id / "program.py").read_text(encoding="utf-8")

    def save(self, r: Recipe, program: str | None = None) -> None:
        d = self.dir / r.id
        d.mkdir(parents=True, exist_ok=True)
        if program is not None:
            (d / "program.py").write_text(program, encoding="utf-8")
        tmp = d / "recipe.tmp"
        tmp.write_text(json.dumps(asdict(r), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(d / "recipe.json")

    def keep_demo(self, r: Recipe) -> None:
        """A copy of the user's demonstration (sheet, template, result): the recipe's proof,
        re-checked before use even if the user's own files change later."""
        proof = self.dir / r.id / "proof"
        proof.mkdir(parents=True, exist_ok=True)
        for src in (r.sheet, r.template, r.result):
            shutil.copy2(src, proof / Path(src).name)

    def proof(self, r: Recipe) -> Path:
        return self.dir / r.id / "proof"

    def delete(self, recipe_id: str) -> bool:
        d = self.dir / recipe_id
        if d.exists() and d.parent == self.dir:
            shutil.rmtree(d)
            return True
        return False

    def record_run(self, r: Recipe, run: dict) -> None:
        run = {"level": r.level, **run}                 # the level it ran at (a recipe can move later)
        r.runs = (r.runs + [run])[-50:]
        self.save(r)

    @staticmethod
    def new_id(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")[:40] or "recipe"
        return f"{slug}-{int(time.time())}"
