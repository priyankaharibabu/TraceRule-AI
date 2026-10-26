"""Session-local hybrid evidence retrieval for the Phase 9 RAG workflow.

Scores here describe retrieval relevance only. They do not establish legal
validity, compliance, applicability, duplication, or conflict.
"""

from __future__ import annotations

import math
from typing import Any, Protocol, Sequence

from backend.services.phase8_retrieval import retrieve_passages

MAX_CHUNK_CHARS = 1800
CHUNK_OVERLAP_CHARS = 180
MAX_SEMANTIC_PASSAGES = 120
MAX_RERANK_PASSAGES = 20
RRF_K = 60
SAFE_PROVIDER_CODES = {
    "missing_api_key", "authentication_failed", "rate_limited", "provider_error",
    "provider_timeout", "network_error", "invalid_provider_response", "configuration_error",
}


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str], *, input_type: str) -> list[list[float]]: ...


class RerankerProvider(Protocol):
    def rerank(self, query: str, passages: Sequence[str]) -> list[tuple[int, float]]: ...


class InMemoryVectorIndex:
    """Replaceable index boundary; this implementation lives only per request."""

    def __init__(self) -> None:
        self._items: list[tuple[dict[str, Any], list[float]]] = []

    @staticmethod
    def _validated_vector(vector: Sequence[float]) -> list[float]:
        if not vector or any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in vector):
            raise ValueError("Embedding vector must contain finite numeric values")
        return [float(value) for value in vector]

    def replace(self, passages: Sequence[dict[str, Any]], vectors: Sequence[Sequence[float]]) -> None:
        if len(passages) != len(vectors):
            raise ValueError("Every passage must have exactly one embedding")
        items = []
        dimension: int | None = None
        for passage, raw_vector in zip(passages, vectors):
            vector = self._validated_vector(raw_vector)
            dimension = dimension or len(vector)
            if len(vector) != dimension:
                raise ValueError("Embedding dimensions do not match")
            items.append((dict(passage), vector))
        self._items = items

    def search(self, query_vector: Sequence[float], *, limit: int) -> list[tuple[dict[str, Any], float]]:
        query = self._validated_vector(query_vector)
        ranked: list[tuple[dict[str, Any], float, int]] = []
        for index, (passage, vector) in enumerate(self._items):
            if len(vector) != len(query):
                raise ValueError("Query and passage embedding dimensions do not match")
            denom = math.sqrt(sum(value * value for value in vector)) * math.sqrt(sum(value * value for value in query))
            score = sum(a * b for a, b in zip(query, vector)) / denom if denom else 0.0
            ranked.append((passage, score, index))
        ranked.sort(key=lambda item: (-item[1], item[2]))
        return [(passage, score) for passage, score, _ in ranked[: max(0, limit)]]


def chunk_evidence_passages(
    passages: Sequence[dict[str, Any]],
    *,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    """Split source passages while copying all source/page provenance fields."""
    if max_chars < 1 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("Chunk size must be positive and overlap smaller than chunk size")
    chunks: list[dict[str, Any]] = []
    stride = max_chars - overlap_chars
    for passage in passages:
        text = str(passage.get("passage_text", ""))
        if not text.strip():
            continue
        starts = list(range(0, len(text), stride))
        chunk_index = 0
        for start in starts:
            body = text[start : start + max_chars]
            if not body.strip():
                continue
            chunk_index += 1
            base_id = str(passage.get("evidence_id", "evidence"))
            chunks.append({
                **passage,
                "parent_evidence_id": base_id,
                "chunk_id": f"{base_id}-C{chunk_index:03d}",
                "chunk_start": start,
                "chunk_end": start + len(body),
                "passage_text": body,
            })
            if start + max_chars >= len(text):
                break
    return chunks


def retrieve_hybrid(
    passages: Sequence[dict[str, Any]],
    query: str,
    *,
    limit: int = 10,
    embedding_provider: EmbeddingProvider | None = None,
    reranker_provider: RerankerProvider | None = None,
) -> dict[str, Any]:
    """Combine BM25 and optional cosine results using reciprocal-rank fusion."""
    clean_query = query.strip()
    if not clean_query:
        return {"status": "empty_query", "results": [], "semantic_status": "not_requested", "rerank_status": "not_requested"}
    if not passages:
        return {"status": "empty_index", "results": [], "semantic_status": "not_requested", "rerank_status": "not_requested"}

    lexical = retrieve_passages(list(passages), clean_query, limit=len(passages))
    lexical_by_id = {str(item.get("chunk_id") or item.get("evidence_id")): item for item in lexical}
    lexical_rank = {key: rank for rank, key in enumerate(lexical_by_id, start=1)}
    merged: dict[str, dict[str, Any]] = {}
    for passage in passages:
        key = str(passage.get("chunk_id") or passage.get("evidence_id"))
        lex = lexical_by_id.get(key)
        merged[key] = {
            **passage,
            "lexical_score": lex.get("retrieval_score") if lex else None,
            "semantic_score": None,
            "fused_score": 0.0,
            "rerank_score": None,
        }
        if key in lexical_rank:
            merged[key]["fused_score"] += 1.0 / (RRF_K + lexical_rank[key])

    semantic_status = "not_requested"
    semantic_error_code: str | None = None
    if embedding_provider is not None:
        if len(passages) > MAX_SEMANTIC_PASSAGES:
            semantic_status = "skipped_passage_limit"
        else:
            try:
                passage_vectors = embedding_provider.embed([str(p.get("passage_text", "")) for p in passages], input_type="passage")
                query_vectors = embedding_provider.embed([clean_query], input_type="query")
                if len(passage_vectors) != len(passages) or len(query_vectors) != 1:
                    raise ValueError("Embedding provider returned an unexpected vector count")
                index = InMemoryVectorIndex()
                index.replace(passages, passage_vectors)
                semantic_ranked = index.search(query_vectors[0], limit=len(passages))
                semantic_rank: dict[str, int] = {}
                for rank, (passage, score) in enumerate(semantic_ranked, start=1):
                    key = str(passage.get("chunk_id") or passage.get("evidence_id"))
                    merged[key]["semantic_score"] = score
                    merged[key]["fused_score"] += 1.0 / (RRF_K + rank)
                    semantic_rank[key] = rank
                semantic_status = "available"
            except Exception as exc:
                # Retain BM25 results and do not expose provider internals.
                code = getattr(exc, "code", None)
                semantic_error_code = code if code in SAFE_PROVIDER_CODES else "provider_failure"
                semantic_status = f"failed_{semantic_error_code}_using_lexical_only"

    ranked = sorted(merged.values(), key=lambda item: (-item["fused_score"], str(item.get("chunk_id") or item.get("evidence_id"))))
    results = ranked[: max(0, limit)]

    rerank_status = "not_requested"
    rerank_error_code: str | None = None
    if reranker_provider is not None and results:
        try:
            reranked = reranker_provider.rerank(clean_query, [str(item.get("passage_text", "")) for item in results[:MAX_RERANK_PASSAGES]])
            if not reranked:
                raise ValueError("Reranker returned no rankings")
            seen: set[int] = set()
            ordered: list[dict[str, Any]] = []
            for source_index, score in reranked:
                if not isinstance(source_index, int) or source_index < 0 or source_index >= min(len(results), MAX_RERANK_PASSAGES) or source_index in seen or not math.isfinite(score):
                    raise ValueError("Reranker returned malformed ranking indices or scores")
                seen.add(source_index)
                item = dict(results[source_index])
                item["rerank_score"] = float(score)
                ordered.append(item)
            ordered.extend(item for index, item in enumerate(results) if index not in seen)
            results = ordered
            rerank_status = "available"
        except Exception as exc:
            code = getattr(exc, "code", None)
            rerank_error_code = code if code in SAFE_PROVIDER_CODES else "provider_failure"
            rerank_status = f"failed_{rerank_error_code}_using_hybrid_order"

    status = "available" if results else "no_matches"
    return {
        "status": status,
        "query": clean_query,
        "results": results,
        "semantic_status": semantic_status,
        "semantic_error_code": semantic_error_code,
        "rerank_status": rerank_status,
        "rerank_error_code": rerank_error_code,
        "score_notice": "Retrieval and reranking scores indicate text relevance only; they do not indicate legal validity, compliance, or conflict.",
    }
