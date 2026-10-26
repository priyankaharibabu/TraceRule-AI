"""Optional NVIDIA NeMo Retriever embedding and reranking adapters."""

from __future__ import annotations

import json
import math
import os
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.config import NvidiaSettings


class RetrievalProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class NvidiaNimRetrievalClient:
    """Uses NVIDIA NeMo Retriever `/v1/embeddings` and `/v1/ranking` APIs."""

    def __init__(self, settings: NvidiaSettings | None = None, *, opener=urlopen):
        self.settings = settings or NvidiaSettings.from_env()
        self._opener = opener
        self.embedding_model = os.getenv("NVIDIA_EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b").strip()
        self.rerank_model = os.getenv("NVIDIA_RERANK_MODEL", "nvidia/llama-nemotron-rerank-vl-1b-v2").strip()
        self.timeout_seconds = min(30, self.settings.extraction_timeout_seconds)
        if not self.embedding_model or not self.rerank_model:
            raise ValueError("NVIDIA retrieval model names must be configured")

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.api_key:
            raise RetrievalProviderError("missing_api_key", "NVIDIA_API_KEY is not configured")
        request = Request(
            f"{self.settings.base_url}/{endpoint.lstrip('/')}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        retries = 0
        while True:
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise RetrievalProviderError("invalid_provider_response", "NVIDIA returned an unexpected retrieval response")
                return parsed
            except HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                code = "authentication_failed" if exc.code in {401, 403} else "rate_limited" if exc.code == 429 else "provider_error"
                message = "NVIDIA rejected the API key" if code == "authentication_failed" else "NVIDIA rate limit reached" if code == "rate_limited" else "NVIDIA retrieval service returned an error"
                error = RetrievalProviderError(code, message, retryable=retryable)
            except (TimeoutError, URLError):
                error = RetrievalProviderError("provider_timeout", "NVIDIA retrieval request timed out or could not connect", retryable=True)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                error = RetrievalProviderError("invalid_provider_response", "NVIDIA returned an invalid retrieval response")
            if error.retryable and retries == 0:
                retries += 1
                continue
            raise error

    def embed(self, texts: Sequence[str], *, input_type: str) -> list[list[float]]:
        if input_type not in {"query", "passage"}:
            raise ValueError("input_type must be query or passage")
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be non-empty text")
        data = self._post("embeddings", {
            "model": self.embedding_model,
            "input": list(texts),
            "input_type": input_type,
            "modality": "text",
            "encoding_format": "float",
        }).get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise RetrievalProviderError("invalid_provider_response", "NVIDIA returned an unexpected embedding count")
        ordered: list[list[float] | None] = [None] * len(texts)
        dimension: int | None = None
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                raise RetrievalProviderError("invalid_provider_response", "NVIDIA returned malformed embedding metadata")
            index = item["index"]
            vector = item.get("embedding")
            if index < 0 or index >= len(texts) or ordered[index] is not None or not isinstance(vector, list) or not vector:
                raise RetrievalProviderError("invalid_provider_response", "NVIDIA returned malformed embedding data")
            if any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in vector):
                raise RetrievalProviderError("invalid_provider_response", "NVIDIA returned non-numeric embedding values")
            if dimension is None:
                dimension = len(vector)
            if len(vector) != dimension:
                raise RetrievalProviderError("invalid_provider_response", "NVIDIA embedding dimensions did not match")
            ordered[index] = [float(value) for value in vector]
        if any(vector is None for vector in ordered):
            raise RetrievalProviderError("invalid_provider_response", "NVIDIA omitted an embedding")
        return [vector for vector in ordered if vector is not None]

    def rerank(self, query: str, passages: Sequence[str]) -> list[tuple[int, float]]:
        if not query.strip() or not passages or any(not text.strip() for text in passages):
            raise ValueError("Reranking needs a non-empty query and passages")
        rankings = self._post("ranking", {
            "model": self.rerank_model,
            "query": {"text": query},
            "passages": [{"text": text} for text in passages],
            "truncate": "END",
        }).get("rankings")
        if not isinstance(rankings, list):
            raise RetrievalProviderError("invalid_provider_response", "NVIDIA returned no ranking list")
        result: list[tuple[int, float]] = []
        for item in rankings:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int) or not isinstance(item.get("logit"), (int, float)) or not math.isfinite(item["logit"]):
                raise RetrievalProviderError("invalid_provider_response", "NVIDIA returned malformed ranking data")
            result.append((item["index"], float(item["logit"])))
        return sorted(result, key=lambda item: (-item[1], item[0]))

