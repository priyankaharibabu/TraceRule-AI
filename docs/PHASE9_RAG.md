# Phase 9: Evidence Retrieval and RAG

## Workflow

`POST /phase9/retrieve` accepts a PDF and search query from loopback requests. It reuses the Phase 8 bounded PyMuPDF extractor, then chunks each page passage to at most 1,800 characters with 180 characters of overlap. Chunk records preserve `document_id`, parent `evidence_id`, `page_number`, and `source_reference`.

BM25 runs locally by default. A per-request `InMemoryVectorIndex` implements the replace/search boundary so another storage adapter can replace it later without changing retrieval consumers. It is discarded after the request; no uploaded PDF, passage, vector, or result is persisted.

Semantic retrieval is optional and is implemented through the NVIDIA NeMo Retriever Embedding NIM `/v1/embeddings` interface. Passage and query vectors use the model's `passage` and `query` input types. Cosine similarity is returned separately as `semantic_score`; BM25's raw score is `lexical_score`. When both are available, reciprocal-rank fusion produces a separate `fused_score`.

NVIDIA reranking is a separate optional request to NeMo Retriever's `/v1/ranking` endpoint. It can reorder the hybrid results and reports a separate `rerank_score`. A reranking score is a ranking-model logit, not a calibrated confidence or a regulatory conclusion. Both external operations require their own browser opt-in. With neither selected, no external provider is called.

Retrieval/provider failures retain the best available local order and return an explicit status. Semantic retrieval is skipped above 120 chunks to bound outbound payloads. Empty queries and empty indexes return empty results without provider calls. Invalid PDF input returns safe validation messages. NVIDIA errors are sanitized; no response body or credential is returned.

## Technology choices

- Existing local BM25 was reused.
- PyMuPDF and the Phase 8 extraction safeguards are reused.
- `InMemoryVectorIndex` is the current per-request implementation; the `EmbeddingProvider` / `RerankerProvider` and index methods are migration seams for a later storage backend.
- LlamaIndex was evaluated and not added. This use case indexes one bounded upload for one request; its orchestration/persistence layer would add a dependency without solving a current requirement. LangGraph remains in the Phase 8 candidate workflow; the retrieval endpoint is a small deterministic pipeline.
- No PostgreSQL, pgvector, Qdrant, or persistent index was added.

NVIDIA's documented embedding contract uses `POST /v1/embeddings`, `input`, `input_type`, and returns indexed embedding vectors ([official NeMo Retriever Embedding API reference](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/latest/reference.html)). The documented text reranking endpoint is `POST /v1/ranking`, returning input indexes and logits ([official NeMo Retriever Reranking API guide](https://docs.nvidia.com/nim/nemo-retriever/text-reranking/latest/using-reranking.html)). The application adapters follow those request/response shapes, but hosted endpoint compatibility and model access were not verified live.

## Validation and limits

- Phase 9 retrieval tests: **15 passed**; deterministic embedding/reranking mocks exercise semantic cosine ranking, RRF, source references, reranking order, pipeline integration, empty query/index, malformed vectors, timeouts/retry, missing key, and provider failures.
- Full backend pytest suite: **224 passed, 48 subtests passed**.
- Full unittest discovery: **147 passed**.
- Frontend production build: passed. Chrome browser checks exercised the BM25-only upload flow, visible page reference, and loading state. External semantic and reranking browser flows were not run because that would require an authorized live provider call; their adapter/pipeline paths were tested with deterministic mocks.
- No live NVIDIA inference was performed. NVIDIA key presence/configuration does not prove that a key, model, endpoint, or account is valid.
- Retrieval relevance is not evidence of legal validity, applicability, compliance, duplication, or conflict. Uploaded sources remain unverified and session-only.

## Next phase

Before persistent retrieval is introduced, define an authorized source-data and retention policy, storage adapter contract, deletion/retention behavior, and operational access controls. Then evaluate production model credentials and latency with a separately approved live inference test. Do not promote these results into the authoritative obligation queue without independent human review.
