"""Muse Spark writes the Playwright code of each block; a static guard checks it before use.

Every block is one function with a fixed signature:

    def run(page, row, ctx):
        ...

`page` is a Playwright (sync) Page already on the target URL, `row` maps sheet headers to
raw cell values, `ctx` is a dict shared by the blocks of one row (`ctx["filled"]` collects
what each fill_field left in its field). Helpers in scope: `transform(value, spec)` (the
plan's transforms), `expect` (Playwright assertions), `re`. Nothing else: no imports, no
files, no OS. The guard rejects anything outside that before the code ever runs.
"""

from __future__ import annotations

import ast
import json
import re
import time

SYSTEM_PROMPT = """\
You are Edith, a desktop assistant building a machine out of blocks. Each block is one
small Python function using Playwright's SYNC API, exactly:

def run(page, row, ctx):
    ...

- `page`: a Playwright Page, already open on the form page.
- `row`: dict, sheet header -> raw cell value (str, int, float or datetime).
- `ctx`: dict shared by the blocks of one row. Every fill_field block MUST end with
  ctx["filled"][<field label>] = <locator>.input_value()   (read back what the field holds).
- In scope: transform(value, spec) applies the block's transform spec and returns the text
  to type; expect (playwright.sync_api.expect); re. Do NOT import anything, do not open
  files, do not use os/sys/subprocess/eval/exec, no names starting with "_".
- Find elements by their accessible names, as in the page snapshot: prefer
  page.get_by_label("<label>", exact=True) for fields and
  page.get_by_role("button", name="<name>", exact=True) for buttons.
- read_row blocks only prepare ctx (e.g. ctx["filled"] = {}); do not navigate.
- save presses the submit button. wait waits for the confirmation (use expect(...).to_be_visible()
  or page.wait_for_url with a timeout of at most 10000 ms). verify raises AssertionError
  with a short message if the page does not confirm success.
- NEVER write a data value in the code (no client names, amounts, dates from the example
  row): every value must come from row["<column>"] through transform(...). Code with a data
  value written in it is rejected.
- Each block has a "contract" (what it must achieve, by meaning); honour it.
- Keep each function short. No comments needed.

Reply with ONE JSON object mapping block id -> the full source of its function, e.g.
{"b1": "def run(page, row, ctx):\\n    ctx['filled'] = {}", ...}"""

ALLOWED_GLOBALS = {"transform", "expect", "re", "page", "row", "ctx"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars",
                   "getattr", "setattr", "delattr", "input", "breakpoint", "os", "sys",
                   "subprocess", "shutil", "pathlib", "importlib", "builtins", "socket", "exit", "quit"}
SAFE_BUILTINS = {name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
                 for name in ("str", "int", "float", "bool", "len", "round", "min", "max", "abs", "range",
                              "dict", "list", "tuple", "set", "isinstance", "enumerate", "zip", "sorted",
                              "any", "all", "Exception", "ValueError", "AssertionError", "RuntimeError",
                              "KeyError", "TypeError", "print")}


class UnsafeCode(ValueError):
    pass


def check_code(source: str) -> None:
    """Raise UnsafeCode unless the source is exactly one `def run(page, row, ctx)` that stays
    inside the allowed names."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnsafeCode(f"syntax error: {exc.msg} (line {exc.lineno})") from None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != "run":
        raise UnsafeCode("the block must be exactly one function named run")
    args = [a.arg for a in tree.body[0].args.args]
    if args != ["page", "row", "ctx"]:
        raise UnsafeCode(f"run must take (page, row, ctx), not ({', '.join(args)})")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef,
                             ast.AsyncFunctionDef, ast.Await, ast.Lambda)):
            raise UnsafeCode(f"{type(node).__name__} is not allowed in a block")
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("_")):
            raise UnsafeCode(f"the name {node.id!r} is not allowed in a block")
        # str.format can reach dunder attributes from inside a string ("{0.__class__}"); f-strings
        # are fine because their expressions are part of the tree checked here.
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in ("format", "format_map")):
            raise UnsafeCode(f"the attribute {node.attr!r} is not allowed in a block")


def _norm(value) -> str:
    return " ".join(str(value).split()).casefold()


def data_values(rows: list[dict], typed: dict[str, str] | None = None) -> tuple[set[str], set[float]]:
    """Every data value a block must never contain literally: cell texts and typed values."""
    texts, numbers = set(), set()
    for row in rows:
        for v in row.values():
            if v is None or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                numbers.add(round(float(v), 2))
            text = _norm(v)
            if len(text) >= 3:
                texts.add(text)
    for v in (typed or {}).values():
        if len(_norm(v)) >= 3:
            texts.add(_norm(v))
    return texts, numbers


def check_anti_cheat(source: str, block: dict, texts: set[str], numbers: set[float]) -> None:
    """No data value written in the code: every value has to come from a column of `row`.

    A fill_field must read row[...]; no block may contain a string equal to a cell value or a
    typed value, nor a number equal to a numeric cell (timeouts and indexes are fine)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _norm(node.value) in texts:
            raise UnsafeCode(f"anti-cheat: the data value {node.value!r} is written in the code; "
                             "take it from row[<column>] instead")
        if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
                and round(float(node.value), 2) in numbers and float(node.value) not in (0, 1, 2)):
            raise UnsafeCode(f"anti-cheat: the data value {node.value!r} is written in the code; "
                             "take it from row[<column>] instead")
    if block.get("type") == "fill_field":
        reads_row = any(isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == "row"
                        for n in ast.walk(tree))
        if not reads_row:
            raise UnsafeCode(f'anti-cheat: a fill_field must take its value from row["{block.get("column")}"]')


def compile_block(source: str, helpers: dict):
    """The block's run() function, executed with only the safe builtins and helpers."""
    check_code(source)
    namespace = {"__builtins__": SAFE_BUILTINS, **helpers}
    exec(compile(source, "<block>", "exec"), namespace)  # noqa: S102 - guarded above
    return namespace["run"]


def parse_reply(text: str) -> dict[str, str]:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    return {str(k): str(v) for k, v in data.items()}


def plan_brief(plan: dict) -> str:
    blocks = [{k: v for k, v in b.items() if k != "code"} for b in plan["blocks"]]
    return json.dumps({"intent": plan["intent"], "blocks": blocks}, ensure_ascii=False, indent=1)


class NoCode(RuntimeError):
    """The model did not reply with usable code, even after a retry."""


def _ask(client, purpose: str, content: str, max_tokens: int, effort: str, log_fn) -> dict[str, str]:
    """One code request, retried once if the reply is empty or not JSON. Reasoning tokens count
    against max_tokens, so an empty reply usually means the budget ran out: the retry doubles
    it and lowers the effort."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
    for attempt in (1, 2):
        t0 = time.monotonic()
        response = client.chat(purpose if attempt == 1 else f"{purpose} (retry)", messages=messages,
                               max_tokens=max_tokens * attempt, reasoning_effort=effort if attempt == 1 else "low")
        choice = response.choices[0]
        text = choice.message.content or ""
        cost = f"{time.monotonic() - t0:.1f}s, ${getattr(response, 'edith_cost', 0):.4f}"
        try:
            code = parse_reply(text)
            if code:
                return code
            raise ValueError("empty object")
        except ValueError as exc:  # JSONDecodeError is a ValueError
            details = getattr(response.usage, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", None) if details else None
            log_fn(f"  {purpose}: unusable reply ({exc}; finish={choice.finish_reason}, "
                   f"reasoning tokens={reasoning}, {cost})")
            if text.strip():
                messages = messages + [{"role": "assistant", "content": text},
                                       {"role": "user", "content": "Reply with the JSON object only."}]
    raise NoCode(f"{purpose}: no usable code after 2 replies")


def _after(after: str | None) -> list[str]:
    if not after:
        return []
    return ["Accessibility snapshot of the page right AFTER one row was saved (a sandbox probe): "
            "write the wait and verify blocks for what it really shows.", after, ""]


def generate(client, plan: dict, snapshot: str, sample_row: dict, log_fn=print,
             after: str | None = None) -> dict[str, str]:
    """Code for every block in one call (cheaper than one call per block)."""
    nl = chr(10)
    content = nl.join([
        "Plan:", plan_brief(plan), "",
        f"Accessibility snapshot of the form page ({plan['target']['url']}):", snapshot, "",
        *_after(after),
        f"Example row: {json.dumps(sample_row, ensure_ascii=False, default=str)}", "",
        "Write the code of every block."])
    t0 = time.monotonic()
    code = _ask(client, "workshop: code all blocks", content, 8000, "low", log_fn)
    log_fn(f"  code for {len(code)} blocks ({time.monotonic() - t0:.1f}s)")
    return code


def regenerate(client, plan: dict, block: dict, old_code: str, problem: str, snapshot: str,
               sample_row: dict, log_fn=print, after: str | None = None) -> str:
    """New code for ONE block, given what went wrong. The other blocks are left alone."""
    nl = chr(10)
    content = nl.join([
        "Plan:", plan_brief(plan), "",
        "Accessibility snapshot of the form page:", snapshot, "",
        *_after(after),
        f"Example row: {json.dumps(sample_row, ensure_ascii=False, default=str)}", "",
        f"Block {block['id']} ({block['type']}: {block.get('title', '')}) currently is:", old_code, "",
        "When it ran on the user's own demonstration it failed:", problem, "",
        f'Fix only this block. Reply with JSON: {{"{block["id"]}": "<full source>"}}'])
    t0 = time.monotonic()
    code = _ask(client, f"workshop: repair {block['id']}", content, 4000, "medium", log_fn)
    log_fn(f"  new code for {block['id']} ({time.monotonic() - t0:.1f}s)")
    return code.get(block["id"]) or next(iter(code.values()))
