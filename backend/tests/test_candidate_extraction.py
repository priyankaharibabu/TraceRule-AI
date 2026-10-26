from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from backend.services.nvidia_llm import LLMResponse
from backend.services.structured_extraction import (
    ExtractionError,
    extract_review_candidate,
)


DOCUMENT_ID = "DOC-SYNTHETIC-P4C"
EVIDENCE_ID = "EVID-SYNTHETIC-P4C-0001"
SECOND_EVIDENCE_ID = "EVID-SYNTHETIC-P4C-0031"
CANDIDATE_ID = "CAND-SYNTHETIC-P4C-0001"
PASSAGE = "A bank shall retain records."
DATA_FILES = {
    "document_registry.csv": [
        "document_id", "filename", "pdf_sha256", "extracted_text_sha256",
        "role", "document_status",
    ],
    "evidence_passages.csv": [
        "evidence_id", "document_id", "source_filename", "source_sha256",
        "pdf_page_index", "printed_page_number", "page_number",
        "section_heading", "paragraph_reference", "passage_text",
        "passage_type", "review_status", "extraction_method",
        "source_status", "source_page_indexes",
    ],
    "obligation_candidates.csv": [
        "candidate_id", "evidence_id", "document_id", "paragraph_reference",
        "candidate_text", "candidate_type", "candidate_reason", "review_status",
        "source_filename", "source_page", "source_section_hint", "original_text",
        "matched_language", "normalized_requirement", "responsible_party",
        "applicability", "conditions", "exceptions", "verification_status",
        "review_notes",
    ],
    "obligation_review_queue.csv": [
        "candidate_id", "candidate_text", "provisional_category", "evidence_id",
        "document_id", "paragraph_reference", "pdf_page_index",
        "printed_page_number", "source_passage_text", "review_decision",
        "reviewer_notes", "reviewed_requirement_text", "reviewed_actor",
        "reviewed_action", "reviewed_conditions", "reviewed_exceptions",
        "applicability_notes",
    ],
    "obligations.csv": [
        "obligation_id", "document_id", "evidence_id", "source_page",
        "source_section", "original_text", "normalized_requirement",
        "responsible_party", "applicability", "conditions", "exceptions",
        "related_obligation_ids", "verification_status", "review_notes",
    ],
}


class CandidateExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        source_hash = "synthetic-source-hash"
        registry = {
            "document_id": DOCUMENT_ID,
            "filename": "synthetic-not-regulatory.pdf",
            "pdf_sha256": source_hash,
            "extracted_text_sha256": "synthetic-text-hash",
            "role": "synthetic_test",
            "document_status": "unverified",
        }
        evidence = [
            self._evidence_row(EVIDENCE_ID, "6", PASSAGE, source_hash),
            self._evidence_row(
                SECOND_EVIDENCE_ID,
                "31",
                "A bank shall preserve a copy.",
                source_hash,
            ),
        ]
        candidate = {
            "candidate_id": CANDIDATE_ID,
            "evidence_id": EVIDENCE_ID,
            "document_id": DOCUMENT_ID,
            "paragraph_reference": "6(1)",
            "candidate_text": PASSAGE,
            "candidate_type": "mandatory_requirement_candidate",
            "candidate_reason": "Synthetic test fixture only.",
            "review_status": "needs_review",
            "source_filename": "synthetic-not-regulatory.pdf",
            "source_page": "7",
            "source_section_hint": "Synthetic paragraph 6",
            "original_text": PASSAGE,
            "matched_language": "shall",
            "normalized_requirement": "",
            "responsible_party": "",
            "applicability": "",
            "conditions": "",
            "exceptions": "",
            "verification_status": "pending_review",
            "review_notes": "",
        }
        queue_row = {
            "candidate_id": CANDIDATE_ID,
            "candidate_text": PASSAGE,
            "provisional_category": "mandatory_requirement_candidate",
            "evidence_id": EVIDENCE_ID,
            "document_id": DOCUMENT_ID,
            "paragraph_reference": "6",
            "pdf_page_index": "6",
            "printed_page_number": "7",
            "source_passage_text": PASSAGE,
            "review_decision": "pending",
        }
        self._write("document_registry.csv", [registry])
        self._write("evidence_passages.csv", evidence)
        self._write("obligation_candidates.csv", [candidate])
        self._write("obligation_review_queue.csv", [queue_row])
        self._write("obligations.csv", [])

    def tearDown(self):
        self.temp_dir.cleanup()

    def _evidence_row(self, evidence_id, paragraph_reference, text, source_hash):
        return {
            "evidence_id": evidence_id,
            "document_id": DOCUMENT_ID,
            "source_filename": "synthetic-not-regulatory.pdf",
            "source_sha256": source_hash,
            "pdf_page_index": "6",
            "printed_page_number": "7",
            "page_number": "7",
            "section_heading": "Synthetic controls",
            "paragraph_reference": paragraph_reference,
            "passage_text": text,
            "passage_type": "synthetic_test",
            "review_status": "needs_review",
            "extraction_method": "paragraph_aware_fallback",
            "source_status": "VERIFY_CURRENT_STATUS",
            "source_page_indexes": "6",
        }

    def _write(self, filename, rows):
        path = self.data_dir / filename
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=DATA_FILES[filename])
            writer.writeheader()
            writer.writerows(rows)

    def _provider_for(self, **overrides):
        payload = {
            "document_id": DOCUMENT_ID,
            "evidence_id": EVIDENCE_ID,
            "page": 7,
            "candidate_obligation": PASSAGE,
            "candidate_type": "explicit_requirement",
            "supporting_quotation": PASSAGE,
            "uncertainty": "Synthetic test only; source status is unverified.",
            "confidence": 0.5,
            "reasoning": "Mocked test response.",
            "requires_human_review": True,
        }
        payload.update(overrides)
        client = Mock()
        client.complete.return_value = LLMResponse(
            json.dumps(payload), "test/model", None, 1.0, 0
        )
        return client

    def test_candidate_extraction_preserves_references_and_stays_provisional(self):
        client = self._provider_for()
        obligations_before = (self.data_dir / "obligations.csv").read_bytes()
        queue_before = (self.data_dir / "obligation_review_queue.csv").read_bytes()

        result = extract_review_candidate(
            CANDIDATE_ID,
            client=client,
            data_dir=self.data_dir,
        )

        self.assertEqual(result["candidate_id"], CANDIDATE_ID)
        self.assertEqual(result["document_id"], DOCUMENT_ID)
        self.assertEqual(result["evidence_id"], EVIDENCE_ID)
        self.assertEqual(result["paragraph_reference"], "6(1)")
        self.assertEqual(result["review_status"], "requires_human_review")
        self.assertIn("not been independently verified", result["source_verification_warning"])
        self.assertEqual(result["extraction"]["supporting_quotation"], PASSAGE)
        self.assertTrue(result["extraction"]["requires_human_review"])
        self.assertEqual(client.complete.call_count, 1)
        self.assertEqual((self.data_dir / "obligations.csv").read_bytes(), obligations_before)
        self.assertEqual((self.data_dir / "obligation_review_queue.csv").read_bytes(), queue_before)

    def test_unknown_candidate_id_is_rejected_before_provider_runs(self):
        client = self._provider_for()
        with self.assertRaises(ExtractionError) as caught:
            extract_review_candidate("CAND-UNKNOWN", client=client, data_dir=self.data_dir)
        self.assertEqual(caught.exception.code, "unknown_candidate")
        client.complete.assert_not_called()

    def test_candidate_with_missing_evidence_reference_is_rejected(self):
        path = self.data_dir / "obligation_candidates.csv"
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["evidence_id"] = "EVID-UNKNOWN"
        self._write("obligation_candidates.csv", rows)
        client = self._provider_for()

        with self.assertRaises(ExtractionError) as caught:
            extract_review_candidate(CANDIDATE_ID, client=client, data_dir=self.data_dir)

        self.assertEqual(caught.exception.code, "invalid_candidate_dataset")
        client.complete.assert_not_called()

    def test_unsupported_quote_is_rejected_by_existing_extraction_validator(self):
        client = self._provider_for(
            candidate_obligation="A bank shall approve every loan.",
            supporting_quotation="A bank shall approve every loan.",
        )
        with self.assertRaises(ExtractionError) as caught:
            extract_review_candidate(CANDIDATE_ID, client=client, data_dir=self.data_dir)
        self.assertEqual(caught.exception.code, "ungrounded_quotation")


if __name__ == "__main__":
    unittest.main()
