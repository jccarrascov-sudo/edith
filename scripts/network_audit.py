"""Network audit: which connections does Edith open, and when?

Runs Edith's whole memory (screen, screen text, clicks, fields, system audio, day archive,
background notes with the local model) in an isolated data folder for --seconds, while every
connection the Python process opens is logged (socket.connect / getaddrinfo hooks, with the
module that asked) and the local model server's connections are polled with netstat. Then it
asks one question (the only moment something may leave the PC) and the same question with no
connection to the cloud. Writes docs/NETWORK_AUDIT.md.

    .venv\\Scripts\\python scripts\\network_audit.py --seconds 90
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOG: list[dict] = []
PHASE = {"name": "start"}


def _caller() -> str:
    for frame in reversed(traceback.extract_stack()[:-3]):
        f = frame.filename.replace("\\", "/")
        if "/edith/" in f or "site-packages" in f:
            mod = f.split("site-packages/")[-1] if "site-packages" in f else "edith/" + f.split("/edith/")[-1]
            return mod.split("/")[0] if "site-packages" in f else mod
    return "?"


def _hook() -> None:
    real_connect, real_gai = socket.socket.connect, socket.getaddrinfo

    def connect(self, address):
        LOG.append({"phase": PHASE["name"], "what": "connect", "to": f"{address[0]}:{address[1]}"
                    if isinstance(address, tuple) else str(address), "by": _caller()})
        return real_connect(self, address)

    def getaddrinfo(host, *a, **kw):
        LOG.append({"phase": PHASE["name"], "what": "dns", "to": str(host), "by": _caller()})
        return real_gai(host, *a, **kw)

    socket.socket.connect = connect
    socket.getaddrinfo = getaddrinfo


def _netstat(pid: int) -> set[str]:
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    peers = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[-1] == str(pid) and parts[3] in ("ESTABLISHED", "SYN_SENT", "TIME_WAIT"):
            peers.add(parts[2])
    return peers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--question", default="What have I been doing in the last minutes?")
    args = ap.parse_args()

    home = Path(tempfile.mkdtemp(prefix="edith-audit-"))
    for name in (".env", "config.toml"):
        if (REPO / name).exists():
            shutil.copy(REPO / name, home / name)
    os.environ["EDITH_HOME"] = str(home)
    _hook()

    from edith.brain.answer import Router
    from edith.brain.local import LocalModel
    from edith.config import load_config
    from edith.ui.agent import text as ui_text
    from edith.ui.app import Memory

    cfg = load_config()
    PHASE["name"] = "memory"
    local = LocalModel(cfg)
    local.start()
    mem = Memory(cfg, local)
    mem.start()
    server_peers: set[str] = set()
    stop = threading.Event()

    def poll() -> None:
        while not stop.wait(2):
            if local.proc:
                server_peers.update(_netstat(local.proc.pid))

    threading.Thread(target=poll, daemon=True).start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        time.sleep(1)
    mem.stop()
    events = Counter()
    from edith.memory.timeline import Timeline
    for e in Timeline(cfg).read():
        events[e["type"]] += 1

    router = Router(cfg, local, lambda k, **kw: ui_text("en", k, **kw))
    PHASE["name"] = "question (cloud)"
    cloud = router.respond(args.question)
    PHASE["name"] = "question (no connection)"
    os.environ["MODEL_API_BASE_URL"] = "http://127.0.0.1:9/v1"     # the cloud is unreachable
    offline = router.respond(args.question)
    stop.set()
    local.stop()
    shutil.rmtree(home, ignore_errors=True)

    def external(to: str) -> bool:
        host = to.rsplit(":", 1)[0]
        return host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0")

    rows = Counter((r["phase"], r["what"], r["to"], r["by"]) for r in LOG)
    lines = ["# Network audit", "",
             f"Run on {time.strftime('%Y-%m-%d %H:%M')} by `scripts/network_audit.py` (memory for "
             f"{args.seconds:.0f} s on the real desktop, isolated data folder, then one question).", "",
             f"Recorded during the run: {dict(events)}.", "",
             "| Phase | Kind | Destination | Opened by | Count | Leaves the PC |", "|---|---|---|---|---|---|"]
    for (phase, what, to, by), n in sorted(rows.items()):
        lines.append(f"| {phase} | {what} | `{to}` | `{by}` | {n} | {'**yes**' if external(to) else 'no'} |")
    ext_memory = [k for k in rows if k[0] == "memory" and external(k[2])]
    lines += ["", f"llama-server (local model) TCP peers seen by netstat: "
              f"{', '.join(sorted(server_peers)) or 'none besides Edith'}.",
              f"Server peers outside this PC: {sorted(p for p in server_peers if external(p)) or 'none'}.", "",
              "## Result", "",
              f"- While recording (no question asked): **{len(ext_memory)} connections left the PC**"
              + (": " + ", ".join(f"`{k[2]}` by `{k[3]}`" for k in ext_memory) if ext_memory else "."),
              f"- The question with the cloud reachable was answered by **{cloud.where}**; "
              f"what was sent: *{cloud.footnote}*.",
              f"- The same question with the cloud unreachable was answered by **{offline.where}** "
              f"(*{offline.footnote}*).", ""]
    out = REPO / "docs" / "NETWORK_AUDIT.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
