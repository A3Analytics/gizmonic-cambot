"""Anthropic Messages adapter. Claude is not an OpenAI-compatible API."""

from __future__ import annotations

import base64

from host.cambot_models.errors import InvalidJson
from host.cambot_models.http import Transport, UrllibTransport, install_redactor, post_json
from host.cambot_models.jsonutil import CAPTION_PROMPT
from host.cambot_models.openai import TextResult, _json_with_retry
from host.cambot_models.questions import DESCRIBE_PROMPT

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        base_url: str = ANTHROPIC_URL,
        transport: Transport | None = None,
        max_tokens: int = 1024,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.transport = transport or UrllibTransport()
        self.max_tokens = max_tokens
        install_redactor(api_key)

    def build_body(self, prompt: str, image_jpeg: bytes) -> dict:
        encoded = base64.standard_b64encode(image_jpeg).decode("ascii")
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def complete_text(self, image_jpeg: bytes, prompt: str) -> TextResult:
        payload, tokens = post_json(
            self.transport,
            self.base_url,
            self.headers(),
            self.build_body(prompt, image_jpeg),
        )
        return TextResult(text=_anthropic_text(payload), model=f"anthropic/{self.model}", tokens=tokens)

    def describe(self, image_jpeg: bytes) -> str:
        return self.complete_text(image_jpeg, DESCRIBE_PROMPT).text

    def caption_json(self, image_jpeg: bytes, prompt: str = CAPTION_PROMPT) -> tuple[dict, int]:
        return _json_with_retry(lambda: self.complete_text(image_jpeg, prompt))


def _anthropic_text(payload: dict) -> str:
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        raise InvalidJson("anthropic")
    texts = [block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
    text = "".join(texts)
    if not text.strip():
        raise InvalidJson("anthropic")
    return text
