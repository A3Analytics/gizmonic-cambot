"""1/8-scale grayscale motion against the previous frame, scored from 0 to 1."""

from __future__ import annotations

from io import BytesIO

from PIL import Image


def downsample(jpeg: bytes) -> list[int]:
    image = Image.open(BytesIO(jpeg)).convert("L")
    width, height = image.size
    small = image.resize((max(1, width // 8), max(1, height // 8)))
    return list(small.tobytes())


def motion_score(previous: list[int] | None, jpeg: bytes) -> tuple[float, list[int]]:
    current = downsample(jpeg)
    if previous is None or len(previous) != len(current) or not current:
        return 0.0, current
    total = sum(abs(left - right) for left, right in zip(previous, current))
    return min(1.0, (total / len(current)) / 255.0), current
