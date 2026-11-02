"""A throwaway copy of the target app, so a new machine is tested without touching real data.

For the demo Invoice Portal (recognised by its /health endpoint) the sandbox is the same
portal version on a free port with a temporary SQLite database. What it stored is compared
field by field with what the user's own submission stored in the real database.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# The demo portal's code ships with the repository (not under PANTALLITA_HOME, which is data).
PORTAL_APP = Path(__file__).resolve().parents[3] / "demo" / "portal" / "app.py"
IGNORED_COLUMNS = {"id", "created_at"}


class NoSandbox(RuntimeError):
    pass


def _get_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _origin(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}"


def portal_info(url: str) -> dict:
    """The portal's /health ({"status", "version", "db"}), or NoSandbox if it is not the demo portal."""
    try:
        info = _get_json(_origin(url) + "/health")
    except Exception as exc:
        raise NoSandbox(f"{url} is not reachable or has no /health endpoint ({exc})") from None
    if not isinstance(info, dict) or "version" not in info:
        raise NoSandbox(f"{url} is not the Invoice Portal")
    return info


def records(url: str) -> list[dict]:
    return _get_json(_origin(url) + "/api/invoices")


def user_record(url: str, demo_end: float) -> dict | None:
    """The real record the user's demonstration created: the one saved closest to its end."""
    rows = records(url)

    def distance(r):
        try:
            return abs(datetime.fromisoformat(r["created_at"]).timestamp() - demo_end)
        except (KeyError, ValueError):
            return float("inf")

    best = min(rows, key=distance, default=None)
    return best if best is not None and distance(best) < 120 else None


def compare(user: dict, tool: dict) -> list[tuple[str, object, object, bool]]:
    """(column, user's value, machine's value, equal) for every stored column."""
    cols = [c for c in user if c not in IGNORED_COLUMNS]
    return [(c, user.get(c), tool.get(c), user.get(c) == tool.get(c)) for c in cols]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PortalSandbox:
    def __init__(self, url: str):
        self.target = url
        self.version = portal_info(url)["version"]
        self.dir = Path(tempfile.mkdtemp(prefix="pantallita-sandbox-"))
        self.db = self.dir / "sandbox.db"
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None
        path = urllib.parse.urlsplit(url).path or "/"
        self.url = f"http://127.0.0.1:{self.port}{path}"

    def __enter__(self) -> "PortalSandbox":
        self.proc = subprocess.Popen(
            [sys.executable, str(PORTAL_APP), "--version", self.version, "--db", str(self.db),
             "--port", str(self.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                portal_info(self.url)
                return self
            except NoSandbox:
                time.sleep(0.2)
        self.__exit__(None, None, None)
        raise NoSandbox("the sandbox portal did not start")

    def records(self) -> list[dict]:
        return records(self.url)

    def __exit__(self, *exc) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for f in self.dir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            self.dir.rmdir()
        except OSError:
            pass
