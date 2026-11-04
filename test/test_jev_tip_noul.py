"""JEV must separate tip-on vs tip-off frames for the pipette noul.

Uses the recorded yes/no fixtures (same framing as the registrar demo).
Skipped when WITNESS_JEV_KEY is unset. Marked live because it calls TypeSafe.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from host.witness_models.jev import JEV_IMAGES_HEADER, JEV_MODEL, JEV_URL

pytestmark = pytest.mark.live

FIXTURES = Path(__file__).resolve().parent / "fixtures"
YES = FIXTURES / "pipette_tip_yes.jpg"
NO = FIXTURES / "pipette_tip_no.jpg"
def _asset(jpeg: bytes) -> dict:
    return {"type": "image", "format": "jpg", "data_base64": base64.standard_b64encode(jpeg).decode("ascii")}


def _score(photo: bytes, yes: bytes, no: bytes, api_key: str) -> float:
    # Match TypeSafe's documented noul criteria examples, not free-text "looks like".
    body = {
        "model": JEV_MODEL,
        "state": {"photo": "img1"},
        "assets": {
            "img1": _asset(photo),
            "yes_ex": _asset(yes),
            "no_ex": _asset(no),
        },
        "questions": {
            "tip_present": {
                "type": "noul",
                "instructions": "Is there a blue disposable pipette tip attached to a pipette in `photo`?",
                "criteria": {
                    "true": {"description": "A tip is attached.", "examples": ["yes_ex"]},
                    "false": {"description": "No tip is attached.", "examples": ["no_ex"]},
                },
            },
        },
    }
    request = urllib.request.Request(
        JEV_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            JEV_IMAGES_HEADER: "true",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise AssertionError(f"TypeSafe returned {exc.code}: {detail[:300]}") from None
    answer = payload["answers"]["tip_present"]["noul"]
    assert isinstance(answer, (int, float))
    return float(answer)


def test_jev_scores_tip_yes_higher_than_tip_no():
    api_key = os.environ.get("WITNESS_JEV_KEY", "").strip()
    if not api_key:
        key_file = Path("jev_api_key.txt")
        if key_file.exists():
            api_key = key_file.read_text(encoding="utf-8").strip()
    if not api_key:
        pytest.skip("WITNESS_JEV_KEY is not set")
    assert YES.is_file() and NO.is_file()
    yes_jpeg = YES.read_bytes()
    no_jpeg = NO.read_bytes()
    yes_score = _score(yes_jpeg, yes_jpeg, no_jpeg, api_key)
    no_score = _score(no_jpeg, yes_jpeg, no_jpeg, api_key)
    # JEV is calibrated, not deterministic; require a clear tip-on vs tip-off gap.
    assert yes_score >= 0.55, f"tip-on scored {yes_score}"
    assert no_score <= 0.25, f"tip-off scored {no_score}"
    assert yes_score - no_score >= 0.35, f"gap too small: yes={yes_score} no={no_score}"
