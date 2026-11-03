"""Minimal live tests of the three Meta APIs Edith builds on. Every call is cost-metered.

1. Muse Spark 1.3: text, and image input (base64 data URL) with known content.
2. SAM 3.1: a synthetic form with a button at a known position, segmented with a text
   prompt, a click-point prompt and a UIA-like box prompt; checks IoU against the true rectangle.
3. Muse Image 1.0: one generated image ($0.01), saved to state/ (git-ignored).

Run: .venv\\Scripts\\python scripts\\test_api.py [--skip-image]
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
import traceback

from PIL import Image, ImageDraw

from edith.brain import tokens
from edith.brain.client import STATE_DIR, MetaClient
from edith.config import load_config
from edith.vision import sam

BUTTON = (330, 395, 530, 445)  # true button rectangle in the synthetic form


def iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def synthetic_form() -> Image.Image:
    img = Image.new("RGB", (800, 500), "#f4f6f8")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((60, 40, 740, 470), radius=12, fill="white", outline="#d6dbe1")
    for i, label in enumerate(["Client", "Tax ID (RUC)", "Invoice date", "Amount (PEN)"]):
        y = 70 + i * 78
        d.text((90, y), label, fill="#1f2a37", font_size=18)
        d.rounded_rectangle((90, y + 26, 710, y + 60), radius=7, fill="white", outline="#c4cad3")
    d.rounded_rectangle(BUTTON, radius=8, fill="#2f6fde")
    d.text((BUTTON[0] + 38, BUTTON[1] + 13), "Save invoice", fill="white", font_size=20)
    return img


def test_spark(client: MetaClient) -> str:
    r = client.chat("test: text", max_tokens=1000, reasoning_effort="low",
                    messages=[{"role": "user", "content": "Reply with exactly: pong"}])
    text_answer = r.choices[0].message.content
    img = Image.new("RGB", (320, 160), "navy")
    ImageDraw.Draw(img).text((40, 50), "7 3 9", fill="yellow", font_size=60)
    r = client.chat("test: image", max_tokens=1000, reasoning_effort="low", messages=[{"role": "user", "content": [
        {"type": "text", "text": "Which digits are written in this image, and what is the background color? One short line."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{tokens.jpeg_b64(img, 90)}"}}]}])
    return f"text -> {text_answer!r}; image -> {r.choices[0].message.content!r}"


def test_sam(client: MetaClient) -> str:
    img = synthetic_form()
    out = []
    t0 = time.monotonic()
    segs = sam.segment(client, img, text="button", purpose="test: sam text")
    best = max(segs, key=lambda s: iou(s.box, BUTTON), default=None)
    out.append(f"text prompt 'button': {len(segs)} match(es), best box {best.box if best else None}, "
               f"IoU {iou(best.box, BUTTON) if best else 0:.2f}, {time.monotonic() - t0:.1f}s")
    # Edith knows the click position and the UIA rectangle, so those are its prompts.
    # (A loose box with a ~30 px margin measured IoU 0.50: SAM expects a fairly tight box.)
    center = ((BUTTON[0] + BUTTON[2]) // 2, (BUTTON[1] + BUTTON[3]) // 2)
    uia_box = (BUTTON[0] - 8, BUTTON[1] - 8, BUTTON[2] + 8, BUTTON[3] + 8)
    for label, kwargs in (("click point", {"point": center}), ("UIA box +8px", {"box": uia_box})):
        t0 = time.monotonic()
        segs = sam.segment(client, img, purpose=f"test: sam {label}", **kwargs)
        if not segs:
            raise RuntimeError(f"{label} prompt: no segment returned")
        score = iou(segs[0].box, BUTTON)
        out.append(f"{label} prompt: box {segs[0].box}, IoU {score:.2f}, "
                   f"mask {None if segs[0].mask is None else segs[0].mask.shape}, {time.monotonic() - t0:.1f}s")
        if score < 0.8:
            raise RuntimeError(f"{label} prompt IoU {score:.2f} < 0.8")
    return "\n           ".join(out)


def test_image(client: MetaClient) -> str:
    r = client.generate_image(
        "test: muse image",
        prompt=("a single soft gray circle on a plain light gray background, neumorphic soft UI, "
                "subtle shadows, minimal, original design"),
        size="1024x1024", output_format="png")
    data = base64.b64decode(r.data[0].b64_json)
    path = STATE_DIR / "muse_image_test.png"
    path.write_bytes(data)
    with Image.open(path) as im:
        return f"generated {im.size[0]}x{im.size[1]} {im.format}, {len(data) // 1024} KB -> {path}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-image", action="store_true")
    args = parser.parse_args()
    client = MetaClient(load_config())
    print(f"model={client.model}")
    tests = [("Muse Spark 1.3", test_spark), ("SAM 3.1", test_sam)]
    if not args.skip_image:
        tests.append(("Muse Image 1.0", test_image))
    failed = 0
    for name, fn in tests:
        try:
            print(f"[ OK ] {name}: {fn(client)}")
        except Exception as exc:  # report and keep going; never print request headers
            failed += 1
            status = getattr(exc, "status_code", "")
            print(f"[FAIL] {name}: {type(exc).__name__} {status} {str(exc)[:300]}")
            traceback.print_exc(limit=3, file=sys.stdout)
    print(client.meter.summary())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
