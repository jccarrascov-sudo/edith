"""Edit from memory, the thinking part: what to write, where it came from, and its preview.

"Put in my notes what the teacher explained", "reply to this email with the data I saw
earlier": the request, the memory (text first, see ask.py) and the target field's current
text go to Muse Spark (or to the local model, see answer.py), which returns JSON:
    {"mode": "append" | "replace", "text": "...", "source_time": "HH:MM:SS", "source": "..."}
or {"error": "..."} when the memory does not hold what is asked. Nothing is written here:
the panel shows a preview (new text in the accent, removed text struck through in gray) and
only Enter applies it."""

from __future__ import annotations

import difflib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime

EDIT = re.compile(
    r"\b(put|add|write|paste|insert|copy|note|jot|reply|answer|respond|fill in|complete|draft)\b.*"
    r"\b(notes?|email|e-mail|mail|message|doc(ument)?|here|this|it|them|field|box|reply)\b"
    r"|^\s*(reply|answer|respond)\b"
    r"|\b(pasa|pon|ponle|agrega|a[nñ]ade|escribe|copia|anota|apunta|responde|contesta|completa|redacta|inserta)\b.*"
    r"\b(apuntes|notas?|correo|mail|mensaje|documento|aqu[ií]|esto|este|esta|campo|respuesta)\b"
    r"|^\s*(responde|contesta)\b", re.IGNORECASE)

EDIT_PROMPT = """\
You are Edith, a desktop assistant with a short memory of the user's computer. The user asks you
to write something into the text field they are in right now (shown below with its current
text), using what happened in the last minutes (also below: screen text, audio transcript,
notes, maybe screenshots).

Reply with JSON only, no code fence:
{"mode": "append" or "replace", "text": "the text to write", "source_time": "HH:MM:SS of the
moment in the memory the content comes from", "source": "a few words: where it came from"}
- "append" adds text at the end of the field (notes, a reply under a quoted email, a list).
  "replace" gives the field's whole new text (only when the user asks to rewrite or correct it).
- Use only facts from the memory; never invent names, numbers or dates. Keep the user's
  language and the field's style (a reply is a short email in the language of the thread;
  notes are short lines). No greeting or signature unless the field is a reply.
- Write as the user, to whoever will read it (the person in the thread, or the user's own
  notes). Never mention Edith, a memory, a screen, or "what I saw"; do not echo the request's
  wording; just state the facts. A reply to "Hi! Can you send me the amount?" from Ana is like
  "Hi Ana,\\n\\nThe amount is PEN 1,250.50.\\n\\nBest,"; notes are like "- Exam: Friday 13".
- If the memory does not contain what is asked, reply {"error": "what is missing, briefly"}."""


@dataclass
class Proposal:
    mode: str
    text: str
    source: str
    source_ts: float | None
    frame: str | None = None        # buffer path of the screenshot it is based on


QUESTION = re.compile(r"^\s*(what|when|where|who|why|how|which|did|do|does|is|are|was|were|can|could|"
                      r"qu[eé]|cu[aá]ndo|d[oó]nde|qui[eé]n|por ?qu[eé]|c[oó]mo|cu[aá]l|hubo)\b|\?\s*$|^\s*¿",
                      re.IGNORECASE)


def is_edit(request: str) -> bool:
    """An order to write something ("put in my notes...", "reply to this email..."), not a question."""
    return bool(EDIT.search(request)) and not QUESTION.search(request)


def target_block(title: str, process: str, kind: str, name: str, text: str, limit: int = 3000) -> str:
    shown = text if len(text) <= limit else "(…)\n" + text[-limit:]
    return (f'The field the user is in: {kind.replace("Control", "").lower()} "{name}" in {process}: "{title}".\n'
            f"Its current text (between the lines):\n-----\n{shown}\n-----")


def parse(reply: str, now: float) -> Proposal | str:
    """A proposal, or the reason there is none."""
    m = re.search(r"\{.*\}", reply, re.DOTALL)
    if not m:
        return "no_json"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return "no_json"
    if data.get("error"):
        return str(data["error"])[:200]
    text = str(data.get("text") or "").strip()
    mode = data.get("mode") if data.get("mode") in ("append", "replace") else "append"
    if not text:
        return "empty"
    return Proposal(mode, text, str(data.get("source") or "")[:80], _clock(str(data.get("source_time") or ""), now))


def _clock(hms: str, now: float) -> float | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$", hms)
    if not m:
        return None
    d = datetime.fromtimestamp(now).replace(hour=int(m.group(1)) % 24, minute=int(m.group(2)),
                                            second=int(m.group(3) or 0), microsecond=0)
    ts = d.timestamp()
    return ts if ts <= now + 60 else ts - 86400


def source_frame(frames: list[dict], ts: float | None) -> dict | None:
    """The screenshot on screen at the source moment: the last one saved at or before it."""
    if not frames:
        return None
    if ts is None:
        return frames[-1]
    before = [f for f in frames if f["ts"] <= ts + 2]
    return before[-1] if before else frames[0]


def preview_html(old: str, p: Proposal, accent: str, muted: str, text_color: str, context_lines: int = 2) -> str:
    """The edit as it will look: new text in the accent, removed text struck through in gray."""
    esc = lambda s: html.escape(s).replace("\n", "<br>")  # noqa: E731
    if p.mode == "append":
        tail = [ln for ln in old.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()][-context_lines:]
        head = f'<span style="color:{muted};">{esc(chr(10).join(tail))}</span><br>' if tail else ""
        return head + f'<span style="color:{accent};">{esc(p.text)}</span>'
    a, b = re.findall(r"\S+|\s+", old), re.findall(r"\S+|\s+", p.text)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(f'<span style="color:{text_color};">{esc("".join(a[i1:i2]))}</span>')
            continue
        if i2 > i1:
            out.append(f'<span style="color:{muted}; text-decoration:line-through;">{esc("".join(a[i1:i2]))}</span>')
        if j2 > j1:
            out.append(f'<span style="color:{accent};">{esc("".join(b[j1:j2]))}</span>')
    return "".join(out)
