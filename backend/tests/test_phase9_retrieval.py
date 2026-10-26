from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

import fitz
from fastapi.testclient import TestClient

from backend.config import NvidiaSettings
from backend.services.document_analyzer import app
from backend.services.nvidia_retrieval import NvidiaNimRetrievalClient, RetrievalProviderError
from backend.services.phase9_pipeline import run_phase9_retrieval
from backend.services.phase9_retrieval import chunk_evidence_passages, retrieve_hybrid


def evidence(evidence_id, text, page=1):
    return {
        "document_id": "UPLOAD-TEST",
        "evidence_id": evidence_id,
        "page_number": page,
        "source_reference": f"notice.pdf#pdf-page={page}",
        "source_provenance_status": "unverified_user_upload",
        "passage_text": text,
    }


class MockEmbeddings:
    def __init__(self, vectors_by_text):
        self.vectors_by_text = vectors_by_text
        self.calls = []

    def embed(self, texts, *, input_type):
        self.calls.append((list(texts), input_type))
        return [self.vectors_by_text[text] for text in texts]


class MockReranker:
    def __init__(self, rankings=None, error=None):
        self.rankings = rankings or []
        self.error = error
        self.calls = 0

    def rerank(self, query, passages):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rankings


class Phase9RetrievalTests(unittest.TestCase):
    def test_chunking_preserves_source_identity_page_and_overlapping_text(self):
        source = evidence("EV-1", "abcdefghij", page=7)
        chunks = chunk_evidence_passages([source], max_chars=6, overlap_chars=2)
        self.assertEqual([chunk["passage_text"] for chunk in chunks], ["abcdef", "efghij"])
        self.assertEqual(chunks[1]["document_id"], "UPLOAD-TEST")
        self.assertEqual(chunks[1]["evidence_id"], "EV-1")
        self.assertEqual(chunks[1]["page_number"], 7)
        self.assertEqual(chunks[1]["source_reference"], "notice.pdf#pdf-page=7")

    def test_chunking_skips_empty_and_rejects_invalid_overlap(self):
        self.assertEqual(chunk_evidence_passages([evidence("EV-1", "  ")]), [])
        with self.assertRaises(ValueError):
            chunk_evidence_passages([], max_chars=4, overlap_chars=4)

    def test_bm25_only_returns_lexical_score_separate_from_semantic(self):
        passages = [evidence("EV-1", "The lender must notify a borrower."), evidence("EV-2", "A generic statement about account records.", 2)]
        result = retrieve_hybrid(passages, "lender notify")
        self.assertEqual(result["results"][0]["evidence_id"], "EV-1")
        self.assertGreater(result["results"][0]["lexical_score"], 0)
        self.assertIsNone(result["results"][0]["semantic_score"])

    def test_semantic_vector_search_and_fusion_retain_distinct_scores(self):
        passages = [evidence("EV-1", "Loan repayment schedule must be updated."), evidence("EV-2", "Borrower instalment calendar changes.", 2)]
        vectors = MockEmbeddings({
            "Loan repayment schedule must be updated.": [1.0, 0.0],
            "Borrower instalment calendar changes.": [0.0, 1.0],
            "payment timetable": [0.0, 1.0],
        })
        result = retrieve_hybrid(passages, "payment timetable", embedding_provider=vectors)
        self.assertEqual(result["semantic_status"], "available")
        self.assertEqual(result["results"][0]["evidence_id"], "EV-2")
        self.assertIsNotNone(result["results"][0]["semantic_score"])
        self.assertIsNotNone(result["results"][0]["fused_score"])
        self.assertEqual(result["results"][0]["page_number"], 2)
        self.assertEqual(vectors.calls[-1][1], "query")

    def test_empty_query_and_empty_index_do_not_call_provider(self):
        provider = MockEmbeddings({})
        self.assertEqual(retrieve_hybrid([evidence("EV-1", "words")], "  ", embedding_provider=provider)["status"], "empty_query")
        self.assertEqual(retrieve_hybrid([], "query", embedding_provider=provider)["status"], "empty_index")
        self.assertEqual(provider.calls, [])

    def test_embedding_failure_falls_back_to_bm25_without_fabricating_scores(self):
        class Failing:
            def embed(self, texts, *, input_type):
                raise RuntimeError("provider detail must not leak")
        result = retrieve_hybrid([evidence("EV-1", "customer notice")], "customer", embedding_provider=Failing())
        self.assertEqual(result["semantic_status"], "failed_provider_failure_using_lexical_only")
        self.assertGreater(result["results"][0]["lexical_score"], 0)
        self.assertIsNone(result["results"][0]["semantic_score"])

    def test_dimension_mismatch_fails_semantics_safely(self):
        provider = MockEmbeddings({"alpha": [1, 0], "query": [1]})
        result = retrieve_hybrid([evidence("EV-1", "alpha")], "query", embedding_provider=provider)
        self.assertEqual(result["semantic_status"], "failed_provider_failure_using_lexical_only")

    def test_reranker_changes_order_but_keeps_retrieval_scores_separate(self):
        passages = [evidence("EV-1", "payment and account review"), evidence("EV-2", "payment schedule notice", 2)]
        base = retrieve_hybrid(passages, "payment")
        reranker = MockReranker([(len(base["results"]) - 1, 3.4), (0, -1.2)])
        result = retrieve_hybrid(passages, "payment", reranker_provider=reranker)
        self.assertEqual(result["rerank_status"], "available")
        self.assertEqual(result["results"][0]["evidence_id"], base["results"][-1]["evidence_id"])
        self.assertEqual(result["results"][0]["rerank_score"], 3.4)
        self.assertIsNotNone(result["results"][0]["lexical_score"])
        self.assertIsNone(result["results"][0]["semantic_score"])

    def test_reranker_failure_keeps_hybrid_order(self):
        passages = [evidence("EV-1", "payment account"), evidence("EV-2", "payment schedule", 2)]
        result = retrieve_hybrid(passages, "payment", reranker_provider=MockReranker(error=RuntimeError("secret")))
        self.assertEqual(result["rerank_status"], "failed_provider_failure_using_hybrid_order")
        self.assertTrue(all(item["rerank_score"] is None for item in result["results"]))

    def test_phase9_pipeline_is_session_only_and_handles_empty_query(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "A borrower receives a written notice.")
        body = pdf.tobytes()
        pdf.close()
        result = run_phase9_retrieval("notice.pdf", body, "")
        self.assertEqual(result["status"], "empty_query")
        self.assertEqual(result["persistence"], "session_only_no_project_data_written")
        self.assertEqual(result["retrieval_mode"], "bm25_only")

    def test_phase9_pipeline_integrates_mock_semantics_and_reranking_with_source_refs(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "Temporary customer notice for semantic retrieval.")
        body = pdf.tobytes()
        pdf.close()
        lexical = run_phase9_retrieval("notice.pdf", body, "customer notice")
        passage_text = lexical["results"][0]["passage_text"]
        embeddings = MockEmbeddings({passage_text: [1.0, 0.0], "semantic query": [1.0, 0.0]})
        reranker = MockReranker([(0, 0.93)])
        result = run_phase9_retrieval(
            "notice.pdf", body, "semantic query", semantic_opt_in=True, rerank_opt_in=True,
            embedding_provider=embeddings, reranker_provider=reranker,
        )
        self.assertEqual(result["semantic_status"], "available")
        self.assertEqual(result["rerank_status"], "available")
        self.assertEqual(result["results"][0]["page_number"], 1)
        self.assertEqual(result["results"][0]["document_id"], result["document_id"])
        self.assertTrue(result["results"][0]["source_reference"].endswith("#pdf-page=1"))
        self.assertIsNotNone(result["results"][0]["semantic_score"])
        self.assertEqual(result["results"][0]["rerank_score"], 0.93)
        self.assertEqual(result["persistence"], "session_only_no_project_data_written")

    def test_nim_adapter_builds_embedding_and_reranking_requests_with_mock_response(self):
        settings = NvidiaSettings(
            api_key="test-token", base_url="https://example.invalid/v1", model="unused", timeout_seconds=5,
            retry_count=1, max_tokens=100, temperature=0, json_mode=True,
        )
        responses = [
            {"data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            {"rankings": [{"index": 0, "logit": 0.7}]},
        ]
        requests = []
        class Response:
            def __init__(self, payload): self.payload = payload
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(self.payload).encode()
        def opener(request, timeout):
            requests.append((request, timeout))
            return Response(responses.pop(0))
        client = NvidiaNimRetrievalClient(settings=settings, opener=opener)
        self.assertEqual(client.embed(["text"], input_type="passage"), [[0.1, 0.2]])
        self.assertEqual(client.rerank("query", ["text"]), [(0, 0.7)])
        self.assertTrue(requests[0][0].full_url.endswith("/embeddings"))
        self.assertTrue(requests[1][0].full_url.endswith("/ranking"))
        self.assertEqual(requests[0][1], 30 if settings.extraction_timeout_seconds >= 30 else settings.extraction_timeout_seconds)

    def test_nim_adapter_missing_key_and_malformed_vectors_are_safe(self):
        settings = NvidiaSettings(api_key="", base_url="https://example.invalid/v1", model="unused", timeout_seconds=5, retry_count=0, max_tokens=100, temperature=0, json_mode=True)
        client = NvidiaNimRetrievalClient(settings=settings, opener=Mock())
        with self.assertRaises(RetrievalProviderError) as caught:
            client.embed(["text"], input_type="passage")
        self.assertEqual(caught.exception.code, "missing_api_key")

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"data":[{"index":0,"embedding":[NaN]}]}'
        configured = NvidiaSettings(api_key="test-token", base_url="https://example.invalid/v1", model="unused", timeout_seconds=5, retry_count=0, max_tokens=100, temperature=0, json_mode=True)
        malformed = NvidiaNimRetrievalClient(settings=configured, opener=lambda request, timeout: Response())
        with self.assertRaises(RetrievalProviderError) as bad_vector:
            malformed.embed(["text"], input_type="passage")
        self.assertEqual(bad_vector.exception.code, "invalid_provider_response")

    def test_nim_retrieval_timeout_retries_once_with_sanitized_failure(self):
        settings = NvidiaSettings(api_key="test-token", base_url="https://example.invalid/v1", model="unused", timeout_seconds=5, retry_count=5, max_tokens=100, temperature=0, json_mode=True)
        opener = Mock(side_effect=TimeoutError("credential-like private details"))
        client = NvidiaNimRetrievalClient(settings=settings, opener=opener)
        with self.assertRaises(RetrievalProviderError) as caught:
            client.embed(["text"], input_type="query")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(caught.exception.code, "provider_timeout")
        self.assertNotIn("credential-like", str(caught.exception))

    def test_phase9_api_defaults_to_local_bm25_and_rejects_remote_callers(self):
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "The bank must notify each customer.")
        body = pdf.tobytes()
        pdf.close()
        local = TestClient(app, client=("127.0.0.1", 54321))
        result = local.post("/phase9/retrieve", files={"file": ("notice.pdf", body, "application/pdf")}, data={"query": "notify customer"})
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload["retrieval_mode"], "bm25_only")
        self.assertEqual(payload["results"][0]["page_number"], 1)
        self.assertTrue(payload["results"][0]["source_reference"].endswith("#pdf-page=1"))
        remote = TestClient(app, client=("192.0.2.10", 54321))
        denied = remote.post("/phase9/retrieve", files={"file": ("notice.pdf", body, "application/pdf")}, data={"query": "notify"})
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
