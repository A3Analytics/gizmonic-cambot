"""Anthropic Messages adapter."""

from __future__ import annotations

import json

import pytest

from host.witness_models.anthropic import ANTHROPIC_VERSION, AnthropicAdapter
from host.witness_models.errors import ModelCallError
from host.witness_models.http import HttpResponse

KEY = "sk-ant-unit-test"
JPEG = b"\xff\xd8\xff\xd9"


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


def test_claude_body_image_source_and_errors():
    transport = FakeTransport([(200, _message("a white field"))])
    adapter = AnthropicAdapter(KEY, transport=transport)
    result = adapter.complete_text(JPEG, "describe")
    assert result.text == "a white field"
    assert result.model == "anthropic/claude-sonnet-4-5"
    sent = json.loads(transport.calls[0]["body"])
    image = sent["messages"][0]["content"][0]
    assert image["type"] == "image"
    assert image["source"]["type"] == "base64"
    assert image["source"]["media_type"] == "image/jpeg"
    assert image["source"]["data"]
    assert sent["messages"][0]["content"][1]["text"] == "describe"
    assert transport.calls[0]["headers"]["x-api-key"] == KEY
    assert transport.calls[0]["headers"]["anthropic-version"] == ANTHROPIC_VERSION
    assert KEY not in transport.calls[0]["body"].decode()

    denied = AnthropicAdapter(KEY, transport=FakeTransport([(401, b"{}")]))
    with pytest.raises(ModelCallError) as raised:
        denied.complete_text(JPEG, "hi")
    assert raised.value.status == "auth"

    limited = AnthropicAdapter(KEY, transport=FakeTransport([(429, b"slow")]))
    with pytest.raises(ModelCallError) as raised:
        limited.complete_text(JPEG, "hi")
    assert raised.value.status == "rate_limited"


def test_claude_caption_drops_after_two_bad_bodies():
    transport = FakeTransport([(200, _message("still not json")), (200, _message("{"))])
    adapter = AnthropicAdapter(KEY, transport=transport)
    with pytest.raises(ModelCallError) as raised:
        adapter.caption_json(JPEG)
    assert raised.value.status == "dropped"
    assert len(transport.calls) == 2


def _message(text: str) -> str:
    return json.dumps({"content": [{"type": "text", "text": text}], "usage": {"input_tokens": 3, "output_tokens": 4}})
