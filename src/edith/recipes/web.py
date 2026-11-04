"""Level 3 of the cascade: the web, without a window.

  (a) the site's API: the machine replays the request the site's own form sends (learned from
      the demonstration; `workshop/network.py`). When a token was saved for that site, it goes as
      `Authorization: Bearer ...`; tokens live in the Windows Credential Manager (`save_token`),
      never in files or in the repository.
  (b) a headless browser that uses the user's session: Edith keeps its own Chromium profile
      (state/web-profile); the user signs in once in the window `open_for_login` shows, and the
      machines then run headless with that session (`workshop/runner.py`).
Edith never tries a CAPTCHA or a two-step verification: when a page asks for one, the run stops
and the panel asks the user to do it in the visible window (`needs_user`).
Web-form recipes reuse the verified machines of the workshop (fabricated, verified by
equivalence on a temporary database, compiled down to the network level when it passes).
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

CRED_PREFIX = "Edith/"
HUMAN_CHECK = re.compile(r"captcha|i'?m not a robot|no soy un robot|recaptcha|hcaptcha|two-step|2-step|two-factor|"
                         r"verificaci[oó]n en dos pasos|c[oó]digo de verificaci[oó]n|verification code|"
                         r"autenticaci[oó]n de dos factores", re.IGNORECASE)


class NeedsUser(RuntimeError):
    """A CAPTCHA or a two-step verification: only the user may do it."""


def site(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def save_token(site_name: str, token: str) -> None:
    import win32cred

    win32cred.CredWrite({"Type": win32cred.CRED_TYPE_GENERIC, "TargetName": CRED_PREFIX + site_name,
                         "UserName": "edith", "CredentialBlob": token,
                         "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)


def token_for(site_name: str) -> str | None:
    import pywintypes
    import win32cred

    try:
        c = win32cred.CredRead(CRED_PREFIX + site_name, win32cred.CRED_TYPE_GENERIC, 0)
    except pywintypes.error:
        return None
    blob = c.get("CredentialBlob") or b""
    return blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)


def delete_token(site_name: str) -> bool:
    import pywintypes
    import win32cred

    try:
        win32cred.CredDelete(CRED_PREFIX + site_name, win32cred.CRED_TYPE_GENERIC, 0)
        return True
    except pywintypes.error:
        return False


def profile_dir() -> Path:
    from ..config import ROOT

    return ROOT / "state" / "web-profile"


def needs_user(page_text: str) -> bool:
    return bool(HUMAN_CHECK.search(page_text or ""))


def open_for_login(url: str, wait_s: float = 600) -> None:
    """A visible window with Edith's own profile, for the user to sign in once (closed by the user)."""
    from playwright.sync_api import sync_playwright

    profile_dir().mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile_dir()), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        try:
            page.wait_for_event("close", timeout=wait_s * 1000)
        except Exception:
            pass
        ctx.close()


# --- web-form recipes (level 3) ------------------------------------------------------------------------

def recipe_from_tool(store, tool_dir: Path, allow: list[str] | None, manual_s: float = 0.0):
    """A level-3 recipe around a verified machine of the workshop: the rows are chosen like any
    recipe ("para Ñandú"), the machine sends them at its lowest verified level."""
    import time

    from ..workshop import build
    from . import forms
    from .store import Recipe, RecipeStore

    plan, _code = build.load_tool(tool_dir)
    sheet = Path(plan["source"]["path"])
    found = forms.demo_row(sheet, plan["demo"]["values"])
    if found is None:
        raise RuntimeError("the demonstration's row is not in the sheet")
    _rows, row, key_column = found
    sub = build.level_for(tool_dir, allow)
    name = (plan.get("name") or site(plan["target"]["url"])).strip()
    r = Recipe(id=RecipeStore.new_id(name), name=name, kind="excel_to_web", level=3,
               steps=[b.get("title", "") for b in plan.get("blocks", [])][:6], sheet=str(sheet), template=str(tool_dir),
               result="", key_column=key_column, demo_key=str(row[key_column]).strip(), out_dir="",
               verified={"level": 3, "ts": time.time(), "sublevel": sub}, manual_s=manual_s, created=time.time(),
               extra={"title": plan["target"].get("page_title") or name, "url": plan["target"]["url"],
                      "sublevel": sub, "typed": plan["demo"]["values"]})
    store.save(r, "# level 3: this recipe runs the verified machine in " + str(tool_dir) + "\n")
    return r


def rows_for(r, keys: list[str]) -> list[tuple[int, dict]]:
    from ..workshop import build

    plan, _ = build.load_tool(Path(r.template))
    sheet, _ = build.prepare(plan)
    want = {k.strip().casefold() for k in keys}
    return [(i, row) for i, row in enumerate(sheet.rows) if str(row.get(r.key_column) or "").strip().casefold() in want]


def preview_text(r, rows: list[tuple[int, dict]]) -> list[tuple[str, str]]:
    """(key, "field: value" lines) of what each row will send; nothing is sent."""
    from ..workshop import build

    plan, _ = build.load_tool(Path(r.template))
    return [(str(row.get(r.key_column)).strip(),
             "\n".join(f"{k}: {v}" for k, v in build.planned_fields(plan, row).items())) for _i, row in rows]


def send(r, rows: list[tuple[int, dict]], allow: list[str] | None) -> list[str]:
    """After Enter: the machine sends the rows (proof first). Returns errors."""
    import logging

    from ..workshop import build

    tool = Path(r.template)
    ok, problems, _stamp = build.dry_run(tool, log_fn=logging.getLogger(__name__).debug, allow=allow)
    if not ok:
        return [f"proof: {problems[:1]}"]
    results = build.run_rows_logged(tool, rows, log_fn=logging.getLogger(__name__).debug, allow=allow)
    return [f"row {x.index + 2}: {x.error or x.skipped}" for x in results if not x.ok]
