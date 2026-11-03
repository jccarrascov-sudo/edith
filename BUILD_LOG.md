# Build log

Everything below was built during the Meta Global AI Developer Hackathon 2026 (from Oct 26).
Numbers are from real runs; failures are listed too.

## Phase 0 — Base (closed Nov 1)
- Repo, MIT license, `.gitignore`, `.env.example` (MODEL_API_KEY / MODEL_API_BASE_URL / MODEL_NAME).
- Pre-commit hook (`.githooks/pre-commit`, LF, no BOM): blocks `.env*`, `buffer/`, audio /
  database / timeline files, secret values from `.env` and key-like lines. Tested with a fake
  `.env.local` and a fake `api_key = "..."` line: both blocked. `privacy-guard` Action passes.
- Invoice Portal v1/v2/v3 (FastAPI + SQLite), 30-invoice Excel with hard cases, `demo_reset`.
- Live API tests: Muse Spark 1.3 text + image OK; SAM 3.1 text prompt IoU 0.98, click-point
  prompt 0.96, tight UIA-like box 0.95 (a loose ~30 px box only 0.50, so it is not used);
  Muse Image 1.0 OK (1600x1600 PNG).
- Session cost meter with the $5 brake and a guard against `-contributor` models.

## Phase 1 — Memory (closed Nov 1)
- Change-detected screen capture (2 s, 0.5 s for 3 s after a click), window history, system
  audio with faster-whisper (CUDA int8), 10-minute rotation, excluded apps, `ask`.
- New: clicks through a low-level *mouse* hook + UIA element under the click; settled text
  field values via UIA ValuePattern (never passwords); page URL from the address bar.
- Live checks: 5/5 portal fields logged with exact values (first try missed one field and
  duplicated another; fixed with a stable field key and logging the first settled value);
  an excluded "My Bank" page left nothing in the buffer; injected clicks ignored.

## Phase 2 — Learning backwards (closed Nov 1)
- Demo episode finder, spreadsheet row matcher (Excel COM), Muse Spark plan of fixed blocks,
  deterministic check that the plan reproduces the demo (max 3 attempts).
- Real demo by the user (Edge + Excel): episode found, row 1 matched 5/5, plan correct on
  attempt 1 ($0.015). Real data exposed 3 bugs, all fixed with tests: clicks attributed to
  the wrong app, the address bar joining the form, bidi marks in Excel's window title.
- Vague requests: with several candidate episodes Muse Spark reads the whole request (time is
  only a hint; "the rest" points at spreadsheet data); 5/5 test phrases resolved sensibly.

## Phase 3 — Fabricate, verify, proof included (closed Nov 1)
- Per-block Playwright code by Muse Spark, code guard, anti-cheat (no data literals, every
  fill reads `row[...]`), text contract per block, headless runner with events (incl. level).
- Equivalence verification in a sandbox portal: the demo + 2 extra rows, block stamp and
  record stamp; expected records for extra rows derived from the demo's relation.
- Single-block self-repair (max 3), `proof/` per machine, dry proof before each use,
  summary with confirmation, empty cells reported and skipped, golden rule enforced in code.
- Results (portal v1, user's own demo):
  - plain Excel: 30/30 exact (independent DB vs. Excel check);
  - hard-case Excel: verification 33/33 over 3 rows on attempt 1; 28 sent + row 18 skipped
    (empty Amount); independent check 29/29 complete rows exact, 0 duplicates;
  - sabotaged date block: dry proof failed and nothing was sent; repair changed only `b4`.
- Costs so far for Phases 0-3 live work: about $0.15 of API credits.
- 76 unit/integration tests passing.

## Redesign — Edith, minimal interface (Nov 2)
- Renamed Pantallita → Edith everywhere: package `src/edith`, CLI `edith` and windowless
  `edith-ui` executables, window titles, prompts, docs, GitHub repository. `PANTALLITA_HOME`
  is still honoured if `EDITH_HOME` is not set. The data (buffer/, state/, tools/) lives in
  the repo folder, so nothing else had to be migrated (there was no AppData folder).
- Removed the mascot and the tangible world (inventor, walking on windows, lasso gesture,
  cards, ghost fan overlay, machine window, shelf, sounds, phrases, repetition detector).
- New interface (PySide6, drawn with QPainter): a 40 px soft dot (gray idle / accent while
  thinking or acting) that opens into a one-field panel; hover-only audio and options;
  agent mode with one gray status line and Enter/Esc confirmation; separate activity view
  with sparklines (RAM, VRAM, memory activity); light/dark following Windows live with a
  250 ms fade.
- Edith's own text field is never read by the memory (FieldWatcher skips its own process).
- Checked: 92 tests pass; the UI starts and quits cleanly on the real desktop (dark theme);
  light and dark renders reviewed. Not yet run end-to-end from the panel against the portal.

## Phase 4 — Interface acceptance (closed Nov 2)
- Whole flow driven from the real panel with the keyboard only (click the dot, type "haz el
  resto", Enter, Enter to confirm), real portal v1, real Muse Spark. The demonstration was
  SIMULATED: row 1 was really submitted to the portal and the memory events describing it
  were written to an isolated timeline (no screen recording, no frames).
- Result (3rd run): plan OK on attempt 1, verification 33/33 on attempt 1, confirmation shown,
  28 sent + row 19 skipped (empty Amount); independent check: 29/29 complete Excel rows in the
  portal's DB, exact, 0 duplicates, 0 unexpected. 58 s end to end (52 s building and
  verifying, 4 s sending), $0.027.
- Failures found on the way, all fixed with tests:
  1. run 1: a repair reply came back empty (reasoning used up max_tokens) and the agent
     crashed with JSONDecodeError. Now an empty/non-JSON reply is retried once with double the
     budget and low effort; if it still fails the block is kept and reported, never a crash.
  2. run 2: the wait-for-confirmation block guessed the success text (no key frames in a
     simulated demo) and 3 repairs guessed again; the save block was also blamed although the
     app held the records. Now a failing block's problem includes the page's accessibility tree
     at that moment, and rows that failed after the save step count as saved.
  3. a click elsewhere closed the panel and the confirmation was hidden. Now a confirmation
     opens the panel without taking the focus.
  Also: the status line counts rows to send (28, not 29), skipped reasons and the demo
  reference are in the panel's language.
- Idle CPU (30 s, after start-up): UI alone 0.11 % of total CPU (2.1 % of one core of 20),
  83 MB; UI + memory recording 1.14 % (22.9 % of one core), 851 MB (whisper model).

## Phase 5 — Compile down & repair by intent (closed Nov 2)
- Network level (`workshop/network.py`): learned from the verified page-level machine's own
  request in the sandbox (not from the user's browser, which is not instrumented), with
  anti-cheat on constants, hidden inputs refreshed per send, and the "saved" response as the
  success check; verified by equivalence like the page level; allow-list in config.toml.
- Repair by intent (`workshop/repair.py`): relocate only fields that are gone; ambiguous ->
  ask for a demo; re-verify; recompile. A newer demo of a changed form rebuilds the machine.
- Sandbox probe of the page after saving, given to code generation and repairs.
- 11 new tests on real portal processes v1/v2/v3 (no model calls): v2 relocation 33/33,
  v3 ambiguous, network capture 33/33, old request fails its proof on v2, anti-cheat.
- Live acceptance, real agent + real Muse Spark, SIMULATED demos (real submit + memory events
  written to an isolated timeline), hard-case Excel (row 19 has an empty Amount):
  - run 1 FAILED at scene A: wait/verify blocks guessed the success message for 3 rounds
    (each repair fixed one and broke the next); scenes B/C then had no machine. Fixed with
    the after-save probe; build errors are now said in the panel's language.
  - run 2, all scenes passed ($0.05 in total):
    A. v1: verification 33/33 on attempt 1, network level 33/33; 28 sent + 1 skipped in
       0.3 s at the network level; independent check 29/29 exact, 0 duplicates.
    B. portal -> v2: proof failed at both levels; b5 relocated "Amount (PEN)" -> "Total due
       (PEN)" with no model call; 33/33; network recompiled; 28 sent in 0.7 s; 28/28 exact
       (fresh v2 database, so the demo row is not in it). 16 s end to end.
    C. portal -> v3: "where does Amount go now: subtotal / total? Show me one" - nothing sent.
    D. one demo by hand in v3 (simulated): new machine, 33/33, network level; 28 sent in
       0.6 s; 29/29 exact.
- Not done: the visible screen-level fallback with SAM (cut, as planned).
