"""Tool functions shared by the HTTP API and the MCP server.

These functions only read the card. They do not call a model.
"""

from __future__ import annotations

import base64
from datetime import timedelta
from urllib.parse import urlencode

from host.witness_index.search import search_records
from host.witness_store.api import Camera
from host.witness_store.format import iso
from host.witness_store.timeparse import parse_bound, parse_range


def get_status(camera: Camera) -> dict:
    return camera.status()


def list_recordings(camera: Camera, start: str, end: str) -> list[dict]:
    begin, finish = parse_range(start, end, camera.now, camera.tz)
    return [{"minute": iso(item.start), "frames": item.frames} for item in camera.card.recordings(begin, finish)]


def search_index(camera: Camera, start: str, end: str, query: str, k: int = 20) -> list[dict]:
    begin, finish = parse_range(start, end, camera.now, camera.tz)
    return search_records(camera.card.records(), begin, finish, query, k=k)


def inspect_window(camera: Camera, t: str, before_s: float, after_s: float, n: int) -> list[dict]:
    when = parse_bound(t, camera.now, camera.tz)
    frames = camera.card.frames(when - timedelta(seconds=before_s), when + timedelta(seconds=after_s))
    if n > 0 and len(frames) > n:
        frames = _even(frames, n)
    content = [
        {
            "type": "image",
            "data": base64.standard_b64encode(frame.jpeg).decode("ascii"),
            "mimeType": "image/jpeg",
        }
        for frame in frames
    ]
    content.append({"type": "text", "text": f"{len(frames)} frames around {iso(when)}"})
    return content


def make_clip(camera: Camera, start: str, end: str) -> dict:
    begin, finish = parse_range(start, end, camera.now, camera.tz)
    query = urlencode({"from": iso(begin), "to": iso(finish)})
    return {
        "clip_url": f"/api/v1/clip.mjpg?{query}",
        "avi_url": f"/api/v1/clip.avi?{query}",
    }


def get_live_frame(camera: Camera) -> list[dict]:
    if not camera.card.live_jpeg:
        return [{"type": "text", "text": "no live frame"}]
    encoded = base64.standard_b64encode(camera.card.live_jpeg).decode("ascii")
    return [{"type": "image", "data": encoded, "mimeType": "image/jpeg"}]


def _even(frames, n: int):
    if n == 1:
        return [frames[len(frames) // 2]]
    last = len(frames) - 1
    indexes = [round(i * last / (n - 1)) for i in range(n)]
    return [frames[index] for index in indexes]
