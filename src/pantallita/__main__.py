"""CLI entry point: python -m pantallita run | ask "question" | status"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from .config import load_config
from .memory.timeline import Timeline


def run(cfg, duration: float | None = None) -> None:
    from .memory.actions import ClickWatcher, FieldWatcher
    from .memory.audio import AudioRecorder
    from .memory.capture import PrivacyGate, ScreenCapture

    log = logging.getLogger("pantallita")
    timeline = Timeline(cfg)
    timeline.prune()
    stop = threading.Event()
    gate = PrivacyGate(cfg.capture["interval_s"])
    capture = ScreenCapture(cfg, timeline, stop, gate)
    clicks = ClickWatcher(cfg, timeline, stop, gate, on_click=capture.burst)
    fields = FieldWatcher(cfg, timeline, stop)
    workers = [threading.Thread(target=capture.run, name="capture", daemon=True),
               threading.Thread(target=clicks.run, name="clicks", daemon=True),
               threading.Thread(target=fields.run, name="fields", daemon=True)]
    recorder = None
    if cfg.audio["enabled"]:
        recorder = AudioRecorder(cfg, timeline, stop, gate)
        workers.append(threading.Thread(target=recorder.run, name="audio", daemon=True))

    def janitor() -> None:
        while not stop.wait(cfg.retention["prune_every_s"]):
            try:
                timeline.prune()
            except Exception:
                log.exception("Prune failed")

    workers.append(threading.Thread(target=janitor, name="janitor", daemon=True))
    for w in workers:
        w.start()
    log.info("pantallita is running. Press Ctrl+C to stop.")
    deadline = time.monotonic() + duration if duration else None
    try:
        # Short waits keep Ctrl+C responsive on Windows.
        while not stop.wait(0.5):
            if deadline and time.monotonic() >= deadline:
                break
    except KeyboardInterrupt:
        log.info("Stopping...")
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=10)
        timeline.prune()
        audio = (f", {recorder.blocks} audio blocks ({recorder.silent_blocks} silent, "
                 f"{recorder.private_blocks} dropped for privacy), "
                 f"{recorder.segments} transcript segments") if recorder else ""
        log.info("Stopped: %d frames saved, %d excluded ticks, %d clicks (%d private, %d injected "
                 "ignored), %d field changes%s", capture.saved, capture.excluded_ticks, clicks.clicks,
                 clicks.private_clicks, clicks.injected_clicks, fields.changes, audio)


def _dpi_aware() -> None:
    """Physical pixels everywhere, so click points, UIA rectangles and screenshots agree."""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pantallita")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="capture screen and system audio until Ctrl+C")
    p_run.add_argument("--duration", type=float, help="stop automatically after N seconds")
    p_ask = sub.add_parser("ask", help="ask a question about the last minutes")
    p_ask.add_argument("question")
    p_ask.add_argument("--dry-run", action="store_true", help="show the context, do not call the API")
    sub.add_parser("status", help="show what is in the buffer")
    p_do = sub.add_parser("do", help='learn from what you just did, e.g. do "do the rest"')
    p_do.add_argument("request", nargs="?", default="do the rest",
                      help='e.g. "what I did 5 minutes ago, do it with the rest"')
    p_do.add_argument("--yes", action="store_true", help="do not ask to confirm the demonstration")
    p_do.add_argument("--plan-only", action="store_true", help="stop after making the plan")
    p_build = sub.add_parser("build", help="fabricate and verify a machine from a plan (default: the latest)")
    p_build.add_argument("plan", nargs="?", type=Path)
    p_build.add_argument("--yes", action="store_true", help="run it on the other rows without asking")
    p_machine = sub.add_parser("machine", help="run a machine from the workshop (no name: list them)")
    p_machine.add_argument("name", nargs="?")
    p_cost = sub.add_parser("cost", help="show the API cost of the current session")
    p_cost.add_argument("--new", action="store_true", help="start a new cost session")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("pantallita").setLevel(logging.DEBUG if args.verbose else logging.INFO)
    # Windows consoles default to a legacy code page; window titles and answers need UTF-8.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    _dpi_aware()
    cfg = load_config()
    if args.command == "run":
        run(cfg, args.duration)
    elif args.command == "ask":
        from .brain.ask import ask
        Timeline(cfg).prune()
        ask(cfg, args.question, dry_run=args.dry_run)
    elif args.command == "cost":
        from .brain.client import CostMeter
        meter = CostMeter(float(cfg.budget["session_limit_usd"]), float(cfg.budget["session_idle_hours"]))
        if args.new:
            meter.new_session()
        print(meter.summary())
    elif args.command == "do":
        from .learn.cli import do_the_rest
        Timeline(cfg).prune()
        code = do_the_rest(cfg, args.request, confirm=not args.yes)
        if code == 0 and not args.plan_only:
            from .workshop.cli import build_and_run
            code = build_and_run(cfg, confirm=not args.yes)
        sys.exit(code)
    elif args.command == "build":
        from .workshop.cli import build_and_run
        sys.exit(build_and_run(cfg, args.plan, confirm=not args.yes))
    elif args.command == "machine":
        from .workshop.cli import find_tool, list_tools, run_machine
        if not args.name:
            list_tools()
        else:
            tool = find_tool(args.name)
            sys.exit(run_machine(tool) if tool else print(f"No machine called {args.name!r}.") or 1)
    elif args.command == "status":
        Timeline(cfg).prune()
        from .status import status
        status(cfg)


if __name__ == "__main__":
    main()
