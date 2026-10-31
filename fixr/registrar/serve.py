#!/usr/bin/env python3
"""LAN registrar for a Witness camera.

The camera POSTs /api/register when it joins the network. This page lists
each camera and refreshes its live frame. The camera password is read from
WITNESS_CAMERA_PASSWORD and is never printed or stored.

  /opt/homebrew/bin/python3.13 fixr/registrar/serve.py
  /opt/homebrew/bin/python3.13 fixr/registrar/serve.py --check
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "run"
REGISTRY = RUN / "cameras.json"
PROFILE = ROOT / "profiles" / "waterbear.json"
HOST_SCHEMA = ROOT / "schema" / "host-profile.schema.json"
FRAME_PATH = "/api/v1/frame.jpg"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
PORT = 8765

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Witness registrar</title>
<style>
  body { font: 16px/1.4 ui-sans-serif, system-ui, sans-serif; margin: 2rem; color: #1c1917; background: #fafaf9; }
  h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
  p.note { color: #57534e; margin-top: 0; }
  .card { background: white; border: 1px solid #e7e5e4; border-radius: 8px; padding: 1rem; margin: 1rem 0; max-width: 52rem; }
  img { max-width: 100%; background: #e7e5e4; }
  .meta { color: #57534e; font-size: 0.9rem; }
  .err { color: #9f1239; }
</style>
</head>
<body>
<h1>Witness registrar</h1>
<p class="note" id="hint">Waiting for a camera to POST /api/register.</p>
<div id="list"></div>
<script>
async function load() {
  const res = await fetch("/api/cameras");
  const data = await res.json();
  const hint = document.getElementById("hint");
  const list = document.getElementById("list");
  if (!data.cameras.length) {
    hint.textContent = "Waiting for a camera to POST /api/register.";
    list.replaceChildren();
    return;
  }
  hint.textContent = data.cameras.length + " camera" + (data.cameras.length === 1 ? "" : "s") + " on the LAN.";
  const stamp = Date.now();
  list.replaceChildren(...data.cameras.map((cam) => {
    const card = document.createElement("section");
    card.className = "card";
    const title = document.createElement("h2");
    title.textContent = cam.id;
    const meta = document.createElement("p");
    meta.className = "meta";
    meta.textContent = cam.ip + " · last seen " + cam.last_seen + (cam.fw ? " · fw " + cam.fw : "");
    const img = document.createElement("img");
    img.alt = "Live view from " + cam.id;
    img.src = "/api/cameras/" + encodeURIComponent(cam.id) + "/frame.jpg?t=" + stamp;
    const err = document.createElement("p");
    err.className = "err";
    if (cam.last_view_error) err.textContent = cam.last_view_error;
    card.append(title, meta, img, err);
    return card;
  }));
}
load();
setInterval(load, 2000);
</script>
</body>
</html>
"""


def load_local_env() -> None:
    """Load fixr/run/secrets.env. Existing environment variables win. Values are never printed."""
    path = RUN / "secrets.env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lan_ip() -> str:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def acceptable_ip(value: str) -> bool:
    try:
        addr = ip_address(value)
    except ValueError:
        return False
    return addr.version == 4 and (addr.is_private or addr.is_loopback)


class Registry:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.cameras: dict[str, dict] = {}
        if path.exists():
            loaded = json.loads(path.read_text())
            self.cameras = {item["id"]: item for item in loaded.get("cameras", [])}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cameras": list(self.cameras.values())}
        self.path.write_text(json.dumps(payload, indent=2) + "\n")

    def upsert(self, camera_id: str, ip: str, fw: str | None, board: str | None) -> dict:
        with self._lock:
            current = self.cameras.get(camera_id, {})
            current.update(
                {
                    "id": camera_id,
                    "ip": ip,
                    "fw": fw or current.get("fw") or "",
                    "board": board or current.get("board") or "",
                    "last_seen": utc_now(),
                    "last_view_error": current.get("last_view_error", ""),
                }
            )
            self.cameras[camera_id] = current
            self.save()
            return dict(current)

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self.cameras.values()]

    def get(self, camera_id: str) -> dict | None:
        with self._lock:
            item = self.cameras.get(camera_id)
            return dict(item) if item else None

    def note_view(self, camera_id: str, error: str) -> None:
        with self._lock:
            item = self.cameras.get(camera_id)
            if not item:
                return
            item["last_view_error"] = error
            if not error:
                item["last_view_ok"] = utc_now()
            self.save()


def fetch_frame(ip: str) -> tuple[int, bytes, str]:
    password = os.environ.get("WITNESS_CAMERA_PASSWORD", "")
    if not password:
        return 503, b"WITNESS_CAMERA_PASSWORD is not set\n", "text/plain"
    url = f"http://{ip}{FRAME_PATH}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + password)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read(2_000_000)
            ctype = resp.headers.get("Content-Type", "image/jpeg")
            return 200, body, ctype
    except urllib.error.HTTPError as exc:
        return 502, f"camera returned HTTP {exc.code}\n".encode(), "text/plain"
    except Exception as exc:
        return 502, f"camera unreachable: {exc.__class__.__name__}\n".encode(), "text/plain"


def make_handler(registry: Registry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                return
            if path == "/api/cameras":
                self._json(200, {"cameras": registry.list()})
                return
            prefix = "/api/cameras/"
            suffix = "/frame.jpg"
            if path.startswith(prefix) and path.endswith(suffix):
                camera_id = path[len(prefix) : -len(suffix)]
                camera = registry.get(camera_id)
                if camera is None:
                    self._send(404, b"unknown camera\n", "text/plain")
                    return
                status, body, ctype = fetch_frame(camera["ip"])
                if status == 200:
                    registry.note_view(camera_id, "")
                else:
                    registry.note_view(camera_id, body.decode().strip())
                self._send(status, body, ctype)
                return
            self._send(404, b"not found\n", "text/plain")

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/register":
                self._send(404, b"not found\n", "text/plain")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                self._json(400, {"ok": False, "error": "body must be 1 to 4096 bytes"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "invalid json"})
                return
            if not isinstance(body, dict):
                self._json(400, {"ok": False, "error": "json object required"})
                return
            camera_id = body.get("id", "")
            if not isinstance(camera_id, str) or not ID_RE.match(camera_id):
                self._json(400, {"ok": False, "error": "invalid id"})
                return
            peer = self.client_address[0]
            if not acceptable_ip(peer):
                self._json(403, {"ok": False, "error": "peer address is not a local address"})
                return
            stated = body.get("ip")
            if stated is not None and stated != peer:
                self._json(400, {"ok": False, "error": "ip does not match the connection"})
                return
            fw = body.get("fw") or ""
            board = body.get("board") or ""
            if not isinstance(fw, str) or not isinstance(board, str):
                self._json(400, {"ok": False, "error": "fw and board must be strings"})
                return
            saved = registry.upsert(camera_id, peer, fw[:32], board[:64])
            self._json(200, {"ok": True, "id": saved["id"], "ip": saved["ip"]})

    return Handler


def serve(host: str, port: int, registry: Registry) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(registry))
    httpd.serve_forever()
    return httpd


def check_profile() -> None:
    profile = json.loads(PROFILE.read_text())
    schema = json.loads(HOST_SCHEMA.read_text())
    missing = [key for key in schema["required"] if key not in profile]
    if missing:
        raise SystemExit(f"profile missing {missing}")
    if profile["python"]["use"]["meets_guide"] is not True:
        raise SystemExit("profile python.use must meet the 3.10 guide")
    if profile["secrets"]["wifi_password_env"] != "WITNESS_WIFI_PASSWORD":
        raise SystemExit("wifi password must be an env var name")
    blob = json.dumps(profile).lower()
    for banned in ("password", "token", "api_key"):
        if f'"{banned}"' in blob:
            raise SystemExit(f"profile contains a secret field: {banned}")


def run_check() -> int:
    check_profile()
    registry = Registry(RUN / "cameras-check.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(registry))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/register",
            data=json.dumps({"id": "witness-test", "fw": "0.0.0", "board": "foriot_s3cam"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            registered = json.loads(resp.read())
        if registered.get("ip") != "127.0.0.1" or registered.get("ok") is not True:
            raise SystemExit(f"register failed: {registered}")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/cameras", timeout=3) as resp:
            listing = json.loads(resp.read())
        if listing["cameras"][0]["id"] != "witness-test":
            raise SystemExit("camera missing from list")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
            page = resp.read().decode()
        if "Witness registrar" not in page:
            raise SystemExit("page missing title")
        frame_url = f"http://127.0.0.1:{port}/api/cameras/witness-test/frame.jpg"
        try:
            urllib.request.urlopen(frame_url, timeout=3)
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503):
                raise SystemExit(f"unexpected frame status {exc.code}")
        else:
            raise SystemExit("frame fetch should fail without a camera")
    finally:
        httpd.shutdown()
        check_file = RUN / "cameras-check.json"
        if check_file.exists():
            check_file.unlink()
    print("registrar=ok profile=ok")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return run_check()
    load_local_env()
    registry = Registry(REGISTRY)
    host = "0.0.0.0"
    ip = lan_ip()
    password_state = "set" if os.environ.get("WITNESS_CAMERA_PASSWORD") else "missing"
    print(f"registrar=http://{ip}:{PORT}/", flush=True)
    print(f"register=POST http://{ip}:{PORT}/api/register", flush=True)
    print(f"camera_password={password_state}", flush=True)
    print(f"frame={FRAME_PATH}", flush=True)
    serve(host, PORT, registry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
