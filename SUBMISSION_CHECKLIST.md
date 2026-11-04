# Submission checklist — Meta Global AI Developer Hackathon 2026

Deadline: **Nov 4, 2026, 9 PM US Eastern (8 PM Lima)**. Indie Developer track.

## Rules (breaking them = disqualification)
- [x] All code written during the hackathon (since Oct 26, 2026); no copied code
- [x] Public repository: github.com/jccarrascov-sudo/edith
- [x] No `.env`, keys or `buffer/` committed (pre-commit hook + GitHub Action privacy-guard)
- [x] README: "Built during the hackathon" table and third-party table up to date
- [x] No asset without a license: everything drawn by code; font Source Serif 4 (OFL, bundled)
- [ ] 3-minute demo video recorded and uploaded (the user)
- [ ] Submission form filled with the repo link and the video link (the user)

## README for judges
- [x] First line: the theme + the problem in one sentence; pitch "Show it once. Edith does the next ones."
- [x] Mermaid diagrams: the execution cascade and the hybrid local/cloud scheme
- [x] "How we use Meta's models" (Muse Glimmer local / Muse Spark / SAM 3.1)
- [x] "Research Edith builds on" (paper, institution, venue, what Edith takes)
- [x] Results with real numbers: demo-checks, benchmark, times per level, time saved, cost
- [x] Privacy and security: what stays local, what goes to the API, what is never recorded,
      confirmations, emergency shortcut
- [x] Setup / run, scope and limitations (what is not built: level 3, free mode, ...)

## Evidence (scripts in the repo, results in `results/`)
- [x] `scripts/demo_check.py` — recipes: learn, variation, apply, undo, ambiguity, lessons, escalation,
      detection, desktop forms at levels 4-5 (5 runs in a row)
- [x] `scripts/memory_check.py` — capture -> temporal question -> edit from memory -> undo, with the
      API, without internet (local answer) and with the budget reached; real cost of 10 questions
- [x] `scripts/benchmark.py` — 20 screenshots Q&A, 10 edit scenarios, key moments vs labels
- [ ] `scripts/benchmark.py --parts resources` — VRAM/RAM, time to first token, GPU use
- [x] `scripts/network_audit.py` — no capture data leaves except what a question sends
- [x] Unit tests: `.venv\Scripts\python -m unittest discover -s tests`

## Before recording the video
- [ ] Restart Edith from the desktop shortcut (new code)
- [ ] `python demo/make_letters.py` (template + invoices.xlsx in demo/data/letters)
- [ ] Optional: Northwind Desktop for levels 4-5: `powershell -File demo/desktop/northwind.ps1 -Store demo\data\northwind.jsonl`
- [ ] Clear old recipes (Options -> Recipes -> Delete) so "learn this" starts fresh
