#!/usr/bin/env python3
"""Two-minute recording check against a camera that is already running.

This does not flash the board. Host tests cover the same query on a fixture
card. Set WITNESS_CAMERA_URL (for example http://192.168.1.20) and
WITNESS_CAMERA_PASSWORD to run it. The password is not printed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    base = os.environ.get("WITNESS_CAMERA_URL", "").rstrip("/")
    token = os.environ.get("WITNESS_CAMERA_PASSWORD", "")
    if not base:
        print("record_check=skipped reason=WITNESS_CAMERA_URL_unset")
        return 0
    if not token:
        print("record_check=skipped reason=WITNESS_CAMERA_PASSWORD_unset")
        return 0
    request = urllib.request.Request(
        base + "/api/v1/recordings?from=-2m&to=-0s",
        headers={"Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            rows = json.loads(response.read().decode())
    except Exception as error:
        print(f"record_check=fail err={type(error).__name__}")
        return 1
    frames = sum(row.get("frames", 0) for row in rows)
    print(f"record_check=ok frames={frames}")
    return 0 if frames > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
