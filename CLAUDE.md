# CLAUDE.md — Edith

> "Do it once. Edith does the rest."
> "Edith never does the same work twice."

Summary of the user's master prompt (Nov 1, 2026), updated by the redesign of Nov 2, 2026:
the app was renamed Pantallita -> Edith, the mascot and the on-screen "tangible world" were
removed and replaced by a minimal soft-UI interface (see "Interface").

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
  soft shadows (light up-left, dark down-right); pressed/active = inset; no borders or
  dividers; 16-24 px corners; ONE accent color, everything else gray; clean sans-serif, lots
  of empty space.
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
- Everything local in buffer/, deleted after 10 min. Excluded apps never captured. Pause in the
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
5. Compile down & repair (Nov 3): network log of the demo browser (allowed domains only);
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
  workshop/ codegen.py runner.py sandbox.py build.py cli.py       (Phase 3)
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
