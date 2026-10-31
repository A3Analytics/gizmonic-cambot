"""JEV System One adapter."""

from __future__ import annotations

import json

import pytest

from host.witness_models.errors import ModelCallError
from host.witness_models.http import HttpResponse
from host.witness_models.jev import JEV_IMAGES_HEADER, JEV_MODEL, JEV_URL, JevAdapter
from host.witness_models.questions import NoulQuestion

KEY = "jev-unit-test-secret"
JPEG = b"\xff\xd8\xff\xd9"
QUESTION = NoulQuestion("present", "Is there a red square in `photo`?", "red square", "yes")


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers, body):
        self.calls.append({"url": url, "headers": dict(headers), "body": body})
        status, payload = self.responses.pop(0)
        if isinstance(payload, str):
            payload = payload.encode()
        return HttpResponse(status, payload)


def test_jev_request_and_noul_parse():
    body = {"model": JEV_MODEL, "answers": {"present": {"type": "noul", "noul": 0.98}}}
    transport = FakeTransport([(200, json.dumps(body))])
    adapter = JevAdapter(KEY, transport=transport)
    scores = adapter.ask(JPEG, [QUESTION])
    assert scores["red square"] == 0.98
    sent = json.loads(transport.calls[0]["body"])
    assert sent["model"] == JEV_MODEL
    assert sent["state"]["photo"] == "img1"
    assert sent["assets"]["img1"]["format"] == "jpg"
    assert sent["assets"]["img1"]["data_base64"]
    assert sent["questions"]["present"]["type"] == "noul"
    assert transport.calls[0]["url"] == JEV_URL
    assert transport.calls[0]["headers"][JEV_IMAGES_HEADER] == "true"
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in transport.calls[0]["body"].decode()


def test_jev_batch_stores_a_score_per_frame():
    answers = {
        "f0__present": {"type": "noul", "noul": 0.91},
        "f1__present": {"type": "noul", "noul": 0.04},
    }
    transport = FakeTransport([(200, json.dumps({"answers": answers, "usage": {"total_tokens": 20}}))])
    adapter = JevAdapter(KEY, transport=transport)
    scored = adapter.ask_frames([("2026-10-31T19:00:00.000Z", JPEG), ("2026-10-31T19:00:03.000Z", JPEG)], [QUESTION])
    assert scored.scores == [{"red square": 0.91}, {"red square": 0.04}]
    sent = json.loads(transport.calls[0]["body"])
    assert sent["state"][0]["t"] == "2026-10-31T19:00:00.000Z"
    assert sent["state"][1]["photo"] == "img1"
    assert "f0__present" in sent["questions"]
    assert "`img1`" in sent["questions"]["f1__present"]["instructions"]


def test_jev_auth_and_dropped_json():
    adapter = JevAdapter(KEY, transport=FakeTransport([(403, b"{}")]))
    with pytest.raises(ModelCallError) as raised:
        adapter.ask(JPEG, [QUESTION])
    assert raised.value.status == "auth"

    bad = FakeTransport([(200, b"{"), (200, b'{"answers":{}}')])
    adapter = JevAdapter(KEY, transport=bad)
    with pytest.raises(ModelCallError) as raised:
        adapter.ask(JPEG, [QUESTION])
    assert raised.value.status == "dropped"
    assert len(bad.calls) == 2


def test_wire_constants_match_the_firmware_header():
    from pathlib import Path

    header = Path("firmware/components/llm/include/witness_llm.h").read_text(encoding="utf-8")
    assert JEV_URL in header
    assert JEV_MODEL in header
    assert JEV_IMAGES_HEADER in header
    assert "2023-06-01" in header
