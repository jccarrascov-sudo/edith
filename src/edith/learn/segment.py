"""Find the user's demonstration in the timeline, after the fact (retroactive learning).

A demonstration is a *form episode*: text fields filled in one app (and, in a browser, on
one page: a new URL starts a new episode, so the address bar never joins a form) with no
more than `max_gap_s` between consecutive edits, closed by its submit click (so invoices
typed one after another are separate episodes), plus the clicks around them, ending with the click
that submits the form (a button clicked within `submit_s` after the last edit). Clicks and
windows in between (e.g. copying values from Excel) belong to the episode too.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

# Where the user talks to Edith: never part of a task.
OWN_PROCESSES = {"windowsterminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe",
                 "python.exe", "pythonw.exe", "code.exe"}


@dataclass
class Segment:
    start: float
    end: float
    process: str
    events: list[dict] = field(default_factory=list)

    @property
    def fields(self) -> list[dict]:
        return [e for e in self.events if e["type"] == "field" and e["process"] == self.process]

    @property
    def final_values(self) -> dict[str, str]:
        """Field name -> the last value it had (a field may be logged twice while editing)."""
        values: dict[str, str] = {}
        for e in self.fields:
            values[e["element"]["name"]] = e["value"]
        return values

    @property
    def submit(self) -> dict | None:
        clicks = [e for e in self.events if e["type"] == "click" and e["process"] == self.process
                  and e.get("element", {}).get("kind") == "button"]
        return clicks[-1] if clicks and clicks[-1]["ts"] >= self.fields[-1]["ts"] else None

    @property
    def url(self) -> str | None:
        urls = [e["url"] for e in self.events if e.get("url") and e["process"] == self.process]
        return urls[0] if urls else None

    @property
    def windows(self) -> list[str]:
        seen: list[str] = []
        for e in self.events:
            label = f'{e.get("process") or "?"}: {e.get("window", "")}'
            if e.get("window") and label not in seen:
                seen.append(label)
        return seen

    def describe(self, now: float | None = None) -> str:
        now = now or time.time()
        mins = max(0, round((now - self.start) / 60))
        submit = self.submit
        names = ", ".join(self.final_values)
        tail = f', then clicked "{submit["element"]["name"]}"' if submit else ""
        return (f"{_hhmm(self.start)}-{_hhmm(self.end)} (~{mins} min ago) in {self.process}"
                f' "{self.fields[-1].get("window", "")}": filled {names}{tail}')


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def find_demos(events: list[dict], max_gap_s: float = 90.0, submit_s: float = 30.0,
               min_fields: int = 2) -> list[Segment]:
    """All form episodes in the events, most recent first."""
    events = sorted((e for e in events if (e.get("process") or "").lower() not in OWN_PROCESSES
                     or e["type"] == "window"), key=lambda e: e["ts"])
    runs: list[list[dict]] = []
    closed = False  # the current run ended with its submit click: the next field starts a new one
    for e in events:
        if e["type"] == "click" and runs and not closed:
            last = runs[-1][-1]
            if (e["process"] == last["process"] and e.get("element", {}).get("kind") == "button"
                    and 0 <= e["ts"] - last["ts"] <= submit_s):
                closed = True
            continue
        if e["type"] != "field":
            continue
        last = runs[-1][-1] if runs else None
        if (last and not closed and e["process"] == last["process"] and e.get("url") == last.get("url")
                and e["ts"] - last["ts"] <= max_gap_s):
            runs[-1].append(e)
        else:
            runs.append([e])
            closed = False
    segments = []
    prev_end = float("-inf")
    for run in runs:
        if len({f["element"]["name"] for f in run}) < min_fields:
            continue
        process = run[0]["process"]
        start = run[0]["ts"] - 15.0  # the first click into the form / the source cell
        end = run[-1]["ts"]
        for e in events:
            if (e["type"] == "click" and e["process"] == process and e.get("element", {}).get("kind") == "button"
                    and end <= e["ts"] <= run[-1]["ts"] + submit_s):
                end = e["ts"]
                break
        # Back up to the first click of this episode, if it happened just before the first edit.
        start = max(start, prev_end + 0.001)  # never reach back into the previous episode
        prior = [e for e in events if e["type"] == "click" and start <= e["ts"] <= run[0]["ts"]]
        if prior:
            start = prior[0]["ts"]
        prev_end = end
        segments.append(Segment(start, end, process, [e for e in events if start <= e["ts"] <= end + 1.0]))
    return sorted(segments, key=lambda s: s.end, reverse=True)


_MINUTES_AGO = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:min|minutes?|minutos?|m\b)", re.I)


def pick(segments: list[Segment], request: str = "", now: float | None = None) -> Segment | None:
    """The episode the request points at ("what I did 5 minutes ago"), else the latest one."""
    if not segments:
        return None
    match = _MINUTES_AGO.search(request or "")
    if not match:
        return segments[0]
    target = (now or time.time()) - float(match.group(1).replace(",", ".")) * 60
    return min(segments, key=lambda s: min(abs(s.start - target), abs(s.end - target)))


CHOOSE_PROMPT = """\
The user asked an on-screen assistant to repeat something they did earlier. People are vague
about time ("a moment ago", "like 5 minutes ago" may be 2 or 9 minutes) and often name the
task by its content instead ("the invoice thing", "what I did in the portal"). Pick the
recorded episode the request refers to. Treat any time in the request as a rough hint;
the content wins when it points clearly at one episode. "The rest" / "the others" means
more rows of the same data, so it points at an episode whose values came from a spreadsheet.
If nothing tells them apart, the most recent one is the usual meaning.
Reply with JSON only: {"episode": <number>, "confident": true|false}"""


def choose(segments: list[Segment], request: str, now: float | None = None,
           client=None, sources: dict[int, str] | None = None) -> tuple[Segment, bool]:
    """(episode the request means, whether that is clear). One candidate needs no model call;
    with several, Muse Spark reads the whole request; if it fails, the time-based pick is used.
    `sources` maps id(segment) to the spreadsheet its values were found in, if any."""
    now = now or time.time()
    fallback = pick(segments, request, now)
    if len(segments) == 1:
        return segments[0], True
    lines = []
    for i, s in enumerate(segments, 1):
        values = ", ".join(f'{k}="{v[:30]}"' for k, v in list(s.final_values.items())[:4])
        found = (sources or {}).get(id(s))
        lines.append(f"{i}. {s.describe(now)}. Values: {values}. "
                     + (f"Values found in a row of {found}." if found else "Values not found in any spreadsheet."))
    try:
        import json

        response = client.chat("learn: choose episode", max_tokens=1500, reasoning_effort="low", messages=[
            {"role": "system", "content": CHOOSE_PROMPT},
            {"role": "user", "content": f'Request: "{request}"\nEpisodes (most recent first):\n' + "\n".join(lines)}])
        text = response.choices[0].message.content or ""
        reply = json.loads(text[text.find("{"):text.rfind("}") + 1])
        index = int(reply["episode"]) - 1
        if 0 <= index < len(segments):
            return segments[index], bool(reply.get("confident"))
    except Exception:
        pass
    return fallback, False
