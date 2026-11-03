# Privacy

> Your screen never leaves your PC. Only what you ask goes to the cloud.

## What Edith records

| Recorded | How | Never |
|---|---|---|
| Screenshots of the primary monitor, only when the screen changed | mss, change detection without the taskbar | the cursor; excluded apps |
| The text of the window in front | UI Automation, like a screen reader | password fields; excluded apps; Edith's own windows |
| Clicks, with the element under them | a low-level **mouse** hook | the keyboard: there is no keyboard hook and the key state is never read (a test checks the whole source tree) |
| Values of text fields once they settle | UI Automation ValuePattern | password fields |
| System audio (what comes out of the speakers) | WASAPI loopback, faster-whisper on the GPU | the microphone |

Excluded apps (password managers, banks, private browsing, and anything added to
`config.toml` `[exclude]`) are never captured, not even their window title, and audio
heard while one is in front is dropped.

## How long it is kept (all on this PC, in `buffer/`, git-ignored)

| What | Kept |
|---|---|
| Screenshots, audio, the raw timeline | 10 minutes |
| The day's text archive (screen text, what was heard, field values, descriptions of screens with little text) and block summaries | 24 hours |
| Important moments: dates and deadlines, exams, tasks, appointments, payments | 12 months |

**Delete everything now** (in the panel's options, two clicks) removes all of it at once:
screenshots, audio, the timeline, the day's text, the notes and the important moments, and the
plans learned from demonstrations. Verified machines (`tools/`) and the cost record are kept.
**Pause memory** stops all recording; the audio button stops listening to the speakers.

Logs (`state/logs/edith.log`, rotating, 1 MB x 4) hold operational events only: no screen
text, no transcript and no row data (found and fixed on Nov 3: the transcript and agent-mode
row values used to be logged).

## What leaves the PC

Nothing of the continuous capture. Muse Glimmer (the local model) runs on this PC through
llama.cpp, bound to `127.0.0.1` on a free port. Only when you ask something or request an
edit does Edith call Muse Spark (Meta Model API), with the cheapest context that can answer:
text first; at most 3 downscaled screenshots, only if the question needs to see something;
day-archive lines only if they match the question. The panel says what was sent
("Sent: 4 screen texts, 1 screenshot · $0.0035"). With **Local only** on, or without a
connection, nothing is sent and the local model answers ("Answered locally").

The runtime check is [NETWORK_AUDIT.md](NETWORK_AUDIT.md) (`scripts/network_audit.py`): 90 s of
real recording opened 0 connections outside the PC; a question opened exactly one, to
`api.meta.ai`.
