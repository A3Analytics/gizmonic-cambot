"""HTTP API for the rolling buffer. Every route except ping requires a bearer token."""

from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from host.witness_store.avi import mjpeg_avi
from host.witness_store.format import Card, iso
from host.witness_store.timeparse import parse_range

DEFAULT_TZ = "America/Los_Angeles"


class Camera:
    def __init__(self, card: Card, token: str, tz: str = DEFAULT_TZ, now: datetime | None = None):
        self.card = card
        self.token = token
        self.tz = ZoneInfo(tz)
        self.now = now or datetime.now(timezone.utc)
        self.budget_paused = False
        self.frames_used = 0
        self.frames_per_day = 2000
        self.tokens_used = 0
        self.tokens_per_day = 500_000

    def dispatch(self, method: str, path: str, headers: dict[str, str] | None = None) -> tuple[int, str, bytes]:
        headers = headers or {}
        parsed = urlparse(path)
        route = parsed.path
        query = parse_qs(parsed.query)
        if method == "GET" and route == "/api/v1/ping":
            body = json.dumps({"ok": True, "id": self.card.camera_id, "fw": "0.1.0"}).encode()
            return 200, "application/json", body
        if not _authorized(headers.get("Authorization"), self.token):
            return 401, "text/plain", b"unauthorized\n"
        if method == "GET" and route == "/api/v1/status":
            return 200, "application/json", json.dumps(self.status()).encode()
        if method == "GET" and route == "/api/v1/recordings":
            return self._recordings(query)
        if method == "GET" and route == "/api/v1/frames":
            return self._frames(query)
        if method == "GET" and route.startswith("/api/v1/frame/") and route.endswith(".jpg"):
            stamp = unquote(route[len("/api/v1/frame/") : -len(".jpg")])
            return self._one_frame(stamp)
        if method == "GET" and route == "/api/v1/frame.jpg":
            if not self.card.live_jpeg:
                return 503, "text/plain", b"camera=fail err=no_frame\n"
            return 200, "image/jpeg", self.card.live_jpeg
        if method == "GET" and route == "/api/v1/clip.mjpg":
            return self._clip(query, avi=False)
        if method == "GET" and route == "/api/v1/clip.avi":
            return self._clip(query, avi=True)
        if method == "GET" and route == "/api/v1/index":
            return self._index(query)
        if method == "GET" and route == "/api/v1/events":
            return self._events(query)
        return 404, "text/plain", b"not found\n"

    def status(self) -> dict:
        return {
            "id": self.card.camera_id,
            "time": iso(self.now),
            "time_synced": self.card.clock_synced,
            "recording": self.card.recording,
            "storage": {
                "used": self.card.used_bytes(),
                "capacity": self.card.capacity,
                "reserve_pct": self.card.reserve_pct,
            },
            "budget": {
                "frames_used": self.frames_used,
                "frames_per_day": self.frames_per_day,
                "tokens_used": self.tokens_used,
                "tokens_per_day": self.tokens_per_day,
                "paused": self.budget_paused,
            },
        }

    def _bounds(self, query: dict[str, list[str]]):
        try:
            return parse_range(_one(query, "from"), _one(query, "to"), self.now, self.tz)
        except (KeyError, ValueError):
            return None

    def _recordings(self, query: dict) -> tuple[int, str, bytes]:
        bounds = self._bounds(query)
        if bounds is None:
            return 400, "text/plain", b"bad range\n"
        start, end = bounds
        rows = [{"minute": iso(item.start), "frames": item.frames} for item in self.card.recordings(start, end)]
        return 200, "application/json", json.dumps(rows).encode()

    def _frames(self, query: dict) -> tuple[int, str, bytes]:
        bounds = self._bounds(query)
        if bounds is None:
            return 400, "text/plain", b"bad range\n"
        every = float(_one(query, "every_s", "1"))
        start, end = bounds
        rows = []
        for frame in self.card.sample(start, end, every):
            stamp = iso(frame.t)
            rows.append({"t": stamp, "url": "/api/v1/frame/" + stamp + ".jpg"})
        return 200, "application/json", json.dumps(rows).encode()

    def _one_frame(self, stamp: str) -> tuple[int, str, bytes]:
        try:
            when = parse_range(stamp, stamp, self.now, self.tz)[0]
        except ValueError:
            return 400, "text/plain", b"bad time\n"
        frame = self.card.nearest(when)
        if frame is None:
            return 404, "text/plain", b"no frame\n"
        return 200, "image/jpeg", frame.jpeg

    def _clip(self, query: dict, avi: bool) -> tuple[int, str, bytes]:
        bounds = self._bounds(query)
        if bounds is None:
            return 400, "text/plain", b"bad range\n"
        start, end = bounds
        frames = self.card.frames(start, end)
        if not avi:
            return 200, "multipart/x-mixed-replace", b"".join(frame.jpeg for frame in frames)
        if not frames:
            return 200, "video/x-msvideo", mjpeg_avi([], 5, 160, 120)
        return 200, "video/x-msvideo", mjpeg_avi([frame.jpeg for frame in frames], 5)

    def _index(self, query: dict) -> tuple[int, str, bytes]:
        bounds = self._bounds(query)
        if bounds is None:
            return 400, "text/plain", b"bad range\n"
        from host.witness_index.search import search_records

        start, end = bounds
        found = search_records(self.card.records(), start, end, _one(query, "q", ""), k=50)
        return 200, "application/json", json.dumps(found).encode()

    def _events(self, query: dict) -> tuple[int, str, bytes]:
        bounds = self._bounds(query)
        if bounds is None:
            return 400, "text/plain", b"bad range\n"
        start, end = bounds
        since = query.get("since_id", [None])[0]
        return 200, "application/json", json.dumps(self.card.events(start, end, since)).encode()


def _authorized(header: str | None, token: str) -> bool:
    if not header or not token:
        return False
    expected = f"Bearer {token}"
    return hmac.compare_digest(header, expected)


def _one(query: dict[str, list[str]], key: str, default: str | None = None) -> str:
    values = query.get(key)
    if not values:
        if default is None:
            raise KeyError(key)
        return default
    return values[0]
