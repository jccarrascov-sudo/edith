"""Privacy controls that act on everything at once: "Delete everything now" and the logs.

Deleting removes all that the memory ever kept, on the spot: screenshots, audio, the timeline,
the day's text archive, the notes (block summaries and the year's important moments), the
conversations and what Edith learned about the user, and the plans learned from demonstrations. Verified machines in tools/ (which the user confirmed) and
the cost ledger are kept, and the panel says so."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading

from .config import ROOT, Config

log = logging.getLogger(__name__)


def delete_everything(cfg: Config) -> int:
    """Delete the memory's files. Returns how many files were removed. Pause the recorders first."""
    from .memory.timeline import Timeline

    removed = 0
    timeline = Timeline(cfg)
    with timeline._locked():
        if cfg.timeline_path.exists():
            cfg.timeline_path.unlink()
            removed += 1
    for folder in (cfg.frames_dir, cfg.audio_dir, cfg.buffer_dir / "day", ROOT / "state" / "plans"):
        if folder.exists():
            for f in folder.rglob("*"):
                if f.is_file():
                    f.unlink(missing_ok=True)
                    removed += 1
    for f in (cfg.notes_path, cfg.notes_path.with_suffix(".tmp"), cfg.buffer_dir / "conversation.jsonl",
              cfg.buffer_dir / "profile.jsonl"):
        if f.exists():
            f.unlink()
            removed += 1
    log.info("Deleted everything: %d files", removed)
    return removed


def setup_logs(verbose: bool = False) -> None:
    """Rotating logs in state/logs (1 MB x 4 files), and no crash on an unexpected error:
    it is logged and Edith keeps running."""
    folder = ROOT / "state" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / "edith.log", maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    logging.getLogger("edith").addHandler(handler)

    def on_error(kind, value, tb):
        logging.getLogger("edith").error("Unexpected error", exc_info=(kind, value, tb))

    sys.excepthook = on_error
    threading.excepthook = lambda a: on_error(a.exc_type, a.exc_value, a.exc_traceback)
