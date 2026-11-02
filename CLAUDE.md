# CLAUDE.md — Pantallita

> "Your screen has an inventor."

## What it is
A tiny inventor character that lives on the screen. It keeps a local memory of the last
10 minutes (screen, clicks, UI Automation field values, active window, system audio).
When the user does a repetitive task once (e.g. copy 1 invoice from Excel into a web portal)
and says "do the rest", it:
1. Finds the demonstration in its memory (retroactive learning — nothing to activate before).
2. Understands the intent and splits it into STEPS (blocks).
3. Fabricates a tool: a Python + Playwright program that does the task directly with the
   data, without moving the mouse.
4. Verifies the tool using the user's demonstration as the test: runs it on the same case
   and compares field by field (SQLite). On mismatch it self-corrects (max 3 attempts).
5. Stores the machine in its "workshop"; next time it is one click.
6. If the portal changes and the tool breaks, the user shows the case once and it repairs
   ONLY the broken block.
7. Fallback: if something cannot be done by code, it acts visibly on screen (walks to the
   element, highlights it with SAM, clicks).
It can also answer questions about the last 10 minutes of screen/audio (`ask`).

The core is fabricate → verify → repair tools. It is NOT an image analyzer nor basic RAG.

**Tangible visuals**: a tool is a little machine made of blocks (each block = one real
program step; clicking a block shows its code); each data row is a card entering the
machine; memory is a film strip; verification is two overlapping stamps whose matching
fields light up; a failure is a block throwing sparks; repair replaces only that block.
100 % original flat style (simple shapes, few colors, soft edges). NEVER copy designs or
characters from any series, film or brand.

## Hackathon rules (breaking them = disqualification)
Meta Global AI Developer Hackathon 2026, Indie Developer track, theme "Push the Frontier".
**Deadline: Nov 4, 2026, 9 PM US Eastern (8 PM Lima).** A 3-minute video is mandatory.
- All code must be new, written since Oct 26, 2026. Never copy code from existing projects.
- Public repo (github.com/jccarrascov-sudo/pantallita). NEVER commit `.env`, keys, or
  anything under `buffer/`. A pre-commit hook (`hooks/pre-commit`) blocks them; keep it installed.
- README must keep an up-to-date "Built during the hackathon" vs. third-party table:
  update it whenever a dependency is added.
- No asset (image, sound, font) without a license. The mascot is drawn by code
  (QPainter/vectors) or generated with Muse Image; sounds are synthesized by code.

## Meta models
- **Muse Spark 1.3** (`muse-spark-1.3`) via the Meta Model API (OpenAI-compatible,
  `https://api.meta.ai/v1`, key in `.env` as `META_API_KEY`). NEVER use any `-contributor` variant.
  Reasoning tokens count against `max_tokens`; set `reasoning_effort`.
  Images: Chat Completions `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}` (tested).
  Token counter: `POST /v1/responses/input_tokens` (tested).
- **SAM 3.1** (`sam-3.1`) via `POST /v1/responses` (stream), text or box/point prompts;
  output is special-token text `<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;...|>`;
  masks decoded with `meta-sam-parser`. Used to segment the exact UI element.
- **Muse Image** (`muse-image-1.0`) via `/v1/images/generations`, $0.01/image, no
  transparent background. Optional, for the mascot's look, at the end.
Verify endpoints in dev.meta.ai docs and test with a minimal script before building on them.

## Budget
$150 in credits. Session cost counter (Muse Spark 1.3: $1.25/M input, $0.15/M cached
input, $4.25/M output) and a brake that stops calls when a session exceeds $5 unless
raised in config.

## Privacy (by design, not optional)
- NEVER record the keyboard. No keylogger in any mode.
- Recorded: screenshots, click position + target element, active window, and text field
  values read via UI Automation (ValuePattern) when they change. Elements with
  IsPassword = true are never read.
- Everything stays local in `buffer/` and is deleted after 10 minutes.
- Excluded apps (password managers, banks): nothing is captured while they are active
  (screen, titles and audio).
- Pause always visible.

## Environment
Windows 11, PowerShell, Python 3.12 in `.venv` (use `.venv\Scripts\python`).
RTX 5050 8 GB VRAM, 16 GB RAM. Must coexist with other apps: max ~1.5 GB VRAM, low idle
CPU (mascot < 2 % CPU). The user NEVER edits files by hand: Claude creates, edits and
installs everything. Exception: secrets — the user writes `.env` themselves; never print
or log the key.

## Style
Code, comments, README and commits in English. Talk to the user in Spanish.
Small descriptive commits whenever something works. Git identity:
JUAN CARLOS CARRASCO VELAPATIÑO <230002519+jccarrascov-sudo@users.noreply.github.com>.

## Mascot (personality)
A tiny inventor who hates seeing the user bored. Proud of his machines, humble when he
fails, never interrupts to show off. Default mode COMPANION: transparent always-on-top
window, click-through except when touched. Walks on the taskbar and on top edges of
windows (Win32 positions), jumps between windows; eyes follow the mouse when idle; never
covers where the user works; goes to the workshop alone in fullscreen/video calls; one
click sends him to the workshop. States: walking, rewinding (unrolls film strip),
building (assembles blocks), verifying (stamps), acting (walks to highlighted element),
confused (scratches head, sparks on the block). Repetition detector: same pattern 3 times
→ yawns, drums fingers, raises a "Want me to do it?" sign; if told no, doesn't insist for
that task. Workshop: shelf on the taskbar with the machines, each with an invented
(editable) name and a time-saved label; total time-returned counter. Short warm phrases
with a touch of humor; language configurable: English (video) and neutral Spanish
without regional slang.

## Demo environment
Local "Invoice Portal" (FastAPI + simple HTML, `demo/portal/app.py`):
v1 form (client, tax ID, date, amount, description) saving to SQLite; v2 = same portal
with the amount field moved and renamed (repair scene), chosen with `--version`.
Excel with 30 fictitious invoices (`demo/make_invoices.py`). `demo/demo_reset.py` resets
portal DB, Excel and buffer. Verification compares what SQLite stored after the user's
demo vs. after running the tool, field by field.

## Tool architecture
A tool is NOT a monolithic script: it is a JSON plan of steps, each with a fixed block
type (read_row, fill_field, click, wait, save, verify) and its own Playwright snippet, so
each block is drawn, verified and repaired separately. The runner executes the plan on
every row and emits events (row in, block done, row out, error in block X) for the UI.

## Code layout
```
src/pantallita/
  memory/   capture.py actions.py audio.py timeline.py winutil.py uia.py   (Phase 1, done)
  brain/    ask.py tokens.py client.py (costs + brake)            (ask working)
  learn/    segment.py source.py plan.py cli.py                  (Phase 2, done)
  workshop/ codegen.py runner.py sandbox.py build.py cli.py       (Phase 3, done)
  vision/   sam.py
  ui/ detect/                                                     (later phases)
demo/       portal/app.py make_invoices.py demo_reset.py
scripts/    test_api.py measure_tokens.py install_hooks.py
hooks/      pre-commit
docs/       ARCHITECTURE.md (incl. "Token efficiency" with measured numbers)
```

## Phases (each: test acceptance criteria yourself, report real results incl. failures,
commit, wait for the user's OK before the next)
0. Base: CLAUDE.md, pre-commit hook, portal v1/v2, 30-invoice Excel, demo_reset, API
   test script (Spark+image, SAM, Muse Image).
1. Memory: change-detected capture (2 s idle, 0.5 s around clicks), timeline with frames,
   clicks (+ UIA element), UIA field value changes (no passwords), active window; audio;
   10-min rotation; exclusions; `ask`. Static screen ≤ 1 frame / 30 s; never keyboard data.
2. Learn backwards: "do the rest" finds the demo segment (confirm its time with the user);
   Muse Spark → intent + JSON block plan mapping portal fields to Excel columns.
3. Fabricate & verify: Playwright code per block; replay the same invoice on a temp DB and
   compare; regenerate only the failing block (max 3); save to tools/<name>/; run the
   other 29. Target 30/30 on v1 without moving the mouse.
4. Mascot & tangible world (overlay, states, machine, cards, stamps, shelf, code-generated
   sounds, repetition sign). Mascot idle CPU < 2 %.
5. Repair & fallback: v2 breaks the amount block → sparks → user shows once → repair only
   that block → back to 30/30. Visible fallback mode with SAM highlight, ≥ 5 invoices, stop button.
6. Results: benchmark (fallback vs. tool, v1 and v2 after repair) with REAL numbers;
   final README.
If time runs out, cut in this order: Muse Image → audio/ask → visible fallback (keep SAM
highlight in verification) → reduced benchmark. NEVER cut learn → fabricate → verify → repair.
