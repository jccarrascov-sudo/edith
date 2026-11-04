"""Who answers a question: Muse Spark in the cloud, or Muse Glimmer on this PC.

Muse Spark is only called when the user asks something, with the cheapest context that can
answer (see ask.py: text first; at most `cloud.max_images` downscaled screenshots, only if the
question needs to see something). The panel then shows, in gray, what was sent
("Sent: 3 screen texts, 1 screenshot · $0.002").

The local model answers instead, with text only (plus at most one small screenshot it
describes itself, if the question needs to see something) when:
  - "Local only" is on ($0, nothing is sent),
  - this month's cloud cost reached the monthly limit (80 % only warns),
  - there is no internet, no API key, an API error, or the session brake stopped calls.
Then the gray line says "Answered locally". Every answer, cloud or local, is recorded in
the cost ledger with its latency (local answers cost $0)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import tokens
from .ask import SYSTEM_PROMPT, ContextOptions, Focus, build_messages, focus, now_focus
from .client import BudgetExceeded, Ledger, append_ledger
from .local import MODEL_NAME, LocalModel, LocalUnavailable
from .notes import NoteBook
from ..config import Config
from ..memory.timeline import Timeline

log = logging.getLogger(__name__)

LOCAL_CONTEXT_CHARS = 9000      # ~2.5k tokens: about 8 s of prompt reading on ~2 GB of VRAM


@dataclass
class Reply:
    text: str
    footnote: str               # the gray line under the answer
    where: str                  # "cloud" | "local" | "none"
    cost: float = 0.0
    latency_s: float = 0.0


class Router:
    def __init__(self, cfg: Config, local: LocalModel | None, t: Callable[..., str],
                 ledger: Ledger | None = None, notes: NoteBook | None = None):
        self.cfg, self.local, self.t = cfg, local, t
        self._system, self._extra = SYSTEM_PROMPT.format(minutes=cfg.retention["minutes"]), ""
        self.ledger = ledger or Ledger()
        self.notes = notes or NoteBook(cfg)
        from ..memory.archive import DayArchive
        from .companion import Conversation, Profile

        self.archive = DayArchive(cfg)
        self.conversation = Conversation(cfg)
        self.profile = Profile(cfg)
        self._thread: list[dict] = []
        self._about = ""
        self.local_only = False

    def month(self) -> tuple[float, float]:
        """(this month's cloud cost, monthly limit)."""
        return self.ledger.month_total(), float(self.cfg.budget["monthly_limit_usd"])

    def respond(self, question: str, on_token: Callable[[str], None] | None = None,
                confirm_images: Callable[[int], bool] | None = None, system: str | None = None,
                extra: str = "") -> Reply:
        """`system` and `extra` (put just before the request) are for other jobs than answering,
        e.g. preparing an edit (brain/edit.py). The conversation so far and what Edith knows
        about the user go with every request; each exchange is remembered and learned from."""
        from . import companion

        t0 = time.monotonic()
        if system is None and (fact := companion.to_remember(question)):
            saved = self.profile.add(fact, "told")
            text = self.t("remembered" if saved else "already_known")
            self.conversation.add(question, text, "remember")
            return Reply(text, "", "none", 0.0, time.monotonic() - t0)
        if system is None and (fact := companion.to_forget(question)):
            text = self.t("forgot" if self.profile.forget(fact) else "forgot_none")
            self.conversation.add(question, text, "forget")
            return Reply(text, "", "none", 0.0, time.monotonic() - t0)
        self._thread = self.conversation.recent()
        self._about = companion.about_user(self.profile.facts(), self.conversation.recall(question))
        reply = self._respond(question, on_token, confirm_images, system, extra, t0)
        if reply.where != "none" and reply.text:
            self.conversation.add(question, reply.text, "edit" if system else "answer")
            if self.cfg.companion["learn"] and system is None:
                threading.Thread(target=self._learn, args=(question, reply), daemon=True).start()
        return reply

    def _learn(self, question: str, reply: Reply) -> None:
        """Note lasting facts about the user, in the background: with the local model if there
        is one; otherwise with the cloud only if this exchange already went there."""
        from . import companion

        if not companion.ABOUT_SELF.search(question):
            return
        if self.local and self.local.installed:
            def generate(system, user):
                return self.local.generate(system, user, max_tokens=200, background=True)
        elif reply.where == "cloud" and not self.local_only:
            def generate(system, user):
                from .client import MetaClient

                r = MetaClient(self.cfg).chat("learn", messages=[{"role": "system", "content": system},
                                                                 {"role": "user", "content": user}],
                                              max_tokens=400, reasoning_effort="low")
                return r.choices[0].message.content or ""
        else:
            return
        n = companion.learn(self.profile, generate, question)
        if n:
            log.info("Learned %d new fact(s) about the user", n)

    def _respond(self, question, on_token, confirm_images, system, extra, t0) -> Reply:
        self._system = system or SYSTEM_PROMPT.format(minutes=self.cfg.retention["minutes"])
        self._extra = extra
        timeline = Timeline(self.cfg)
        timeline.prune()
        spent, limit = self.month()
        reason = "local_only" if self.local_only else ("budget" if spent >= limit else None)
        if system is None:                          # an answer, not another job (e.g. an edit)
            from . import talk

            k = talk.kind(question)
            if k in ("chat", "unsure"):
                chat = self._chat(question, on_token, reason, t0, can_escalate=k == "unsure")
                if chat is not None:
                    return chat
            elif k == "automate":
                self._system = talk.GUIDE_PROMPT
        f = focus(question, time.time(), self.cfg.retention_s)
        if system is None and talk.kind(question) == "screen":
            f = now_focus(time.time())              # what is on the screen right now
        notes = self.notes.for_questions()
        if reason is None:
            try:
                return self._cloud(question, f, notes, timeline, on_token, confirm_images, spent, limit, t0)
            except BudgetExceeded:
                reason = "session_brake"
            except SystemExit:                          # no API key in .env / environment
                reason = "no_key"
            except Exception as exc:                    # no internet, timeout, API error
                log.warning("Cloud answer failed (%s: %s); answering locally", type(exc).__name__, exc)
                reason = "offline"
        return self._local(question, f, notes, timeline, on_token, reason, t0)

    # --- conversation (no memory) -----------------------------------------------------------------
    def _chat(self, question: str, on_token, reason: str | None, t0: float, can_escalate: bool) -> Reply | None:
        """A natural answer with nothing of the screen sent. None = the model asked for the
        memory (only when can_escalate), so the caller answers with it."""
        from . import talk

        system = talk.CHAT_PROMPT.format(token=talk.NEED_MEMORY)
        gate = talk.Gate(on_token)
        text, where, cost = None, "cloud", 0.0
        if reason is None:
            try:
                from .client import MetaClient

                text, _usage, cost = MetaClient(self.cfg).chat_stream(
                    "chat", on_token=gate, messages=self._with_person(
                        [{"role": "system", "content": system}, {"role": "user", "content": question}]),
                    max_tokens=2500, reasoning_effort="minimal")   # reasoning tokens count too
            except Exception as exc:                # no key, no internet, API error, brake
                log.info("Cloud chat unavailable (%s); answering locally", type(exc).__name__)
                text = None
        if text is None:
            if not self.local or not self.local.installed:
                return Reply(self.t("no_local", why=self.t(f"why_{reason or 'offline'}")), "", "none")
            self.local.ensure_started()
            try:
                text = self.local.generate(self._local_system(system), self._local_thread() + question,
                                           max_tokens=300, on_token=gate)
            except LocalUnavailable:
                return Reply(self.t("no_local", why=self.t(f"why_{reason or 'offline'}")), "", "none")
            where = "local"
            append_ledger(self.ledger.path, {"ts": time.time(), "model": MODEL_NAME, "purpose": "chat", "input": 0,
                                             "cached": 0, "output": 0, "images": 0, "cost": 0.0,
                                             "latency_s": round(time.monotonic() - t0, 2)})
        if talk.Gate.asked_for_memory(text):
            if can_escalate:
                log.info("Chat asked for the memory; answering with it")
                return None
            text = text.replace(talk.NEED_MEMORY, "").strip()
        foot = self.t("answered_locally") if where == "local" else self.t(
            "sent_chat_thread" if self._thread or self._about else "sent_chat", cost=f"${cost:.4f}")
        return Reply(text.strip(), foot, where, cost, time.monotonic() - t0)

    # --- cloud ----------------------------------------------------------------------------------
    def _options(self, f: Focus, notes: list[dict], images: int, archive: list[dict] | None = None) -> ContextOptions:
        c = self.cfg.cloud
        older = float(c["older_image_width"]) / float(c["image_width"])
        return ContextOptions(history_scales=[1.0, older], diff_crops=True, max_images=images,
                              image_width=int(c["image_width"]), focus=f, notes=notes, archive=archive or [])

    def _archive(self, question: str, f: Focus, max_chars: int) -> list[dict]:
        """Lines of the day's text archive that match the question, from before the 10-minute
        buffer (what is still in the buffer is sent from there)."""
        now = time.time()
        since, until = f.span if f.span else (0.0, now)
        until = min(until, now - self.cfg.retention_s + 30)
        if until <= since:
            return []
        hits = self.archive.search(question, since, until, max_chars=max_chars)
        if not hits and f.span:                     # "what was I doing at 9:30?": the period itself
            hits = [r for r in self.archive.read(since, until) if r["kind"] != "window"][-12:]
        return hits

    def _cloud(self, question, f, notes, timeline, on_token, confirm_images, spent, limit, t0) -> Reply:
        from .client import MetaClient

        events = timeline.read()
        has_text = any(e["type"] in ("text", "transcript", "field") for e in events
                       if f.start <= e["ts"] <= f.end + 1)
        images = int(self.cfg.cloud["max_images"]) if (f.visual or not has_text) else 0
        archive = self._archive(question, f, int(self.cfg.archive["max_chars_cloud"]))
        messages, stats = self._messages(timeline, question, f, notes, images, archive)
        if stats["images"] and self.cfg.cloud["confirm_images"] and confirm_images \
                and not confirm_images(stats["images"]):
            messages, stats = self._messages(timeline, question, f, notes, 0, archive)
        client = MetaClient(self.cfg)
        text, usage, cost = client.chat_stream(
            "ask", on_token=on_token, messages=messages, max_tokens=int(self.cfg.cloud["max_output_tokens"]),
            reasoning_effort=self.cfg.ask["reasoning_effort"])
        if usage:
            log.info("ask: %s in (%s cached), %s out, %s images, $%.4f", usage.prompt_tokens,
                     getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0),
                     usage.completion_tokens, stats["images"], cost)
        foot = self.t("sent", what=self._sent(stats), cost=f"${cost:.4f}")
        spent += cost
        if spent >= limit * float(self.cfg.budget["warn_at"]):
            foot += self.t("budget_warn", pct=round(100 * spent / limit))
        return Reply(text.strip(), foot, "cloud", cost, time.monotonic() - t0)

    def _messages(self, timeline, question, f, notes, images, archive=None):
        messages, stats = build_messages(self.cfg, timeline, question, self._options(f, notes, images, archive))
        messages[0]["content"] = self._system
        if self._extra:
            messages[1]["content"].insert(len(messages[1]["content"]) - 1, {"type": "text", "text": self._extra})
        return self._with_person(messages), stats

    # --- the conversation and the person ------------------------------------------------------
    def _with_person(self, messages: list[dict]) -> list[dict]:
        """[system, user] -> [system, what is known about the user, the thread so far, user]."""
        from .companion import history_messages

        about = [{"role": "system", "content": self._about}] if self._about else []
        return messages[:1] + about + history_messages(self._thread) + messages[1:]

    def _local_system(self, system: str) -> str:
        return system + (f"\n\n{self._about}" if self._about else "")

    def _local_thread(self) -> str:
        from .companion import history_text

        return f"The conversation so far:\n{history_text(self._thread)}\n\nNow the user says:\n" if self._thread else ""

    def _sent(self, stats: dict) -> str:
        parts = []
        for key, n in (("screen_texts", stats["screen_texts"]), ("summaries", stats["summaries"]),
                       ("moments", stats["moments"]), ("archive", stats["archive"]),
                       ("transcripts", stats["transcript_segments"]),
                       ("screenshots", stats["images"])):
            if n:
                parts.append(self.t(f"sent_{key}" + ("_1" if n == 1 else ""), n=n))
        if self._thread or self._about:
            parts.append(self.t("sent_conversation"))
        return ", ".join(parts) or self.t("sent_windows")

    # --- local ----------------------------------------------------------------------------------
    def _local(self, question, f, notes, timeline, on_token, reason, t0) -> Reply:
        why = self.t(f"why_{reason}")
        if not self.local or not self.local.installed:
            return Reply(self.t("no_local", why=why), "", "none")
        self.local.ensure_started()
        archive = self._archive(question, f, int(self.cfg.archive["max_chars_local"]))
        _, stats = build_messages(self.cfg, timeline, question, self._options(f, notes, 0, archive))
        context = stats["context_text"]
        if len(context) > LOCAL_CONTEXT_CHARS:      # keep the start (what period) and the newest part
            context = context[:1200] + "\n(…)\n" + context[-(LOCAL_CONTEXT_CHARS - 1200):]
        if f.visual:
            context += self._describe(f, timeline)
        try:
            extra = f"\n\n{self._extra}" if self._extra else ""
            text = self.local.generate(self._local_system(self._system),
                                       context + extra + "\n\n" + self._local_thread() + f"Question: {question}",
                                       max_tokens=600, on_token=on_token)
        except LocalUnavailable as exc:
            log.warning("Local answer failed: %s", exc)
            return Reply(self.t("no_local", why=why), "", "none")
        latency = time.monotonic() - t0
        append_ledger(self.ledger.path, {"ts": time.time(), "model": MODEL_NAME, "purpose": "ask", "input": 0,
                               "cached": 0, "output": 0, "images": 0, "cost": 0.0, "latency_s": round(latency, 2)})
        return Reply(text.strip(), self.t("answered_locally") + (f" · {why}" if why else ""), "local", 0.0, latency)

    def _describe(self, f: Focus, timeline: Timeline) -> str:
        """One small screenshot, described by the local model (slow on the CPU, so just one)."""
        from PIL import Image

        from .ask import hhmmss, pick_frames

        frames = [e for e in timeline.read() if e["type"] == "frame" and f.start <= e["ts"] <= f.end + 1
                  and timeline.buffer_path(e["path"]).exists()]
        picked = pick_frames(frames, f, 1)
        if not picked:
            return ""
        fr = picked[-1]
        with Image.open(timeline.buffer_path(fr["path"])) as img:
            b64 = tokens.jpeg_b64(tokens.fit_width(img.convert("RGB"), 640))
        try:
            desc = self.local.describe(b64, "Describe this screenshot in 3 sentences: the app, what it shows, "
                                            "and any visible text that matters.", background=False)
        except LocalUnavailable:
            return ""
        return f'\n\nScreenshot at {hhmmss(fr["ts"])} ({fr["process"]}: "{fr["window"]}"), described: {desc}'


def cli(cfg: Config, question: str, dry_run: bool = False, local_only: bool = False) -> None:
    """`edith ask`: the answer on stdout; what was sent (or "Answered locally") on stderr."""
    import sys

    from ..ui.agent import text as ui_text

    lang = cfg.ui.get("language", "en")
    local = LocalModel(cfg)
    router = Router(cfg, local, lambda key, **kw: ui_text(lang, key, **kw))
    router.local_only = local_only
    if dry_run:
        f = focus(question, time.time(), cfg.retention_s)
        images = int(cfg.cloud["max_images"]) if f.visual else 0
        _, stats = build_messages(cfg, Timeline(cfg), question, router._options(f, router.notes.for_questions(), images))
        print(f"[edith] focus {f.kind}, {stats['images']} images, {router._sent(stats)}", file=sys.stderr)
        print(stats["context_text"])
        return
    try:
        reply = router.respond(question, on_token=lambda s: print(s, end="", flush=True))
        print("\n" if reply.text else "", end="")
        if reply.where == "none":
            print(reply.text)
        print(f"[edith] {reply.footnote} ({reply.latency_s:.1f} s)", file=sys.stderr)
    finally:
        local.stop()
