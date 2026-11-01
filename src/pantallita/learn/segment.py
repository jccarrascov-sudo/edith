"""Find the user's demonstration in the timeline, after the fact (retroactive learning).

A demonstration is a *form episode*: text fields filled in one app (and, in a browser, on
one page: a new URL starts a new episode, so the address bar never joins a form) with no
more than `max_gap_s` between consecutive edits, plus the clicks around them, ending with the click
that submits the form (a button clicked within `submit_s` after the last edit). Clicks and
windows in between (e.g. copying values from Excel) belong to the episode too.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

# Where the user talks to Pantallita: never part of a task.
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
    fields = [e for e in events if e["type"] == "field"]
    runs: list[list[dict]] = []
    for f in fields:
        last = runs[-1][-1] if runs else None
        if (last and f["process"] == last["process"] and f.get("url") == last.get("url")
                and f["ts"] - last["ts"] <= max_gap_s):
            runs[-1].append(f)
        else:
            runs.append([f])
    segments = []
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
        prior = [e for e in events if e["type"] == "click" and start <= e["ts"] <= run[0]["ts"]]
        if prior:
            start = prior[0]["ts"]
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
