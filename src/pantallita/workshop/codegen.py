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
You are Pantallita, a tiny inventor building a machine out of blocks. Each block is one
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


def generate(client, plan: dict, snapshot: str, sample_row: dict, log_fn=print) -> dict[str, str]:
    """Code for every block in one call (cheaper than one call per block)."""
    content = (f"Plan:\n{plan_brief(plan)}\n\nAccessibility snapshot of the form page "
               f"({plan['target']['url']}):\n{snapshot}\n\nExample row: "
               f"{json.dumps(sample_row, ensure_ascii=False, default=str)}\n\n"
               "Write the code of every block.")
    t0 = time.monotonic()
    response = client.chat("workshop: code all blocks", max_tokens=8000, reasoning_effort="low",
                           messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                     {"role": "user", "content": content}])
    code = parse_reply(response.choices[0].message.content)
    log_fn(f"  code for {len(code)} blocks ({time.monotonic() - t0:.1f}s, "
           f"${getattr(response, 'pantallita_cost', 0):.4f})")
    return code


def regenerate(client, plan: dict, block: dict, old_code: str, problem: str, snapshot: str,
               sample_row: dict, log_fn=print) -> str:
    """New code for ONE block, given what went wrong. The other blocks are left alone."""
    content = (f"Plan:\n{plan_brief(plan)}\n\nAccessibility snapshot of the form page:\n{snapshot}\n\n"
               f"Example row: {json.dumps(sample_row, ensure_ascii=False, default=str)}\n\n"
               f"Block {block['id']} ({block['type']}: {block.get('title', '')}) currently is:\n"
               f"{old_code}\n\nWhen it ran on the user's own demonstration it failed:\n{problem}\n\n"
               f'Fix only this block. Reply with JSON: {{"{block["id"]}": "<full source>"}}')
    t0 = time.monotonic()
    response = client.chat(f"workshop: repair {block['id']}", max_tokens=4000, reasoning_effort="medium",
                           messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                     {"role": "user", "content": content}])
    code = parse_reply(response.choices[0].message.content)
    log_fn(f"  new code for {block['id']} ({time.monotonic() - t0:.1f}s, "
           f"${getattr(response, 'pantallita_cost', 0):.4f})")
    return code.get(block["id"]) or next(iter(code.values()))
