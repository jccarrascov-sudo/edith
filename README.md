# Pantallita

**Your screen has an inventor.**

Built for the Meta Global AI Developer Hackathon 2026 (Indie Developer track).

An on-screen companion that remembers the last ~10 minutes of your screen and system audio,
so you can ask it what just happened: *"what was the video about?"*, *"what was that error
in the terminal 5 minutes ago?"*. Later phases will let it act on the computer while
visually showing what it is about to touch (with SAM 3).

The brain is **Muse Spark** (`muse-spark-1.3`) through the Meta Model API.

## How it works (Phase 1)

```
 screen (mss, every 2 s) ──► change detection (64x36 grayscale diff) ──► buffer/frames/*.jpg ┐
 foreground window (ctypes) ─────────────────────────────────────────────────────────────────┤
 speakers (WASAPI loopback, 15 s blocks) ──► silence gate ──► faster-whisper (CUDA int8) ────┤
                                                                                             ▼
                                              buffer/timeline.jsonl (rolling 10-minute window)
                                                                                             │
 pantallita ask "..." ◄── Muse Spark ◄── transcript + window history + 8-12 key frames ◄──────┘
```

- **Capture** saves a frame only when the screen changed (>2% mean difference) or every 30 s.
- **Privacy**: windows matching the exclusion list in `config.toml` (password managers,
  banks, private browsing...) are never captured, not even their title. Everything lives
  in `buffer/`, which is git-ignored and pruned continuously to the last 10 minutes.
- **Audio** is the system output (what you hear), not your microphone. Silent blocks are
  skipped; whisper is loaded only when there is something to transcribe.
- **Ask** sends the transcript, a compact window history and the most informative frames
  (highest change per time slice + the most recent ones), and prints the tokens spent.

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
.venv\Scripts\python -m unittest discover -s tests       # tests
```

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
| Muse Spark via the [Meta Model API](https://dev.meta.ai/docs) | Multimodal reasoning (remote API) | Meta terms |

No images, sounds or other assets are included.

## License

[MIT](LICENSE)
