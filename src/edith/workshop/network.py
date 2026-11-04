"""Compile down: the network level. The same machine, as the form's own HTTP request.

A machine that already passed its page-level verification is run once more in the sandbox
while the browser records the request its save block sends. That request is generalised:
every form value that equals what a fill_field block put in its field is taken from that
block (row[column] through its transform), hidden inputs are read fresh from the form page
before each send, empty values stay empty. Any other literal value is a data value written
by hand, so the request is NOT compiled (anti-cheat). The response that meant "saved" (status
and redirect path) becomes the success check.

The result (tools/<name>/network.json) is verified by equivalence exactly like the page level,
and is used only for allow-listed domains. No browser is opened at this level.
"""

from __future__ import annotations

import html.parser
import http.cookiejar
import time
import urllib.error
import urllib.parse
import urllib.request

from . import codegen, runner

LEVEL = "network"
FORM_TYPE = "application/x-www-form-urlencoded"


class NotCompiled(RuntimeError):
    """The page-level machine cannot be expressed as one form request."""


def allowed(url: str, allow: list[str] | None) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return bool(host) and any(host == d.lower() or host.endswith("." + d.lower()) for d in (allow or []))


def _path(url: str) -> str:
    return urllib.parse.urlsplit(url).path or "/"


# --- capture: record the request of a verified page-level run -----------------------------------

def capture(plan: dict, code: dict[str, str], row: dict, url: str, timeout_ms: int = 8000) -> dict:
    """Run the page-level machine on one row (meant for a sandbox URL) and turn the request its
    save block sent into a network spec. Raises NotCompiled when that is not possible."""
    from playwright.sync_api import sync_playwright

    blocks = runner.compile_blocks(plan, code)
    fills = [b for b in plan["blocks"] if b["type"] == "fill_field"]
    seen: dict = {}

    def on_request(req) -> None:
        if req.method == "POST" and req.resource_type == "document" and "request" not in seen:
            seen["request"] = req

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url)
            ctx: dict = {"filled": {}}
            for b in plan["blocks"]:
                if b["type"] == "save" and "form_page" not in seen:
                    seen["form_page"] = _path(page.url)
                    seen["hidden"] = page.eval_on_selector_all(
                        "form input[type=hidden]", "els => els.map(e => e.name).filter(Boolean)")
                    page.on("request", on_request)
                blocks[b["id"]](page, row, ctx)
            request = seen.get("request")
            if request is None:
                raise NotCompiled("the save step did not send a form request (a script-driven page?)")
            response = request.response()
            content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
            body = urllib.parse.parse_qsl(request.post_data or "", keep_blank_values=True)
            status = response.status if response else None
            location = (response.headers.get("location") if response else None) or ""
            action = request.url
        finally:
            browser.close()

    if content_type != FORM_TYPE:
        raise NotCompiled(f"the form is sent as {content_type or 'unknown'}, not as a plain form")
    if status is None or status >= 400:
        raise NotCompiled(f"the recorded request was answered with {status}")
    by_value: dict[str, list[str]] = {}
    for b in fills:
        by_value.setdefault(ctx["filled"].get(b["field"]["label"], None), []).append(b["id"])
    fields, used = [], set()
    for name, value in body:
        owners = [bid for bid in by_value.get(value, []) if bid not in used]
        if name in seen.get("hidden", []):
            fields.append({"name": name, "hidden": True})
        elif value and owners:
            fields.append({"name": name, "block": owners[0]})
            used.add(owners[0])
        elif value == "":
            fields.append({"name": name, "const": ""})
        else:
            raise NotCompiled(f"the form sends {name}={value!r}, which no step of the machine filled")
    missing = [b["id"] for b in fills if b["id"] not in used]
    if missing:
        raise NotCompiled(f"the request does not carry the value of {', '.join(missing)}")
    return {"level": LEVEL, "method": "POST", "action": _path(action), "form_page": seen.get("form_page", "/"),
            "content_type": FORM_TYPE, "fields": fields,
            "success": {"status": status, "location_path": _path(location) if location else None}}


def check_spec(spec: dict, texts: set[str], numbers: set[float]) -> None:
    """Anti-cheat for the network level: constants must not be data values."""
    for f in spec["fields"]:
        if "const" in f:
            codegen.check_anti_cheat(f'def run(page, row, ctx):\n    x = {f["const"]!r}', {"type": "const"},
                                     texts, numbers)


# --- run ----------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # keep the 3xx: it is the success signal


class _Hidden(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and (a.get("type") or "").lower() == "hidden" and a.get("name"):
            self.values[a["name"]] = a.get("value") or ""


def _origin(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


def body_for(plan: dict, spec: dict, row: dict, hidden: dict[str, str]) -> tuple[list[tuple[str, str]], dict]:
    """(form body, field label -> text) for one row."""
    blocks = {b["id"]: b for b in plan["blocks"]}
    body, filled = [], {}
    for f in spec["fields"]:
        if "block" in f:
            b = blocks[f["block"]]
            value = runner.transform(row.get(b["column"]), b["transform"])
            filled[b["field"]["label"]] = value
        elif f.get("hidden"):
            value = hidden.get(f["name"], "")
        else:
            value = f["const"]
        body.append((f["name"], value))
    return body, filled


def run_rows(plan: dict, spec: dict, rows: list[tuple[int, dict]], url: str, on_event=None,
             stop_on_error: bool = True, timeout_s: float = 10) -> list[runner.RowResult]:
    emit = on_event or (lambda e: None)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect)
    origin = _origin(url)
    results: list[runner.RowResult] = []
    t_run = time.monotonic()
    emit({"event": "run_start", "rows": len(rows), "level": LEVEL})
    for index, row in rows:
        empty = runner.empty_cells(plan, row)
        if empty:
            reason = "empty " + ", ".join(empty)
            emit({"event": "row_skipped", "row": index, "reason": reason})
            results.append(runner.RowResult(index, False, skipped=reason))
            continue
        result = _send(opener, plan, spec, origin, index, row, emit, timeout_s)
        results.append(result)
        if not result.ok and stop_on_error:
            break
    emit({"event": "run_end", "ok": sum(r.ok for r in results),
          "failed": sum(not r.ok and not r.skipped for r in results), "skipped": sum(bool(r.skipped) for r in results),
          "seconds": round(time.monotonic() - t_run, 2), "level": LEVEL})
    return results


def _send(opener, plan, spec, origin, index, row, emit, timeout_s) -> runner.RowResult:
    t0 = time.monotonic()
    emit({"event": "row_in", "row": index})
    save_id = next(b["id"] for b in plan["blocks"] if b["type"] == "save")
    filled: dict = {}
    try:
        hidden: dict[str, str] = {}
        if any(f.get("hidden") for f in spec["fields"]):
            with opener.open(origin + spec["form_page"], timeout=timeout_s) as r:
                parser = _Hidden()
                parser.feed(r.read().decode("utf-8", "replace"))
                hidden = parser.values
        body, filled = body_for(plan, spec, row, hidden)
        data = urllib.parse.urlencode(body).encode("utf-8")
        headers = {"Content-Type": spec["content_type"]}
        from ..recipes import web

        token = web.token_for(web.site(origin))            # Windows Credential Manager, never a file
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(origin + spec["action"], data=data, method="POST", headers=headers)
        try:
            with opener.open(req, timeout=timeout_s) as r:
                status, location = r.status, r.headers.get("Location") or ""
        except urllib.error.HTTPError as exc:  # 3xx (not followed) and 4xx/5xx land here
            status, location = exc.code, exc.headers.get("Location") or ""
        want = spec["success"]
        if status != want["status"] or (want.get("location_path") and _path(location) != want["location_path"]):
            raise RuntimeError(f"the app did not confirm the save (answered {status}"
                               + (f" -> {_path(location)}" if location else "") + f", expected {want['status']}"
                               + (f" -> {want['location_path']}" if want.get("location_path") else "") + ")")
    except Exception as exc:
        error = runner.short_error(exc)
        emit({"event": "block_error", "row": index, "block": save_id, "error": error})
        emit({"event": "row_out", "row": index, "ok": False, "filled": filled, "level": LEVEL})
        return runner.RowResult(index, False, filled, save_id, error, time.monotonic() - t0)
    emit({"event": "row_out", "row": index, "ok": True, "filled": filled, "level": LEVEL})
    return runner.RowResult(index, True, filled, seconds=time.monotonic() - t0)
