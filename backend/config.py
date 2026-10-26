"""Environment-backed configuration for external model connectivity."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path = PROJECT / ".env") -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class NvidiaSettings:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: int
    retry_count: int
    max_tokens: int
    temperature: float
    json_mode: bool
    extraction_timeout_seconds: int = 60
    extraction_max_tokens: int = 1024

    @classmethod
    def from_env(cls) -> "NvidiaSettings":
        _load_dotenv()
        base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
        if not base_url.startswith("https://"):
            raise ValueError("NVIDIA_BASE_URL must use HTTPS")
        return cls(
            api_key=os.getenv("NVIDIA_API_KEY", "").strip(),
            base_url=base_url,
            model=os.getenv("NVIDIA_MODEL", "openai/gpt-oss-20b").strip(),
            timeout_seconds=_int_env("NVIDIA_TIMEOUT_SECONDS", 20, 1, 300),
            retry_count=_int_env("NVIDIA_RETRY_COUNT", 2, 0, 5),
            max_tokens=_int_env("NVIDIA_MAX_TOKENS", 512, 1, 32768),
            temperature=_float_env("NVIDIA_TEMPERATURE", 0.0, 0.0, 2.0),
            json_mode=os.getenv("NVIDIA_JSON_MODE", "true").strip().lower() in {"1", "true", "yes", "on"},
            extraction_timeout_seconds=_int_env("NVIDIA_EXTRACTION_TIMEOUT_SECONDS", 60, 1, 120),
            extraction_max_tokens=_int_env("NVIDIA_EXTRACTION_MAX_TOKENS", 1024, 1, 4096),
        )
