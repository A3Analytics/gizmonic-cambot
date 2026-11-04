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
    DevState,
    Registry,
    camera_is_stale,
    disable_all_indexing,
    idle_due,
    load_dev_mode,
    make_handler,
    maybe_idle_stop,
    save_dev_mode,
)

PASSWORD = "ux-test-password"


class FakeCamera(BaseHTTPRequestHandler):
    indexing = False
    jev = False

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
        if self.path == "/api/v1/frame.jpg":
            body = b"\xff\xd8\xff\xd9"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
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
        enabled = bool(body.get("enabled"))
        if self.path == "/api/v1/indexing":
            type(self).indexing = enabled
            self._json(200, {"enabled": self.indexing})
            return
        if self.path == "/api/v1/jev":
            type(self).jev = enabled
            self._json(200, {"enabled": self.jev})
            return
        self.send_error(404)


@pytest.fixture()
def stack(tmp_path: Path):
    FakeCamera.indexing = False
    FakeCamera.jev = False
    camera = ThreadingHTTPServer(("127.0.0.1", 0), FakeCamera)
    camera_thread = threading.Thread(target=camera.serve_forever, daemon=True)
    camera_thread.start()
    camera_ip, camera_port = camera.server_address

    registry = Registry(tmp_path / "cameras.json")
    dev_state = DevState(path=tmp_path / "dev_mode.json")
    registrar = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(registry, dev_state))
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
    assert 'id="switch-status"' in PAGE
    assert "removeCamera" in PAGE
    assert 'textContent = "Remove"' in PAGE
    assert "Turning GPT indexing on" in PAGE
    assert "Turning JEV indexing on" in PAGE
    assert "cameraStale" in PAGE
    assert "syncDevMode" in PAGE
    assert "/api/dev-idle" in PAGE
    assert "5 minutes idle" in PAGE


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
