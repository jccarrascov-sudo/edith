"""SAM 3.1: the exact pixels of a UI element, from a text prompt or from a known box/point.

Pantallita usually already knows roughly where an element is (UI Automation rectangle or
the click position), so it sends a box/point prompt and gets back a tight box + mask, used
to highlight the element (fallback mode, verification stamps, film strip).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import meta_sam_parser as sam
import numpy as np
from PIL import Image

from ..brain import tokens
from ..brain.client import MetaClient

Box = tuple[int, int, int, int]


@dataclass
class Segment:
    object_id: str
    box: Box                 # left, top, right, bottom in image pixels
    confidence: float | None
    mask: np.ndarray | None  # H x W bool, same size as the image


def object_prompt(size: tuple[int, int], box: Box | None = None,
                  point: tuple[int, int] | None = None) -> str:
    """Box and/or point prompt in SAM's special-token syntax (built by Meta's parser library)."""
    prompt_box = sam.PromptBox(*box) if box else None
    points = [sam.PromptPoint(x=point[0], y=point[1])] if point else []
    frame = sam.FramePrompt(frame=0, box=prompt_box, points=points)
    return sam.build_object_prompt(size=size, objects=[sam.PromptObject(id=1, prompts=[frame])])


def parse(events: list[dict]) -> list[Segment]:
    async def source():
        for event in events:
            yield event

    async def run():
        parsed = sam.parse_responses_stream(source(), sam.image_segmentation_format())
        return await parsed.final_result()

    result = asyncio.run(run())
    boxes: dict[str, sam.SegmentationBoxRecord] = {}
    masks: dict[str, sam.SegmentationMaskRecord] = {}
    for record in result.records:
        if record.kind == "box":
            boxes[str(record.object_id)] = record
        elif record.kind == "mask":
            masks[str(record.object_id)] = record
    segments = []
    for oid, b in boxes.items():
        mask = None
        if oid in masks:
            m = masks[oid].mask
            raster = np.frombuffer(sam.decode_mask_to_raster(m), dtype=np.uint8)
            mask = raster.reshape(m.height, m.width).astype(bool)
        segments.append(Segment(oid, (int(b.left), int(b.top), int(b.right), int(b.bottom)),
                                b.confidence, mask))
    return segments


def segment(client: MetaClient, image: Image.Image, text: str | None = None, box: Box | None = None,
            point: tuple[int, int] | None = None, purpose: str = "segment") -> list[Segment]:
    """Segment `image` with either a noun phrase (`text`) or a box/point prompt."""
    if bool(text) == bool(box or point):
        raise ValueError("Use either a text prompt or a box/point prompt (SAM does not mix them).")
    prompt = text or object_prompt(image.size, box, point)
    data_url = f"data:image/png;base64,{tokens.png_b64(image)}"
    return parse(client.segment(purpose, data_url, prompt))
