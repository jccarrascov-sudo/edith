"""Running a recipe for a request: "do the same for Panadería Ñandú", "hazlo para Lazy Comet".

- The recipe and its values are found with no model and no API call: the request is matched
  against the key column of each recipe's sheet ("todos"/"all" = every row).
- Before each use the recipe's proof runs too (the user's own row must still give the user's
  own document), in parallel, so a broken recipe never runs.
- Everything is written to a copy in the recipe's run folder; the panel shows the result with
  what changed from the template in the accent. Enter puts the documents next to the user's
  (an existing file is backed up first); Undo removes them / puts the backups back, refused if
  the user changed a document since.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import docs, sandbox
from .store import Recipe, RecipeStore

ALL = re.compile(r"\b(todos|todas|todo el excel|cada (cliente|fila)|all( of them| rows| clients)?|every(one| row| client))\b",
                 re.IGNORECASE)
LEARN = re.compile(r"^\W*(edith\W+)?(aprende|learn|memoriza|guarda)\b.{0,30}\b(esto|this|eso|that|it|lo que hice|what i did)\b"
                   r"|^\W*(edith\W+)?(aprende esto|learn this)\W*$", re.IGNORECASE)
# A question about a client ("what did Lazy Comet pay?") is not an order.
QUESTION = re.compile(r"^\s*[¿]|\?\s*$|^\s*(what|when|where|who|why|how|which|did|qu[eé]|cu[aá]ndo|d[oó]nde|qui[eé]n|"
                      r"por ?qu[eé]|c[oó]mo|cu[aá]l|cu[aá]nto)\b", re.IGNORECASE)
POLITE = re.compile(r"^\s*[¿]?\s*(can|could|would|will) you\b|^\s*[¿]?\s*(puedes|podr[ií]as|me (puedes|podr[ií]as))\b",
                    re.IGNORECASE)


@dataclass
class Match:
    recipe: Recipe
    keys: list[str]
    ambiguous: list[str] = field(default_factory=list)   # "Lazy Comet": which of these?
    recipes: list[str] = field(default_factory=list)     # two recipes fit: which one? (their names)


@dataclass
class Output:
    key: str
    made: Path            # in the run folder
    final: Path           # where Enter puts it


@dataclass
class Preview:
    recipe: Recipe
    outputs: list[Output]
    seconds: float
    old_text: str                       # the template's text
    new_text: str                       # the first document's text
    failed: list[tuple[str, str]] = field(default_factory=list)   # (key, why)


@dataclass
class Applied:
    recipe_id: str
    files: list[tuple[Path, Path | None, str]]   # (final, backup or None, sha256 written)
    ts: float


def _keys_of(r: Recipe) -> list[str]:
    try:
        _h, rows = docs.sheet_rows(r.sheet)
    except Exception:
        return []
    return [str(row.get(r.key_column)).strip() for row in rows if str(row.get(r.key_column) or "").strip()]


STOP = {"the", "and", "for", "same", "para", "con", "del", "los", "las", "una", "uno", "otra", "otro", "haz",
        "hazlo", "mismo", "misma", "also", "too", "please", "then", "now", "ahora", "tambien", "favor"}


def _words(text: str) -> set[str]:
    return {w for w in re.sub(r"[^\w]+", " ", docs.fold(text)).split() if len(w) >= 3 and w not in STOP}


def match(request: str, recipes: list[Recipe]) -> Match | None:
    """The recipe and the rows a request asks for, or None. Each key scores the request's words
    it contains; keys whose words are inside another key's are dropped ("Lazy Comet Coffee"
    -> only the coffee bar); keys with the same words are ambiguous ("Lazy Comet": which one?)."""
    if QUESTION.search(request) and not POLITE.search(request):
        return None
    asked = _words(request)
    named = [r for r in recipes if _mentions(request, r)]
    if named:                                        # "la carta para...", "en Northwind para...": only those
        recipes = named
    found: list[Match] = []
    for r in recipes:
        keys = _keys_of(r)
        if not keys:
            continue
        if ALL.search(request) and (len(recipes) == 1 or _mentions(request, r)):
            return Match(r, keys)
        hit = {k: frozenset(_words(k) & asked) for k in keys}
        hit = {k: w for k, w in hit.items() if w}
        if not hit:
            continue
        kept = {k: w for k, w in hit.items() if not any(w < o for o in hit.values())}
        groups: dict[frozenset, list[str]] = {}
        for k, w in kept.items():
            groups.setdefault(w, []).append(k)
        keys_out = [g[0] for g in groups.values() if len(g) == 1]
        unclear = [k for g in groups.values() if len(g) > 1 for k in g]
        found.append(Match(r, keys_out, unclear if not keys_out else []))
    if not found:
        return None
    top = max(len(m.keys) for m in found)
    best = [m for m in found if len(m.keys) == top]
    if len(best) > 1 and top:                         # the same rows fit two recipes: ask which
        return Match(best[0].recipe, best[0].keys, recipes=[m.recipe.name for m in best])
    return best[0]


def _mentions(request: str, r: Recipe) -> bool:
    words = _words(r.name) | ({"carta", "letter", "documento", "document", "word"} if r.kind == "excel_to_word"
                              else {"formulario", "form"} | _words(r.extra.get("title", "")))
    return bool(words & _words(request))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Runner:
    def __init__(self, store: RecipeStore):
        self.store = store
        self.last: Applied | None = None
        # (recipe, key, error, line) -> a fixed recipe, or raises escalate.GaveUp; set by the app
        self.escalator = None
        self.allow: list[str] | None = None   # sites where a web recipe may use the network level

    def proof_ok(self, r: Recipe, program: str, work: Path) -> tuple[bool, str]:
        proof = self.store.proof(r)
        sheet, template, result = (proof / Path(r.sheet).name, proof / Path(r.template).name, proof / Path(r.result).name)
        if not (sheet.exists() and template.exists() and result.exists()):
            return False, "the recipe's proof is missing"
        out = sandbox.run(program, r.level, str(sheet), str(template), r.demo_key, str(work / "proof"))
        if not out.ok:
            return False, out.error
        diff = docs.compare(result, out.out)
        return (not diff), (diff[0] if diff else "")

    def prepare(self, m: Match) -> Preview:
        """Run the recipe (and its proof) into a run folder: nothing of the user's is touched."""
        from .lessons import lock

        r = m.recipe
        if r.kind == "excel_to_form":
            return self._prepare_form(m)
        if r.kind == "excel_to_web":
            return self._prepare_web(m)
        with lock(r.id):                     # a lesson being learned finishes first
            pass
        program = self.store.program(r)
        t0 = time.monotonic()
        work = self.store.dir / r.id / "runs" / time.strftime("%Y%m%d-%H%M%S")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            proof = pool.submit(self.proof_ok, r, program, work)
            jobs = {k: pool.submit(sandbox.run, program, r.level, r.sheet, r.template, k, str(work / f"k{i}"))
                    for i, k in enumerate(m.keys[:30])}
            ok, why = proof.result()
            if not ok:
                raise RuntimeError(f"proof: {why}")
            results = {k: job.result() for k, job in jobs.items()}
        outputs, failed = [], []
        for i, k in enumerate(m.keys[:30]):
            o = results[k]
            if not o.ok:                                # a second try (a passing glitch)
                o = sandbox.run(program, r.level, r.sheet, r.template, k, str(work / f"k{i}b"))
            if not o.ok and self.escalator is not None and r.kind == "excel_to_word":   # failed twice: Muse Spark
                from .escalate import GaveUp

                try:
                    r = self.escalator(r, k, o.error, o.line)
                    program = self.store.program(r)
                    o = sandbox.run(program, r.level, r.sheet, r.template, k, str(work / f"k{i}c"))
                except GaveUp as exc:
                    failed.append((k, exc.line))
                    continue
            if o.ok:
                made = Path(o.out)
                outputs.append(Output(k, made, Path(r.out_dir) / made.name))
            else:
                failed.append((k, o.error))
        if not outputs:
            raise RuntimeError(failed[0][1] if failed else "nothing was made")
        seconds = time.monotonic() - t0
        return Preview(r, outputs, seconds, docs.shape(r.template).text(), docs.shape(outputs[0].made).text(), failed)

    def _prepare_form(self, m: Match) -> Preview:
        """Dry runs on the hidden desktop: what would be typed, row by row; nothing is saved."""
        from . import forms

        r = m.recipe
        t0 = time.monotonic()
        work = self.store.dir / r.id / "runs" / time.strftime("%Y%m%d-%H%M%S")
        done, failed = forms.preview(self.store, r, m.keys, work)
        if failed:                                      # a second try, then Muse Spark takes over
            from .escalate import GaveUp

            again, failed = forms.preview(self.store, r, [k for k, _w in failed], work / "retry")
            done += again
            still = []
            for k, why in failed:
                if self.escalator is None:
                    still.append((k, why))
                    continue
                try:
                    r = self.escalator(r, k, why, None)
                    more, bad = forms.preview(self.store, r, [k], work / "escalated")
                    done += more
                    still += bad
                except GaveUp as exc:
                    still.append((k, exc.line))
            failed = still
        if not done:
            raise RuntimeError(failed[0][1] if failed else "nothing was made")
        outputs = []
        for i, (k, values) in enumerate(done):
            made = work / f"k{i}" / "values.txt"
            made.write_text("\n".join(f"{n}: {v}" for n, v in values.items()), encoding="utf-8")
            outputs.append(Output(k, made, Path(r.extra.get("title", r.name))))
        first = outputs[0].made.read_text(encoding="utf-8")
        return Preview(r, outputs, time.monotonic() - t0, "", first, failed)

    def _prepare_web(self, m: Match) -> Preview:
        """What each row will send to the site (from the verified machine); nothing is sent."""
        from . import web

        r = m.recipe
        t0 = time.monotonic()
        work = self.store.dir / r.id / "runs" / time.strftime("%Y%m%d-%H%M%S")
        rows = web.rows_for(r, m.keys)
        if not rows:
            raise RuntimeError("none of those rows is in the sheet")
        outputs = []
        for i, (k, lines) in enumerate(web.preview_text(r, rows)):
            made = work / f"k{i}" / "values.txt"
            made.parent.mkdir(parents=True, exist_ok=True)
            made.write_text(lines, encoding="utf-8")
            outputs.append(Output(k, made, Path(r.extra.get("title", r.name))))
        return Preview(r, outputs, time.monotonic() - t0, "", outputs[0].made.read_text(encoding="utf-8"), [])

    def _apply_web(self, p: Preview) -> None:
        from . import web

        r = p.recipe
        errors = web.send(r, web.rows_for(r, [o.key for o in p.outputs]), self.allow)
        if errors:
            raise RuntimeError(errors[0])
        self.store.record_run(r, {"ts": time.time(), "keys": [o.key for o in p.outputs], "seconds": round(p.seconds, 2),
                                  "ok": True, "applied": True, "failed": 0})
        self.last = None                         # a site's records cannot be taken back from here
        return None

    def _apply_form(self, p: Preview) -> Applied:
        """After Enter: the app's data files are backed up (when known), then the program presses
        the button for real, on the hidden desktop."""
        from . import forms

        r = p.recipe
        work = p.outputs[0].made.parent.parent
        backups = []
        for f in r.extra.get("state", []):
            b = work / "backup" / Path(f).name
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, b)
            backups.append((Path(f), b))
        errors = forms.submit_for_real(self.store, r, [o.key for o in p.outputs], work)
        if errors:
            raise RuntimeError(errors[0])
        self.last = Applied(r.id, [(f, b, _sha(f)) for f, b in backups], time.time()) if backups else None
        self.store.record_run(r, {"ts": time.time(), "keys": [o.key for o in p.outputs], "seconds": round(p.seconds, 2),
                                  "ok": True, "applied": True, "failed": len(p.failed)})
        return self.last

    def apply(self, p: Preview) -> Applied:
        if p.recipe.kind == "excel_to_form":
            return self._apply_form(p)
        if p.recipe.kind == "excel_to_web":
            return self._apply_web(p)
        files = []
        backups = p.outputs[0].made.parent.parent / "backup"
        for o in p.outputs:
            backup = None
            if o.final.exists():
                backups.mkdir(parents=True, exist_ok=True)
                backup = backups / o.final.name
                shutil.copy2(o.final, backup)
            o.final.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(o.made, o.final)
            files.append((o.final, backup, _sha(o.final)))
        self.last = Applied(p.recipe.id, files, time.time())
        self.store.record_run(p.recipe, {"ts": time.time(), "keys": [o.key for o in p.outputs],
                                         "seconds": round(p.seconds, 2), "ok": True, "applied": True,
                                         "failed": len(p.failed)})
        return self.last

    def undo(self) -> tuple[bool, str]:
        a = self.last
        if a is None:
            return False, "nothing"
        for final, _backup, sha in a.files:
            if final.exists() and _sha(final) != sha:
                return False, "changed_since"
        for final, backup, _sha_ in a.files:
            if backup is not None:
                shutil.copy2(backup, final)
            else:
                final.unlink(missing_ok=True)
        self.last = None
        return True, ""
