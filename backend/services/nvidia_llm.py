"""Small OpenAI-compatible chat client for NVIDIA-hosted models."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.config import NvidiaSettings

LOGGER = logging.getLogger(__name__)


class LLMError(Exception):
    """Sanitized, structured failure from the model service."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "http_status": self.http_status,
        }


@dataclass
class LLMResponse:
    content: str | None
    model: str
    usage: dict[str, int] | None
    latency_ms: float
    retry_count: int
    finish_reason: str | None = None


def _log_request(*, model: str, latency_ms: float, usage: dict[str, int] | None, status: str, retries: int) -> None:
    # Deliberately exclude prompts, completions, and all credential values.
    LOGGER.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "latency_ms": round(latency_ms, 2),
        "token_usage": usage,
        "response_status": status,
        "retry_count": retries,
    }, sort_keys=True))


class LLMClient:
    """Single completion interface; callers do not handle HTTP/provider details."""

    def __init__(self, settings: NvidiaSettings | None = None, *, opener=urlopen):
        self.settings = settings or NvidiaSettings.from_env()
        self._opener = opener

    def complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        json_mode: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: int | None = None,
        retry_count: int | None = None,
    ) -> LLMResponse:
        api_key = self.settings.api_key.strip()
        if not api_key:
            raise LLMError("missing_api_key", "NVIDIA_API_KEY is not configured")
        if not prompt.strip():
            raise LLMError("invalid_request", "Prompt must not be empty")

        use_json = self.settings.json_mode if json_mode is None else json_mode
        request_timeout = self.settings.timeout_seconds if timeout_seconds is None else timeout_seconds
        max_retries = self.settings.retry_count if retry_count is None else retry_count
        if not 1 <= request_timeout <= 300:
            raise LLMError("invalid_request", "Request timeout must be between 1 and 300 seconds")
        if not 0 <= max_retries <= 5:
            raise LLMError("invalid_request", "Retry count must be between 0 and 5")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "max_tokens": self.settings.max_tokens if max_tokens is None else max_tokens,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "stream": False,
        }
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort

        request = Request(
            f"{self.settings.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        retries = 0
        while True:
            try:
                with self._opener(request, timeout=request_timeout) as response:
                    raw = response.read()
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict) and "error" in parsed:
                    # Some gateways return an error envelope with HTTP 200. Do not
                    # misreport that as a malformed completion or model absence.
                    raise LLMError(
                        "provider_error",
                        "NVIDIA returned an API error response",
                        http_status=200,
                    )
                if not isinstance(parsed, dict):
                    raise LLMError("invalid_provider_response", "NVIDIA returned an unexpected response shape")
                first_choice = parsed["choices"][0]
                message = first_choice["message"]
                if not isinstance(message, dict):
                    raise LLMError("invalid_provider_response", "NVIDIA returned an unexpected message shape")
                content = message.get("content")
                if content is not None and not isinstance(content, str):
                    raise LLMError("invalid_provider_response", "NVIDIA response content was not a text string")
                finish_reason = first_choice.get("finish_reason")
                if finish_reason is not None and not isinstance(finish_reason, str):
                    finish_reason = None
                usage_raw = parsed.get("usage")
                usage = None
                if isinstance(usage_raw, dict):
                    usage = {
                        key: int(usage_raw[key])
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                        if isinstance(usage_raw.get(key), int)
                    }
                latency = (time.monotonic() - started) * 1000
                response_status = "incomplete" if content is None else "success"
                _log_request(model=self.settings.model, latency_ms=latency, usage=usage, status=response_status, retries=retries)
                return LLMResponse(content, self.settings.model, usage, latency, retries, finish_reason)
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if exc.code in {401, 403}:
                    code, message = "authentication_failed", "NVIDIA rejected the API key"
                elif exc.code == 429:
                    code, message = "rate_limited", "NVIDIA rate limit reached"
                else:
                    code, message = "provider_error", "NVIDIA service returned an error"
                error = LLMError(code, message, retryable=retryable, http_status=exc.code)
            except LLMError as exc:
                error = exc
            except (TimeoutError, URLError) as exc:
                error = LLMError(
                    "timeout" if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError) else "network_error",
                    "NVIDIA request timed out" if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError) else "Could not reach NVIDIA API",
                    retryable=True,
                )
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError, IndexError, TypeError):
                error = LLMError("invalid_provider_response", "NVIDIA returned an invalid chat response")

            if error.retryable and retries < max_retries:
                retries += 1
                continue
            latency = (time.monotonic() - started) * 1000
            _log_request(model=self.settings.model, latency_ms=latency, usage=None, status=error.code, retries=retries)
            error.retry_count = retries
            error.latency_ms = latency
            raise error
