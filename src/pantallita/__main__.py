"""CLI entry point: python -m pantallita run | ask "question" | status"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from .config import load_config
from .memory.timeline import Timeline


def run(cfg, duration: float | None = None) -> None:
    from .memory.audio import AudioRecorder
    from .memory.capture import PrivacyGate, ScreenCapture

    log = logging.getLogger("pantallita")
    timeline = Timeline(cfg)
    timeline.prune()
    stop = threading.Event()
    gate = PrivacyGate(cfg.capture["interval_s"])
    capture = ScreenCapture(cfg, timeline, stop, gate)
    workers = [threading.Thread(target=capture.run, name="capture", daemon=True)]
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
        log.info("Stopped: %d frames saved, %d excluded ticks%s",
                 capture.saved, capture.excluded_ticks, audio)


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
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("pantallita").setLevel(logging.DEBUG if args.verbose else logging.INFO)
    # Windows consoles default to a legacy code page; window titles and answers need UTF-8.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    cfg = load_config()
    if args.command == "run":
        run(cfg, args.duration)
    elif args.command == "ask":
        from .brain.ask import ask
        Timeline(cfg).prune()
        ask(cfg, args.question, dry_run=args.dry_run)
    elif args.command == "status":
        Timeline(cfg).prune()
        from .status import status
        status(cfg)


if __name__ == "__main__":
    main()
