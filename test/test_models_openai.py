"""OpenAI adapter: request shape, response parse, and error mapping."""

from __future__ import annotations

import json
import logging

import pytest

from host.witness_models.errors import ModelCallError
from host.witness_models.http import HttpResponse
from host.witness_models.openai import OpenAIAdapter

KEY = "sk-unit-test-secret"
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


def _adapter(responses, **kwargs):
    transport = FakeTransport(responses)
    adapter = OpenAIAdapter(KEY, transport=transport, **kwargs)
    return adapter, transport


def test_openai_body_has_image_and_model():
    adapter, transport = _adapter([(200, _chat("a red square"))])
    adapter.complete_text(JPEG, 'say "hi"')
    sent = json.loads(transport.calls[0]["body"])
    assert sent["model"] == "gpt-4.1-mini"
    parts = sent["messages"][0]["content"]
    assert parts[0]["text"] == 'say "hi"'
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert KEY not in transport.calls[0]["body"].decode()
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"


def test_openai_parses_text(caplog):
    caplog.set_level(logging.INFO, logger="witness.models")
    adapter, _transport = _adapter([(200, _chat("a red square"))])
    result = adapter.complete_text(JPEG, "what do you see")
    assert result.text == "a red square"
    assert result.status == "ok"
    assert result.model == "openai/gpt-4.1-mini"
    assert KEY not in caplog.text


def test_openai_auth_and_rate_limit_do_not_retry():
    adapter, transport = _adapter([(401, b"{}")])
    with pytest.raises(ModelCallError) as raised:
        adapter.complete_text(JPEG, "hi")
    assert raised.value.status == "auth"
    assert len(transport.calls) == 1

    adapter, transport = _adapter([(429, b"{}")])
    with pytest.raises(ModelCallError) as raised:
        adapter.complete_text(JPEG, "hi")
    assert raised.value.status == "rate_limited"


def test_openai_no_vision():
    adapter, _transport = _adapter([(400, b'{"error":"image input is not supported"}')])
    with pytest.raises(ModelCallError) as raised:
        adapter.complete_text(JPEG, "hi")
    assert raised.value.status == "no_vision"


def test_openai_caption_retries_invalid_json_once_then_drops():
    bad = _chat("not json")
    adapter, transport = _adapter([(200, bad), (200, bad)])
    with pytest.raises(ModelCallError) as raised:
        adapter.caption_json(JPEG)
    assert raised.value.status == "dropped"
    assert len(transport.calls) == 2


def test_openai_caption_accepts_schema_on_second_try():
    good = {
        "caption": "red square on white",
        "objects": ["square"],
        "actions": ["resting"],
        "robot_state": "idle",
    }
    adapter, transport = _adapter([(200, _chat("nope")), (200, _chat(json.dumps(good)))])
    parsed, _tokens = adapter.caption_json(JPEG)
    assert parsed["caption"] == "red square on white"
    assert len(transport.calls) == 2


def test_tls_maps_without_logging_the_key(caplog):
    caplog.set_level(logging.INFO, logger="witness.models")

    class TlsTransport:
        def post(self, url, headers, body):
            from host.witness_models.errors import TlsFailure

            raise TlsFailure("handshake")

    adapter = OpenAIAdapter(KEY, transport=TlsTransport())
    with pytest.raises(ModelCallError) as raised:
        adapter.complete_text(JPEG, "hi")
    assert raised.value.status == "tls"
    assert KEY not in caplog.text


def _chat(text: str) -> str:
    return json.dumps({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 12}})
