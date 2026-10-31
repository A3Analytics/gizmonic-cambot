"""OpenAI-compatible Chat Completions adapter. Images are base64 data URLs."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from host.witness_models.errors import InvalidJson, ModelCallError
from host.witness_models.http import Transport, UrllibTransport, install_redactor, post_json
from host.witness_models.jsonutil import CAPTION_PROMPT, parse_caption_json
from host.witness_models.questions import DESCRIBE_PROMPT

OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


@dataclass
class TextResult:
    text: str
    model: str
    tokens: int
    status: str = "ok"


class OpenAIAdapter:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str = OPENAI_URL,
        transport: Transport | None = None,
        max_tokens: int = 1024,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
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
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        },
                    ],
                }
            ],
        }

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def complete_text(self, image_jpeg: bytes, prompt: str) -> TextResult:
        payload, tokens = post_json(
            self.transport,
            f"{self.base_url}/chat/completions",
            self.headers(),
            self.build_body(prompt, image_jpeg),
        )
        return TextResult(text=_openai_text(payload), model=f"openai/{self.model}", tokens=tokens)

    def describe(self, image_jpeg: bytes) -> str:
        return self.complete_text(image_jpeg, DESCRIBE_PROMPT).text

    def caption_json(self, image_jpeg: bytes, prompt: str = CAPTION_PROMPT) -> tuple[dict, int]:
        return _json_with_retry(lambda: self.complete_text(image_jpeg, prompt))


def _openai_text(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise InvalidJson("openai") from error
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        content = "".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise InvalidJson("openai")
    return content


def _json_with_retry(call) -> tuple[dict, int]:
    tokens = 0
    for attempt in (1, 2):
        try:
            result = call()
            tokens += result.tokens
            return parse_caption_json(result.text), tokens
        except InvalidJson:
            if attempt == 2:
                raise ModelCallError("dropped") from None
        except ModelCallError:
            raise
    raise ModelCallError("dropped")
