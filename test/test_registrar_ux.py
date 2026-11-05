"""Registrar UX contracts: indexing switches, stale cameras, remove."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fixr.registrar.serve import (
    DEV_IDLE_S,
    PAGE,
    POLL_MS_DEFAULT,
    POLL_MS_HIGH_PERF,
    DevState,
    HighPerfState,
    Registry,
    camera_is_stale,
    disable_all_indexing,
    idle_due,
    load_dev_mode,
    load_high_perf,
    make_handler,
    maybe_idle_stop,
    save_dev_mode,
    save_high_perf,
    set_cameras_perf,
)

PASSWORD = "ux-test-password"


class FakeCamera(BaseHTTPRequestHandler):
    indexing = False
    jev = False
    perf = False
    frame_delay_s = 0.0
    frame_hits = 0
    latest_seq = 0

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
        if self.path == "/api/v1/indexing":
            self._json(200, {"enabled": self.indexing})
            return
        if self.path == "/api/v1/jev":
            self._json(200, {"enabled": self.jev})
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
            if self.frame_delay_s:
                import time

                time.sleep(self.frame_delay_s)
            body = b"\xff\xd8\xff\xd9"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Witness-Preview", "cached")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/v1/index/latest":
            if not self.jev:
                self.send_response(204)
                self.end_headers()
                return
            type(self).latest_seq += 1
            seq = self.latest_seq
            when = datetime(2026, 11, 5, 6, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=2 * seq)
            self._json(
                200,
                {
                    "t": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "caption": "",
                    "model": "typesafe/jev-1.14.0",
                    "watch": {"pipette tip": 0.9 if seq % 2 else 0.1},
                },
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self._auth():
            self.send_error(401)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length) or b"{}")
        enabled = bool(body.get("enabled"))
        if self.path == "/api/v1/indexing":
            type(self).indexing = enabled
            self._json(200, {"enabled": self.indexing})
            return
        if self.path == "/api/v1/jev":
            type(self).jev = enabled
            self._json(200, {"enabled": self.jev})
            return
        if self.path == "/api/v1/perf":
            type(self).perf = enabled
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
def stack(tmp_path: Path):
    FakeCamera.indexing = False
    FakeCamera.jev = False
    FakeCamera.perf = False
    FakeCamera.frame_delay_s = 0.0
    FakeCamera.frame_hits = 0
    FakeCamera.latest_seq = 0
    camera = ThreadingHTTPServer(("127.0.0.1", 0), FakeCamera)
    camera_thread = threading.Thread(target=camera.serve_forever, daemon=True)
    camera_thread.start()
    camera_ip, camera_port = camera.server_address

    registry = Registry(tmp_path / "cameras.json")
    dev_state = DevState(path=tmp_path / "dev_mode.json")
    high_perf = HighPerfState(path=tmp_path / "high_perf.json")
    registrar = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(registry, dev_state, high_perf))
    registrar_thread = threading.Thread(target=registrar.serve_forever, daemon=True)
    registrar_thread.start()
    host, port = registrar.server_address
    os.environ["WITNESS_CAMERA_PASSWORD"] = PASSWORD
    registry.upsert("witness-live", f"{camera_ip}:{camera_port}", "setup", "foriot_s3cam")
    registry.upsert(
        "witness-dead",
        "127.0.0.1:9",
        "setup",
        "foriot_s3cam",
    )
    with registry._lock:
        registry.cameras["witness-dead"]["last_seen"] = "2000-01-01T00:00:00Z"
        registry.save()

    base = f"http://{host}:{port}"
    yield {
        "base": base,
        "registry": registry,
        "camera": FakeCamera,
        "dev_state": dev_state,
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


def test_page_has_switch_and_remove_hooks():
    assert 'id="indexing"' in PAGE
    assert 'id="jev"' in PAGE
    assert 'id="dev-mode"' in PAGE
    assert 'id="high-perf"' in PAGE
    assert 'id="switch-status"' in PAGE
    assert "removeCamera" in PAGE
    assert 'textContent = "Remove"' in PAGE
    assert "Turning GPT indexing on" in PAGE
    assert "Turning JEV indexing on" in PAGE
    assert "cameraStale" in PAGE
    assert "syncDevMode" in PAGE
    assert "syncHighPerf" in PAGE
    assert "armLoadTimer" in PAGE
    assert "SWITCH_SYNC_MS" in PAGE
    assert "/api/dev-idle" in PAGE
    assert "5 minutes idle" in PAGE
    assert str(POLL_MS_DEFAULT) in PAGE
    assert str(POLL_MS_HIGH_PERF) in PAGE


def test_camera_is_stale_helper():
    now = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)
    assert camera_is_stale({"last_seen": "2000-01-01T00:00:00Z"}, now=now) is True
    fresh = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert camera_is_stale({"last_seen": fresh}, now=now) is False
    assert camera_is_stale({}, now=now) is True


def test_indexing_and_jev_switches_round_trip(stack):
    base = stack["base"]
    status, body = _json(f"{base}/api/cameras/witness-live/indexing")
    assert status == 200 and body["enabled"] is False
    status, body = _json(
        f"{base}/api/cameras/witness-live/indexing",
        method="POST",
        payload={"enabled": True},
    )
    assert status == 200 and body["enabled"] is True
    assert stack["camera"].indexing is True

    status, body = _json(f"{base}/api/cameras/witness-live/jev")
    assert status == 200 and body["enabled"] is False
    status, body = _json(
        f"{base}/api/cameras/witness-live/jev",
        method="POST",
        payload={"enabled": True},
    )
    assert status == 200 and body["enabled"] is True
    assert stack["camera"].jev is True

    status, body = _json(
        f"{base}/api/cameras/witness-live/jev",
        method="POST",
        payload={"enabled": False},
    )
    assert status == 200 and body["enabled"] is False


def test_dead_camera_switch_returns_json_error(stack):
    base = stack["base"]
    req = urllib.request.Request(
        f"{base}/api/cameras/witness-dead/indexing",
        data=json.dumps({"enabled": True}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(req, timeout=5)
    err = raised.value
    assert err.code >= 400
    payload = json.loads(err.read().decode())
    assert "error" in payload
    assert payload.get("enabled") is False


def test_remove_camera_from_registry(stack):
    base = stack["base"]
    status, listing = _json(f"{base}/api/cameras")
    assert status == 200
    ids = {cam["id"] for cam in listing["cameras"]}
    assert "witness-dead" in ids
    status, body = _json(f"{base}/api/cameras/witness-dead", method="DELETE")
    assert status == 200 and body["ok"] is True
    status, listing = _json(f"{base}/api/cameras")
    ids = {cam["id"] for cam in listing["cameras"]}
    assert "witness-dead" not in ids
    assert "witness-live" in ids


def test_dev_mode_persist_and_idle_helpers(tmp_path: Path):
    path = tmp_path / "dev_mode.json"
    assert load_dev_mode(path) is False
    save_dev_mode(True, path)
    assert load_dev_mode(path) is True
    now = datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc)
    assert idle_due(None, now=now) is False
    recent = now - timedelta(seconds=30)
    assert idle_due(recent, now=now) is False
    old = now - timedelta(seconds=DEV_IDLE_S)
    assert idle_due(old, now=now) is True


def test_disable_all_indexing_posts_both_switches():
    seen = []

    def poster(ip, path, body):
        seen.append((ip, path, body))
        return 200, b'{"enabled":false}', "application/json"

    class FakeRegistry:
        def list(self):
            return [{"id": "a", "ip": "10.0.0.1"}, {"id": "b", "ip": "10.0.0.2"}]

    stopped = disable_all_indexing(FakeRegistry(), poster=poster)
    assert stopped == 2
    paths = {(ip, path) for ip, path, _ in seen}
    assert paths == {
        ("10.0.0.1", "/api/v1/indexing"),
        ("10.0.0.1", "/api/v1/jev"),
        ("10.0.0.2", "/api/v1/indexing"),
        ("10.0.0.2", "/api/v1/jev"),
    }


def test_dev_mode_api_and_idle_stop(stack):
    base = stack["base"]
    status, body = _json(f"{base}/api/dev-mode")
    assert status == 200
    assert body["enabled"] is False
    assert body["idle_s"] == DEV_IDLE_S

    status, body = _json(f"{base}/api/dev-mode", method="POST", payload={"enabled": True})
    assert status == 200 and body["enabled"] is True
    assert stack["dev_state"].enabled is True
    assert load_dev_mode(stack["tmp_path"] / "dev_mode.json") is True

    status, body = _json(
        f"{base}/api/cameras/witness-live/indexing",
        method="POST",
        payload={"enabled": True},
    )
    assert status == 200 and body["enabled"] is True
    status, body = _json(
        f"{base}/api/cameras/witness-live/jev",
        method="POST",
        payload={"enabled": True},
    )
    assert status == 200 and body["enabled"] is True
    assert stack["camera"].indexing is True
    assert stack["camera"].jev is True

    status, body = _json(f"{base}/api/activity", method="POST", payload={})
    assert status == 200 and body["ok"] is True

    status, body = _json(f"{base}/api/dev-idle", method="POST", payload={})
    assert status == 200 and body["ok"] is True
    assert stack["camera"].indexing is False
    assert stack["camera"].jev is False

    # Idle watchdog path: mark activity old, then maybe_idle_stop turns cameras off again.
    stack["camera"].indexing = True
    stack["camera"].jev = True
    stack["dev_state"].touch(datetime.now(timezone.utc) - timedelta(seconds=DEV_IDLE_S + 1))
    assert maybe_idle_stop(stack["registry"], stack["dev_state"]) is True
    assert stack["camera"].indexing is False
    assert stack["camera"].jev is False


def test_high_perf_api_and_camera_push(stack):
    base = stack["base"]
    status, body = _json(f"{base}/api/high-perf")
    assert status == 200
    assert body["enabled"] is False
    assert body["poll_ms"] == POLL_MS_DEFAULT
    assert stack["camera"].perf is False

    status, body = _json(f"{base}/api/high-perf", method="POST", payload={"enabled": True})
    assert status == 200 and body["enabled"] is True
    assert body["poll_ms"] == POLL_MS_HIGH_PERF
    assert body["cameras_updated"] >= 1
    assert stack["high_perf"].enabled is True
    assert load_high_perf(stack["tmp_path"] / "high_perf.json") is True
    assert stack["camera"].perf is True

    status, body = _json(f"{base}/api/cameras/witness-live/perf")
    assert status == 200 and body["enabled"] is True and body["capture_ms"] == 200

    status, body = _json(f"{base}/api/high-perf", method="POST", payload={"enabled": False})
    assert status == 200 and body["enabled"] is False
    assert body["poll_ms"] == POLL_MS_DEFAULT
    assert stack["camera"].perf is False


def test_high_perf_persist_helper(tmp_path: Path):
    path = tmp_path / "high_perf.json"
    assert load_high_perf(path) is False
    save_high_perf(True, path)
    assert load_high_perf(path) is True


def test_set_cameras_perf_posts_enabled():
    seen = []

    def poster(ip, path, body):
        seen.append((ip, path, body))
        return 200, b'{"enabled":true}', "application/json"

    class FakeRegistry:
        def list(self):
            return [{"id": "a", "ip": "10.0.0.1"}]

    assert set_cameras_perf(FakeRegistry(), True, poster=poster) == 1
    assert seen == [("10.0.0.1", "/api/v1/perf", {"enabled": True})]


def test_caption_proxy_passes_every_new_jev_score(stack):
    """Each new on-camera record must reach the page; none cached or repeated."""
    base = stack["base"]
    req = urllib.request.Request(f"{base}/api/cameras/witness-live/caption")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 204

    status, body = _json(f"{base}/api/cameras/witness-live/jev", method="POST", payload={"enabled": True})
    assert status == 200 and body["enabled"] is True
    seen = []
    for _ in range(10):
        status, row = _json(f"{base}/api/cameras/witness-live/caption")
        assert status == 200
        seen.append((row["t"], row["watch"]["pipette tip"]))
    stamps = [t for t, _ in seen]
    assert len(set(stamps)) == 10 and stamps == sorted(stamps)
    assert {score >= 0.5 for _, score in seen} == {True, False}


def test_page_renders_each_new_watch_time():
    """The page polls the caption on every load and redraws when t changes."""
    assert "updateCaption(card, cam.id)" in PAGE or "updateCaption(card, camera)" in PAGE
    assert "row.t === card.lastWatchT" in PAGE
    assert "renderWatch(card, row)" in PAGE
