"""Noticing a task done twice, to offer it as a recipe ("¿Guardo esto como receta?").

- Word from Excel: two documents saved today that came from the same template (each is a
  close variant of an older document they were both opened from) and are not a recipe yet.
- A desktop form: the same fields of the same app filled and submitted twice (from the
  timeline that the memory already keeps: field values and clicks, never the keyboard).
The suggestion is one line in the panel, never a pop-up; "No" is remembered so it is not asked
again; the whole thing can be turned off in the options.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import docs
from .store import RecipeStore


@dataclass
class Suggestion:
    kind: str                      # "excel_to_word" | "excel_to_form"
    name: str                      # what the panel calls it ("Payment reminder")
    signature: str                 # stable id, to remember a "No"
    template: Path | None = None   # Word: the template both documents came from
    result: Path | None = None     # Word: the newest of them (the demonstration)
    process: str = ""              # form: the app
    fields: tuple[str, ...] = ()


def word_twice(recent: list[tuple[Path, float]], recipes: list, hours: float = 12) -> Suggestion | None:
    since = time.time() - hours * 3600
    words = [p for p, _t in recent if p.suffix.lower() == ".docx" and not p.name.startswith("~$")]
    saved = sorted({p for p in words if p.stat().st_mtime >= since}, key=lambda p: -p.stat().st_mtime)
    known = {docs.fold(str(Path(r.template))) for r in recipes}
    texts: dict[Path, str] = {}

    def text(p: Path) -> str:
        if p not in texts:
            try:
                texts[p] = docs.shape(p).text()
            except Exception:
                texts[p] = ""
        return texts[p]

    candidates = set(words) | {q for p in saved for q in p.parent.glob("*.docx") if not q.name.startswith("~$")}
    for i, a in enumerate(saved):
        for b in saved[i + 1:]:
            if a.parent != b.parent or docs.similarity(text(a), text(b)) < 0.6:
                continue
            older = [t for t in candidates if t not in (a, b) and t.stat().st_mtime < min(a.stat().st_mtime, b.stat().st_mtime)]
            scored = sorted(((docs.similarity(text(t), text(a)) + docs.similarity(text(t), text(b)), t) for t in older),
                            reverse=True)
            if not scored or scored[0][0] < 0.8:
                continue
            template = scored[0][1]
            if docs.fold(str(template)) in known:
                continue
            return Suggestion("excel_to_word", template.stem, f"word:{docs.fold(str(template))}", template, a)
    return None


def form_twice(events: list[dict], recipes: list) -> Suggestion | None:
    from ..learn.segment import find_demos

    seen: dict[tuple, int] = {}
    for s in find_demos(events):
        if not s.submit:
            continue
        key = (s.process.lower(), tuple(sorted(s.final_values)), s.submit["element"]["name"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= 2:
            process, fields, button = key
            if any(getattr(r, "kind", "") == "excel_to_form" and docs.fold(getattr(r, "template", "")) == process
                   for r in recipes):
                continue
            title = (s.fields[-1].get("window") or process).split(" - ")[0]
            return Suggestion("excel_to_form", title, f"form:{process}:{','.join(fields)}", process=process, fields=fields)
    return None


class Detector:
    def __init__(self, cfg, store: RecipeStore):
        self.cfg = cfg
        self.store = store
        self.path = store.dir.parent / "suggestions.json"

    def declined(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text(encoding="utf-8")).get("declined", []))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def decline(self, s: Suggestion) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"declined": sorted(self.declined() | {s.signature})}), encoding="utf-8")

    def check(self, recent: list[tuple[Path, float]] | None = None, events: list[dict] | None = None) -> Suggestion | None:
        from .learn import recent_files

        recipes = self.store.all()
        no = self.declined()
        try:
            s = word_twice(recent_files(12 * 60) if recent is None else recent, recipes)
        except Exception:
            s = None
        if s and s.signature not in no:
            return s
        if events is None:
            from ..memory.timeline import Timeline

            try:
                events = Timeline(self.cfg).read()
            except Exception:
                events = []
        s = form_twice(events, recipes)
        return s if s and s.signature not in no else None
