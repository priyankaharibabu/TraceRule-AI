from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import fitz
from fastapi.testclient import TestClient

from backend.services.document_analyzer import app
from backend.services.nvidia_llm import LLMError
from backend.services.phase8_pipeline import MAX_PASSAGE_CHARS, _page_chunks, run_phase8_analysis
from backend.services.phase8_retrieval import retrieve_passages


SOURCE = "The lender must notify the customer before changing the repayment schedule."


class MockProvider:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = 0

    def generate(self, evidence, *, validation_feedback=""):
        self.calls += 1
        if self.error:
            raise self.error
        return self.responses.pop(0)


def make_pdf(text=SOURCE):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    content = pdf.tobytes()
    pdf.close()
    return content


def model_response(obligation=SOURCE):
    return json.dumps({
        "candidate_type": "explicit_requirement",
        "candidate_obligation": obligation,
        "supporting_quotation": SOURCE,
        "uncertainty": "Applicability and current legal status require human review.",
        "confidence": 0.8,
        "reasoning": "The wording contains a mandatory term.",
        "requires_human_review": True,
    })


class Phase8PipelineTests(unittest.TestCase):
    def test_pdf_extraction_and_local_retrieval_retain_page_references_without_provider(self):
        provider = MockProvider()
        result = run_phase8_analysis("policy.pdf", make_pdf(), provider=provider)
        self.assertEqual(result["pages_processed"], 1)
        self.assertTrue(result["retrieved_passages"])
        passage = result["retrieved_passages"][0]
        self.assertEqual(passage["page_number"], 1)
        self.assertIn("#pdf-page=1", passage["source_reference"])
        self.assertEqual(result["source_provenance_status"], "unverified_user_upload")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(result["persistence"], "session_only_no_project_data_written")

    def test_bm25_ranks_terms_and_keeps_stable_source_fields(self):
        rows = [
            {"evidence_id": "a", "passage_text": "The lender must notify the borrower."},
            {"evidence_id": "b", "passage_text": "This section describes general background."},
        ]
        ranked = retrieve_passages(rows, "lender must notify")
        self.assertEqual(ranked[0]["evidence_id"], "a")
        self.assertIn("retrieval_score", ranked[0])

    def test_extraction_chunks_a_single_long_source_line(self):
        chunks = _page_chunks("x" * (MAX_PASSAGE_CHARS + 17))
        self.assertEqual([len(chunk) for chunk in chunks], [MAX_PASSAGE_CHARS, 17])
        self.assertEqual("".join(chunks), "x" * (MAX_PASSAGE_CHARS + 17))

    def test_grounded_generation_stays_unverified_inactive_and_session_only(self):
        provider = MockProvider([model_response()])
        result = run_phase8_analysis("notice.pdf", make_pdf(), use_nvidia=True, provider=provider)
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        passage = next(p for p in result["retrieved_passages"] if p["evidence_id"] == candidate["evidence_id"])
        self.assertIn(candidate["supporting_quotation"], passage["passage_text"])
        self.assertEqual(candidate["verification_status"], "unverified_candidate")
        self.assertEqual(candidate["review_status"], "pending_human_review")
        self.assertEqual(candidate["activation_status"], "inactive")
        self.assertEqual(result["persistence"], "session_only_no_project_data_written")

    def test_invalid_model_output_gets_one_repair_attempt_then_is_blocked(self):
        provider = MockProvider(["not json", model_response("not in source")])
        result = run_phase8_analysis("notice.pdf", make_pdf(), use_nvidia=True, provider=provider)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["workflow_status"], "generation_failed")
        self.assertTrue(any(error["code"] == "invalid_model_output" for error in result["validation_errors"]))

    def test_provider_failure_is_safe_and_cannot_create_candidate(self):
        provider = MockProvider(error=LLMError("timeout", "NVIDIA request timed out", retryable=True))
        result = run_phase8_analysis("notice.pdf", make_pdf(), use_nvidia=True, provider=provider)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["workflow_status"], "generation_failed")
        self.assertTrue(all(item["activation_status"] == "inactive" for item in result["candidates"]))

    def test_bad_files_fail_without_calling_provider(self):
        provider = MockProvider([model_response()])
        result = run_phase8_analysis("notice.pdf", b"not a pdf", use_nvidia=True, provider=provider)
        self.assertEqual(result["workflow_status"], "failed")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(provider.calls, 0)

    def test_upload_api_defaults_to_no_provider_and_rejects_non_pdf(self):
        client = TestClient(app, client=("127.0.0.1", 54321))
        response = client.post(
            "/phase8/analyze",
            files={"file": ("source.pdf", make_pdf("Alpha parameter appears in this paragraph."), "application/pdf")},
            data={"focus_query": "Alpha parameter", "use_nvidia": "false"},
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["provider"], None)
        self.assertTrue(result.get("provider_opt_in_required"))
        self.assertEqual(result["retrieved_passages"][0]["passage_text"], "Alpha parameter appears in this paragraph.")
        invalid = client.post("/phase8/analyze", files={"file": ("source.txt", b"hello", "text/plain")})
        self.assertEqual(invalid.status_code, 400)
        remote = TestClient(app, client=("192.0.2.8", 54321))
        self.assertEqual(remote.post("/phase8/analyze", files={"file": ("source.pdf", make_pdf(), "application/pdf")}).status_code, 403)

    def test_api_opt_in_uses_isolated_mock_provider(self):
        client = TestClient(app, client=("127.0.0.1", 54321))
        provider = MockProvider([model_response()])
        with patch("backend.services.phase8_pipeline.NvidiaNimCandidateProvider", return_value=provider):
            response = client.post(
                "/phase8/analyze",
                files={"file": ("source.pdf", make_pdf(), "application/pdf")},
                data={"use_nvidia": "true"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(response.json()["provider"], "NVIDIA NIM")
        self.assertEqual(response.json()["candidates"][0]["activation_status"], "inactive")


if __name__ == "__main__":
    unittest.main()
