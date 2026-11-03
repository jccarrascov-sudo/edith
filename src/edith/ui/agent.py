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

log = logging.getLogger(__name__)

TEXT = {
    "en": {
        "placeholder": "Ask about your day, or say “do the rest”",
        "thinking": "Thinking…",
        "rewinding": "Looking through the last minutes…",
        "no_demo": "I don't see a form you filled in the last {minutes} minutes. Do one by hand, then ask me again.",
        "planning": "Working out the steps…",
        "building": "Building and checking it against what you did…",
        "checking": "Checking “{name}” against its proof…",
        "repairing": "The page changed. Repairing only what moved…",
        "compiling": "Looking for a faster way to send it…",
        "not_verified": "I couldn't make it match what you did, so I sent nothing.",
        "no_record": "I can't find what your demo saved in the page, so I have nothing to check against. "
                     "Do one by hand, then ask me again.",
        "cant_build": "I couldn't build it ({error}), so I sent nothing.",
        "ambiguous": "The form changed and I'm not sure where “{column}” goes now: {options}. "
                     "Show me one: fill one by hand, then ask me again.",
        "repaired": "The form changed: “{column}” now goes in “{new}”. I checked it against your demo again.\n",
        "confirm": "{note}Like {demo}.\nSend {n} rows{totals}.{skipped}\nEnter to send · Esc to cancel",
        "demo_ref": "your demo at {time} in {where}",
        "skipped_row": "\nRow {row} is skipped ({why}).",
        "empty": "empty {cols}",
        "writing": "Writing row {i} of {n} in {where} · {level}…",
        "cancelled": "Okay, nothing sent.",
        "done": "{ok} sent{skipped}{failed} ({level} level, {seconds}). About {saved} saved.",
        "level_network": "network",
        "level_page": "page",
        "done_skipped": ", {n} skipped",
        "done_skipped_1": ", 1 skipped",
        "done_failed": ", {n} failed",
        "failed": "Something went wrong, so I stopped: {error}",
        "nothing": "There is nothing left to send.",
        "hotkey_label": "Shortcut: {combo}",
        "hotkey_press": "Press the combination… (Esc cancels)",
        "hotkey_need_mod": "Use Ctrl, Alt, Shift or Win plus a letter, digit or F key.",
        "hotkey_taken": "{combo} is already used by another app. Keeping {old}.",
        "hotkey_saved": "Shortcut: {combo}.",
        "hotkey_default": "Default",
        "sent": "Sent: {what} · {cost}",
        "sent_screen_texts": "{n} screen texts", "sent_screen_texts_1": "1 screen text",
        "sent_summaries": "{n} summaries", "sent_summaries_1": "1 summary",
        "sent_moments": "{n} key moments", "sent_moments_1": "1 key moment",
        "sent_archive": "{n} lines from earlier today", "sent_archive_1": "1 line from earlier today",
        "sent_transcripts": "{n} transcript lines", "sent_transcripts_1": "1 transcript line",
        "sent_screenshots": "{n} screenshots", "sent_screenshots_1": "1 screenshot",
        "sent_windows": "the window history",
        "budget_warn": " · {pct} % of this month's budget",
        "answered_locally": "Answered locally",
        "why_local_only": "",
        "why_budget": "monthly limit reached",
        "why_session_brake": "session limit reached",
        "why_no_key": "no API key",
        "why_offline": "no connection",
        "no_local": "I can't answer right now: the cloud is not available ({why}) and the local model is not ready.",
        "local_restarting": "The local model stopped; restarting it…",
        "local_restarted": "The local model is back.",
        "confirm_images": "This question needs {n} screenshots. Send them to Muse Spark?\nEnter to send · Esc for text only",
        "local_only_on": "Local only: on",
        "local_only_off": "Local only: off",
        "about_moment": "What happened at {time} ({text})?",
        "privacy": "Your screen never leaves your PC. Only what you ask goes to the cloud.",
        "edit_no_target": "Click in the field where the text should go, then open me with Ctrl+Alt+E and ask again.",
        "edit_nothing": "I didn't find that in the last minutes ({why}), so I wrote nothing.",
        "edit_hint": "Enter to apply · Esc to discard",
        "based_on": "Based on: {source} · {ago}",
        "ago_s": "{n} s ago", "ago_min": "{n} min ago",
        "edit_done": "Done. You can undo it from here.",
        "edit_discarded": "Discarded. Nothing was written.",
        "edit_failed": "I couldn't write it ({why}). Nothing was changed.",
        "why_edit_unreadable": "the field can't be read",
        "why_edit_window_gone": "the window is no longer there",
        "why_edit_not_written": "the text didn't appear in the field",
        "why_edit_no_json": "the reply had no edit",
        "why_edit_empty": "empty text",
        "undo_label": "Undo Edith's last edit",
        "undo_done": "Undone.",
        "undo_failed": "I couldn't undo it ({why}).",
        "why_undo_nothing": "there is no edit to undo",
        "why_undo_window_gone": "the window is no longer there",
        "why_undo_changed_since": "you changed the text after my edit; undoing would lose your changes",
        "why_undo_not_undone": "the app's undo didn't bring the text back",
        "presentation_on": "Presentation mode: on",
        "presentation_off": "Presentation mode: off",
        "onb1_title": "Edith remembers the last minutes",
        "onb1_body": "It keeps the last 10 minutes of your screen and audio, and today's text, so you can ask "
                     "what happened, or say “put that in my notes” or “do the rest”. "
                     "It always shows you what it will do first.",
        "onb2_title": "Private by design",
        "onb2_body": "Your screen never leaves your PC. Only what you ask goes to the cloud.\n\n"
                     "Screenshots and audio last 10 minutes, the day's text 24 hours, important dates a year. "
                     "Pause or delete everything at any time in the options.",
        "onb3_title": "Open it from anywhere",
        "onb3_body": "Press {combo} in any app, or click the dot. You can change the shortcut now.",
        "onb_change": "Change shortcut",
        "onb_next": "Next",
        "onb_start": "Start",
        "delete_all": "Delete everything now",
        "delete_confirm": "Click again to delete everything",
        "deleted": "Deleted: {n} files (screenshots, audio, the day's text, notes and important moments). "
                   "Verified machines and the cost record are kept.",
    },
    "es": {
        "placeholder": "Pregunta por tu día, o di “haz el resto”",
        "thinking": "Pensando…",
        "rewinding": "Revisando los últimos minutos…",
        "no_demo": "No veo un formulario que hayas llenado en los últimos {minutes} minutos. Haz uno a mano y vuelve a pedírmelo.",
        "planning": "Entendiendo los pasos…",
        "building": "Construyendo y comprobando contra lo que hiciste…",
        "checking": "Comprobando “{name}” con su prueba…",
        "repairing": "La página cambió. Reparando solo lo que se movió…",
        "compiling": "Buscando una forma más rápida de enviarlo…",
        "not_verified": "No logré que coincidiera con lo que hiciste, así que no envié nada.",
        "no_record": "No encuentro lo que guardó tu demo en la página, así que no tengo con qué comprobar. "
                     "Haz una a mano y vuelve a pedírmelo.",
        "cant_build": "No pude construirla ({error}), así que no envié nada.",
        "ambiguous": "El formulario cambió y no sé dónde va ahora “{column}”: {options}. "
                     "Muéstrame una: llena una a mano y vuelve a pedírmelo.",
        "repaired": "El formulario cambió: “{column}” ahora va en “{new}”. Lo volví a comprobar con tu demo.\n",
        "confirm": "{note}Como {demo}.\nEnviar {n} filas{totals}.{skipped}\nEnter para enviar · Esc para cancelar",
        "demo_ref": "tu demo de las {time} en {where}",
        "skipped_row": "\nLa fila {row} se omite ({why}).",
        "empty": "{cols} vacío",
        "writing": "Escribiendo fila {i} de {n} en {where} · {level}…",
        "cancelled": "Listo, no envié nada.",
        "done": "{ok} enviadas{skipped}{failed} (nivel {level}, {seconds}). Unos {saved} ahorrados.",
        "level_network": "red",
        "level_page": "página",
        "done_skipped": ", {n} omitidas",
        "done_skipped_1": ", 1 omitida",
        "done_failed": ", {n} con error",
        "failed": "Algo falló y me detuve: {error}",
        "nothing": "No queda nada por enviar.",
        "hotkey_label": "Atajo: {combo}",
        "hotkey_press": "Presiona la combinación… (Esc cancela)",
        "hotkey_need_mod": "Usa Ctrl, Alt, Shift o Win más una letra, un número o una tecla F.",
        "hotkey_taken": "Otra app ya usa {combo}. Se mantiene {old}.",
        "hotkey_saved": "Atajo: {combo}.",
        "hotkey_default": "Por defecto",
        "sent": "Enviado: {what} · {cost}",
        "sent_screen_texts": "{n} textos de pantalla", "sent_screen_texts_1": "1 texto de pantalla",
        "sent_summaries": "{n} resúmenes", "sent_summaries_1": "1 resumen",
        "sent_moments": "{n} momentos clave", "sent_moments_1": "1 momento clave",
        "sent_archive": "{n} líneas de más temprano", "sent_archive_1": "1 línea de más temprano",
        "sent_transcripts": "{n} líneas de audio", "sent_transcripts_1": "1 línea de audio",
        "sent_screenshots": "{n} capturas", "sent_screenshots_1": "1 captura",
        "sent_windows": "el historial de ventanas",
        "budget_warn": " · {pct} % del presupuesto del mes",
        "answered_locally": "Respondido localmente",
        "why_local_only": "",
        "why_budget": "límite mensual alcanzado",
        "why_session_brake": "límite de la sesión alcanzado",
        "why_no_key": "sin clave de API",
        "why_offline": "sin conexión",
        "no_local": "No puedo responder ahora: la nube no está disponible ({why}) y el modelo local no está listo.",
        "local_restarting": "El modelo local se detuvo; reiniciándolo…",
        "local_restarted": "El modelo local volvió.",
        "confirm_images": "Esta pregunta necesita {n} capturas. ¿Enviarlas a Muse Spark?\nEnter para enviar · Esc para solo texto",
        "local_only_on": "Solo local: sí",
        "local_only_off": "Solo local: no",
        "about_moment": "¿Qué pasó a las {time} ({text})?",
        "privacy": "Tu pantalla nunca sale de tu PC. Solo lo que preguntas va a la nube.",
        "edit_no_target": "Haz clic en el campo donde va el texto, ábreme con Ctrl+Alt+E y vuelve a pedírmelo.",
        "edit_nothing": "No encontré eso en los últimos minutos ({why}), así que no escribí nada.",
        "edit_hint": "Enter para aplicar · Esc para descartar",
        "based_on": "Basado en: {source} · {ago}",
        "ago_s": "hace {n} s", "ago_min": "hace {n} min",
        "edit_done": "Listo. Puedes deshacerlo desde aquí.",
        "edit_discarded": "Descartado. No se escribió nada.",
        "edit_failed": "No pude escribirlo ({why}). No se cambió nada.",
        "why_edit_unreadable": "no se puede leer el campo",
        "why_edit_window_gone": "la ventana ya no está",
        "why_edit_not_written": "el texto no apareció en el campo",
        "why_edit_no_json": "la respuesta no traía una edición",
        "why_edit_empty": "texto vacío",
        "undo_label": "Deshacer la última edición de Edith",
        "undo_done": "Deshecho.",
        "undo_failed": "No pude deshacerlo ({why}).",
        "why_undo_nothing": "no hay ninguna edición que deshacer",
        "why_undo_window_gone": "la ventana ya no está",
        "why_undo_changed_since": "cambiaste el texto después de mi edición; deshacer borraría tus cambios",
        "why_undo_not_undone": "el deshacer de la app no recuperó el texto",
        "presentation_on": "Modo presentación: sí",
        "presentation_off": "Modo presentación: no",
        "onb1_title": "Edith recuerda los últimos minutos",
        "onb1_body": "Guarda los últimos 10 minutos de tu pantalla y audio, y el texto del día, para que "
                     "preguntes qué pasó, o digas “pásalo a mis apuntes” o “haz el resto”. "
                     "Siempre te muestra antes lo que va a hacer.",
        "onb2_title": "Privada por diseño",
        "onb2_body": "Tu pantalla nunca sale de tu PC. Solo lo que preguntas va a la nube.\n\n"
                     "Las capturas y el audio duran 10 minutos, el texto del día 24 horas, las fechas importantes "
                     "un año. Pausa o borra todo cuando quieras en las opciones.",
        "onb3_title": "Ábrela desde cualquier lugar",
        "onb3_body": "Presiona {combo} en cualquier app, o haz clic en el punto. Puedes cambiar el atajo ahora.",
        "onb_change": "Cambiar atajo",
        "onb_next": "Siguiente",
        "onb_start": "Empezar",
        "delete_all": "Borrar todo ahora",
        "delete_confirm": "Haz clic otra vez para borrar todo",
        "deleted": "Borrado: {n} archivos (capturas, audio, el texto del día, notas y momentos importantes). "
                   "Se conservan las máquinas verificadas y el registro de costos.",
    },
}

# A request is a task (agent mode) when it asks to do/repeat something, not to explain it.
TASK = re.compile(
    r"\b(do|finish|repeat|fill|send|enter)\b.*\b(rest|others?|remaining|all|same|them)\b"
    r"|\b(haz|hazlo|hazme|termina|repite|llena|completa|env[ií]a|ingresa)\b.*\b(resto|dem[aá]s|otr[oa]s|tod[oa]s|mismo|ellas|ellos)\b"
    r"|^\s*(do it|do the rest|hazlo|haz el resto)\s*[.!]*\s*$", re.IGNORECASE)


def text(lang: str, key: str, **kw) -> str:
    return TEXT.get(lang, TEXT["en"]).get(key, TEXT["en"][key]).format(**kw)


UNDO = re.compile(r"^\s*(undo|undo (it|that|the last edit)|deshaz(lo)?|deshacer)\b", re.IGNORECASE)


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


def shown_again(tool_dir: Path, demo: seg_mod.Segment) -> bool:
    """The user did the task again after the machine was built, in a form with different
    fields (e.g. after Edith asked "show me one"): learn from that new demo instead."""
    plan = json.loads((tool_dir / "plan.json").read_text(encoding="utf-8"))
    labels = {b["field"]["label"] for b in plan["blocks"] if b["type"] == "fill_field"}
    return demo.end > plan["demo"].get("end", 0) + 1 and set(demo.final_values) != labels


def duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes >= 1 else f"{round(seconds)} s"


class Agent(QObject):
    status = Signal(str)      # the one gray line ("" clears it)
    preview = Signal(object)  # an edit to confirm (brain.edit.Proposal), or None when it is gone
    outline = Signal(object)  # presentation mode: the screen rectangle Edith acts on, or None
    undoable = Signal(bool)   # an applied edit can be undone
    _ui = Signal(object)      # run a function on the UI thread (clipboard, keys)
    reply = Signal(str)       # the answer under the text field (repeated while it streams)
    footnote = Signal(str)    # gray line under the answer: what was sent, or "Answered locally"
    busy_changed = Signal(bool)

    def __init__(self, cfg: Config, local=None):
        from ..brain.answer import Router

        super().__init__()
        self.cfg = cfg
        self.lang = cfg.ui.get("language", "en")
        self.busy = False
        self.awaiting = False                  # a confirmation is on screen
        self._decision = threading.Event()
        self._confirmed = False
        self.router = Router(cfg, local, self.t)
        from ..edit.target import Typist

        self.typist = Typist()
        self.target = None                     # the field in front when the panel was opened
        self._ui.connect(lambda job: job())

    def t(self, key: str, **kw) -> str:
        return text(self.lang, key, **kw)

    # --- UI thread ------------------------------------------------------------------------------
    def submit(self, request: str) -> None:
        request = request.strip()
        if not request or self.busy:
            return
        from ..brain.edit import is_edit

        target = (self._task if is_task(request) else self._undo if UNDO.search(request)
                  else self._edit if is_edit(request) else self._ask)
        self.footnote.emit("")
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
        self.status.emit(self.t("thinking"))
        streamed = {"text": "", "shown": 0.0}

        def on_token(piece: str) -> None:
            if not streamed["text"]:
                self.status.emit("")
            streamed["text"] += piece
            now = time.monotonic()
            if now - streamed["shown"] > 0.12:          # repaint at most ~8 times a second
                streamed["shown"] = now
                self.reply.emit(streamed["text"].strip())

        answer = self.router.respond(question, on_token=on_token, confirm_images=self._confirm_images)
        self.reply.emit(answer.text)
        self.footnote.emit(answer.footnote)

    # --- edit from memory -----------------------------------------------------------------------
    def _on_ui(self, fn):
        """Run fn on the UI thread and wait for its result."""
        box, done = [], threading.Event()

        def job():
            try:
                box.append(fn())
            except Exception as exc:
                log.exception("UI job failed")
                box.append(exc)
            finally:
                done.set()

        self._ui.emit(job)
        done.wait(60)
        if not box or isinstance(box[0], Exception):
            raise RuntimeError(f"UI job failed: {box[0] if box else 'timeout'}")
        return box[0]

    def _edit(self, request: str) -> None:
        from ..brain import edit as ed

        t = self.target
        if t is None:
            self.reply.emit(self.t("edit_no_target"))
            return
        self.status.emit(self.t("rewinding"))
        r = self.router.respond(request, system=ed.EDIT_PROMPT,
                                extra=ed.target_block(t.title, t.process, t.kind, t.name, t.text))
        if r.where == "none":
            self.reply.emit(r.text)
            return
        p = ed.parse(r.text, time.time())
        if isinstance(p, str):
            why = self.t(f"why_edit_{p}") if p in ("no_json", "empty") else p
            self.reply.emit(self.t("edit_nothing", why=why))
            return
        timeline = Timeline(self.cfg)
        frames = [e for e in timeline.read() if e["type"] == "frame" and timeline.buffer_path(e["path"]).exists()]
        fr = ed.source_frame(frames, p.source_ts)
        p.frame = str(timeline.buffer_path(fr["path"])) if fr else None
        if p.source_ts is None and fr:
            p.source_ts = fr["ts"]
        self.status.emit("")
        self.footnote.emit(r.footnote)
        self._decision.clear()
        self.awaiting = True
        self.preview.emit(p)
        self.outline.emit(t.rect)
        self._decision.wait()
        self.awaiting = False
        if not self._confirmed:
            self.preview.emit(None)
            self.outline.emit(None)
            self.reply.emit(self.t("edit_discarded"))
            return
        ok, why = self._on_ui(lambda: self.typist.apply(t, p.mode, p.text))
        self.preview.emit(None)
        self.outline.emit(None)
        self.undoable.emit(self.typist.last is not None)
        self.reply.emit(self.t("edit_done") if ok else self.t("edit_failed", why=self.t(f"why_edit_{why}")))

    def _undo(self, _request: str = "") -> None:
        ok, why = self._on_ui(self.typist.undo)
        self.undoable.emit(self.typist.last is not None)
        self.reply.emit(self.t("undo_done") if ok else self.t("undo_failed", why=self.t(f"why_undo_{why}")))

    def _confirm_images(self, n: int) -> bool:
        """Ask before screenshots go to the cloud (only if cloud.confirm_images is on)."""
        self._decision.clear()
        self.awaiting = True
        self.status.emit("")
        self.reply.emit(self.t("confirm_images", n=n))
        self._decision.wait()
        self.awaiting = False
        self.reply.emit("")
        self.status.emit(self.t("thinking"))
        return self._confirmed

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
        self.note = ""
        if tool_dir and shown_again(tool_dir, demo):
            log.info("a newer demo fills a different form: building a new machine from it")
            tool_dir = None
        if tool_dir:
            if not self._prove(tool_dir):
                return
        else:
            self.status.emit(self.t("planning"))
            plan = plan_mod.make_plan(self.cfg, demo, request, events, log_fn=log.debug)   # holds row data
            plan_mod.save_plan(plan)
            self.status.emit(self.t("building"))
            try:
                code, v = build.fabricate(self.cfg, plan, log_fn=log.debug)
            except build.BuildError as exc:
                self.reply.emit(self.t(exc.code) if exc.code else self.t("cant_build", error=str(exc)))
                return
            if not v.ok:
                self.reply.emit(self.t("not_verified"))
                return
            tool_dir = build.save_tool(plan, code, v)
            self.status.emit(self.t("compiling"))
            build.compile_down(tool_dir, log_fn=log.debug)
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
        plan, _code = build.load_tool(tool_dir)
        allow = self.cfg.network["allow"]
        self.status.emit(self.t("checking", name=plan["name"]))
        ok, _problems, _stamp = build.dry_run(tool_dir, log_fn=log.debug, allow=allow)
        if ok:
            return True
        self.status.emit(self.t("repairing"))
        r = build.repair(self.cfg, tool_dir, allow=allow, log_fn=log.debug)
        if r.ambiguous:
            a = r.ambiguous[0]
            self.reply.emit(self.t("ambiguous", column=a.column, options=" / ".join(f"“{c}”" for c in a.candidates)))
            return False
        if not r.ok:
            self.reply.emit(self.t("not_verified"))
            return False
        self.note = "".join(self.t("repaired", column=x.column, new=x.new_label) for x in r.relocated)
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
        self.reply.emit(self.t("confirm", note=getattr(self, "note", ""), demo=self._demo_ref(demo), n=s["send"],
                               totals=totals, skipped=skipped))
        self._decision.wait()
        self.awaiting = False
        if not self._confirmed:
            self.reply.emit(self.t("cancelled"))
            return

        self.reply.emit("")
        where = urllib.parse.urlsplit(plan["target"]["url"]).netloc or plan["target"]["url"]
        allow = self.cfg.network["allow"]
        level = self.t(f"level_{build.level_for(tool_dir, allow)}")
        count = {"i": 0}

        def on_event(event: dict) -> None:
            if event.get("event") == "row_in":
                count["i"] += 1
                self.status.emit(self.t("writing", i=count["i"], n=s["send"], where=where, level=level))

        t0 = time.monotonic()
        results = build.run_rows_logged(tool_dir, rows, log_fn=log.debug, on_event=on_event, allow=allow)
        seconds = time.monotonic() - t0
        ok = sum(r.ok for r in results)
        n_skipped = sum(bool(r.skipped) for r in results)
        failed = len(results) - ok - n_skipped
        saved = max(0.0, plan.get("seconds_per_row_by_hand", 0) * ok - seconds)
        self.reply.emit(self.t(
            "done", ok=ok, saved=duration(saved), level=level,
            seconds=f"{seconds:.1f} s" if seconds < 60 else duration(seconds),
            skipped=(self.t("done_skipped_1") if n_skipped == 1 else self.t("done_skipped", n=n_skipped))
            if n_skipped else "",
            failed=self.t("done_failed", n=failed) if failed else ""))
