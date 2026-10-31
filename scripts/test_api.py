"""Smoke test for the Meta Model API: one text call and one image call (base64 data URL).

Run: .venv\\Scripts\\python scripts\\test_api.py
"""

from __future__ import annotations

import base64
import io
import sys

from openai import OpenAI
from PIL import Image, ImageDraw

from pantallita.config import load_api_settings


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    api = load_api_settings()
    client = OpenAI(api_key=api.api_key, base_url=api.base_url)
    print(f"base_url={api.base_url} model={api.model}")

    r = client.chat.completions.create(
        model=api.model, max_tokens=1000, reasoning_effort="low",
        messages=[{"role": "user", "content": "Reply with exactly: pong"}])
    print("text :", r.choices[0].message.content, "| usage:", r.usage)

    # A generated image whose content we know, so we can tell the image really arrived.
    img = Image.new("RGB", (320, 160), "navy")
    ImageDraw.Draw(img).text((40, 50), "7 3 9", fill="yellow", font_size=60)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    r = client.chat.completions.create(
        model=api.model, max_tokens=1000, reasoning_effort="low",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Which digits are written in this image, and what is the background color? One short line."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}])
    print("image:", r.choices[0].message.content, "| usage:", r.usage)


if __name__ == "__main__":
    main()
