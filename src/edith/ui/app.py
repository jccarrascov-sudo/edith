"""`edith ui` (or `edith-ui`): Edith on screen, with its memory running in the same process."""

from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtWidgets import QApplication

from ..config import Config
from .agent import Agent
from .hotkey import GlobalHotkeys
from .outline import Outline
from .panel import Actions, Edith
from .stats import Sampler, StatsView
from .theme import Theme

log = logging.getLogger(__name__)


class Memory:
    """The recorders of `edith run` as daemon threads, which can be paused and resumed.
    Pausing stops them; resuming starts fresh ones on the same buffer."""

    def __init__(self, cfg: Config, local=None):
        self.cfg = cfg
        self.local = local          # the local model, for background notes
        self.stop_event: threading.Event | None = None
        self.recorder = None
        self.listening = True

    @property
    def running(self) -> bool:
        return self.stop_event is not None

    def start(self) -> None:
        from ..memory.actions import ClickWatcher, FieldWatcher
        from ..memory.audio import AudioRecorder
        from ..memory.capture import PrivacyGate, ScreenCapture
        from ..memory.screentext import TextWatcher
        from ..memory.timeline import Timeline

        if self.running:
            return
        cfg, stop = self.cfg, threading.Event()
        timeline = Timeline(cfg)
        timeline.prune()
        gate = PrivacyGate(cfg.capture["interval_s"])
        texts = TextWatcher(cfg, timeline, stop, gate)
        capture = ScreenCapture(cfg, timeline, stop, gate, on_frame=texts.poke)
        workers = [capture.run, texts.run, ClickWatcher(cfg, timeline, stop, gate, on_click=capture.burst).run,
                   FieldWatcher(cfg, timeline, stop).run]
        self.recorder = None
        if cfg.audio["enabled"]:
            self.recorder = AudioRecorder(cfg, timeline, stop, gate)
            self.recorder.listening = self.listening
            workers.append(self.recorder.run)

        def janitor() -> None:
            while not stop.wait(cfg.retention["prune_every_s"]):
                try:
                    timeline.prune()
                except Exception:
                    log.exception("Prune failed")

        if cfg.notes["enabled"] and self.local is not None:
            from ..brain.notes import NotesWorker
            workers.append(NotesWorker(cfg, timeline, self.local, stop).run)

        for w in workers + [janitor]:
            threading.Thread(target=w, daemon=True).start()
        self.stop_event = stop

    def stop(self) -> None:
        if self.stop_event:
            self.stop_event.set()
        self.stop_event = None

    def toggle(self) -> bool:
        self.stop() if self.running else self.start()
        return self.running

    def toggle_audio(self) -> bool:
        self.listening = not self.listening
        if self.recorder:
            self.recorder.listening = self.listening
        return self.listening


def main(cfg: Config, memory: bool = True) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Edith")
    app.setApplicationDisplayName("Edith")
    app.setQuitOnLastWindowClosed(False)
    from ..brain.local import LocalModel

    local = LocalModel(cfg)
    local.start()                      # loads and warms up in the background
    mem = Memory(cfg, local)
    if memory:
        mem.start()
    sampler_stop = threading.Event()
    theme = Theme()
    agent = Agent(cfg, local)
    local.on_notice = lambda key: None if agent.busy else agent.status.emit(agent.t(key))
    hotkeys = GlobalHotkeys()
    stats = StatsView(cfg, theme, Sampler(cfg, sampler_stop), agent.lang)

    def open_activity(panel: Edith) -> None:
        panel.collapse()
        stats.open_near(panel)

    edith = Edith(theme, agent, Actions(
        toggle_audio=mem.toggle_audio if cfg.audio["enabled"] else None,
        toggle_memory=mem.toggle, open_activity=open_activity, quit=app.quit), hotkeys)
    edith.set_memory_on(mem.running)
    outline = Outline(theme)          # presentation mode only
    agent.outline.connect(lambda rect: outline.show_at(rect) if rect and edith.presentation else outline.hide())
    edith.show()
    try:
        return app.exec()
    finally:
        mem.stop()
        sampler_stop.set()
        hotkeys.close()
        local.stop()


def launch() -> None:
    """Entry point of the windowless `edith-ui` executable."""
    from ..__main__ import main as cli
    cli(["ui"])
