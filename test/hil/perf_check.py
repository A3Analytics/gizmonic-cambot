#!/usr/bin/env python3
"""Live A/B check for high-performance preview mode.

Does not flash the board. Set CAMBOT_CAMERA_URL and CAMBOT_CAMERA_PASSWORD.
Never prints the password.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request


def _request(base: str, token: str, path: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + token},
    )
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=8) as resp:
        body = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        return resp.status, body, ctype


def _frame_latencies(base: str, token: str, n: int = 20) -> list[float]:
    samples = []
    for _ in range(n):
        started = time.perf_counter()
        status, body, _ = _request(base, token, "/api/v1/frame.jpg")
        elapsed = time.perf_counter() - started
        if status != 200 or len(body) < 2 or body[:2] != b"\xff\xd8":
            raise RuntimeError(f"frame.jpg bad status={status} bytes={len(body)}")
        samples.append(elapsed)
    return samples


def main() -> int:
    base = os.environ.get("CAMBOT_CAMERA_URL", "").rstrip("/")
    token = os.environ.get("CAMBOT_CAMERA_PASSWORD", "")
    if not base:
        print("perf_check=skipped reason=CAMBOT_CAMERA_URL_unset")
        return 0
    if not token:
        print("perf_check=skipped reason=CAMBOT_CAMERA_PASSWORD_unset")
        return 0

    try:
        status, body, _ = _request(base, token, "/api/v1/frame.jpg")
        if status != 200 or len(body) < 2:
            print(f"perf_check=fail step=install_smoke status={status} bytes={len(body)}")
            return 1
        print(f"perf_check=ok step=install_smoke bytes={len(body)}")

        _request(base, token, "/api/v1/perf", method="POST", payload={"enabled": False})
        time.sleep(0.3)
        off = _frame_latencies(base, token, 20)
        off_p50 = statistics.median(off)
        print(f"perf_check=ok step=latency_off p50_ms={off_p50 * 1000:.1f}")

        status, raw, _ = _request(base, token, "/api/v1/perf", method="POST", payload={"enabled": True})
        if status != 200:
            print(f"perf_check=fail step=enable status={status}")
            return 1
        enabled = json.loads(raw.decode())
        if not enabled.get("enabled"):
            print("perf_check=fail step=enable body=false")
            return 1
        time.sleep(0.5)
        on = _frame_latencies(base, token, 20)
        on_p50 = statistics.median(on)
        print(f"perf_check=ok step=latency_on p50_ms={on_p50 * 1000:.1f}")

        # Cached preview should stay snappy. High perf must not stall the frame API.
        if on_p50 > 0.5:
            print(f"perf_check=fail step=budget on_p50_ms={on_p50 * 1000:.1f}")
            return 1
        if on_p50 > off_p50 * 1.5 and on_p50 > 0.25:
            print(
                f"perf_check=fail step=regression off_p50_ms={off_p50 * 1000:.1f} "
                f"on_p50_ms={on_p50 * 1000:.1f}"
            )
            return 1

        # Counts are bucketed by minute, so a 30s window can miss the current bucket.
        status, raw, _ = _request(base, token, "/api/v1/recordings?from=-2m&to=-0s")
        if status != 200:
            print(f"perf_check=fail step=recordings status={status}")
            return 1
        rows = json.loads(raw.decode())
        frames = sum(row.get("frames", 0) for row in rows)
        print(f"perf_check=ok step=recording frames={frames}")
        if frames <= 0:
            print("perf_check=fail step=recording starved")
            return 1

        _request(base, token, "/api/v1/perf", method="POST", payload={"enabled": False})
        status, raw, _ = _request(base, token, "/api/v1/perf")
        restored = json.loads(raw.decode())
        if restored.get("enabled") or restored.get("capture_ms") != 200 or restored.get("fflush_every") != 1:
            print(f"perf_check=fail step=restore body={restored}")
            return 1
        print("perf_check=ok step=restore")
        print(
            f"perf_check=ok off_p50_ms={off_p50 * 1000:.1f} on_p50_ms={on_p50 * 1000:.1f} "
            f"frames={frames}"
        )
        return 0
    except urllib.error.HTTPError as exc:
        print(f"perf_check=fail err=HTTPError status={exc.code}")
        return 1
    except Exception as exc:
        print(f"perf_check=fail err={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
