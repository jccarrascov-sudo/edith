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
- Renamed the app to Edith everywhere: package `src/edith`, CLI `edith` and windowless
  `edith-ui` executables, window titles, prompts, docs, GitHub repository. The legacy home variable
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

## Final version, Phase 2 - Interface additions (Nov 3)
- Dot: a third state, dimmer gray while the memory is paused (both palettes). The raised
  circle and the status dot are now computed from one rectangle (the widget's, minus equal
  margins), so they cannot drift apart. Measured on the real desktop (DPR 1.0): widget
  76x76 at (1196, 558); circle center = dot center = (38, 38) in the widget = (1234, 596) on
  screen; the pixel at that center is exactly the idle color.
- Global shortcut (`ui/hotkey.py`, Win32 RegisterHotKey on its own message-loop thread):
  default Ctrl+Alt+E opens/closes the panel with the cursor in the field (checked live:
  open + active + focused, then closed). Options -> "Shortcut: Ctrl+Alt+E": "Press the
  combination... (Esc cancels)", modifier + one key required (letter, digit, F1-F24, Space),
  a combination owned by another app is refused and the old one kept, "Default" restores it;
  saved in state/ui.json and applied at once.
- The options no longer fit one line in Spanish (buttons were cut); they are now two short
  lines above the hover buttons.
- 11 new tests (parsing, key capture, a really taken combination, persistence, centers,
  paused color): 114 pass.
- Not yet: latency and cost sparklines in the metrics view (they need the Phase 3 engine).


## Final version - local engine measurements (Nov 3)
Muse Glimmer 30B UD-Q2_K_XL (llama.cpp b11515, Vulkan, RTX 5050 8 GB, 16 GB RAM), 8k context.
- It is a DENSE model (52 layers, no experts), so `--cpu-moe` does nothing; the only VRAM
  knob is the number of GPU layers (`-ngl`). The output and embedding tensors are 1 GB each.
- It always reasons first (its template defaults to "Reasoning strength: high");
  `--reasoning off` and `--reasoning-budget 0` are ignored, and with 200 tokens the answer
  came back EMPTY (all reasoning). `chat_template_kwargs.reasoning_strength = "low"`: 28 s
  for one sentence. Starting the answer channel directly (`<|start|>assistant to=user
  <|message|>` on `/completion`): the same sentence in 7.8 s, 29 tokens, correct.
- Low process priority (`--prio -1`) halves the speed (5.0 -> 2.4 tok/s at 26 layers), so
  it is not used; the server runs at normal priority.
- Text, answer channel, normal priority, one sample each:

  | GPU layers | VRAM (server) | Time to first token | Speed |
  |---|---|---|---|
  | 6 | ~2.2 GB | 1.7 s | 3.2 tok/s |
  | 14 | - | 1.5 s | 3.7 tok/s |
  | 26 | ~6.2 GB | 1.1 s | 5.0 tok/s |

  Server RAM peak 10-11 GB (the machine has ~10 GB free at idle).
- Image (portal screenshot, vision encoder on the CPU, 26 layers, chat endpoint with low
  reasoning): 54 s, described the form, its five fields and the button correctly. Raw
  `/completion` with an image failed ("Failed to tokenize prompt"); the vision encoder on
  the GPU failed at 26 layers ("failed to process mtmd chunk", out of VRAM).
- Decision: default 6 GPU layers (~2 GB VRAM, about 1.6x slower than 6 GB), vision encoder
  on the CPU; images only in low-priority background batches.

## Final version, Phase 3 - Memory and performance, hybrid engine (Nov 3)
- Screen text (`memory/screentext.py`): after each kept frame, the foreground window's text
  is read through UI Automation (labels, headings, cells, field values; never passwords,
  never excluded apps or Edith itself) and logged when it changed. Real 90 s run: 16 reads
  (Edge, Terminal, Spotify...), 0.3 s per read on a Gmail page. Buttons/tabs were dropped
  after the first try (browser chrome like "Minimize", "Back").
- Change detection now ignores the taskbar (work area only), so the clock does not create
  frames; the cursor was never in the grab.
- Local engine (`brain/local.py`): llama-server with 6 GPU layers, answer channel opened
  directly, warm-up at start (ready in 11-29 s), streaming, timeouts, background jobs wait
  for questions. Watchdog: server killed -> question waited and answered in 4.2 s with a new
  process. Two real bugs found and fixed: (1) a server orphaned by a crashed test survived
  and (2) the next run adopted it because it answered on the same port. Now every start
  uses a free port and the server is in a Windows job object, so it dies with Edith even on
  a crash (checked with os._exit: 0 servers left).
- Context (`brain/ask.py`): text first; screen text sent as diffs; temporal focus parsed
  from the question (N min ago, last N minutes, what changed, between HH:MM and HH:MM, in
  English and Spanish); screenshots only if the question needs to see (max 3, older ones
  640 px). "5 min ago" gets the last frame saved before then (found live: the first version
  sent no screenshot because no frame was saved inside the window).
- Router (`brain/answer.py`): cloud only on questions; local when "Local only", no
  connection, no key, session brake or monthly limit; gray line with what was sent or
  "Answered locally · reason"; 80 % warning; ledger with latency for both.
- Notes (`brain/notes.py`): no-model filter -> Glimmer classifies -> key moments; slide
  changes without a model; 10-minute block summaries (cut to whole sentences: Glimmer
  ignored the 50-word limit).
- Panel: streamed answers (Markdown rendered), footnote, 3 key moments (click = ask), Local
  only toggle; Activity: answer time + daily cost cards.
- Live acceptance (isolated EDITH_HOME, a lecture + real Invoice Portal screenshots, real
  models):

  | Case | Who answered | Time | Cost | Result |
  |---|---|---|---|---|
  | Key moments (filter + Glimmer) | local | 23.7 s | $0 | slide, exam (Nov 13), homework 4 due Monday: 3/3 |
  | Block summary | local | 48-53 s | $0 | correct facts, too long (now cut) |
  | "When is the final exam... due Monday?" | Muse Spark, text only | 8.6 s | $0.0035 | correct |
  | "What changed in the portal in the last 7 minutes?" | Muse Spark + 1 screenshot | 11.8 s | $0.0067 | correct (client, amount) |
  | "What was on screen 6 min ago?" (after the fix) | Muse Spark + 1 screenshot | 12.5 s | $0.0066 | correct |
  | Same exam question, Local only | Glimmer | 27.4 s | $0 | correct |
  | No connection (bad API URL) | Glimmer | 44.6 s | $0 | correct, "Answered locally · no connection" |
  | Monthly limit reached | Glimmer | 15.7 s | $0 | correct, "· monthly limit reached" |

- Tests: 27 new (focus parsing, frame picks, diffs, ledger, moments, routing with fakes,
  prompt injection guard): 141 pass.

## Final version, Phase 4 - Edit from memory (Nov 3)
- `edit/target.py`: the target is the field in front when Edith is opened. With Ctrl+Alt+E
  the app still has the focus; for a click on the dot, the closed dot now has
  WS_EX_NOACTIVATE, so the click does not steal the focus (a WM_MOUSEACTIVATE handler was
  tried first and did not work with Qt's window). Text read through TextPattern/ValuePattern
  (never passwords or excluded apps). Apply = bring the window back, Ctrl+End (append) or
  Ctrl+A (replace), paste with the user's clipboard saved and restored, read back to check.
  Undo = the app's Ctrl+Z, checked; refused if the text changed since; if the app's undo
  reaches the user's own typing, Edith redoes what it undid and reports it.
- `brain/edit.py`: intent (edit vs question vs "do the rest" vs undo), the edit prompt, JSON
  parsing, the source screenshot, the preview (new text in the accent, removed words struck
  through in gray). The router takes another system prompt plus the target field, so edits
  also fall back to the local model.
- Panel: preview, hints, "Based on: [thumbnail] source · N min ago", an always-visible
  "Undo Edith's last edit" while there is one; presentation mode (Ctrl+Alt+P) with a
  click-through outline (`ui/outline.py`).
- Found on the way:
  1. a probe of Windows 11 Notepad landed in the user's own open note (Notepad restores tabs);
     the test line was removed with Ctrl+Z and the note checked. Since then live tests only use
     windows the test opens itself (a Playwright Chromium with local pages).
  2. Chromium builds its accessibility tree on the first UIA query, so the first read of the
     focused element was a bare pane: the capture now retries once after 250 ms.
  3. an empty Chromium textarea reads as U+FFFC, so Edith thought it had text and added a
     leading newline: normalized.
  4. the first replies said "Here is the data I saw earlier": the prompt now asks to write as
     the user, with an example.
  5. an existing privacy test caught a GetAsyncKeyState call (to release held modifiers):
     removed; Edith never reads the keyboard.
- Live results (isolated memory: a lecture + real Invoice Portal screenshots; real Muse Spark;
  targets in a test Chromium):

  | Scenario | Time to preview | Applied | Undo |
  |---|---|---|---|
  | "put in my notes what the teacher said about the exam and the homework" | 8.0 s | 2 correct lines appended | text back exactly |
  | "reply to this email with the data I saw earlier" | 9.0-9.8 s | client, tax ID, amount exact | text back exactly |
  | user typed after Edith's edit, then undo | - | - | refused, user's text kept |
  | user typed and deleted, then undo | - | - | refused, field left exactly as it was |

  Presentation mode: the outline was visible around the field during the preview. Opening
  by a click on the dot: target captured, panel active with the cursor in its field.
- Tests: 13 new (intent, parsing, preview HTML, undo refusals without sending a key,
  router with the edit prompt): 154 pass.

## Final version - day memory and important moments for a year (Nov 3, user's request)
The user asked that Edith keep "any important detail" of the day, and that important things
(dates, anything whose omission could hurt) stay for a year.
- `memory/archive.py`: before the 10-minute buffer is pruned, its text (new screen lines per
  window, transcript, field values, window changes) goes to buffer/day/YYYY-MM-DD.jsonl, kept
  24 h, no model involved. Questions search it (accent-folded terms, numbers also without
  separators, rarity-weighted overlap, neighbours only within 90 s) and send only the matching
  lines from before the buffer. Time words beyond 10 minutes are understood: "at 9:30",
  "this morning/afternoon", "N min ago".
- Screens with little readable text are described by Glimmer in the background (at most one
  every 5 minutes) into the archive.
- Important moments (deadline, task, exam; the filter now also catches appointments, meetings,
  payments, dues) are kept 365 days; slides and summaries 24 h. Glimmer turns relative dates
  into real ones (a `date` field). Every question gets the year's important moments.
- Live (isolated home, real models):
  | Check | Result |
  |---|---|
  | "homework 4 is due next Monday" said on Tue Nov 3 | moment with date 2026-11-09, 16.9 s, local |
  | a screen with no readable text | described correctly (fields, values), 96 s, background |
  | RUC + amount of an invoice seen 3 h ago, among 300 noise lines | Muse Spark exact; 5 archive lines sent, $0.0030, 7.5 s |
  | an exam noted 40 days ago | "Monday December 14, chapters 1-6", $0.0030 |
  | the RUC question, Local only | Glimmer exact, 30.6 s |
- One bug found by the tests: a match's neighbours were added even 17 minutes apart (an
  unrelated "pancakes" line was sent); now only within 90 s.
- 10 new tests: 164 pass.

## Final version, Phase 5 - Privacy and robustness (Nov 3)
- Network audit (`scripts/network_audit.py` -> docs/NETWORK_AUDIT.md): the whole memory ran
  90 s on the real desktop (34 frames, 13 screen texts, 10 clicks, 9 transcript lines, notes
  with the local model) with every socket connect and DNS lookup of the process logged, and
  llama-server's connections polled with netstat. Result: 0 connections left the PC while
  recording (27, all to the local model on 127.0.0.1; llama-server had no outside peer). A
  question opened exactly one: `api.meta.ai` ("Sent: 13 screen texts, 9 transcript lines ·
  $0.0055"); with the cloud unreachable the local model answered.
- Static scan: the only other network code is the demo browser/portal (local), the
  sandbox, and the whisper download, which is tried offline first (local_files_only).
- Delete everything now (options, two clicks): pauses, deletes frames, audio, timeline, day
  archive, notes and year moments, learned plans; resumes. Keeps tools/ and the cost record.
- Rotating logs in state/logs (1 MB x 4); unexpected errors (any thread) are logged and Edith
  keeps running. The model watchdog was done in Phase 3.
- Found in the first real run: **the log contained the audio transcript** (and agent-mode
  logs contained row values). Logs outlive the 10 minutes and "Delete everything", so that
  was a privacy leak: the transcript is no longer logged and agent-mode details go to debug
  level only (not written unless -v).
- Onboarding (`ui/onboarding.py`): 3 screens on the first run only (what Edith does; the
  privacy sentence and retention; the shortcut, changeable there), Enter = next, Esc = skip.
  Real first run: the dot and the onboarding were on screen, the local model ready in ~40 s,
  no errors in the log, no llama-server left after quitting.
- Tests: 5 new (delete keeps machines and costs, rotating log + errors not fatal, onboarding
  pages, shortcut change in onboarding, two-click delete): 169 pass.

