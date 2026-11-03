"""What Edith does with a line typed in the panel, off the UI thread.

A question about the last minutes goes to Muse Spark with the memory (`brain.ask`). A task
("do the rest") runs agent mode: find the demonstration in memory, find or fabricate its
verified machine, run its proof dry, ask for confirmation, run it. Progress is ONE line of
gray text in the panel (`status`); nothing is drawn on the screen."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..config import Config
from ..learn import segment as seg_mod
from ..learn import source as src_mod
from ..memory.timeline import Timeline
from ..workshop import build
from ..workshop import proof as proof_mod

log = logging.getLogger(__name__)

TEXT = {
    "en": {
        "placeholder": "Ask about the last 10 minutes, or say “do the rest”",
        "thinking": "Thinking…",
        "rewinding": "Looking through the last minutes…",
        "no_demo": "I don't see a form you filled in the last {minutes} minutes. Do one by hand, then ask me again.",
        "planning": "Working out the steps…",
        "building": "Building and checking it against what you did…",
        "checking": "Checking “{name}” against its proof…",
        "repairing": "The proof failed. Repairing only the broken step…",
        "not_verified": "I couldn't make it match what you did, so I sent nothing.",
        "confirm": "Like {demo}.\nSend {n} rows{totals}.{skipped}\nEnter to send · Esc to cancel",
        "demo_ref": "your demo at {time} in {where}",
        "skipped_row": "\nRow {row} is skipped ({why}).",
        "empty": "empty {cols}",
        "writing": "Writing row {i} of {n} in {where}…",
        "cancelled": "Okay, nothing sent.",
        "done": "{ok} sent{skipped}{failed}. About {saved} saved.",
        "done_skipped": ", {n} skipped",
        "done_skipped_1": ", 1 skipped",
        "done_failed": ", {n} failed",
        "failed": "Something went wrong, so I stopped: {error}",
        "nothing": "There is nothing left to send.",
    },
    "es": {
        "placeholder": "Pregunta por los últimos 10 minutos, o di “haz el resto”",
        "thinking": "Pensando…",
        "rewinding": "Revisando los últimos minutos…",
        "no_demo": "No veo un formulario que hayas llenado en los últimos {minutes} minutos. Haz uno a mano y vuelve a pedírmelo.",
        "planning": "Entendiendo los pasos…",
        "building": "Construyendo y comprobando contra lo que hiciste…",
        "checking": "Comprobando “{name}” con su prueba…",
        "repairing": "La prueba falló. Reparando solo el paso roto…",
        "not_verified": "No logré que coincidiera con lo que hiciste, así que no envié nada.",
        "confirm": "Como {demo}.\nEnviar {n} filas{totals}.{skipped}\nEnter para enviar · Esc para cancelar",
        "demo_ref": "tu demo de las {time} en {where}",
        "skipped_row": "\nLa fila {row} se omite ({why}).",
        "empty": "{cols} vacío",
        "writing": "Escribiendo fila {i} de {n} en {where}…",
        "cancelled": "Listo, no envié nada.",
        "done": "{ok} enviadas{skipped}{failed}. Unos {saved} ahorrados.",
        "done_skipped": ", {n} omitidas",
        "done_skipped_1": ", 1 omitida",
        "done_failed": ", {n} con error",
        "failed": "Algo falló y me detuve: {error}",
        "nothing": "No queda nada por enviar.",
    },
}

# A request is a task (agent mode) when it asks to do/repeat something, not to explain it.
TASK = re.compile(
    r"\b(do|finish|repeat|fill|send|enter)\b.*\b(rest|others?|remaining|all|same|them)\b"
    r"|\b(haz|hazlo|hazme|termina|repite|llena|completa|env[ií]a|ingresa)\b.*\b(resto|dem[aá]s|otr[oa]s|tod[oa]s|mismo|ellas|ellos)\b"
    r"|^\s*(do it|do the rest|hazlo|haz el resto)\s*[.!]*\s*$", re.IGNORECASE)


def text(lang: str, key: str, **kw) -> str:
    return TEXT.get(lang, TEXT["en"]).get(key, TEXT["en"][key]).format(**kw)


def is_task(request: str) -> bool:
    return bool(TASK.search(request))


def origin(url: str | None) -> str:
    if not url:
        return ""
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def find_machine(workbook: Path, url: str) -> Path | None:
    """A verified machine in the workshop that already moves rows of this workbook into this site."""
    if not build.TOOLS_DIR.exists():
        return None
    for d in sorted(build.TOOLS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        plan_file = d / "plan.json"
        if not plan_file.exists():
            continue
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        same_source = Path(plan["source"]["path"]).name.lower() == workbook.name.lower()
        if same_source and origin(plan["target"]["url"]) == origin(url):
            return d
    return None


def duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes >= 1 else f"{round(seconds)} s"


class Agent(QObject):
    status = Signal(str)      # the one gray line ("" clears it)
    reply = Signal(str)       # the answer under the text field
    busy_changed = Signal(bool)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.lang = cfg.ui.get("language", "en")
        self.busy = False
        self.awaiting = False                  # a confirmation is on screen
        self._decision = threading.Event()
        self._confirmed = False

    def t(self, key: str, **kw) -> str:
        return text(self.lang, key, **kw)

    # --- UI thread ------------------------------------------------------------------------------
    def submit(self, request: str) -> None:
        request = request.strip()
        if not request or self.busy:
            return
        target = self._task if is_task(request) else self._ask
        self._set_busy(True)
        threading.Thread(target=self._guarded, args=(target, request), daemon=True).start()

    def decide(self, confirmed: bool) -> None:
        if self.awaiting:
            self._confirmed = confirmed
            self._decision.set()

    # --- worker ---------------------------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.busy_changed.emit(busy)

    def _guarded(self, target, request: str) -> None:
        try:
            target(request)
        except Exception as exc:  # never leave the panel waiting
            log.exception("request failed")
            self.reply.emit(self.t("failed", error=f"{type(exc).__name__}: {exc}"[:160]))
        finally:
            self.awaiting = False
            self.status.emit("")
            self._set_busy(False)

    def _ask(self, question: str) -> None:
        from ..brain.ask import answer

        self.status.emit(self.t("thinking"))
        Timeline(self.cfg).prune()
        reply, report = answer(self.cfg, question)
        if report:
            log.info(report)
        self.reply.emit(reply.strip())

    def _task(self, request: str) -> None:
        from ..learn import plan as plan_mod
        from ..learn.cli import spreadsheet_sources

        self.status.emit(self.t("rewinding"))
        Timeline(self.cfg).prune()
        events = Timeline(self.cfg).read()
        demos = seg_mod.find_demos(events)
        if not demos:
            self.reply.emit(self.t("no_demo", minutes=self.cfg.retention["minutes"]))
            return
        client = None
        if len(demos) > 1:
            from ..brain.client import MetaClient
            client = MetaClient(self.cfg)
        demo, _sure = seg_mod.choose(demos, request, time.time(), client, spreadsheet_sources(demos, events))

        titles = [e.get("window", "") for e in events if (e.get("process") or "").lower() == "excel.exe"]
        workbook = src_mod.find_workbook(titles)
        tool_dir = find_machine(workbook, demo.url) if workbook and demo.url else None
        if tool_dir:
            if not self._prove(tool_dir):
                return
        else:
            self.status.emit(self.t("planning"))
            plan = plan_mod.make_plan(self.cfg, demo, request, events, log_fn=log.info)
            plan_mod.save_plan(plan)
            self.status.emit(self.t("building"))
            code, v = build.fabricate(self.cfg, plan, log_fn=log.info)
            if not v.ok:
                self.reply.emit(self.t("not_verified"))
                return
            tool_dir = build.save_tool(plan, code, v)
        self._confirm_and_run(tool_dir, demo)

    def _demo_ref(self, demo: seg_mod.Segment) -> str:
        """Short: "your demo at 21:49 in Invoice Portal" (the page, not the browser's name)."""
        title = (demo.fields[-1].get("window") or "") if demo.fields else ""
        parts = [p.strip() for p in title.split(" - ") if p.strip()]
        where = parts[-2] if len(parts) >= 3 else (parts[0] if parts else demo.process)
        return self.t("demo_ref", time=time.strftime("%H:%M", time.localtime(demo.start)), where=where)

    def _why(self, reason: str) -> str:
        """build.summary's reasons ("empty Amount") in the panel's language."""
        return self.t("empty", cols=reason[6:]) if reason.startswith("empty ") else reason

    def _prove(self, tool_dir: Path) -> bool:
        """Dry proof before every use; on failure repair against the stored demo (golden rule)."""
        plan, code = build.load_tool(tool_dir)
        self.status.emit(self.t("checking", name=plan["name"]))
        ok, _problems, _stamp = build.dry_run(tool_dir, log_fn=log.info)
        if ok:
            return True
        self.status.emit(self.t("repairing"))
        _demo, expected = proof_mod.load(tool_dir)
        code, v = build.fabricate(self.cfg, plan, code=code, user_record=dict(expected["record"]), log_fn=log.info)
        if not v.ok:
            self.reply.emit(self.t("not_verified"))
            return False
        build.save_tool(plan, code, v)
        return True

    def _confirm_and_run(self, tool_dir: Path, demo: seg_mod.Segment) -> None:
        plan, _ = build.load_tool(tool_dir)
        rows = build.rows_to_run(plan)
        s = build.summary(plan, rows)
        if not s["send"]:
            self.reply.emit(self.t("nothing"))
            return
        totals = "".join(f" · {label} {value:,.2f}" for label, value in s["totals"].items())
        skipped = "".join(self.t("skipped_row", row=i + 2, why=self._why(why)) for i, why in s["skipped"])
        self.status.emit("")
        self._decision.clear()
        self.awaiting = True
        self.reply.emit(self.t("confirm", demo=self._demo_ref(demo), n=s["send"], totals=totals, skipped=skipped))
        self._decision.wait()
        self.awaiting = False
        if not self._confirmed:
            self.reply.emit(self.t("cancelled"))
            return

        self.reply.emit("")
        where = urllib.parse.urlsplit(plan["target"]["url"]).netloc or plan["target"]["url"]
        count = {"i": 0}

        def on_event(event: dict) -> None:
            if event.get("event") == "row_in":
                count["i"] += 1
                self.status.emit(self.t("writing", i=count["i"], n=s["send"], where=where))

        t0 = time.monotonic()
        results = build.run_rows_logged(tool_dir, rows, log_fn=log.info, on_event=on_event)
        ok = sum(r.ok for r in results)
        n_skipped = sum(bool(r.skipped) for r in results)
        failed = len(results) - ok - n_skipped
        saved = max(0.0, plan.get("seconds_per_row_by_hand", 0) * ok - (time.monotonic() - t0))
        self.reply.emit(self.t(
            "done", ok=ok, saved=duration(saved),
            skipped=(self.t("done_skipped_1") if n_skipped == 1 else self.t("done_skipped", n=n_skipped))
            if n_skipped else "",
            failed=self.t("done_failed", n=failed) if failed else ""))
