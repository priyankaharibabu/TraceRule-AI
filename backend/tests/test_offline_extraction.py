from __future__ import annotations

import json
import unittest

from backend.services.offline_extraction import (
    DeterministicMockProvider,
    ExtractionError,
    ExtractionProviderError,
    OfflineCandidateExtractor,
    load_output_schema,
    validate_candidate_response,
)


PASSAGE = "A bank shall conduct enhanced due diligence before entering an LSP agreement."
METADATA = {
    "document_id": "DOC-SYNTHETIC-TEST",
    "evidence_id": "EVID-SYNTHETIC-TEST",
    "page": 18,
    "paragraph_reference": "6(2)",
}
MOCK_FIELDS = {
    "potential_actor": "A bank",
    "potential_action": "conduct enhanced due diligence",
    "applicability": "Before entering an LSP agreement.",
    "conditions": None,
    "exceptions": None,
    "candidate_obligation": "A bank shall conduct enhanced due diligence before entering an LSP agreement.",
    "supporting_quote": PASSAGE,
    "uncertainty": "MOCK ONLY: supplied test interpretation; not a legal assessment.",
}


def make_payload(**changes):
    payload = {
        **METADATA,
        **MOCK_FIELDS,
        "review_status": "requires_human_review",
        "provider_kind": "mock",
    }
    payload.update(changes)
    return payload


class OfflineExtractionTests(unittest.TestCase):
    def test_schema_requires_references_quote_and_human_review(self):
        schema = load_output_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("supporting_quote", schema["required"])
        self.assertEqual(schema["properties"]["review_status"]["const"], "requires_human_review")

    def test_valid_mock_output_is_marked_mock_and_provisional(self):
        provider = DeterministicMockProvider(**MOCK_FIELDS)
        result = OfflineCandidateExtractor(provider).extract(PASSAGE, METADATA)
        self.assertEqual(result["provider_kind"], "mock")
        self.assertEqual(result["review_status"], "requires_human_review")
        self.assertEqual(result["supporting_quote"], PASSAGE)
        self.assertEqual(result["document_id"], METADATA["document_id"])
        self.assertIn("MOCK ONLY", result["uncertainty"])

    def test_invalid_structure_rejects_unknown_and_missing_fields(self):
        for payload in (
            {key: value for key, value in make_payload().items() if key != "potential_action"},
            make_payload(unapproved_field="not allowed"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ExtractionError, "exactly"):
                    validate_candidate_response(json.dumps(payload), PASSAGE, METADATA, provider_kind="mock")

    def test_missing_quote_is_rejected(self):
        with self.assertRaisesRegex(ExtractionError, "supporting_quote"):
            validate_candidate_response(
                json.dumps(make_payload(supporting_quote="")),
                PASSAGE,
                METADATA,
                provider_kind="mock",
            )

    def test_unsupported_quote_is_rejected(self):
        with self.assertRaises(ExtractionError) as caught:
            validate_candidate_response(
                json.dumps(make_payload(supporting_quote="A bank shall approve every loan.")),
                PASSAGE,
                METADATA,
                provider_kind="mock",
            )
        self.assertEqual(caught.exception.code, "unsupported_quote")

    def test_missing_source_ids_are_rejected_before_provider_runs(self):
        provider = DeterministicMockProvider()
        with self.assertRaises(ExtractionError) as caught:
            OfflineCandidateExtractor(provider).extract(PASSAGE, {"document_id": "DOC-ONLY"})
        self.assertEqual(caught.exception.code, "missing_source_ids")

    def test_human_verified_status_cannot_be_returned(self):
        with self.assertRaises(ExtractionError) as caught:
            validate_candidate_response(
                json.dumps(make_payload(review_status="verified")),
                PASSAGE,
                METADATA,
                provider_kind="mock",
            )
        self.assertEqual(caught.exception.code, "human_review_required")

    def test_candidate_must_be_grounded_in_quote(self):
        with self.assertRaises(ExtractionError) as caught:
            validate_candidate_response(
                json.dumps(make_payload(candidate_obligation="A bank must approve every loan.")),
                PASSAGE,
                METADATA,
                provider_kind="mock",
            )
        self.assertEqual(caught.exception.code, "unsupported_candidate")

    def test_malformed_json_and_provider_errors_are_sanitized(self):
        with self.assertRaises(ExtractionError) as caught:
            validate_candidate_response("{bad", PASSAGE, METADATA, provider_kind="mock")
        self.assertEqual(caught.exception.code, "invalid_json")

        class FailingProvider:
            provider_kind = "mock"

            def extract(self, passage, metadata):
                raise ExtractionProviderError("secret provider detail")

        with self.assertRaises(ExtractionError) as caught:
            OfflineCandidateExtractor(FailingProvider()).extract(PASSAGE, METADATA)
        self.assertEqual(caught.exception.code, "provider_error")
        self.assertNotIn("secret provider detail", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
