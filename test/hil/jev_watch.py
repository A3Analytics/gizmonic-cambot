#!/usr/bin/env python3
"""Watch how often JEV scores actually reach /api/v1/index/latest.

Turns JEV indexing on, polls the camera's latest index record, optionally
loads the camera like the registrar page does, optionally captures the serial
log, and always turns JEV (and GPT) indexing off at the end so it does not
burn tokens. Does not flash the board. Never prints the camera password.

    python test/hil/jev_watch.py --seconds 90 --ui-load --serial /dev/cu.usbserial-210

Exit code 1 when the longest gap between JEV updates exceeds --max-gap.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "fixr" / "run"
SECRET_WORDS = ("password", "bearer", "api_key", "apikey", "sk-")


def load_env_file() -> None:
    path = RUN / "secrets.env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def camera_url() -> str:
    url = os.environ.get("CAMBOT_CAMERA_URL", "").rstrip("/")
    if url:
        return url
    registry = RUN / "cameras.json"
    if registry.exists():
        cameras = json.loads(registry.read_text())
        if isinstance(cameras, dict) and "cameras" in cameras:
            cameras = cameras["cameras"]
        rows = cameras.values() if isinstance(cameras, dict) else cameras
        for cam in rows:
            if isinstance(cam, dict) and cam.get("ip"):
                return "http://" + cam["ip"]
    return ""


class Camera:
    def __init__(self, base: str, token: str):
        self.base = base
        self.token = token

    def call(self, path: str, method: str = "GET", payload: dict | None = None, timeout: float = 8.0):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Authorization": "Bearer " + self.token}
        )
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()

    def switch(self, path: str, enabled: bool) -> bool | None:
        try:
            _, body = self.call(path, "POST", {"enabled": enabled}, timeout=8)
            return bool(json.loads(body.decode()).get("enabled"))
        except Exception:
            return None


def parse_t(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def serial_reader(port: str, stop: threading.Event, lines: list, log_path: Path) -> None:
    try:
        import serial
    except ImportError:
        lines.append((time.time(), "serial=skipped reason=pyserial_missing"))
        return
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 0.2
    ser.dtr = False
    ser.rts = False
    try:
        ser.open()
    except Exception as exc:
        lines.append((time.time(), f"serial=fail err={type(exc).__name__}"))
        return
    buf = b""
    with log_path.open("w") as log:
        while not stop.is_set():
            chunk = ser.read(4096)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").rstrip()
                if any(word in text.lower() for word in SECRET_WORDS):
                    text = "[redacted]"
                now = time.time()
                lines.append((now, text))
                log.write(f"{now:.3f} {text}\n")
                log.flush()
    ser.close()


def page_get(url: str, timeout: float = 8.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read()


def ui_load(cam: Camera, stop: threading.Event, stats: dict, interval: float) -> None:
    while not stop.is_set():
        started = time.time()
        try:
            cam.call("/api/v1/frame.jpg", timeout=8)
            stats["frames"].append(time.time() - started)
        except Exception:
            stats["frame_errors"] += 1
        wait = interval - (time.time() - started)
        if wait > 0:
            stop.wait(wait)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--poll", type=float, default=0.25)
    parser.add_argument("--max-gap", type=float, default=4.0)
    parser.add_argument("--ui-load", action="store_true", help="also fetch frame.jpg like the page does")
    parser.add_argument("--ui-interval", type=float, default=0.5)
    parser.add_argument("--serial", default="", help="serial port to capture, e.g. /dev/cu.usbserial-210")
    parser.add_argument("--keep-on", action="store_true", help="leave JEV on afterwards")
    parser.add_argument("--wait", type=float, default=0, help="seconds to wait for the camera to answer first")
    parser.add_argument(
        "--registrar",
        default="",
        help="registrar base URL; poll like the page does (cameras, frame, caption) every --poll seconds",
    )
    args = parser.parse_args()

    load_env_file()
    base = camera_url()
    token = os.environ.get("CAMBOT_CAMERA_PASSWORD", "")
    if not base or not token:
        print("jev_watch=skipped reason=camera_url_or_password_unset")
        return 0
    cam = Camera(base, token)
    deadline = time.time() + args.wait
    while True:
        try:
            cam.call("/api/v1/jev", timeout=3)
            break
        except Exception:
            if time.time() >= deadline:
                print("jev_watch=fail step=reach_camera")
                return 1
            time.sleep(2)
    stop = threading.Event()
    serial_lines: list = []
    ui_stats = {"frames": [], "frame_errors": 0}
    threads = []
    if args.serial:
        log_path = RUN / "jev_watch_serial.log"
        threads.append(threading.Thread(target=serial_reader, args=(args.serial, stop, serial_lines, log_path), daemon=True))
    if args.ui_load:
        threads.append(threading.Thread(target=ui_load, args=(cam, stop, ui_stats, args.ui_interval), daemon=True))
    for thread in threads:
        thread.start()

    registrar = args.registrar.rstrip("/")
    camera_id = ""
    if registrar:
        _, body = page_get(registrar + "/api/cameras")
        ip = base.split("://", 1)[-1]
        for row in json.loads(body.decode()).get("cameras", []):
            if row.get("ip") == ip:
                camera_id = row["id"]
        if not camera_id:
            print("jev_watch=fail step=camera_not_registered")
            stop.set()
            return 1
        args.ui_load = True

    updates: list[tuple[float, float | None, dict]] = []
    poll_times: list[float] = []
    poll_errors = 0
    last_t = None
    started = time.time()
    try:
        cam.switch("/api/v1/indexing", False)
        if cam.switch("/api/v1/jev", True) is not True:
            print("jev_watch=fail step=enable")
            return 1
        print(
            f"jev_watch=on camera={base} seconds={args.seconds:.0f} ui_load={args.ui_load} "
            f"registrar={registrar or 'no'}"
        )
        while time.time() - started < args.seconds:
            t0 = time.time()
            try:
                if registrar:
                    page_get(registrar + "/api/cameras")
                    f0 = time.time()
                    try:
                        page_get(f"{registrar}/api/cameras/{camera_id}/frame.jpg?t={int(f0 * 1000)}")
                        ui_stats["frames"].append(time.time() - f0)
                    except Exception:
                        ui_stats["frame_errors"] += 1
                    c0 = time.time()
                    status, body = page_get(f"{registrar}/api/cameras/{camera_id}/caption")
                    poll_times.append(time.time() - c0)
                else:
                    status, body = cam.call("/api/v1/index/latest", timeout=8)
                    poll_times.append(time.time() - t0)
                if status == 200 and body:
                    row = json.loads(body.decode())
                    t = row.get("t")
                    if t and t != last_t:
                        last_t = t
                        updates.append((time.time(), parse_t(t), row.get("watch") or {}))
                        scores = " ".join(f"{k}={v:.2f}" for k, v in (row.get("watch") or {}).items())
                        print(f"update +{time.time() - started:6.1f}s t={t} {scores}", flush=True)
            except Exception as exc:
                poll_errors += 1
                poll_times.append(time.time() - t0)
                print(f"poll_error +{time.time() - started:6.1f}s err={type(exc).__name__}", flush=True)
            wait = args.poll - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
    finally:
        jev_off = cam.switch("/api/v1/jev", False)
        if args.keep_on and jev_off is not None:
            cam.switch("/api/v1/jev", True)
        else:
            cam.switch("/api/v1/indexing", False)
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
        print(f"jev_watch=off jev={'on' if args.keep_on else 'off'}")

    elapsed = time.time() - started
    arrivals = [a for a, _, _ in updates]
    # The first score needs a keyframe plus a TLS handshake; report it apart.
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    if arrivals:
        print(f"first_update_s={arrivals[0] - started:.1f}")
        gaps.append(started + elapsed - arrivals[-1])
    else:
        gaps = [elapsed]
    lags = [a - t for a, t, _ in updates if t is not None]
    print(f"updates={len(updates)} seconds={elapsed:.0f} rate_per_min={len(updates) * 60 / elapsed:.1f}")
    print(
        f"gap_s median={statistics.median(gaps):.1f} max={max(gaps):.1f} "
        f"over_{args.max_gap:.0f}s={sum(1 for g in gaps if g > args.max_gap)}"
    )
    if lags:
        print(f"record_to_ui_s median={statistics.median(lags):.1f} max={max(lags):.1f}")
    if poll_times:
        print(
            f"latest_poll_ms median={statistics.median(poll_times) * 1000:.0f} "
            f"max={max(poll_times) * 1000:.0f} errors={poll_errors}"
        )
    if args.ui_load:
        frames = ui_stats["frames"]
        if frames:
            print(
                f"frame_ms median={statistics.median(frames) * 1000:.0f} max={max(frames) * 1000:.0f} "
                f"n={len(frames)} errors={ui_stats['frame_errors']}"
            )
        else:
            print(f"frame_ms n=0 errors={ui_stats['frame_errors']}")
    if args.serial:
        texts = [text for _, text in serial_lines]
        for key in ("index=ok", "index=dropped", "index=paused", "jev=fail", "model_http", "alert=", "Guru", "abort"):
            print(f"serial {key} count={sum(1 for text in texts if key in text)}")
    return 1 if max(gaps) > args.max_gap else 0


if __name__ == "__main__":
    sys.exit(main())
