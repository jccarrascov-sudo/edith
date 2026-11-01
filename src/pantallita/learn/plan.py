"""Demonstration -> intent + a JSON plan of blocks (Muse Spark), checked against the demo.

Block types are fixed so each block can be drawn, verified and repaired on its own:
  read_row    take the next row of the source sheet
  fill_field  put one column (through a transform) into one form field, found by its label
  click       press a button/link that is not the final submit
  save        submit the form
  wait        wait for the page to confirm (text / url / load)
  verify      check the row was accepted
A plan is accepted only if replaying its transforms on the demonstrated row gives exactly
the values the user typed; otherwise the errors go back to the model (max 3 attempts).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path

from PIL import Image

from ..brain import tokens
from ..config import ROOT, Config
from ..memory.timeline import Timeline
from . import segment as seg_mod
from . import source as src_mod

log = logging.getLogger(__name__)

PLANS_DIR = ROOT / "state" / "plans"
BLOCK_TYPES = {"read_row", "fill_field", "click", "save", "wait", "verify"}
TRANSFORM_KINDS = {"text", "display", "date", "number"}

SYSTEM_PROMPT = """\
You are Pantallita, a tiny inventor who turns one demonstration into a reusable machine.
You get a recording of something the user did once (clicks, the values text fields ended
with, windows), the spreadsheet the values came from, and the matching row. Work out the
intent and write a plan of blocks that does the same task for every row, directly in the
browser (it will be run with Playwright; no mouse).

Reply with ONE JSON object, no prose, with exactly these keys:
{
  "name": "a short, warm, invented machine name (2-3 words, e.g. 'Invoice Hopper')",
  "intent": "one sentence: what the user does, for each row",
  "blocks": [ ...in execution order... ]
}
Block shapes (ids b1, b2, ... in order; "title" is a 2-5 word label shown on the block):
  {"id","type":"read_row","title"}
  {"id","type":"fill_field","title","field":{"label": <exact field name from the recording>,
       "kind":"edit"|"combo"}, "column": <exact sheet header>, "transform": <transform>}
  {"id","type":"click","title","target":{"role":"button"|"link","name": <exact name>}}
  {"id","type":"save","title","target":{"role":"button","name": <exact name of the submit button>}}
  {"id","type":"wait","title","until":{"kind":"text"|"url"|"load","value": <text or url part, or "">}}
  {"id","type":"verify","title","expect":{"kind":"text","value": <text that confirms success, or "">}}
Transforms turn the raw cell into the text the user typed:
  {"kind":"text"}                                  the cell as plain text
  {"kind":"display"}                               the cell as Excel displays it
  {"kind":"date","format": <strftime, e.g. "%d/%m/%Y">}
  {"kind":"number","decimals": <int>,"thousands": true|false}
Rules: start with read_row; one fill_field per field the user filled, in the order they
were filled; use the exact field names and headers given; pick each transform so that the
demonstrated row produces EXACTLY the typed text; end with save, then wait and verify."""


# --- transforms ---------------------------------------------------------------------------

def apply_transform(value, transform: dict, number_format: str = "General") -> str:
    kind = transform.get("kind", "text")
    if value is None:
        return ""
    if kind == "display":
        return src_mod.excel_display(value, number_format)
    if kind == "date":
        if isinstance(value, str):
            value = _parse_date(value)
        return value.strftime(transform.get("format", "%Y-%m-%d"))
    if kind == "number":
        number = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
        decimals = int(transform.get("decimals", 2))
        return f"{number:,.{decimals}f}" if transform.get("thousands") else f"{number:.{decimals}f}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _parse_date(text: str) -> datetime:
    for fmt in src_mod.DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"not a date: {text!r}")


# --- checking ------------------------------------------------------------------------------

def check_plan(plan: dict, typed: dict[str, str], sheet: src_mod.Sheet, row: dict,
               submit_name: str | None) -> list[str]:
    """Every reason the plan would not reproduce the demonstration (empty = good)."""
    errors = []
    if not isinstance(plan.get("name"), str) or not plan.get("name"):
        errors.append('"name" is missing')
    if not isinstance(plan.get("intent"), str) or not plan.get("intent"):
        errors.append('"intent" is missing')
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return errors + ['"blocks" must be a non-empty list']
    types = [b.get("type") for b in blocks]
    for b in blocks:
        if b.get("type") not in BLOCK_TYPES:
            errors.append(f'block {b.get("id")}: unknown type {b.get("type")!r}')
    if types[0] != "read_row":
        errors.append("the first block must be read_row")
    if types.count("save") != 1:
        errors.append(f"there must be exactly one save block (found {types.count('save')})")
    fills = [b for b in blocks if b.get("type") == "fill_field"]
    labels = [b.get("field", {}).get("label") for b in fills]
    for name in typed:
        if name not in labels:
            errors.append(f'no fill_field for the field "{name}"')
    for b in fills:
        label, column = b.get("field", {}).get("label"), b.get("column")
        if label not in typed:
            errors.append(f'block {b.get("id")}: "{label}" is not a field the user filled; '
                          f"use one of {list(typed)}")
            continue
        if column not in sheet.headers:
            errors.append(f'block {b.get("id")}: column "{column}" does not exist; headers are {sheet.headers}')
            continue
        transform = b.get("transform") or {}
        if transform.get("kind") not in TRANSFORM_KINDS:
            errors.append(f'block {b.get("id")}: unknown transform {transform!r}')
            continue
        try:
            produced = apply_transform(row.get(column), transform, sheet.formats.get(column, "General"))
        except Exception as exc:
            errors.append(f'block {b.get("id")}: transform {transform} failed on {row.get(column)!r}: {exc}')
            continue
        if " ".join(produced.split()) != " ".join(typed[label].split()):
            errors.append(f'block {b.get("id")} ("{label}"): column "{column}" with {json.dumps(transform)} '
                          f'gives "{produced}" but the user typed "{typed[label]}"')
    if "save" in types and fills and types.index("save") < max(i for i, t in enumerate(types) if t == "fill_field"):
        errors.append("save must come after every fill_field")
    if submit_name:
        save = next((b for b in blocks if b.get("type") == "save"), None)
        if save and save.get("target", {}).get("name") != submit_name:
            errors.append(f'the save block must press "{submit_name}" (the button the user clicked)')
    return errors


# --- prompt --------------------------------------------------------------------------------

def segment_lines(seg: seg_mod.Segment) -> list[str]:
    lines = []
    for e in seg.events:
        t = f"+{e['ts'] - seg.start:5.1f}s"
        el = e.get("element") or {}
        if e["type"] == "click":
            lines.append(f'{t} {e["button"]} click on {el.get("kind", "?")} "{el.get("name", "")}" '
                         f'in {e["process"]} "{e.get("window", "")}"')
        elif e["type"] == "field":
            lines.append(f'{t} field "{el.get("name")}" ({el.get("kind")}) = "{e["value"]}" in {e["process"]}')
        elif e["type"] == "window":
            lines.append(f'{t} window -> {e.get("process")} "{e.get("window", "")}"')
    return lines


def sheet_lines(sheet: src_mod.Sheet, match: src_mod.RowMatch) -> list[str]:
    demo = sheet.rows[match.index]
    lines = [f'Spreadsheet "{sheet.path.name}", sheet "{sheet.name}", {len(sheet.rows)} data rows.',
             "Headers and Excel number formats: "
             + ", ".join(f'"{h}" ({sheet.formats.get(h, "General")})' for h in sheet.headers),
             f"Demonstrated row (data row {match.index + 1}), raw value -> as Excel shows it:"]
    for h in sheet.headers:
        lines.append(f'  "{h}": {_raw(demo.get(h))} -> "{sheet.display(demo, h)}"')
    lines.append("Which cell each typed value came from (found by comparing values):")
    for name, col in match.columns.items():
        lines.append(f'  field "{name}" <- column {json.dumps(col)}')
    return lines


def _raw(value) -> str:
    if isinstance(value, (datetime, date)):
        return f"date {value.strftime('%Y-%m-%d')}"
    return json.dumps(value, ensure_ascii=False, default=str)


def key_frames(timeline: Timeline, seg: seg_mod.Segment, all_events: list[dict]) -> list[tuple[str, Image.Image]]:
    """The filled form right before submitting, and the confirmation right after."""
    frames = [e for e in all_events if e["type"] == "frame" and timeline.buffer_path(e["path"]).exists()]
    submit_ts = seg.submit["ts"] if seg.submit else seg.end
    before = [f for f in frames if seg.start <= f["ts"] <= submit_ts and f["process"] == seg.process]
    after = [f for f in frames if submit_ts < f["ts"] <= submit_ts + 6 and f["process"] == seg.process]
    picked = []
    if before:
        picked.append(("The form, filled in, just before submitting", before[-1]))
    if after:
        picked.append(("The page just after submitting", after[-1]))
    out = []
    for label, f in picked:
        with Image.open(timeline.buffer_path(f["path"])) as im:
            out.append((label, tokens.fit_width(im.convert("RGB"), 960)))
    return out


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# --- main entry ----------------------------------------------------------------------------

class LearnError(RuntimeError):
    pass


def make_plan(cfg: Config, seg: seg_mod.Segment, request: str, all_events: list[dict],
              client=None, max_attempts: int = 3, log_fn=print) -> dict:
    timeline = Timeline(cfg)
    typed = seg.final_values
    excel_titles = [e.get("window", "") for e in all_events if (e.get("process") or "").lower() == "excel.exe"]
    path = src_mod.find_workbook(excel_titles)
    if not path:
        raise LearnError("I can't find the spreadsheet the values came from (is it open in Excel?).")
    sheet = src_mod.load_sheet(path)
    match = src_mod.match_row(sheet, typed)
    if not match:
        raise LearnError(f"None of the rows in {path.name} matches what you typed: {typed}")
    row = sheet.rows[match.index]
    submit = seg.submit
    submit_name = submit["element"]["name"] if submit else None
    log_fn(f"  source: {path.name} (sheet {sheet.name}), your row = data row {match.index + 1} "
           f"({match.matched}/{len(typed)} fields matched a cell)")

    text = [f'User request: "{request or "do the rest"}"', "",
            f"Recording of the demonstration (app: {seg.process}, page: {seg.url or 'unknown'}):"]
    text += segment_lines(seg)
    text += ["", f"Final value of each field the user filled: {json.dumps(typed, ensure_ascii=False)}",
             f"Submit button clicked: {json.dumps(submit_name)}", ""]
    text += sheet_lines(sheet, match)
    content: list[dict] = [{"type": "text", "text": "\n".join(text)}]
    for label, img in key_frames(timeline, seg, all_events):
        content.append({"type": "text", "text": label + ":"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{tokens.jpeg_b64(img, 75)}"}})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]

    if client is None:
        from ..brain.client import MetaClient
        client = MetaClient(cfg)
    plan, errors = None, []
    for attempt in range(1, max_attempts + 1):
        t0 = time.monotonic()
        response = client.chat(f"learn: plan (attempt {attempt})", messages=messages,
                               max_tokens=6000, reasoning_effort="medium")
        reply = response.choices[0].message.content or ""
        try:
            plan = parse_json(reply)
            errors = check_plan(plan, typed, sheet, row, submit_name)
        except (ValueError, json.JSONDecodeError) as exc:
            plan, errors = None, [f"the reply was not valid JSON: {exc}"]
        log_fn(f"  attempt {attempt}: {'plan OK' if not errors else f'{len(errors)} problem(s)'} "
               f"({time.monotonic() - t0:.1f}s, ${getattr(response, 'pantallita_cost', 0):.4f})")
        if not errors:
            break
        for err in errors:
            log_fn(f"    - {err}")
        messages += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "That plan does not reproduce the demonstration:\n- "
                      + "\n- ".join(errors) + "\nReply with the corrected JSON object only."}]
    if errors:
        raise LearnError(f"I couldn't get a plan that reproduces your demo after {max_attempts} tries.")

    plan["source"] = {"path": str(path), "sheet": sheet.name, "header_row": 1, "rows": len(sheet.rows)}
    plan["target"] = {"url": seg.url, "app": seg.process, "page_title": seg.fields[-1].get("window", "")}
    plan["demo"] = {"row_index": match.index, "values": typed, "submit": submit_name,
                    "start": seg.start, "end": seg.end}
    plan["created_at"] = time.time()
    return plan


def save_plan(plan: dict) -> Path:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    path = PLANS_DIR / f"plan-{datetime.fromtimestamp(plan['created_at']).strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def describe_plan(plan: dict) -> list[str]:
    lines = [f'Machine "{plan["name"]}": {plan["intent"]}']
    for b in plan["blocks"]:
        detail = ""
        if b["type"] == "fill_field":
            detail = f' {b["field"]["label"]!r} <- {b["column"]!r} {json.dumps(b["transform"])}'
        elif b["type"] in ("click", "save"):
            detail = f' press {b["target"].get("role")} {b["target"].get("name")!r}'
        elif b["type"] in ("wait", "verify"):
            detail = " " + json.dumps(b.get("until") or b.get("expect"), ensure_ascii=False)
        lines.append(f'  [{b["id"]}] {b["type"]:<10} {b.get("title", "")}{detail}')
    return lines
