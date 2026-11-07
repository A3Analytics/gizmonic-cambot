"""Offline contracts for high-performance preview mode."""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fixr.registrar.serve import (
    POLL_MS_DEFAULT,
    POLL_MS_HIGH_PERF,
    HighPerfState,
    Registry,
    load_high_perf,
    make_handler,
)

PASSWORD = "perf-test-password"


class FakeCamera(BaseHTTPRequestHandler):
    perf = False
    frame_hits = 0

    def log_message(self, fmt: str, *args) -> None:
        return

    def _auth(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {PASSWORD}"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._auth():
            self.send_error(401)
            return
        if self.path == "/api/v1/perf":
            self._json(
                200,
                {
                    "enabled": self.perf,
                    "capture_ms": 200,
                    "fflush_every": 5 if self.perf else 1,
                    "preview_cached": True,
                },
            )
            return
        if self.path == "/api/v1/frame.jpg":
            type(self).frame_hits += 1
            body = b"\xff\xd8\xff\xd9"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Cambot-Preview", "cached")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self._auth():
            self.send_error(401)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/v1/perf":
            type(self).perf = bool(body.get("enabled"))
            self._json(
                200,
                {
                    "enabled": self.perf,
                    "capture_ms": 200,
                    "fflush_every": 5 if self.perf else 1,
                    "preview_cached": True,
                },
            )
            return
        self.send_error(404)


@pytest.fixture()
def perf_stack(tmp_path: Path):
    FakeCamera.perf = False
    FakeCamera.frame_hits = 0
    camera = ThreadingHTTPServer(("127.0.0.1", 0), FakeCamera)
    threading.Thread(target=camera.serve_forever, daemon=True).start()
    camera_ip, camera_port = camera.server_address

    registry = Registry(tmp_path / "cameras.json")
    high_perf = HighPerfState(path=tmp_path / "high_perf.json")
    registrar = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(registry, high_perf_state=high_perf))
    threading.Thread(target=registrar.serve_forever, daemon=True).start()
    host, port = registrar.server_address
    os.environ["CAMBOT_CAMERA_PASSWORD"] = PASSWORD
    registry.upsert("cambot-live", f"{camera_ip}:{camera_port}", "setup", "foriot_s3cam")
    yield {
        "base": f"http://{host}:{port}",
        "camera": FakeCamera,
        "high_perf": high_perf,
        "tmp_path": tmp_path,
    }
    registrar.shutdown()
    camera.shutdown()


def _json(url: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read()
        return resp.status, json.loads(body) if body else {}


def test_poll_constants():
    assert POLL_MS_DEFAULT == 2000
    assert POLL_MS_HIGH_PERF == 500
    assert POLL_MS_HIGH_PERF * 4 == POLL_MS_DEFAULT


def test_cached_frame_proxy_is_fast(perf_stack):
    base = perf_stack["base"]
    latencies = []
    for _ in range(8):
        started = time.perf_counter()
        req = urllib.request.Request(f"{base}/api/cameras/cambot-live/frame.jpg")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            assert resp.status == 200
            assert body[:2] == b"\xff\xd8"
        latencies.append(time.perf_counter() - started)
    assert statistics.median(latencies) < 0.1
    assert perf_stack["camera"].frame_hits >= 8


def test_perf_round_trip_restores_defaults(perf_stack):
    base = perf_stack["base"]
    status, body = _json(f"{base}/api/cameras/cambot-live/perf", method="POST", payload={"enabled": True})
    assert status == 200
    assert body["enabled"] is True
    assert body["capture_ms"] == 200
    assert body["fflush_every"] == 5
    assert perf_stack["camera"].perf is True

    status, body = _json(f"{base}/api/cameras/cambot-live/perf", method="POST", payload={"enabled": False})
    assert status == 200
    assert body["enabled"] is False
    assert body["capture_ms"] == 200
    assert perf_stack["camera"].perf is False


def test_high_perf_absent_file_defaults_off(tmp_path: Path):
    path = tmp_path / "missing-high-perf.json"
    assert load_high_perf(path) is False
    assert HighPerfState(path=path).enabled is False
