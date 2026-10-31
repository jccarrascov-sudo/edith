# Pantallita — Architecture

## Memory (Phase 1)

```
screen (mss, every 2 s) ──► change detection (64x36 grayscale diff) ──► buffer/frames/*.jpg ┐
foreground window (ctypes) ─────────────────────────────────────────────────────────────────┤
speakers (WASAPI loopback, 15 s blocks) ──► silence gate ──► faster-whisper (CUDA int8) ────┤
                                                                                            ▼
                                             buffer/timeline.jsonl (rolling 10-minute window)
```

- `capture.py`: saves a frame only if the screen changed (> 2 % mean difference) or every 30 s.
- `audio.py`: system output only (never the microphone); silent blocks are not transcribed.
- `timeline.py`: append-only JSONL shared between processes through a lock file; pruned every 5 s.
- `uia.py`: UI Automation reader (visible elements, values via ValuePattern). Password fields
  are never read. Nothing in Pantallita ever hooks the keyboard.
- Privacy: while an excluded app (password managers, banks, private browsing) is in front,
  no frame, title, process name or audio block is kept (`PrivacyGate`).

## Token efficiency

Every call to Muse Spark is billed per input token, and screenshots dominate the bill
(~1,200 tokens for one 1280x720 frame). Pantallita applies four techniques, all in
`src/pantallita/tokens.py` and wired into `ask.py`. Each is inspired by recent GUI-agent
research. Those papers prune or compress inside the model; we only control what we send
through an API, so we adapted the ideas to that setting.

### 1. Decreasing resolution for history (inspired by GUIPruner, 2026)

GUIPruner ([Spatio-Temporal Token Pruning for Efficient High-Resolution GUI Agents](https://arxiv.org/abs/2602.23235))
shows that GUI agents have a recency bias, and its *Temporal-Adaptive Resolution* shrinks
older screenshots with a decay schedule. We do the same: the current frame is sent at full
resolution (1280 px wide), the previous one at 75 %, and all older ones at 50 % (configurable:
`ask.history_scales`). Images are always **downscaled uniformly**, never pruned into patches
with holes, so the model still sees a coherent picture.

For `ask` we use the milder 100/75/50 schedule rather than 100/50/25: questions such as
"what was that error 5 minutes ago?" need old frames to stay readable. Agent loops that act
on the current screen (planning, repair) can use the steeper 100/50/25 schedule.

### 2. Crops of the relevant area (inspired by SimpAgent, ICCV 2025)

SimpAgent ([Less is More: Empowering GUI Agent with Context-Aware Simplification](https://openaccess.thecvf.com/content/ICCV2025/papers/Chen_Less_is_More_Empowering_GUI_Agent_with_Context-Aware_Simplification_ICCV_2025_paper.pdf),
HIT Shenzhen and Huawei, [code](https://github.com/JiuTian-VL/SimpAgent)) found that unrelated
elements distract GUI agents, and removes them with **masking**. Through an API, masking
saves nothing: a black-masked screenshot costs exactly the same tokens as the full one
(measured below). So when we know the element (from UI Automation or SAM), we send a
**crop** of it with a 48 px margin, at full detail (`tokens.crop_with_margin`). Near screen
edges the crop slides inward instead of shrinking.

### 3. Text before images (inspired by A11y-Compressor, 2026)

A11y-Compressor ([arXiv 2605.00551](https://arxiv.org/abs/2605.00551), ACL SRW 2026) turns a
linearized accessibility tree into a compact structured observation at ~22 % of the tokens.
`uia.compressed_tree` keeps only visible, named, interactive elements (plus short labels),
one line each: `[id] type "name" = "value" @x,y,w,h`, deduplicated, with password values
never read.

Our measurement adds a caveat: in a busy browser window (221 nodes, mostly tabs and
toolbar buttons) the compressed tree still cost **4x more than a screenshot**. Text is not
automatically cheaper. `tokens.prefer_text()` therefore sends the tree only when its
estimated cost is below the screenshot's, using constants calibrated with the official
counter (~768 px per image token, ~3 characters per text token), and never for decisions
that are visual by nature. Small forms such as the demo invoice portal take a few hundred
tokens as text.

### 4. History as differences

Instead of repeating full states, we send what changed between steps:
- **Text**: the window history lists only *changes* of foreground window; whisper's short
  segments are merged into ~30 s lines; UIA field values are recorded only when they change.
- **Images**: an older key frame is sent as a crop of the region that changed since the
  previous key frame (`tokens.changed_box` on a 64x36 grid); a frame with no visible change
  becomes a one-line text note. The newest frame is always complete.

### Measurements

All numbers were obtained with `scripts/measure_tokens.py`, which calls the official
`POST /v1/responses/input_tokens` endpoint (counts only, generates nothing), on real data
from the developer's machine on 2026-10-31 (muse-spark-1.3). During capture a YouTube video
was playing most of the time, which is close to the worst case for visual diffs.

`ask` context, same 10-minute buffer and question (55 frames, 24 transcript segments,
39 window events; 12 key frames sent):

| `ask` context | Input tokens | vs. old default |
|---|---:|---:|
| Old default: raw events, 12 frames at 1024 px | 12,166 | — |
| + Text as diffs (merged transcript, window changes only) | 11,850 | -3 % |
| Every frame at full 1280 px, no decay (reference) | 16,878 | +39 % |
| + Decreasing resolution 100 / 75 / 50 % | 7,386 | **-39 %** (-56 % vs. full res) |
| + Decreasing resolution 100 / 50 / 25 % | 4,835 | -60 % (-71 % vs. full res) |
| + Visual history as diffs (changed-region crops), 100 / 75 / 50 % | 7,288 | -40 % |

Live screenshot of a browser window on a 1920x1080 screen (fixed request overhead of
167 tokens excluded):

| Known element | Input tokens | Saving |
|---|---:|---:|
| Whole screen at 1280 px | 1,198 | — |
| Crop of one text field + 48 px margin (1433x140, full detail) | 262 | **-78 %** |
| Same field, rest of the screen masked black (SimpAgent-style) | 1,198 | 0 % |

| Same browser window as... | Input tokens | vs. raw tree |
|---|---:|---:|
| Raw linearized UIA tree (2,032 nodes) | 90,475 | — |
| Compressed UIA tree (221 nodes) | 4,938 | -95 % |
| Screenshot at 1280 px | 1,152 | -99 % |

Takeaways:
- Decreasing resolution is the biggest win for history-heavy prompts; with it on, a real
  `ask` answered with the same level of detail at 7,042 input tokens (11,281 before).
- Crops cut ~4/5 of the cost when the target element is known; masking does not help over an API.
- Visual diffs helped little here (-1 % extra) because a playing video changes most of the
  screen; they are expected to matter more for form-filling sessions, where only a field changes.
- Compressing the UIA tree is essential (-95 %), but for large windows a screenshot is still
  cheaper, hence the cost-based `prefer_text()` decision.
