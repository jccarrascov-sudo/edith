"""Token-efficiency helpers for everything we send to Muse Spark.

See docs/ARCHITECTURE.md, section "Token efficiency", for the rationale and measurements.
"""

from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

Box = tuple[int, int, int, int]  # left, top, right, bottom (pixels)


def history_scales(n: int, schedule: list[float]) -> list[float]:
    """Scale for each of `n` images ordered oldest -> newest.

    The newest image gets schedule[0], the one before schedule[1], ...; older images
    reuse the last value. (Temporal decay, inspired by GUIPruner's TAR.)
    """
    if n <= 0:
        return []
    per_age = [schedule[min(age, len(schedule) - 1)] for age in range(n)]
    return per_age[::-1]


def scale_image(img: Image.Image, scale: float) -> Image.Image:
    """Uniform downscale of the whole image: no pruned patches, no holes."""
    if scale >= 1.0:
        return img
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.LANCZOS)


def fit_width(img: Image.Image, width: int) -> Image.Image:
    return img if img.width <= width else scale_image(img, width / img.width)


def crop_with_margin(img: Image.Image, box: Box, margin: int = 48, min_size: int = 160) -> tuple[Image.Image, Box]:
    """Crop `box` plus a context margin, never smaller than `min_size` per side."""
    left, top, right, bottom = box
    cx, cy = (left + right) / 2, (top + bottom) / 2
    w = min(img.width, int(max(right - left + 2 * margin, min_size)))
    h = min(img.height, int(max(bottom - top + 2 * margin, min_size)))
    # Near an edge, slide the window inward instead of shrinking it.
    x0 = int(min(max(0, cx - w / 2), img.width - w))
    y0 = int(min(max(0, cy - h / 2), img.height - h))
    crop = (x0, y0, x0 + w, y0 + h)
    return img.crop(crop), crop


def changed_box(before: Image.Image, after: Image.Image, grid: tuple[int, int] = (64, 36),
                cell_threshold: float = 0.08) -> Box | None:
    """Bounding box (in `after` pixels) of the grid cells that changed between two frames."""
    def cells(img: Image.Image) -> np.ndarray:
        return np.asarray(img.convert("L").resize(grid, Image.BILINEAR), dtype=np.float32) / 255.0

    diff = np.abs(cells(before) - cells(after)) > cell_threshold
    if not diff.any():
        return None
    rows, cols = np.where(diff)
    sx, sy = after.width / grid[0], after.height / grid[1]
    # One cell of padding so the change keeps a bit of context.
    return (int(max(0, cols.min() - 1) * sx), int(max(0, rows.min() - 1) * sy),
            int(min(grid[0], cols.max() + 2) * sx), int(min(grid[1], rows.max() + 2) * sy))


def box_area_ratio(box: Box, size: tuple[int, int]) -> float:
    return (box[2] - box[0]) * (box[3] - box[1]) / float(size[0] * size[1])


# Calibrated on muse-spark-1.3 with /v1/responses/input_tokens (docs/ARCHITECTURE.md):
# a 1280x720 JPEG counted 1,198 tokens and a 1433x140 crop 262, i.e. ~1 token per 768 px.
PIXELS_PER_TOKEN = 768
CHARS_PER_TOKEN = 3.0  # conservative for UIA lines (quotes, digits, short words)


def estimate_image_tokens(size: tuple[int, int]) -> int:
    return int(size[0] * size[1] / PIXELS_PER_TOKEN) + 1


def estimate_text_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def prefer_text(tree_text: str, image_size: tuple[int, int], needs_pixels: bool = False) -> bool:
    """Send the compressed UIA tree instead of a screenshot only when it is cheaper.

    Measured: a 221-node browser tree cost ~4x more than the screenshot, while a small
    form is a few hundred tokens. `needs_pixels` covers decisions that are visual by nature.
    """
    if needs_pixels or not tree_text.strip():
        return False
    return estimate_text_tokens(tree_text) < estimate_image_tokens(image_size)


def jpeg_b64(img: Image.Image, quality: int = 75) -> str:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=quality)
    return base64.b64encode(out.getvalue()).decode("ascii")


def png_b64(img: Image.Image) -> str:
    """Lossless encoding, for images whose exact pixels matter (e.g. SAM masks)."""
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")
