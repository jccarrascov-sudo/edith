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
    best: Match | None = None
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
        m = Match(r, keys_out, unclear if not keys_out else [])
        if best is None or len(m.keys) > len(best.keys):
            best = m
    return best


def _mentions(request: str, r: Recipe) -> bool:
    return bool(_words(r.name + " " + Path(r.template).stem) & _words(request))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Runner:
    def __init__(self, store: RecipeStore):
        self.store = store
        self.last: Applied | None = None

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
        r = m.recipe
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
            outputs, failed = [], []
            for k, job in jobs.items():
                o = job.result()
                if o.ok:
                    made = Path(o.out)
                    outputs.append(Output(k, made, Path(r.out_dir) / made.name))
                else:
                    failed.append((k, o.error))
        if not outputs:
            raise RuntimeError(failed[0][1] if failed else "nothing was made")
        seconds = time.monotonic() - t0
        return Preview(r, outputs, seconds, docs.shape(r.template).text(), docs.shape(outputs[0].made).text(), failed)

    def apply(self, p: Preview) -> Applied:
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
