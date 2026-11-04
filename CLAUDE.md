# CLAUDE.md — Edith

> "Do it once. Edith does the rest."
> "Edith never does the same work twice."

Summary of the user's master prompt (Nov 1, 2026), updated by the redesign of Nov 2, 2026:
the app was renamed Edith, the mascot and the on-screen "tangible world" were
removed and replaced by a minimal soft-UI interface (see "Interface").

## FINAL VERSION (master prompt of Nov 2, 2026, 22:30 — takes precedence where it differs)
Concept: Edith remembers the last 10 minutes of screen and audio and turns them into
actions: it PROPOSES the edit, the user decides. Work phase by phase: run tests, commit with
a clear message, report. Verify what exists instead of redoing it. No dead code.
- HYBRID engine (user's decision): LOCAL model = Muse Glimmer 30B GGUF (Meta, Apache-2.0,
  vision; unsloth UD-Q2_K_XL 2-bit + mmproj-kquant) through llama.cpp's llama-server, "light
  usage": screen text comes cheaply from UIA (no model); Glimmer works in low-priority
  batches (block summaries every 10 min, key moments, describing <=2 small images, offline
  answers). VRAM default chosen from measurements (2 GB vs 6 GB). Nothing of the continuous
  capture is ever sent to the cloud.
- Muse Spark via the Meta Model API ONLY when the user asks or requests an edit. Key from env
  var META_API_KEY (never in code/repo). Send only what is relevant, cheapest first: (a) text
  (day summaries, local descriptions/extracted text), (b) images only if the question needs
  to see something, max 3, downscaled. Fixed prefix first for prompt caching. Excluded apps
  never sent, not even as text.
- Cost: tokens and $ per query/day/month ($1.25/M in, $0.15/M cached in, $4.25/M out,
  configurable); metrics view sparkline of daily cost + month total; monthly limit: 80 % warn,
  100 % -> local only. "Local only" option ($0). Panel shows in small gray what was sent
  ("Sent: 2 summaries, 1 screenshot"); optional confirm before sending images (off).
- No internet / API error / budget reached -> answer with the local model, gray line
  "Answered locally". Text everywhere: "Your screen never leaves your PC. Only what you ask
  goes to the cloud." (never claim "100 % local").
- Interface additions: dot = gray idle, dim gray capture paused, accent busy (fades only);
  dot centered geometrically (report both centers' coordinates); global hotkey (default
  Ctrl+Alt+E, customizable in options: "Press the combination…", modifier + key required,
  Esc cancels, warns if taken and keeps the old one, saved and applied at once, restore
  default). Metrics view: VRAM, latency, cost sparklines.
- Memory: change-significant captures only; 10-min ring buffer; transcript on the same
  timestamps; temporal questions get the timestamped SEQUENCE of relevant frames ("what
  changed?", "what was there 5 min ago?", "summarize between X and Y"). Key moments in the
  background (slide change, dates/deadlines/tasks mentioned, explicit instructions "this is
  in the exam"): max 3 in the panel, one line each, time in gray, click = ask about it, no
  pop-ups. Warm-up at start, token streaming, timeouts everywhere, low priority, no GPU hogging.
- STAR FEATURE, edit from memory: "put in my notes what the teacher explained", "reply to
  this email with the data I saw earlier": find it in the timeline, prepare the edit in the
  open app. NEVER act directly: preview with new text subtly in accent and removed text struck
  through in gray; Enter = apply, Esc = discard (small gray hints); below "Based on:
  [thumbnail] X min ago". "Undo Edith's last edit" always available. Presentation mode (off by
  default, own hotkey): soft outline on the element Edith acts on.
- "Do the rest" (forms, verified machines) stays as a second action (user did not ask to drop it).
- Privacy & robustness: network audit (no capture data leaves except what a question sends),
  pause, excluded apps, "delete everything now"; model watchdog with auto-restart and a short
  message (Edith never closes); rotating logs; onboarding of max 3 screens (what it does, the
  privacy sentence above, the hotkey editable there).
- Evidence: tests (change detection, 10-min buffer, hotkey, model recovery, preview/undo);
  benchmark script -> CSV (VRAM/RAM avg+peak, time to first token, total time, GPU use with a
  heavy app open; 20 screenshots Q&A accuracy; 10 edit-from-memory scenarios: % correct and
  time to preview; key moments vs hand-labelled; 2-bit vs 3-bit quant); demo-check script
  (capture -> temporal question -> edit from memory -> undo) with API, without internet
  (local answer) and with the budget reached, OK/FAIL per step; README for judges (problem in
  2 lines, Mermaid of the hybrid flow, how Muse is used, benchmark table, daily/monthly cost
  table, install/run, privacy). Final report must include the real cost of 10 test questions.
- Phone / multi-machine features: they never existed in this repo (nothing to flag).

## RECIPES (user's spec of Nov 3, 2026, 22:40 — takes precedence where it differs)
"Show it once. Edith does the next ones." Every task is compiled to the LOWEST execution level
that reproduces the same result; nothing runs on the user's visible screen. Cascade: 1 direct
data (file libraries) > 2 app API (Office COM / app CLI) > 3 web API (official API with token in
Windows Credential Manager; else headless Playwright with the user's session profile; never
CAPTCHAs/2FA: ask the user) > 4 background UI Automation (Invoke/ValuePattern, no mouse/focus) on
a separate virtual desktop > 5 SAM 3.1 vision, same desktop.
Two memories in Edith's folder, editable/deletable from options: RECIPES (steps, params,
official level, program, verification, manual time) and LESSONS (one-line preferences with the
app/context; created when the user edits the preview, writes "corrige: ...", edits an applied
result minutes later, or rejects a preview and does it differently; the local model summarizes
"Aprendí: ..." in small gray, discardable; relevant lessons applied before every run).
Learning: Ctrl+Alt+R / "Edith, aprende esto"; automatic detection of a sequence repeated 2+
times -> "¿Guardo esto como receta?" in the panel; free mode induces recipes from successful runs.
Compile: Muse Spark writes the program for level 1, else 2, ... 5; MANDATORY verification on
copies vs the user's result; max 2 corrections per level; code whitelist; timeout 30 s (1-3) /
3 min (4-5). Run: local model picks recipe + params + lessons (no API if only params change);
logic change -> Muse Spark adapts and re-verifies; a step failing 2x -> Muse Spark replans,
then next level, then stop with a one-line reason; always on copies; preview of the RESULT;
Enter/Esc; backup for Undo; small gray "Compilado por Muse Spark · Nivel N · Hecho en X s"
(overrides "no technical details in the panel" for this line); extra confirmation before
sending email/messages, deleting, payments; Ctrl+Alt+Q stops everything. Free mode: Muse Spark
plans, UIA + SAM 3.1 perception, 25 steps / 3 min. Stages A-E (git tags etapa-A...).
PRIORITY ORDER (user, same night; read first): P1 = stage A + demo recipe Excel -> Word < 3 s +
demo-check 5/5; P2 = simple lessons (preview edit or "corrige:"), detection (d) behind an OFF
flag; P3 = escalation to Muse Spark after 2 failures; P4 only if all pass: levels 4-5 + automatic
detection. Never start a priority before the previous passes; after each: demo-check, commit,
report; revert what breaks demo-check; unfinished work stays behind an OFF flag and out of the UI.
Status: stage A done (src/edith/recipes/, scripts/demo_check.py, demo/make_letters.py); P2 done
(recipes/lessons.py: triggers a and b; lessons live in the recipe's program, re-verified; c and d not built);
P3 done (recipes/escalate.py). pywin32 ignores keyword args to COM methods: level-2 programs use positional ones.

## Product (the video revolves around this; no command is typed in the video)
A minimal desktop assistant with a local memory of the last 10 minutes.
1. The user does ONE task by hand (e.g. 1 invoice from Excel into a web portal), recording nothing on purpose.
2. REQUEST: the user opens the panel and says "do the rest" (any wording that asks to do or
   repeat something goes to agent mode; anything else is a question for `ask`).
3. Edith searches its memory for the demonstration and where its data came from
   (retroactive learning), understands the intent, splits it into blocks and FABRICATES a machine.
4. VERIFY BY EQUIVALENCE: run the machine on the same case the user did, on a temporary DB;
   the state change must equal the demo's. Otherwise repair only the failing block (max 3).
5. CONFIRM: before submitting, the panel shows a summary (rows, totals, skipped rows and
   the demo it is based on). Enter confirms, Esc cancels. Nothing is drawn over other windows.
6. COMPILE DOWN: use the lowest level that passes verification: network (replay the form's
   request) > page (Playwright by labels) > screen (visible fallback).
7. PROOF INCLUDED: every machine stores the demo and the expected state change; before each
   use it runs that proof dry. If it fails it does not run and goes to repair.
8. REPAIR BY INTENT: each block has a text contract ("put the Amount column in the field
   that means amount"). If the portal changes, relocate the field alone (UIA/SAM) and
   re-verify; only if ambiguous or failing, Edith asks to be shown one again.
9. Request with no prior demo → "do one by hand, then ask me again"; the next request works.
Also: ask what happened on screen/audio in the last 10 min, from the same text field.

## Interface (Nov 2 redesign; replaces the mascot and the tangible world)
Same stack: PySide6, everything drawn with QPainter, no assets. Rule: if an element is not
needed at that moment, it is not shown.
- At rest: a ~40 px neumorphic dot, always on top, draggable, no text or icon. The center's
  color is the ONLY status indicator: gray = idle, accent = thinking/acting. No animations,
  at most a soft fade.
- Click: it opens into a minimal panel with ONE text field and the answer under it. The audio
  button and the options (Activity, Pause memory, Quit) appear only while hovering the panel.
  Esc or a click outside closes it back into the dot.
- Agent mode: no overlays or outlines on screen; only one gray line of text in the panel
  ("Writing row 3 of 29 in ...").
- Soft UI: one flat background color; panel and buttons the SAME color; relief only from two
  soft shadows (light up-left, dark down-right, equal offset and blur); the closed dot has a
  centered symmetric glow; window margin >= offset + blur on all sides; pressed/active = inset;
  no borders or dividers; 16-24 px corners; ONE accent color, everything else gray.
- Visual adjustments (user, Nov 3): font Source Serif 4 ONLY (bundled, OFL; 400 text, 600
  titles/big numbers; 14 px text, 12 px secondary, 28 px big numbers). Text #1A1B1E light /
  #F2F3F5 dark, secondary = text at 60 % (62 % in light: 60 % fails 4.5:1); accent #5B7CFA
  light (text in accent: #2A55F9, 4.6:1) / #7AA7FF dark; every text >= 4.5:1 (tested). Panel:
  equal 24 px inner margins, one text axis, opens centered on the dot, kept on screen.
  Screenshots + centers: capturas-ui/ (local).
- Aesthetics (user, Nov 3): "Apple standards", a visual delight; shadows must be soft and thin
  (real Gaussian blur, tinted, low opacity), harmonious colors, and NO cost or technical details
  in the panel (they live in Activity; `[ui] show_details` restores them). Every change is
  checked on screenshots in both themes before it is called done.
- Humanity (user, Nov 3): small talk and general questions are answered naturally WITHOUT the
  memory (nothing of the screen sent); the memory only when the question warrants it; doubts
  about the current screen are answered from it; Edith guides the user to set up automations
  (`brain/talk.py`).
- Charts only where there is data, in a separate view (Activity: RAM, VRAM, memory activity
  in the last 10 min): 1.5-2 px accent sparkline, no axes/grid/legend, at most a 10-15 %
  fill under the line, the important number large on top, exact values only in a tooltip.
- Light/dark follow the Windows app theme live (no restart); all colors are variables with a
  light set (bg ~#E6E9EF, white light shadow, blue-gray dark shadow) and a dark set (bg
  ~#23262B, light shadow ~#2D3036, dark shadow ~#191B1F, very subtle); 200-300 ms fade.
- Texts in English (video) and neutral Spanish, configurable (`[ui] language`).

## Hackathon rules (breaking them = disqualification)
Meta Global AI Developer Hackathon 2026, Indie Developer track, theme "Push the Frontier".
**Deadline: Nov 4, 2026, 9 PM US Eastern (8 PM Lima).** A 3-minute video is mandatory.
- All code new, written since Oct 26, 2026. Never copy existing code.
- PUBLIC repo (github.com/jccarrascov-sudo/edith). Never commit .env, keys or buffer/.
- README "Built during the hackathon" vs. third-party table, always up to date.
- No asset without a license: everything drawn by code or made with Muse Image.

## Meta models & budget
- Muse Spark 1.3 via the Meta Model API (OpenAI-compatible). Env vars: MODEL_API_KEY,
  MODEL_API_BASE_URL, MODEL_NAME=muse-spark-1.3. Never read or print the key's value.
  NEVER any "-contributor" variant. Reasoning tokens count against max_tokens.
- SAM 3 / 3.1 (`sam-3.1`, `/v1/responses`, box/point prompts, `meta-sam-parser`): locate
  elements in the demo and in repair.
  Point prompt IoU 0.96, tight box 0.95; a loose box (~30 px margin) only 0.50.
- Muse Image (`muse-image-1.0`, $0.01/image): only checked by scripts/test_api.py; not used by the app.
- Verify endpoints at dev.meta.ai with minimal scripts before building; if something is not
  available, tell the user before looking for alternatives.
- Session cost meter ($1.25/M in, $0.15/M cached in, $4.25/M out) and a $5 session brake (configurable).

## Privacy (mandatory)
- NEVER record the keyboard, in any mode.
- Recorded: screenshots, click position + element, active window, field values via UIA
  ValuePattern when they change; IsPassword = true is never read.
- Row data is read from the spreadsheet itself, never from the image. Edith's own text field
  is never read by the memory (questions are not memory).
- Network: only requests of the demo browser (Chromium opened by Edith via
  Playwright/CDP), only for allow-listed domains (default: the local portal). The network
  level is only used on allowed domains.
- Everything local in buffer/. Raw capture (frames, audio, timeline) deleted after 10 min; the
  day's TEXT archive and block summaries kept 24 h; important moments (dates, deadlines, exams,
  tasks, appointments, payments) kept 12 months (user's decision, Nov 3: "important things whose
  omission could affect the user must stay for a year"; "any important detail" must be kept).
  Conversations with Edith and lasting facts about the user (brain/companion.py) kept 12
  months; deleted by "Delete everything now". Excluded apps never captured. Pause in the
  panel's options; the audio button turns system-audio listening off.

## Environment & style
Windows 11, PowerShell, Python 3.12 in .venv (`.venv\Scripts\python`). RTX 5050 8 GB VRAM,
16 GB RAM. Max ~1.5 GB VRAM; the UI < 2 % CPU idle (it repaints only on change).
The user NEVER edits files by hand: Claude creates, edits and installs everything.
Code, comments, README, docs and commits in English; talk to the user in Spanish.
Small commits, push each time something works. Git identity:
JUAN CARLOS CARRASCO VELAPATIÑO <230002519+jccarrascov-sudo@users.noreply.github.com>.

## Demo environment
Local "Invoice Portal" (FastAPI + HTML + SQLite, `demo/portal/app.py`, own save endpoint):
v1 client, tax ID, date, amount, description; v2 amount moved and renamed (automatic
repair); v3 two amount fields (ambiguity: must ask "Show me one"). Excel with 30 fictitious
invoices with hard cases: accents and ñ, amounts with commas and decimals, dates in 2
formats, 1 empty cell that is reported without breaking. `demo_reset` resets portal, DB,
buffer and workshop.

## Machine architecture
JSON plan of fixed block types (read_row, fill_field, click, wait, save, verify), each with
a text contract and its code per level (network/page). The runner emits events (row in,
block done, row out, error in block X, level used); the panel shows them as one status line.
Anti-cheat: no data value hard-coded; every value comes from a column. Besides the demo
case, verify 2 more rows before processing all. Store in tools/<name>/: plan.json, blocks,
proof/ (demo + expected state change). Golden rule: NOTHING enters the workshop without
passing its equivalence verification.

## Token efficiency (cite in docs/ARCHITECTURE.md "Token efficiency")
Text before images (compressed UIA tree; A11y-Compressor 2026); crops of the relevant area
(SimpAgent, ICCV 2025); current + previous screenshot, earlier steps as text (UI-TARS 2025),
older screenshots at uniformly decreasing resolution (GUIPruner 2026), history as diffs;
"ask" frames by relevance + temporal coverage, max 8 (Adaptive Keyframe Sampling, CVPR 2025);
prompt caching (fixed prefix first, variable part last; verify how it activates). Log tokens
before/after each optimization.

## Repo & presentation
.gitignore (.env, .venv/, __pycache__/, buffer/, *.wav, *.sqlite, *.db, tools/* except
tools/.gitkeep); .env.example with the 3 vars (empty key); pre-commit hook in
.githooks/pre-commit (UTF-8 without BOM, LF, core.hooksPath) blocking .env, buffer/,
.wav/.sqlite/.db and key-like lines; GitHub Action privacy-guard failing on tracked .env or
buffer/. README sections: tagline + second sentence; "Demo video: coming Nov 4"; The
problem; What Edith does; The interface; Why it's new; Meta
models; Privacy by design (free hackathon credits; production would use the standard
endpoint); Results ("—" until real data; NEVER invent numbers); Scope and limitations;
Related work (GPA Salesforce 2026, Work2Skill 2026, ShowUI-Aloha NUS 2026, AppAgentX
Westlake 2025, self-healing selectors in testing); Built during the hackathon ("(planned)"
for what doesn't exist yet); Architecture (Mermaid); Roadmap; Setup; License MIT.
Also docs/ARCHITECTURE.md, docs/PRIVACY.md, BUILD_LOG.md (update when closing each phase),
SUBMISSION_CHECKLIST.md, LICENSE MIT (user's git name, 2026).

## Phases (run Get-Date at the start of each and report time left; if a phase slips more
than half a day, warn and apply the cuts)
0-2. Done (memory, learn backwards). Only the small (B) adjustments.
3. Fabricate, verify, proof included (Nov 1): page-level code per block, equivalence on a
   temp DB, anti-cheat, 2 extra rows, per-block repair (max 3), text contract per block,
   proof/, summary with confirmation. Accept: 30/30 on v1, empty cell reported without
   breaking, clear verification log, dry proof runs before each use.
4. Interface (redesigned Nov 2): dot + panel, agent mode with a status line and Enter/Esc
   confirmation, activity view, live light/dark. Accept: whole flow from the panel,
   understandable without the terminal; UI < 2 % CPU idle.
5. Compile down & repair (DONE Nov 2; network level learned from the sandbox run, not the user's browser): network log of the demo browser (allowed domains only);
   network level verified like the others; the level used is reported in the status line. Repair by intent: v2
   repairs itself; v3 asks to be shown one.
   Accept: v2 back to 30/30 changing one block unaided; v3 asks for a demo then works;
   network level passes verification.
6. Results & delivery (Nov 4, done before 3 PM Lima): benchmark with REAL numbers on 30
   invoices: (a) click-by-click agent, (b) Muse Spark writes a script from a text
   description only, (c) page-level machine, (d) network-level machine, (e) after v2 +
   repair. Correct/30, time, cost, tokens per invoice. Full README; remove "(planned)";
   go through SUBMISSION_CHECKLIST.md.
Cuts if late, in order: (1) section 14 except README Roadmap, (2) audio + ask, (3) activity
view, (4) automatic repair (keep asking for a demo), (5) network level. NEVER cut: core
(learn → fabricate → verify), proof included, confirmation before sending.

## Growing workshop (only after Phase 5)
The model is not retrained; the workshop of verified machines grows. (1) Request without a
demo → offer to try as an agent; if confirmed in the panel, compile that run into a machine
with proof. (2) Reuse verified equivalent blocks (type +
contract) from tools/, log tokens saved. (3) Never learn from unconfirmed/unverified runs.
(4) README Roadmap: learning from corrections at confirmation, long-term text-only pattern memory
under user control, repair library, offline version with Muse Glimmer. If no time: only (4).

## Code layout
```
src/edith/
  memory/   capture.py actions.py audio.py timeline.py winutil.py uia.py   (Phase 1)
  brain/    ask.py tokens.py client.py (costs + brake)
  learn/    segment.py source.py plan.py cli.py                  (Phase 2)
  workshop/ codegen.py runner.py sandbox.py build.py cli.py proof.py (Phase 3)
            network.py repair.py                                    (Phase 5)
  vision/   sam.py
  ui/       app.py panel.py agent.py theme.py stats.py           (interface)
demo/       portal/app.py make_invoices.py demo_reset.py
scripts/    test_api.py measure_tokens.py install_hooks.py
hooks/      pre-commit
docs/       ARCHITECTURE.md
```

## Working agreement
Each phase: test the acceptance criteria yourself, report REAL results (incl. failures),
update BUILD_LOG.md and README, commit + push, wait for the user's OK before the next.
