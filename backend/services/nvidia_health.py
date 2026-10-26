"""Non-throwing NVIDIA API connectivity and model health check."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.services.nvidia_llm import LLMClient, LLMError


def check_nvidia_health(client: LLMClient | None = None) -> dict[str, Any]:
    """Make one minimal authenticated completion and return a safe status payload."""
    try:
        client = client or LLMClient()
        settings = client.settings
        if not settings.api_key:
            return {
                "status": "not_configured",
                "authenticated": None,
                "model_available": None,
                "network_connected": None,
                "model": settings.model,
                "latency_ms": None,
                "token_usage": None,
                "retry_count": 0,
                "error": {"code": "missing_api_key", "message": "Set NVIDIA_API_KEY in .env or the process environment"},
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        response = client.complete(
            "Reply with the single word OK.",
            system_prompt="Connectivity check. Reply only OK.",
            json_mode=False,
            # Match the verified GPT-OSS request shape while keeping the probe bounded.
            max_tokens=256,
            temperature=0.0,
            reasoning_effort="low",
            timeout_seconds=min(settings.timeout_seconds, 15),
            retry_count=0,
        )
        if not response.content:
            return {
                "status": "unavailable",
                "authenticated": True,
                "model_available": True,
                "network_connected": True,
                "model": response.model,
                "latency_ms": response.latency_ms,
                "token_usage": response.usage,
                "retry_count": response.retry_count,
                "error": {
                    "code": "incomplete_completion",
                    "message": "NVIDIA returned no final answer content",
                    "finish_reason": response.finish_reason,
                },
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        return {
            "status": "available",
            "authenticated": True,
            "model_available": True,
            "network_connected": True,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "token_usage": response.usage,
            "retry_count": response.retry_count,
            "error": None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except LLMError as exc:
        return {
            "status": "unavailable",
            "authenticated": False if exc.code == "authentication_failed" else None,
            # A generic API or parse error does not prove that the model is absent.
            "model_available": False if exc.code == "model_not_available" else None,
            "network_connected": False if exc.code in {"network_error", "timeout"} else None,
            "model": getattr(getattr(client, "settings", None), "model", None),
            "latency_ms": getattr(exc, "latency_ms", None),
            "token_usage": None,
            "retry_count": getattr(exc, "retry_count", 0),
            "error": exc.as_dict(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        # Configuration and transient failures must never take down the caller.
        return {
            "status": "unavailable",
            "authenticated": None,
            "model_available": None,
            "network_connected": None,
            "model": None,
            "latency_ms": None,
            "token_usage": None,
            "retry_count": 0,
            "error": {"code": "health_check_failed", "message": "Health check could not complete"},
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
