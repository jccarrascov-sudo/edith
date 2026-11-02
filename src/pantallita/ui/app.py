"""`pantallita companion`: the inventor on screen, with his memory running in the background."""

from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtWidgets import QApplication

from ..config import Config
from . import phrases
from .mascot import Mascot

log = logging.getLogger(__name__)


def start_memory(cfg: Config, stop: threading.Event) -> list[threading.Thread]:
    """The same recorders as `pantallita run`, as daemon threads of this process."""
    from ..memory.actions import ClickWatcher, FieldWatcher
    from ..memory.audio import AudioRecorder
    from ..memory.capture import PrivacyGate, ScreenCapture
    from ..memory.timeline import Timeline

    timeline = Timeline(cfg)
    timeline.prune()
    gate = PrivacyGate(cfg.capture["interval_s"])
    capture = ScreenCapture(cfg, timeline, stop, gate)
    workers = [capture.run, ClickWatcher(cfg, timeline, stop, gate, on_click=capture.burst).run,
               FieldWatcher(cfg, timeline, stop).run]
    if cfg.audio["enabled"]:
        workers.append(AudioRecorder(cfg, timeline, stop, gate).run)

    def janitor() -> None:
        while not stop.wait(cfg.retention["prune_every_s"]):
            try:
                timeline.prune()
            except Exception:
                log.exception("Prune failed")

    threads = [threading.Thread(target=w, daemon=True) for w in workers + [janitor]]
    for th in threads:
        th.start()
    return threads


class Companion:
    def __init__(self, cfg: Config, app: QApplication):
        self.cfg = cfg
        self.app = app
        self.lang = cfg.ui.get("language", "en")
        self.mascot = Mascot()
        self.mascot.clicked.connect(self.on_mascot_clicked)
        self.mascot.show()
        self.mascot.say(phrases.say("hello", self.lang), 4)

    def on_mascot_clicked(self) -> None:
        if self.mascot.in_workshop:
            self.mascot.leave_workshop()
            self.mascot.say(phrases.say("back", self.lang), 2.5)
        else:
            avail = self.app.primaryScreen().availableGeometry()
            self.mascot.go_to_workshop(avail.right() - 60)
            self.mascot.say(phrases.say("workshop", self.lang), 2.5)


def main(cfg: Config, memory: bool = True) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    stop = threading.Event()
    if memory:
        start_memory(cfg, stop)
    companion = Companion(cfg, app)  # noqa: F841 - kept alive by the event loop
    try:
        return app.exec()
    finally:
        stop.set()
