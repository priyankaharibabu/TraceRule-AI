"""In-memory PDF extraction and hybrid retrieval orchestration."""

from __future__ import annotations

from typing import Any

from backend.services.nvidia_retrieval import NvidiaNimRetrievalClient
from backend.services.phase8_pipeline import Phase8InputError, _extract_pdf_agent
from backend.services.phase9_retrieval import (
    EmbeddingProvider,
    RerankerProvider,
    chunk_evidence_passages,
    retrieve_hybrid,
)


class _UnavailableProvider:
    def __init__(self, code: str = "configuration_error"):
        self.code = code

    def embed(self, texts, *, input_type):
        raise RuntimeError(self.code)

    def rerank(self, query, passages):
        raise RuntimeError(self.code)


def run_phase9_retrieval(
    file_name: str,
    file_bytes: bytes,
    query: str,
    *,
    semantic_opt_in: bool = False,
    rerank_opt_in: bool = False,
    embedding_provider: EmbeddingProvider | None = None,
    reranker_provider: RerankerProvider | None = None,
) -> dict[str, Any]:
    """Retrieve source-backed passages without generating or persisting rules."""
    if len(query) > 500:
        raise Phase8InputError("Search query must be 500 characters or fewer.")
    extracted = _extract_pdf_agent({"file_name": file_name, "file_bytes": file_bytes, "completed_nodes": []})
    if extracted.get("workflow_status") != "ready":
        return {
            "status": "invalid_source",
            "source_filename": file_name,
            "source_provenance_status": "unverified_user_upload",
            "validation_errors": extracted.get("validation_errors", []),
            "results": [],
            "persistence": "session_only_no_project_data_written",
        }

    chunks = chunk_evidence_passages(extracted.get("passages", []))
    client: NvidiaNimRetrievalClient | None = None
    provider_error = False
    if (semantic_opt_in and embedding_provider is None) or (rerank_opt_in and reranker_provider is None):
        try:
            client = NvidiaNimRetrievalClient()
        except Exception:
            provider_error = True
    if semantic_opt_in and embedding_provider is None:
        embedding_provider = client if client else _UnavailableProvider()
    if rerank_opt_in and reranker_provider is None:
        reranker_provider = client if client else _UnavailableProvider()

    result = retrieve_hybrid(
        chunks,
        query,
        embedding_provider=embedding_provider if semantic_opt_in else None,
        reranker_provider=reranker_provider if rerank_opt_in else None,
    )
    if provider_error:
        if semantic_opt_in:
            result["semantic_status"] = "configuration_unavailable_using_lexical_only"
        if rerank_opt_in:
            result["rerank_status"] = "configuration_unavailable_using_previous_order"
    return {
        **result,
        "document_id": extracted.get("document_id"),
        "source_filename": extracted.get("source_filename", file_name),
        "source_sha256": extracted.get("source_sha256"),
        "source_provenance_status": "unverified_user_upload",
        "chunks_indexed": len(chunks),
        "retrieval_mode": "hybrid" if semantic_opt_in else "bm25_only",
        "persistence": "session_only_no_project_data_written",
    }
