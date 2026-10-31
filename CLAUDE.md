# CLAUDE.md — pantallita

## What this is
**pantallita** is an on-screen companion that remembers the last ~10 minutes of screen and
system audio, answers questions about what happened ("what was the video about?"), and later
will act on the computer while visually showing what it is about to touch (via SAM 3).
The brain is **Muse Spark** through the **Meta Model API** (OpenAI-SDK compatible;
`base_url` and key live in `.env`).

Built for the **Meta Global AI Developer Hackathon 2026** — Indie Developer track, theme
"Push the Frontier". **Deadline: Nov 4, 2026, 9 PM US Eastern.**

## Hackathon rules (breaking them = disqualification)
- All code must be new, written during the event. Never copy code from existing projects.
- The repo is public: NEVER commit `.env`, API keys, or anything under `buffer/`
  (screenshots and audio are private). Check `.gitignore` and `git status` before every commit.
- Keep it clear what is ours vs. third-party: whenever a library is added, update the
  "Built during the hackathon" table in `README.md`.
- No assets (images, sprites, sounds) without a license.

## Environment
- Windows 11, PowerShell. Python 3.12 in `.venv` — always use `.venv\Scripts\python`.
- GPU: RTX 5050 (8 GB VRAM, Blackwell), 16 GB RAM. The app must coexist with other apps:
  budget **≤ ~1.5 GB VRAM** and **low idle CPU**.
- The user never edits files by hand: Claude creates/edits everything and installs
  dependencies itself. Secrets (`.env`) are the exception — the user creates that file.

## Style
- Code, comments, README and commit messages in **English** (global judges).
- Talk to the user in **Spanish**.
- Small, descriptive commits after each working piece.

## Architecture (Phase 1)
```
src/pantallita/
  __main__.py  CLI: run | ask "question" | status
  config.py    loads config.toml (tomllib) + .env
  winutil.py   foreground window title + process name via ctypes (no pywin32)
  capture.py   mss every 2 s; diff vs last *saved* frame at 64x36 grayscale; save if
               > threshold (~2%) or 30 s elapsed; JPEG q70, max 1280 px wide; exclusion list
  audio.py     WASAPI loopback (PyAudioWPatch) in ~15 s blocks; skip silence (RMS + VAD);
               faster-whisper "small", CUDA int8 (CPU fallback)
  timeline.py  buffer/timeline.jsonl events (frame / transcript); rolling retention (10 min)
  ask.py       context = transcript + compact window-change list + 8–12 key frames
               (top change score spread over time + most recent) → Muse Spark multimodal;
               answers cite approximate time; prints tokens/images sent
  status.py    frame/segment counts and buffer disk usage
config.toml    thresholds, intervals, retention, exclusion list
buffer/        frames/, audio/, timeline.jsonl  (git-ignored, private)
```

## Phase 1 acceptance criteria
- Static screen → at most one frame every 30 s.
- Buffer never holds anything older than 10 min.
- An excluded window never appears in `buffer/`.
- 1 min YouTube video → `ask "what was the video about?"` answers correctly from audio.
- `git status` never shows `buffer/` or `.env`.
- Measure and report idle VRAM and CPU with `run` active.
