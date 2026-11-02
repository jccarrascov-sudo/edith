"""Proof included: the user's demonstration and the state change it caused, kept with the machine.

tools/<name>/proof/
  demo.json      the demonstrated row (raw cells), what the user typed, the submit button
  expected.json  the record the user's submission stored (the expected state change), and
                 how each stored column relates to a sheet column, learned from that record

The relation is what lets the machine be checked on rows the user never did: if the demo
shows sheet "Date" 2026-09-10 (a date) stored as "2026-09-10" and "Amount" 4216.96 stored as
4216.96, then for any other row the expected record is derived the same way. No value is
written by hand; everything comes from the demo and the sheet.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from ..learn import source as src_mod
from .runner import empty_cells

IGNORED = {"id", "created_at"}


def _as_date(value) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        for fmt in src_mod.DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _as_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    try:
        return round(float(str(value).replace(",", "").strip()), 2)
    except ValueError:
        return None


def _as_text(value) -> str | None:
    if value is None or isinstance(value, (datetime, date)):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


CANON = {"text": _as_text, "date": _as_date, "number": _as_number}


def relate(record: dict, demo_row: dict) -> dict[str, dict]:
    """stored column -> {"column": sheet header, "as": text|date|number} or {"const": value}."""
    relation = {}
    for col, stored in record.items():
        if col in IGNORED:
            continue
        found = None
        for kind in ("date", "number", "text"):  # most specific first
            for header, cell in demo_row.items():
                canon = CANON[kind](cell)
                if canon is not None and stored is not None and canon == (round(stored, 2) if kind == "number"
                                                                           and isinstance(stored, (int, float)) else stored):
                    found = {"column": header, "as": kind}
                    break
            if found:
                break
        relation[col] = found or {"const": stored}
    return relation


def expected_record(relation: dict[str, dict], row: dict) -> dict:
    out = {}
    for col, rule in relation.items():
        out[col] = CANON[rule["as"]](row.get(rule["column"])) if "column" in rule else rule["const"]
    return out


def pick_extra_rows(plan: dict, rows: list[dict], n: int = 2) -> list[int]:
    """Rows to verify besides the demo: complete rows, preferring ones whose cells have a
    different type than the demo's (a date written as text, an amount written as text...)."""
    demo_i = plan["demo"]["row_index"]
    demo = rows[demo_i]
    cols = [b["column"] for b in plan["blocks"] if b["type"] == "fill_field"]

    def oddness(row):
        return sum(type(row.get(c)) is not type(demo.get(c)) for c in cols)

    candidates = [i for i, r in enumerate(rows) if i != demo_i and not empty_cells(plan, r)]
    candidates.sort(key=lambda i: (-oddness(rows[i]), i))
    picked, seen_kinds = [], set()
    for i in candidates:  # different kinds of oddness first, then fill up
        kind = tuple(type(rows[i].get(c)).__name__ for c in cols)
        if kind not in seen_kinds:
            picked.append(i)
            seen_kinds.add(kind)
        if len(picked) == n:
            return picked
    for i in candidates:
        if i not in picked:
            picked.append(i)
        if len(picked) == n:
            break
    return picked


def encode_row(row: dict) -> dict:
    """Cells as JSON, keeping dates as dates: {"$date": "2026-09-10T00:00:00"}."""
    return {k: {"$date": v.isoformat()} if isinstance(v, (datetime, date)) else v for k, v in row.items()}


def decode_row(row: dict) -> dict:
    return {k: datetime.fromisoformat(v["$date"]) if isinstance(v, dict) and "$date" in v else v
            for k, v in row.items()}


def _json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def save(tool_dir: Path, plan: dict, demo_row: dict, record: dict) -> None:
    d = tool_dir / "proof"
    d.mkdir(parents=True, exist_ok=True)
    demo = {"row_index": plan["demo"]["row_index"], "row": encode_row(demo_row), "typed": plan["demo"]["values"],
            "submit": plan["demo"].get("submit"), "start": plan["demo"].get("start"), "end": plan["demo"].get("end")}
    (d / "demo.json").write_text(_json(demo), encoding="utf-8")
    expected = {"record": {k: v for k, v in record.items() if k not in IGNORED},
                "relation": relate(record, demo_row)}
    (d / "expected.json").write_text(_json(expected), encoding="utf-8")


def load(tool_dir: Path) -> tuple[dict, dict] | None:
    d = tool_dir / "proof"
    if not (d / "demo.json").exists() or not (d / "expected.json").exists():
        return None
    demo = json.loads((d / "demo.json").read_text(encoding="utf-8"))
    demo["row"] = decode_row(demo["row"])
    return demo, json.loads((d / "expected.json").read_text(encoding="utf-8"))
