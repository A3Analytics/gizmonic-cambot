"""HTTP POST used by the model adapters.

Logs the URL and status only. Response bodies and headers are not logged,
because an error page can echo a request header.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from host.witness_models.errors import InvalidJson, ModelCallError, TlsFailure

log = logging.getLogger("witness.models")


@dataclass
class HttpResponse:
    status: int
    body: bytes


class Transport:
    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        raise NotImplementedError


class UrllibTransport(Transport):
    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def post(self, url: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return HttpResponse(response.status, response.read())
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read())
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, ssl.SSLError):
                raise TlsFailure(type(reason).__name__) from None
            text = str(reason).lower()
            if "certificate" in text or "tls" in text or "ssl" in text:
                raise TlsFailure("tls") from None
            raise ModelCallError("dropped") from None
        except ssl.SSLError:
            raise TlsFailure("tls") from None


def classify_http(status: int, body: bytes) -> str | None:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limited"
    if status == 400:
        lowered = body.lower()
        if b"image" in lowered or b"vision" in lowered:
            return "no_vision"
        return "dropped"
    if status >= 400:
        return "dropped"
    return None


def install_redactor(secret: str) -> None:
    if not secret:
        return

    class Redact(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            if secret in message:
                record.msg = message.replace(secret, "[redacted]")
                record.args = ()
            return True

    log.addFilter(Redact())


def post_json(transport: Transport, url: str, headers: dict[str, str], payload: dict) -> tuple[dict, int]:
    body = json.dumps(payload).encode("utf-8")
    try:
        response = transport.post(url, headers, body)
    except TlsFailure:
        log.info("model_http status=tls url=%s", url)
        raise ModelCallError("tls") from None
    log.info("model_http status=%s url=%s", response.status, url)
    mapped = classify_http(response.status, response.body)
    if mapped:
        raise ModelCallError(mapped)
    try:
        parsed = json.loads(response.body)
    except json.JSONDecodeError as error:
        raise InvalidJson("body") from error
    if not isinstance(parsed, dict):
        raise InvalidJson("body")
    return parsed, _tokens(parsed)


def _tokens(payload: dict) -> int:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return total
    input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        return input_tokens + output_tokens
    return 0
