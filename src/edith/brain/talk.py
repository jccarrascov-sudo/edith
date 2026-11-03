"""Conversation: Edith answers like a person, and uses the memory only when it is asked about.

"Hi", "thanks", "who are you?" or "what is recursion?" get a natural, short answer with nothing
of the screen sent anywhere. A question about what the user saw, heard or did ("what did the
teacher say?", "what was on screen 5 min ago?") goes to the memory (answer.py, ask.py).
In between, a cheap filter decides when it can; when it cannot, the model answers as a
conversation and may ask for the memory with a hidden token, which makes Edith ask again with
the memory: the user never sees the token."""

from __future__ import annotations

import re
from typing import Callable

NEED_MEMORY = "[[MEMORY]]"

# Clear signs that the question is about the user's own recent screen, audio or actions.
MEMORY = re.compile(
    r"\b(remember|recall|earlier|ago|just now|a moment ago|this (morning|afternoon)|"
    r"on (my|the) screen|my screen|i (saw|was|did|watched|heard|read|wrote|opened|had)|did i|was i|"
    r"(they|he|she|teacher|professor|video|class|meeting|call) (said|say|says|explained|mentioned)|"
    r"what (did|was|were) (i|they|he|she|it|the)|which (page|tab|window|app|file)|summari[sz]e|what changed|"
    r"recuerdas|recuerda|hace (un rato|rato|\d+|poco|unos)|esta (ma[nñ]ana|tarde)|en (mi|la) pantalla|"
    r"(vi|viste|estaba|estuve|hice|escuch[eé]|le[ií]|escrib[ií]|abr[ií]|ten[ií]a)\b|"
    r"(dijo|dijeron|explic[oó]|mencion[oó]|coment[oó])|qu[eé] (hab[ií]a|pas[oó]|cambi[oó])|"
    r"cu[aá]l (era|fue)|resume|resumen|el video|la clase|la reuni[oó]n|el correo|el profe)\b",
    re.IGNORECASE)

# Small talk: greetings, thanks, how are you, who are you.
CHAT = re.compile(
    r"^\s*[¿¡]?\s*(hi|hello|hey|good (morning|afternoon|evening|night)|thanks|thank you|ok(ay)?|cool|nice|"
    r"how are you|who are you|what can you do|what('?s| is) your name|"
    r"hola|buen(os|as) (d[ií]as|tardes|noches)|gracias|muchas gracias|qu[eé] tal|c[oó]mo est[aá]s|"
    r"qui[eé]n eres|qu[eé] puedes hacer|c[oó]mo te llamas|genial|perfecto|vale)\b[\s!.?¿¡,]*.{0,40}$",
    re.IGNORECASE)

CHAT_PROMPT = """\
You are Edith, a calm, warm desktop companion. Talk like a thoughtful person: natural, kind,
brief (one to three sentences unless more is truly needed), in the user's language. No lists
unless asked, no emojis, no talk about being an AI.
You can also remember what happened on the user's computer in the last minutes (screen, audio,
what they did) and today's important details, but you were NOT given any of it now.
If answering would need that, i.e. the user asks about something they saw, heard, read or did,
reply exactly with {token} and nothing else. Otherwise just answer."""


# A doubt about what is on the screen right now: "what does this error mean?", "explain this".
SCREEN = re.compile(
    r"\b(this|these|here|on (my )?screen( now)?|i'?m (looking at|seeing|reading)|right now)\b.{0,40}"
    r"|\b(what (is|does|means?)|explain|why|how)\b.{0,30}\b(this|these|that error|this error|the error)\b"
    r"|\b(esto|esta|este|estos|estas|aqu[ií]|en (mi )?pantalla( ahora)?|lo que (estoy viendo|veo|tengo abierto)|"
    r"ahora mismo)\b|\b(qu[eé] (es|significa|quiere decir)|expl[ií]ca(me)?|por qu[eé]|c[oó]mo)\b.{0,30}"
    r"\b(esto|este error|el error|esta)\b", re.IGNORECASE)

# Asking how to automate something with Edith ("how do I automate this?", "ayúdame a automatizar").
AUTOMATE = re.compile(
    r"\b(automat\w*|do (this|it|them) (for me|automatically)|repetitive|over and over|"
    r"automatiz\w*|hacerlo autom[aá]tico|repetitiv\w*|una y otra vez|ens[eé][nñ]ar(te|le)? a)\b", re.IGNORECASE)

GUIDE_PROMPT = """You are Edith, a calm, warm desktop companion who can automate repetitive work by watching it
done once. The user wants to automate something. Below is what is on their screen now and in
the last minutes. Guide them like a patient friend, in their language, in a few short steps
tailored to what you see (name their file, their form, their columns):
- What Edith can automate today: copying rows of a spreadsheet (Excel) into a web form, one
  per row, the same way the user did it once.
- How: 1) fill ONE row's form by hand, as always, and submit it; 2) open Edith and say
  "do the rest" (or "haz el resto"); 3) Edith rebuilds the task from what it saw, checks it
  against the user's own example and two more rows in a safe copy, then shows a summary
  (how many rows, totals, skipped rows); 4) Enter sends, Esc cancels. If the form changes
  later, Edith repairs itself or asks to be shown once more.
- If what they want is not that kind of task, say so honestly and suggest the closest thing.
No lists longer than four steps, no jargon, no talk about models or tokens."""


def kind(question: str) -> str:
    """"automate" (how to automate), "screen" (a doubt about the screen right now), "memory"
    (about what happened), "chat" (small talk) or "unsure"."""
    if AUTOMATE.search(question):
        return "automate"
    if SCREEN.search(question) and not MEMORY.search(question):
        return "screen"
    if MEMORY.search(question):
        return "memory"
    if CHAT.search(question):
        return "chat"
    return "unsure"


class Gate:
    """Wraps a token callback: holds back the first characters until it is clear the model did
    not ask for the memory, so the hidden token is never shown."""

    def __init__(self, on_token: Callable[[str], None] | None):
        self.on_token = on_token
        self.buffer = ""
        self.open = False
        self.escalated = False

    def __call__(self, piece: str) -> None:
        if self.escalated:
            return
        if self.open:
            if self.on_token:
                self.on_token(piece)
            return
        self.buffer += piece
        head = self.buffer.lstrip()
        if NEED_MEMORY.startswith(head[:len(NEED_MEMORY)]) and len(head) < len(NEED_MEMORY):
            return                                    # could still become the token: wait
        if head.startswith(NEED_MEMORY):
            self.escalated = True
            return
        self.open = True
        if self.on_token:
            self.on_token(self.buffer)

    @staticmethod
    def asked_for_memory(text: str) -> bool:
        return text.strip().startswith(NEED_MEMORY)
