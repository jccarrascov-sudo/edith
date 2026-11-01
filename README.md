# Pantallita

**Your screen has an inventor.**

Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track).

An on-screen companion that remembers the last ~10 minutes of your screen and system audio,
so you can ask it what just happened: *"what was the video about?"*, *"what was that error
in the terminal 5 minutes ago?"*. Later phases will let it act on the computer while
visually showing what it is about to touch (with SAM 3).

The brain is **Muse Spark** (`muse-spark-1.3`) through the Meta Model API.

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
 pantallita ask "..." ◄── Muse Spark ◄── transcript + windows + actions + 8-12 key frames ◄─┘
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
- **Ask** sends the transcript, a compact window history and the most informative frames
  (highest change per time slice + the most recent ones), and prints the tokens spent.
- **Token efficiency**: decreasing resolution for older frames, crops of known elements,
  compressed UI Automation text when it is cheaper than pixels, and history sent as
  differences. Measured with the official token counter: a real `ask` went from 11,281 to
  7,042 input tokens. Details and numbers in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#token-efficiency).

## Setup (Windows 11, Python 3.12)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .
copy .env.example .env   # then put your Meta Model API key in META_API_KEY
```

## Usage

```powershell
.venv\Scripts\python -m pantallita run              # Ctrl+C to stop
.venv\Scripts\python -m pantallita ask "what was the video about?"
.venv\Scripts\python -m pantallita ask "..." --dry-run   # show the context, no API call
.venv\Scripts\python -m pantallita status
.venv\Scripts\python -m pantallita do "what I did 3 minutes ago, do it with the rest"
.venv\Scripts\python -m unittest discover -s tests       # tests
.venv\Scripts\python scripts\measure_tokens.py           # token measurements (counts only)
```

## How it works: learning backwards (Phase 2)

Nothing has to be switched on before the task: Pantallita rewinds its memory.

1. **Find the demonstration.** A *form episode* is a run of text fields filled in one app
   and one page (≤ 90 s between edits), plus the clicks around it, ending with the button
   clicked right after the last edit. "N minutes ago" in the request picks the closest
   episode; otherwise the latest. The user confirms it by its time.
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

## Demo environment

A local **Invoice Portal** (FastAPI + SQLite) and an Excel file with 30 fictitious invoices
are the controlled setting for the fabricate / verify / repair scenes.

```powershell
.venv\Scripts\python demo\demo_reset.py                 # empty portal DB, regenerate Excel, clear buffer/
.venv\Scripts\python demo\portal\app.py --version v1    # http://127.0.0.1:8765
.venv\Scripts\python demo\portal\app.py --version v2    # same portal, amount field moved and renamed
```

The Excel is written to `demo/data/invoices.xlsx`. All names and tax IDs are invented
(tax IDs start with 99, a prefix no real RUC uses).

## Safety hook

`.venv\Scripts\python scripts\install_hooks.py` enables `hooks/pre-commit`, which blocks
commits containing `.env` files, anything under `buffer/`, audio/database/timeline files,
or any line that contains a secret value from the local `.env`.

## Related work

**[AppAgentX](https://arxiv.org/abs/2503.02268)** (Westlake University, 2025;
[code](https://github.com/Westlake-AGI-Lab/AppAgentX)) is the closest idea we know of: a
smartphone GUI agent that keeps a memory of its *own* task executions, spots repetitive action
sequences and evolves them into high-level "shortcut" actions, so later runs need fewer
reasoning steps. Pantallita is designed around different choices:

| | AppAgentX | Pantallita |
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
libraries/models, installed from PyPI / Hugging Face and not vendored:

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

No images, sounds or other assets are included.

## License

[MIT](LICENSE)
