"""Edith remembers the conversation and the person, like a friend would.

- The thread: the last exchanges of the last hours go with every request, so "and why?" or
  "put that in my notes" are understood from what was said just before.
- Past conversations: every exchange is kept (`companion.keep_days`, default a year) and the
  ones that match a question ("remember what I asked you yesterday about recursion?") are
  found like the day archive is searched.
- What Edith knows about the user: lasting facts (name, studies, work, people, goals, how they
  like answers). "Remember that ..." saves one at once and "forget that ..." removes it; after
  each exchange the local model, in the background, notes anything new worth remembering.
  They are used naturally, never recited.
All of it stays on this PC in buffer/ (git-ignored) and goes with "Delete everything now"; a
request sends only the recent thread, what is known about the user and matching past lines."""

from __future__ import annotations

import json
import logging
import re
import threading
import time

from ..config import Config
from ..memory.archive import fold, rank

log = logging.getLogger(__name__)

REMEMBER = re.compile(r"^\s*[¿¡]?\s*(please\s+)?(remember|recuerda|acu[eé]rdate( de)?|no olvides|ten en cuenta)"
                      r"(\s+que|\s+that)?\s*[:,]?\s+(?P<fact>.{3,})$", re.IGNORECASE)
FORGET = re.compile(r"^\s*[¿¡]?\s*(please\s+)?(forget|olvida|olv[ií]date( de)?)(\s+que|\s+that)?\s*[:,]?\s+(?P<fact>.{3,})$",
                    re.IGNORECASE)

# "remember what I asked you yesterday?", "recuerda lo que vimos en clase": questions about the
# memory, not something to remember.
_ASKS = re.compile(r"^(when|what|where|who|how|which|why|if|whether|the time|cu[aá]ndo|qu[eé]|d[oó]nde|"
                   r"qui[eé]n|c[oó]mo|cu[aá]l|por qu[eé]|si|lo que|la vez|el momento)\b", re.IGNORECASE)


def _fact(pattern: re.Pattern, request: str) -> str | None:
    m = pattern.match(request)
    if not m or "?" in request or request.lstrip().startswith("¿") or _ASKS.match(m.group("fact")):
        return None
    return m.group("fact")


def to_remember(request: str) -> str | None:
    """The fact in "remember that ..." (None if it is a question about the past)."""
    return _fact(REMEMBER, request)


def to_forget(request: str) -> str | None:
    return _fact(FORGET, request)


LEARN_PROMPT ="""You keep a short list of lasting facts about a person you talk with: their name, what they
study or do, people and places in their life, goals, likes and dislikes, and how they like to be
answered. Read what the person just said and list only NEW lasting facts they said about
THEMSELVES (not questions, not things about the world). Write each as a short sentence in their
language, in the third person ("Se llama Camila", "Estudia en la PUCP", "Prefers short answers").
Reply with a JSON array of strings only, [] if there is nothing new.
Already known:
{known}"""

# Only messages where the person talks about themselves are worth a model's time.
ABOUT_SELF = re.compile(
    r"\b(i am|i'?m|my|me|i (study|work|live|like|love|hate|prefer|have|want|need)|call me|"
    r"soy|me llamo|mi|mis|estudio|trabajo|vivo|me gusta|me encanta|odio|prefiero|tengo|quiero|necesito|"
    r"ll[aá]mame|llevo|curso|mi nombre)\b", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", fold(text)).strip()


class Conversation:
    def __init__(self, cfg: Config):
        self.path = cfg.buffer_dir / "conversation.jsonl"
        self.c = cfg.companion
        self._lock = threading.Lock()

    def add(self, question: str, answer: str, kind: str = "answer") -> None:
        if not question.strip() or not answer.strip():
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "q": question.strip(), "a": answer.strip()[:4000],
                                    "kind": kind}, ensure_ascii=False) + "\n")

    def read(self) -> list[dict]:
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        keep = time.time() - float(self.c["keep_days"]) * 86400
        return [t for t in out if t["ts"] >= keep]

    def recent(self) -> list[dict]:
        """The thread: the last `history_turns` exchanges of the last `history_hours`."""
        since = time.time() - float(self.c["history_hours"]) * 3600
        return [t for t in self.read() if t["ts"] >= since][-int(self.c["history_turns"]):]

    def recall(self, question: str, max_chars: int = 1500) -> list[dict]:
        """Older exchanges (not in the thread) that match the question."""
        thread = {t["ts"] for t in self.recent()}
        older = [{"ts": t["ts"], "text": f'User: {t["q"]}\nEdith: {t["a"]}'} for t in self.read() if t["ts"] not in thread]
        return rank(older, question, max_chars=max_chars, neighbours=0)

    def prune(self) -> None:
        keep = self.read()
        with self._lock:
            if self.path.exists():
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in keep), encoding="utf-8")
                tmp.replace(self.path)


class Profile:
    def __init__(self, cfg: Config):
        self.path = cfg.buffer_dir / "profile.jsonl"
        self._lock = threading.Lock()

    def facts(self) -> list[str]:
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return []
        out: list[str] = []
        for line in lines:
            try:
                out.append(json.loads(line)["fact"])
            except (json.JSONDecodeError, KeyError):
                continue
        return out

    def add(self, fact: str, source: str) -> bool:
        fact = fact.strip().rstrip(".") + "."
        known = {_norm(f) for f in self.facts()}
        if len(fact) < 4 or _norm(fact) in known:
            return False
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "fact": fact, "source": source}, ensure_ascii=False) + "\n")
        return True

    def forget(self, what: str) -> int:
        """Remove every fact that contains the words of `what`."""
        words = [w for w in _norm(what).split() if len(w) > 2]
        if not words:
            return 0
        keep, gone = [], 0
        for f in self.facts():
            if all(w in _norm(f) for w in words):
                gone += 1
            else:
                keep.append(f)
        if gone:
            with self._lock:
                self.path.write_text("".join(json.dumps({"ts": time.time(), "fact": f, "source": "kept"},
                                                        ensure_ascii=False) + "\n" for f in keep), encoding="utf-8")
        return gone


def about_user(facts: list[str], past: list[dict]) -> str:
    """The part of a prompt with what Edith knows about the user (empty if nothing)."""
    parts = []
    if facts:
        parts.append("What you know about the user from earlier conversations (use it naturally, like a "
                     "friend would; never recite it):\n" + "\n".join(f"- {f}" for f in facts[-60:]))
    if past:
        parts.append("Earlier conversations that may be relevant:\n" + "\n".join(p["text"] for p in past))
    return "\n\n".join(parts)


def history_messages(thread: list[dict]) -> list[dict]:
    out = []
    for t in thread:
        out.append({"role": "user", "content": t["q"]})
        out.append({"role": "assistant", "content": t["a"]})
    return out


def history_text(thread: list[dict]) -> str:
    return "\n".join(f'User: {t["q"]}\nEdith: {t["a"]}' for t in thread)


def learn(profile: Profile, generate, said: str) -> int:
    """New lasting facts in what the person said (only their words: never Edith's answer, which
    once taught it that "she is called Edith"). Returns how many were saved."""
    if not ABOUT_SELF.search(said):
        return 0
    known = "\n".join(f"- {f}" for f in profile.facts()[-60:]) or "(nothing yet)"
    try:
        reply = generate(LEARN_PROMPT.format(known=known), said)
    except Exception:
        log.debug("learning from the exchange failed", exc_info=True)
        return 0
    m = re.search(r"\[.*\]", reply or "", re.DOTALL)
    try:
        items = json.loads(m.group(0)) if m else []
    except json.JSONDecodeError:
        return 0
    return sum(profile.add(str(i), "learned") for i in items if isinstance(i, str))
