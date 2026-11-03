# Edith

**Do it once. Edith does the rest.**

Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track).

A minimal desktop assistant that remembers the last ~10 minutes of your screen and system
audio, so you can ask it what just happened: *"what was the video about?"*, *"what was that
error in the terminal 5 minutes ago?"*. Do a task once by hand and say *"do the rest"*: it
learns the task from its memory, builds a machine, verifies it against what you did, and
runs it on the remaining rows after you confirm.

Two Meta models share the work. **Muse Glimmer 30B** runs on your PC (llama.cpp, ~2 GB of
VRAM) for background notes and for answers when the cloud is not used; **Muse Spark**
(`muse-spark-1.3`, Meta Model API) answers only when you ask something, with the cheapest
context that can answer. *Your screen never leaves your PC. Only what you ask goes to the
cloud.*

## The interface

Edith stays out of the way. Rule: if something is not needed right now, it is not shown.

- **At rest** it is a single 40 px soft dot, always on top and draggable, with no text or
  icon. The dot's center is the only status indicator: gray when idle, a dimmer gray while
  the memory is paused, the accent color while thinking or acting (a soft fade, never an
  animation). The status dot and the raised circle are drawn from the same rectangle, so
  they share one geometric center, with equal margins all round for the shadows.
- **Ctrl+Alt+E** (a global shortcut) opens and closes the panel from any app, with the
  cursor in the text field. It can be changed in the options: *"Press the combination…"*,
  a modifier plus a key is required, Esc cancels, a combination another app already uses is
  refused (the old one stays), and *Default* restores Ctrl+Alt+E. It is saved and applied
  at once.
- **Click** the dot and it opens into a panel with one text field and the answer under it.
  The audio button (system audio on/off) and the options (Activity, Pause memory, Quit,
  Shortcut, Local only) fade in only while the mouse is over the panel. Esc or a click
  elsewhere closes it back into the dot.
- **Answers stream** in token by token. Under each one, a small gray line says what was sent
  (*"Sent: 4 screen texts, 1 summary, 2 transcript lines · $0.0035"*), or *"Answered
  locally"* (with the reason: no connection, monthly limit reached...). From 80 % of the
  monthly budget it also says how much is used.
- **Key moments** (at most 3, one line each, the time in gray) appear under the field when
  there is no answer on screen: a slide change, a date or deadline, a task, "this is in the
  exam". A click asks about that moment. There are no pop-ups.
- **Agent mode** draws nothing on the screen: no overlays, no outlines. Progress is one line
  of gray text in the panel (*"Writing row 3 of 29 in 127.0.0.1:8765…"*). Before sending,
  the panel shows what will be sent (rows, totals, skipped rows); Enter sends, Esc cancels.
- **Activity** opens a separate view with RAM, VRAM, the memory's activity over the last
  10 minutes, answer time (cloud and local) and the daily cloud cost (this month's total as
  the big number): the number large, a 1.75 px accent sparkline under it for the trend, a faint
  fill, no axes, grid or legend; the exact value is in a tooltip under the mouse.
- **Soft UI**: one flat background color shared by the panel and the buttons; relief comes
  only from two soft shadows (light up-left, dark down-right), inset when pressed; no
  borders or dividers, 16-24 px corners, one accent color, everything else gray.
- **Light and dark** follow the Windows app theme live, without restarting: every color is
  a variable with a light and a dark set, and a theme change fades over 250 ms.

## How it works: memory (Phase 1)

```
 screen (mss, every 2 s; 0.5 s after a click) ──► change detection ──► buffer/frames/*.jpg ─┐
 foreground window (ctypes) ─────────────────────────────────────────────────────────────────┤
 mouse clicks (low-level MOUSE hook) ──► UIA element under the click ────────────────────────┤
 focused text field (UIA ValuePattern, never passwords) ──► value once it settles ───────────┤
 speakers (WASAPI loopback, 15 s blocks) ──► silence gate ──► faster-whisper (CUDA int8) ────┤
                                                                                             ▼
                                              buffer/timeline.jsonl (rolling 10-minute window)
                                                                                             │
 window text after each change (UI Automation, no model) ─────────────────────────────────┤
                                                                                             │
 local model (Muse Glimmer, low priority) ──► key moments + block summaries ◄────────────────┤
                                                                                             │
 a question ──► text first (+ ≤3 screenshots if it needs to see) ──► Muse Spark ─────────────┘
              └─► local only / offline / monthly limit ──► Muse Glimmer ("Answered locally")
```

- **Capture** saves a frame only when the screen changed (>2% mean difference) or every 30 s.
  After a click it looks every 0.5 s for 3 s, so the result of each action is caught.
- **Actions**: each click is logged with its position and the UI Automation element under
  it (type, name, rectangle); each text field's value is logged once it settles or focus
  leaves it, read via ValuePattern like a screen reader does. **The keyboard is never
  recorded** (there is no keyboard hook at all) and password fields are never read.
  These are what "do the rest" learns from.
- **Privacy**: windows matching the exclusion list in `config.toml` (password managers,
  banks, private browsing...) are never captured, not even their title. Everything lives
  in `buffer/`, which is git-ignored and pruned continuously to the last 10 minutes.
- **Audio** is the system output (what you hear), not your microphone. Silent blocks are
  skipped; whisper is loaded only when there is something to transcribe.
- **Screen text**: after each significant change, the text of the window in front is read
  through UI Automation (like a screen reader; no OCR, no model, never password fields) and
  kept when it changed. Change detection ignores the taskbar (its clock) and the cursor.
- **Ask** is text first: window history, clicks and field values, the transcript, the screen
  text (later reads only add the lines that changed) and the local model's notes.
  Screenshots are added only when the question needs to see something (at most 3, the
  older ones smaller or cropped to what changed). Temporal questions are narrowed to the
  period they name and get its timestamped sequence: *"what changed in the last 5
  minutes?"*, *"what was on screen 5 min ago?"* (the last frame saved before then, since
  frames are only saved on change), *"summarize between 10:30 and 10:40"*.
- **Hybrid engine**: Muse Spark only when you ask. Muse Glimmer (local) writes key moments
  (a no-model filter first, so it runs rarely) and a short summary of each 10-minute block,
  always after any question, and answers when "Local only" is on, without internet, on an
  API error or once the monthly limit is reached. A watchdog restarts it if it stops; the
  server is tied to Edith's process, so it never outlives it. Costs per question, day and
  month are in `state/costs.jsonl`; the monthly limit is in `config.toml`.
- **Token efficiency**: decreasing resolution for older frames, crops of known elements,
  compressed UI Automation text when it is cheaper than pixels, and history sent as
  differences. Measured with the official token counter: a real `ask` went from 11,281 to
  7,042 input tokens. Details and numbers in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#token-efficiency).

## Setup (Windows 11, Python 3.12)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .
copy .env.example .env   # then put your Meta Model API key in MODEL_API_KEY
# Optional local model (~13 GB): llama.cpp and Muse Glimmer 30B (2-bit) with its vision projector
winget install ggml.llamacpp
.venv\Scripts\hf download unsloth/Muse-Glimmer-30B-GGUF Muse-Glimmer-30B-UD-Q2_K_XL.gguf mmproj-kquant.gguf
```

## Usage

```powershell
.venv\Scripts\edith-ui                          # Edith on screen (no console window)
.venv\Scripts\edith                             # the same, from a console
.venv\Scripts\python -m edith run              # memory only, Ctrl+C to stop
.venv\Scripts\python -m edith ask "what was the video about?"
.venv\Scripts\python -m edith ask "..." --dry-run   # show the context, no model call
.venv\Scripts\python -m edith ask "..." --local     # answer with the local model only ($0)
.venv\Scripts\python -m edith cost                  # session and month cost
.venv\Scripts\python -m edith status
.venv\Scripts\python -m edith do "what I did 3 minutes ago, do it with the rest"
.venv\Scripts\python -m unittest discover -s tests       # tests
.venv\Scripts\python scripts\measure_tokens.py           # token measurements (counts only)
```

## How it works: learning backwards (Phase 2)

Nothing has to be switched on before the task: Edith rewinds its memory.

1. **Find the demonstration.** A *form episode* is a run of text fields filled in one app
   and one page (≤ 90 s between edits), plus the clicks around it, ending with the button
   clicked right after the last edit. People are vague about time, so with several
   candidates Muse Spark reads the whole request ("the invoice thing", "like 5 minutes
   ago", "do it with the rest" = values that came from a spreadsheet) and treats times as
   rough hints; if it is not sure, the candidates are listed. The user confirms the one
   picked by its time.
2. **Find the source row.** The spreadsheet comes from the Excel window in the timeline
   (open workbooks via COM, else the usual folders). Every typed value is compared with
   every cell through the ways it can be shown (Excel display format, plain numbers,
   common date formats): the row matching most fields is the demonstrated one.
3. **Plan with Muse Spark.** The recording, the sheet (headers, formats, the matched row)
   and two key frames (the filled form, the confirmation page) become an intent and a JSON
   plan of fixed blocks: `read_row`, `fill_field` (label ← column + transform), `click`,
   `save`, `wait`, `verify`. Fields are addressed by their accessible names, which are what
   UI Automation recorded and what Playwright's `get_by_label` uses.
4. **Check the plan against the demo.** Replaying each transform on the demonstrated row
   must give exactly what the user typed; otherwise the errors go back to the model
   (max 3 attempts). Plans are saved to `state/plans/`.

## How it works: fabricate and verify (Phase 3)

1. **Code per block.** Muse Spark writes one small Playwright function per block,
   `def run(page, row, ctx)`, from the plan (each block has a *text contract*, e.g. "Put the
   Amount column, as a number with 2 decimals, into the field that means the invoice
   amount") and the page's accessibility snapshot (text, cheaper than a screenshot).
   Fields and buttons are found by accessible name.
2. **Guard and anti-cheat.** Generated code is parsed before it runs: exactly one `run`
   function, no imports, no file/OS access, no dunder or `str.format` escapes, a minimal set
   of builtins. No data value may be written in the code (any string equal to a cell or a
   typed value, any number equal to a numeric cell is rejected), and every `fill_field`
   must read `row[...]`.
3. **Verify by equivalence.** In a *sandbox* copy of the app (same portal version,
   temporary database) the machine replays the demonstrated row **and 2 more rows** (chosen
   to differ in cell types: an amount written as text, a whole number...). Each row gets
   two stamps:
   - **block stamp**: each field must hold exactly what the user typed (demo) or what the
     plan's transforms give (extra rows);
   - **record stamp**: the stored record must equal the user's own submission (demo), or
     what the demo *implies* for that row: the relation between sheet cells and stored
     columns is learned from the demo (e.g. date cell -> ISO date, amount -> number), never
     written by hand.
4. **Self-repair.** A block that raises or mismatches is regenerated *on its own* with the
   error, and the rows are replayed (max 3 rounds).
5. **Proof included.** A machine that passes (and only then: nothing unverified enters the
   workshop) is stored in `tools/<name>/`: `plan.json`, `blocks/*.py`, `verification.json`,
   `proof/` (the demo row, what was typed, and the expected state change) and `runs/*.jsonl`
   (every row/block event, with the level used). **Before each use** the machine replays
   its proof dry in a sandbox; if it fails it does not run and goes to repair.
6. **Summary and confirmation.** Before sending: how many rows, the total of every amount
   column, and which rows will be skipped. A row with an empty cell is reported and
   skipped; the run goes on.

```powershell
.venv\Scripts\python -m edith build            # latest plan -> verified machine -> run the rest
.venv\Scripts\python -m edith machine          # list the workshop
.venv\Scripts\python -m edith machine "invoice runner"
```

Real runs on portal v1 (the user's own demo in Edge):

| Run | Result |
|---|---|
| Plain Excel | code for 9 blocks in 8.5 s ($0.009); verification 11/11 on attempt 1; 29 rows in 5 s; independent DB vs. Excel check **30/30 exact** |
| Excel with hard cases (accents/ñ, text amounts, ISO text dates, 1 empty cell) | code in 14 s ($0.015); verification **33/33 checks over 3 rows** on attempt 1; summary "28 rows, total 85,922.76, row 18 skipped: empty Amount"; dry proof 11/11 before use; 28 sent + 1 skipped in 5 s; independent check **29/29 complete rows exact, the empty-cell row not stored, 0 duplicates** |
| Sabotage: date block changed to `%Y-%m-%d` in the stored machine | dry proof FAIL (10/11), nothing sent; repair rewrote only `b4` ($0.007); 33/33; proof PASS again |

## How it works: compile down and repair by intent (Phase 5)

**Compile down.** A machine that passed its page-level verification (Playwright by labels) is
run once more in the sandbox while the browser records the request its save step sends. The
request is generalised: each form value that equals what a fill step put in its field is
taken from that step (`row[column]` through its transform); hidden inputs are read fresh from
the form before each send; any other literal value counts as a data value written by hand,
so the request is not compiled (the same anti-cheat rule as the blocks). The response that
meant "saved" (status and redirect) becomes the success check. This network-level version is
verified by equivalence on the same cases (demo + 2 extra rows) and stored only if it passes;
it is used only for sites in `[network] allow` (default: the local portal). No browser is
opened at this level: 28 invoices go out in under a second.

**Repair by intent.** Before each use, the proof runs dry at the level that will be used. If
it fails, Edith first checks whether only the request changed (page level still passes:
recompile). Otherwise it reads the form as it is now and relocates only the fields that are
gone, among the fields no other step uses:
- one unused field shares a meaningful word with the old label or the column → that one;
- two or more do (portal v3: "Amount - subtotal" and "Amount - total") → ambiguous: Edith
  does not guess, it asks to be shown one;
- none does but only one field is left (portal v2: "Total due") → that one;
- otherwise Muse Spark reads the step's contract and the candidates, and may say "ambiguous".

Then everything is verified again by equivalence and compiled down again. When the user does
the task again by hand in a form with different fields, the next request learns from that
new demonstration.

**Seeing the confirmation.** Wait/verify steps used to be written for a guessed success
message. Before writing code, a sandbox probe now saves the demo row once (fields by label,
the save button by name) and passes the page's accessibility tree after saving to Muse Spark;
a failing step's repair also gets the page as it was at that moment.

## Demo environment

A local **Invoice Portal** (FastAPI + SQLite) and an Excel file with 30 fictitious invoices
are the controlled setting for the fabricate / verify / repair scenes.

```powershell
.venv\Scripts\python demo\demo_reset.py                 # empty portal DB, regenerate Excel, clear buffer/, workshop, plans
.venv\Scripts\python demo\portal\app.py --version v1    # http://127.0.0.1:8765
.venv\Scripts\python demo\portal\app.py --version v2    # amount field moved and renamed (automatic repair)
.venv\Scripts\python demo\portal\app.py --version v3    # two amount fields (ambiguous: "Show me one")
```

The Excel is written to `demo/data/invoices.xlsx`. All names and tax IDs are invented
(tax IDs start with 99, a prefix no real RUC uses). It has hard cases on purpose: accents
and ñ, amounts stored as text with thousands commas, whole amounts, dates as real Excel
dates and as ISO text, and one empty amount cell.

## Safety hook

`.venv\Scripts\python scripts\install_hooks.py` enables `.githooks/pre-commit`, which blocks
commits containing `.env` files, anything under `buffer/`, audio/database/timeline files,
any line that contains a secret value from the local `.env`, and lines that look like an
API key or token. The `privacy-guard` GitHub Action fails if any such file is tracked.

## Related work

**[AppAgentX](https://arxiv.org/abs/2503.02268)** (Westlake University, 2025;
[code](https://github.com/Westlake-AGI-Lab/AppAgentX)) is the closest idea we know of: a
smartphone GUI agent that keeps a memory of its *own* task executions, spots repetitive action
sequences and evolves them into high-level "shortcut" actions, so later runs need fewer
reasoning steps. Edith is designed around different choices:

| | AppAgentX | Edith |
|---|---|---|
| Learns from | the agent's own past executions | a **human demonstration**, even retroactively: you do the task once, then ask, without having pressed "record" |
| What it produces | higher-level GUI actions, still executed on screen by the agent | **code** (a plan of Playwright blocks) that does the task directly, **without touching the screen** or the mouse |
| How it knows it works | task success while running | it **replays your demonstration as a test** and compares the result field by field before running on the rest of the data |
| When the app changes | evolves new shortcuts over time | **repairs itself**: only the broken block is regenerated, using one new demonstration |

Token-efficiency techniques are inspired by GUIPruner, SimpAgent and A11y-Compressor; see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#token-efficiency).

## Built during the hackathon

Everything in this repository (all code under `src/` and `tests/`, configuration and docs)
was written from scratch during the hackathon. Third-party pieces are used only as
libraries/models, installed from PyPI / Hugging Face and not vendored.

| Built during the hackathon (all original code) | Status |
|---|---|
| Local memory: change-detected capture, mouse-only click log, UIA field values, system audio, 10-min buffer, exclusions | done |
| `ask` over the last 10 minutes, with token optimizations | done |
| Learning backwards: demo episode finder, spreadsheet row matcher, Muse Spark block plan + check | done |
| Workshop: per-block code, code guard, headless runner, sandbox equivalence verification, single-block repair | done |
| Text contract per block, anti-cheat check, 2 extra verification rows, proof included, dry run before use | done |
| Invoice Portal v1/v2/v3 + 30-invoice Excel with hard cases + demo reset | done |
| Pre-commit hook and privacy-guard GitHub Action | done |
| Minimal soft-UI interface: dot, one-field panel, agent status line, confirm with Enter/Esc | done |
| Global shortcut (default Ctrl+Alt+E), customizable from the panel; dimmer dot while paused | done |
| Screen text through UI Automation after each change; change detection without the taskbar clock | done |
| Hybrid engine: local Muse Glimmer (watchdog, warm-up, streaming, tied to Edith's process) + Muse Spark only on questions | done |
| Text-first context, temporal focus (minutes ago / what changed / between), at most 3 screenshots | done |
| Key moments and block summaries in the background; at most 3 moments in the panel | done |
| Cost per question/day/month, monthly limit (80 % warning, 100 % local), "Local only", "Sent: ..." line | done |
| Activity: answer time and daily cost sparklines | done |
| Activity view with sparklines (RAM, VRAM, memory activity) | done |
| Light/dark theme following Windows live, with a fade | done |
| Compile down: network level learned from the verified machine's own request in the sandbox (allow-listed domains), verified by equivalence | done |
| Repair by intent (relocate moved fields by meaning, ask for a demo when ambiguous), re-verified | done |
| Visible fallback mode with SAM highlight | (planned) |
| Benchmark with real numbers | (planned) |

| Third-party component | Used for | License |
|---|---|---|
| [mss](https://github.com/BoboTiG/python-mss) | Screen capture | MIT |
| [Pillow](https://python-pillow.org/) | Resizing, grayscale, JPEG encoding | MIT-CMU |
| [NumPy](https://numpy.org/) | Frame differences, audio math | BSD-3-Clause |
| [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) | WASAPI loopback recording | MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (+ CTranslate2, Silero VAD) | Speech-to-text | MIT |
| [Whisper "small" model](https://huggingface.co/Systran/faster-whisper-small) (OpenAI, converted by Systran) | Speech-to-text weights | MIT |
| NVIDIA cuBLAS / cuDNN wheels (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) | GPU runtime for CTranslate2 | NVIDIA license |
| [openai](https://github.com/openai/openai-python) Python SDK | Client for the OpenAI-compatible Meta Model API | Apache-2.0 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | Loading `.env` | BSD-3-Clause |
| [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) | Reading Windows UI Automation (element names, types, values) | Apache-2.0 |
| [comtypes](https://github.com/enthought/comtypes) | COM bindings used by uiautomation | MIT |
| [FastAPI](https://github.com/fastapi/fastapi) (+ [Starlette](https://github.com/encode/starlette)) | Demo "Invoice Portal" web app | MIT / BSD-3-Clause |
| [Uvicorn](https://github.com/encode/uvicorn) | Serving the demo portal locally | BSD-3-Clause |
| [python-multipart](https://github.com/Kludex/python-multipart) | Parsing the portal's form posts | Apache-2.0 |
| [openpyxl](https://foss.heptapod.net/openpyxl/openpyxl) | Generating/reading the demo invoices Excel | MIT |
| [Playwright](https://github.com/microsoft/playwright-python) (+ Chromium) | Driving the browser in fabricated tools | Apache-2.0 |
| [meta-sam-parser](https://dev.meta.ai/) (Meta) | Building SAM 3.1 box/point prompts and decoding its masks | SAM License |
| Muse Spark via the [Meta Model API](https://dev.meta.ai/docs) | Multimodal reasoning (remote API) | Meta terms |
| [Muse Glimmer 30B](https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF) (Meta; GGUF by Unsloth) | Local model: notes, offline answers (downloaded, not in the repo) | Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` | Runs the local model (installed with winget, not in the repo) | MIT |
| SAM 3.1 via the Meta Model API | Segmenting UI elements (remote API) | Meta terms |
| Muse Image via the Meta Model API | Only checked by `scripts/test_api.py`; not used by the app | Meta terms |
| [PySide6-Essentials](https://doc.qt.io/qtforpython-6/) (Qt for Python) | The dot, the panel and the activity view (drawn with QPainter) | LGPL-3.0 |

No images, sounds, fonts or other assets are included: everything on screen is drawn by code.

## License

[MIT](LICENSE)
