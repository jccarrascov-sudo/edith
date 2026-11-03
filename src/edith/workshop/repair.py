"""Repair by intent: when the target page changes, relocate only the fields that moved.

Every fill_field block has a contract ("put the Amount column in the field that means
amount"). When its field is no longer on the page, the block is pointed at the field that
now carries that meaning, chosen among the fields no other block uses:
  - exactly one unused field shares a word with the old label or the column -> that one;
  - two or more share it (e.g. "Amount - subtotal" and "Amount - total") -> ambiguous: Edith
    does NOT guess, it asks to be shown one;
  - none shares a word but only one field is left -> that one;
  - otherwise Muse Spark reads the contract and the candidates, and may answer "ambiguous".
Afterwards the machine is verified again by equivalence; nothing is trusted unverified.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field

STOPWORDS = {"the", "and", "for", "del", "los", "las", "una", "por"}


@dataclass
class Relocation:
    block_id: str
    column: str
    old_label: str
    new_label: str | None = None                       # None = ambiguous
    candidates: list[str] = field(default_factory=list)
    how: str = ""


def words(text: str) -> set[str]:
    """Meaningful words of a label: units in parentheses ("(PEN)") and short words dropped."""
    text = re.sub(r"\([^)]*\)", " ", text or "").lower()
    return {w for w in re.findall(r"[a-záéíóúñü]+", text) if len(w) >= 3 and w not in STOPWORDS}


def form_fields(url: str, timeout_ms: int = 8000) -> list[dict]:
    """The form's fillable fields as the page shows them now (label, name, placeholder).
    Read-only: the page is opened, nothing is typed or sent."""
    from playwright.sync_api import sync_playwright

    script = """els => els.filter(e => !['hidden', 'submit', 'button', 'reset', 'image'].includes((e.type || '').toLowerCase()))
      .map(e => ({label: ((e.labels && e.labels[0] && e.labels[0].innerText) || e.getAttribute('aria-label')
                          || e.placeholder || e.name || '').trim(),
                  name: e.name || '', placeholder: e.placeholder || ''}))"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url)
            return page.eval_on_selector_all("form input, form textarea, form select", script)
        finally:
            browser.close()


def missing_blocks(plan: dict, fields: list[dict]) -> list[dict]:
    labels = {f["label"] for f in fields}
    return [b for b in plan["blocks"] if b["type"] == "fill_field" and b["field"]["label"] not in labels]


ASK_PROMPT = """\
A web form changed. A step of an automation must put a spreadsheet value into the field that
means the same as before. Pick that field among the candidates, or say it is ambiguous if
more than one could mean it or none clearly does. Reply with JSON only:
{"label": "<exact candidate label>" or null, "why": "<short reason>"}"""


def relocate(plan: dict, fields: list[dict], client=None, log_fn=print) -> list[Relocation]:
    """One Relocation per fill_field whose field is gone (see the module docstring)."""
    missing = missing_blocks(plan, fields)
    if not missing:
        return []
    claimed = {b["field"]["label"] for b in plan["blocks"] if b["type"] == "fill_field"} - \
              {b["field"]["label"] for b in missing}
    unused = [f["label"] for f in fields if f["label"] and f["label"] not in claimed]
    out = []
    for b in missing:
        r = Relocation(b["id"], b["column"], b["field"]["label"])
        key = words(b["field"]["label"]) | words(b["column"])
        overlap = [label for label in unused if words(label) & key]
        if len(overlap) >= 2:
            r.candidates, r.how = overlap, f"{len(overlap)} fields could mean {b['column']!r}"
        elif len(overlap) == 1:
            r.new_label, r.how = overlap[0], f"the only field that shares a word with {b['field']['label']!r}"
        elif len(unused) == 1 and len(missing) == 1:
            r.new_label, r.how = unused[0], "the only field no other step uses"
        elif unused and client is not None:
            r.new_label, r.how = _ask(client, b, unused, log_fn)
            if r.new_label is None:
                r.candidates = unused
        else:
            r.candidates, r.how = unused, "no field clearly means it"
        if r.new_label:
            unused.remove(r.new_label)
        log_fn(f"  [{b['id']}] {b['field']['label']!r} is gone -> "
               + (f"{r.new_label!r} ({r.how})" if r.new_label else f"ambiguous: {r.candidates} ({r.how})"))
        out.append(r)
    return out


def _ask(client, block: dict, candidates: list[str], log_fn) -> tuple[str | None, str]:
    content = (f"Step contract: {block.get('contract', '')}\nSpreadsheet column: {block['column']}\n"
               f"Old field label: {block['field']['label']}\nCandidate fields now on the page: "
               f"{json.dumps(candidates, ensure_ascii=False)}")
    try:
        response = client.chat(f"repair: relocate {block['id']}", max_tokens=1500, reasoning_effort="low",
                               messages=[{"role": "system", "content": ASK_PROMPT},
                                         {"role": "user", "content": content}])
        text = response.choices[0].message.content or ""
        reply = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as exc:
        log_fn(f"  [{block['id']}] could not ask the model ({type(exc).__name__}); treating it as ambiguous")
        return None, "the model could not be asked"
    label = reply.get("label")
    if label in candidates:
        return label, f"Muse Spark: {reply.get('why', '')}".strip()
    return None, f"Muse Spark: {reply.get('why', 'ambiguous')}"


def apply(plan: dict, code: dict[str, str], relocations: list[Relocation]) -> tuple[dict, dict[str, str]]:
    """The plan and code pointed at the new fields. Only the relocated blocks change: their
    field label, the demo values keyed by that label, and the label written in their code
    (if the code does not name it literally, verification will regenerate that block)."""
    plan, code = copy.deepcopy(plan), dict(code)
    for r in relocations:
        if not r.new_label:
            raise ValueError(f"{r.block_id} is ambiguous")
        block = next(b for b in plan["blocks"] if b["id"] == r.block_id)
        block["field"]["label"] = r.new_label
        values = plan["demo"]["values"]
        plan["demo"]["values"] = {(r.new_label if k == r.old_label else k): v for k, v in values.items()}
        for quote in ('"', "'"):
            code[r.block_id] = code[r.block_id].replace(f"{quote}{r.old_label}{quote}", f"{quote}{r.new_label}{quote}")
    return plan, code
