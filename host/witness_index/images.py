"""Solid frames with an optional shape, for motion and index tests."""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw


def shape_jpeg(draw_shape: bool = True, origin: tuple[int, int] = (40, 30)) -> bytes:
    image = Image.new("RGB", (160, 120), (255, 255, 255))
    if draw_shape:
        draw = ImageDraw.Draw(image)
        x, y = origin
        draw.rectangle([x, y, x + 40, y + 40], fill=(220, 20, 20))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
