"""MJPEG AVI muxed from indexed JPEG frames. Odd-sized chunks are padded."""

from __future__ import annotations

import struct

from host.witness_store.jpegsize import jpeg_size


def mjpeg_avi(frames: list[bytes], fps: int, width: int = 0, height: int = 0) -> bytes:
    if frames and (width == 0 or height == 0):
        width, height = jpeg_size(frames[0])
    width = width or 160
    height = height or 120
    fps = fps or 5
    count = len(frames)
    largest = max((len(frame) for frame in frames), default=0)
    micro = int(1_000_000 / fps)
    avih = struct.pack(
        "<14I",
        micro,
        0,
        0,
        0x10,
        count,
        0,
        1,
        largest,
        width,
        height,
        0,
        0,
        0,
        0,
    )
    strh = struct.pack(
        "<4s4sIHH8I4h",
        b"vids",
        b"MJPG",
        0,
        0,
        0,
        0,
        1,
        fps,
        0,
        count,
        largest,
        0,
        0,
        0,
        0,
        width,
        height,
    )
    compression = struct.unpack("<I", b"MJPG")[0]
    strf = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        24,
        compression,
        width * height * 3,
        0,
        0,
        0,
        0,
    )
    hdrl = _list(b"hdrl", _chunk(b"avih", avih) + _list(b"strl", _chunk(b"strh", strh) + _chunk(b"strf", strf)))
    movi_body = b""
    index = b""
    cursor = 4
    for frame in frames:
        chunk = _chunk(b"00dc", frame)
        index += struct.pack("<4sIII", b"00dc", 0x10, cursor, len(frame))
        movi_body += chunk
        cursor += len(chunk)
    body = hdrl + _list(b"movi", movi_body) + _chunk(b"idx1", index)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"AVI " + body


def _chunk(tag: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) % 2 else b""
    return tag + struct.pack("<I", len(payload)) + payload + pad


def _list(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    pad = b"\x00" if len(body) % 2 else b""
    return b"LIST" + struct.pack("<I", len(body)) + body + pad
