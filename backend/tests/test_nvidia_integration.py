from __future__ import annotations

import json
import logging
import unittest
import csv
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

from backend.config import NvidiaSettings
from backend.services.nvidia_health import check_nvidia_health
from backend.services.nvidia_llm import LLMClient, LLMError, LLMResponse
from backend.services.structured_extraction import (
    ExtractionError,
    extract_candidate_obligation,
    validate_extraction_response,
)


def settings(**changes) -> NvidiaSettings:
    values = {
        "api_key": "test-secret-key",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "test/model",
        "timeout_seconds": 2,
        "retry_count": 1,
        "max_tokens": 64,
        "temperature": 0.0,
        "json_mode": True,
    }
    values.update(changes)
    return NvidiaSettings(**values)


def successful_opener(content: str | None = '{"candidate": "value"}', usage=None, finish_reason="stop", reasoning_content=None):
    message = {"content": content}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    payload = {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": usage or {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
    }
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(payload).encode()
    return Mock(return_value=response)


class NvidiaClientTests(unittest.TestCase):
    def test_missing_api_key_is_structured_and_health_does_not_crash(self):
        for api_key in ("", " \t\n "):
            with self.subTest(api_key=repr(api_key)):
                opener = Mock()
                client = LLMClient(settings(api_key=api_key), opener=opener)
                with self.assertRaises(LLMError) as caught:
                    client.complete("hello")
                self.assertEqual(caught.exception.code, "missing_api_key")
                self.assertEqual(opener.call_count, 0)
        health = check_nvidia_health(LLMClient(settings(api_key=""), opener=Mock()))
        self.assertEqual(health["status"], "not_configured")

    def test_authorization_header_uses_configured_bearer_credential(self):
        credential = "offline-test-credential"
        opener = successful_opener()
        LLMClient(settings(api_key=credential), opener=opener).complete("hello")
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), f"Bearer {credential}")

    def test_api_unavailable_health_is_structured(self):
        opener = Mock(side_effect=URLError("offline"))
        client = LLMClient(settings(), opener=opener)
        health = check_nvidia_health(client)
        self.assertEqual(health["status"], "unavailable")
        self.assertFalse(health["network_connected"])
        self.assertEqual(health["error"]["code"], "network_error")
        self.assertEqual(opener.call_count, 1)

    def test_generic_http_error_does_not_claim_model_is_unavailable(self):
        opener = Mock(side_effect=HTTPError("https://example.test", 400, "Bad Request", {}, None))
        health = check_nvidia_health(LLMClient(settings(), opener=opener))
        self.assertEqual(health["error"]["code"], "provider_error")
        self.assertEqual(health["error"]["http_status"], 400)
        self.assertIsNone(health["model_available"])

    def test_success_status_error_envelope_is_provider_error_not_model_absence(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"error": {"code": "request_rejected", "message": "redacted"}}).encode()
        health = check_nvidia_health(LLMClient(settings(), opener=Mock(return_value=response)))
        self.assertEqual(health["error"]["code"], "provider_error")
        self.assertEqual(health["error"]["http_status"], 200)
        self.assertIsNone(health["model_available"])

    def test_invalid_response_shape_does_not_claim_model_is_unavailable(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"unexpected": True}).encode()
        health = check_nvidia_health(LLMClient(settings(), opener=Mock(return_value=response)))
        self.assertEqual(health["error"]["code"], "invalid_provider_response")
        self.assertIsNone(health["model_available"])

    def test_invalid_api_key_is_not_retried(self):
        opener = Mock(side_effect=HTTPError("https://example.test", 401, "Unauthorized", {}, None))
        client = LLMClient(settings(), opener=opener)
        with self.assertRaises(LLMError) as caught:
            client.complete("hello")
        self.assertEqual(caught.exception.code, "authentication_failed")
        self.assertEqual(opener.call_count, 1)

    def test_timeout_retries_then_returns_structured_error(self):
        opener = Mock(side_effect=TimeoutError("private detail"))
        client = LLMClient(settings(retry_count=2), opener=opener)
        with self.assertRaises(LLMError) as caught:
            client.complete("hello")
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(caught.exception.retry_count, 2)
        self.assertEqual(opener.call_count, 3)
        self.assertNotIn("private detail", caught.exception.message)

    def test_transient_network_error_retries_and_succeeds(self):
        opener = Mock(side_effect=[URLError("offline"), successful_opener()()])
        client = LLMClient(settings(), opener=opener)
        response = client.complete("hello", system_prompt="system")
        self.assertEqual(response.retry_count, 1)
        self.assertEqual(response.usage["total_tokens"], 7)
        request = opener.call_args_list[0].args[0]
        request_json = json.loads(request.data.decode())
        self.assertEqual(request_json["response_format"], {"type": "json_object"})
        self.assertEqual(request_json["messages"][0]["role"], "system")

    def test_health_check_uses_reasoning_safe_completion_cap(self):
        opener = successful_opener(content="OK")
        health = check_nvidia_health(LLMClient(settings(timeout_seconds=20, retry_count=2), opener=opener))
        self.assertEqual(health["status"], "available")
        payload = json.loads(opener.call_args.args[0].data.decode())
        self.assertEqual(payload["max_tokens"], 256)
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertNotIn("response_format", payload)
        self.assertEqual(opener.call_args.kwargs["timeout"], 15)
        self.assertEqual(opener.call_count, 1)

    def test_null_content_with_length_finish_is_incomplete_and_reasoning_is_not_final(self):
        opener = successful_opener(content=None, finish_reason="length", reasoning_content="OK")
        health = check_nvidia_health(LLMClient(settings(retry_count=2), opener=opener))
        self.assertEqual(health["status"], "unavailable")
        self.assertEqual(health["error"]["code"], "incomplete_completion")
        self.assertEqual(health["error"]["finish_reason"], "length")
        self.assertEqual(health["model_available"], True)
        self.assertEqual(health["retry_count"], 0)
        self.assertEqual(opener.call_count, 1)

    def test_health_timeout_is_single_bounded_attempt(self):
        opener = Mock(side_effect=TimeoutError("private detail"))
        health = check_nvidia_health(LLMClient(settings(timeout_seconds=20, retry_count=2), opener=opener))
        self.assertEqual(health["error"]["code"], "timeout")
        self.assertEqual(health["retry_count"], 0)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(opener.call_args.kwargs["timeout"], 15)

    def test_invalid_provider_json_is_rejected(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"not-json"
        client = LLMClient(settings(), opener=Mock(return_value=response))
        with self.assertRaises(LLMError) as caught:
            client.complete("hello")
        self.assertEqual(caught.exception.code, "invalid_provider_response")

    def test_api_key_never_appears_in_logs(self):
        credential = "offline-secret-for-log-test"
        output = []

        class Capture(logging.Handler):
            def emit(self, record):
                output.append(record.getMessage())

        logger = logging.getLogger("backend.services.nvidia_llm")
        handler = Capture()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            LLMClient(settings(api_key=credential), opener=successful_opener()).complete("private prompt")
        finally:
            logger.removeHandler(handler)
        self.assertTrue(output)
        self.assertNotIn(credential, "".join(output))
        self.assertNotIn("private prompt", "".join(output))

    def test_api_key_never_appears_in_provider_exception(self):
        credential = "offline-secret-for-error-test"
        opener = Mock(side_effect=HTTPError("https://example.test", 401, credential, {}, None))
        with self.assertRaises(LLMError) as caught:
            LLMClient(settings(api_key=credential), opener=opener).complete("hello")
        self.assertEqual(caught.exception.code, "authentication_failed")
        self.assertNotIn(credential, str(caught.exception))
        self.assertNotIn(credential, caught.exception.message)


class ExtractionValidationTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            "document_id": "DOC-CB-2025",
            "evidence_id": "EVID-1",
            "printed_page_number": "18",
            "passage_text": "A candidate statement.",
        }
        self.valid = {
            "document_id": "DOC-CB-2025",
            "evidence_id": "EVID-1",
            "page": 18,
            "candidate_obligation": "A candidate statement.",
            "candidate_type": "explicit_requirement",
            "supporting_quotation": "A candidate statement.",
            "uncertainty": "Applicability not assessed.",
            "confidence": 0.82,
            "reasoning": "The passage uses mandatory language.",
            "requires_human_review": True,
        }

    def test_valid_structured_response(self):
        result = validate_extraction_response(json.dumps(self.valid), self.evidence)
        self.assertTrue(result["requires_human_review"])
        self.assertEqual(result["supporting_quotation"], self.evidence["passage_text"])
        self.assertEqual(result["source_quote_start"], 0)
        self.assertEqual(result["source_quote_end"], len(self.evidence["passage_text"]))

    def test_linewrap_whitespace_is_mapped_to_exact_source_quote_and_offsets(self):
        self.evidence["passage_text"] = "The bank shall\nmaintain records."
        self.valid["candidate_obligation"] = "The bank shall maintain records."
        self.valid["supporting_quotation"] = "The bank shall maintain records."

        result = validate_extraction_response(json.dumps(self.valid), self.evidence)

        self.assertEqual(result["model_supporting_quotation"], "The bank shall maintain records.")
        self.assertEqual(result["supporting_quotation"], "The bank shall\nmaintain records.")
        start, end = result["source_quote_start"], result["source_quote_end"]
        self.assertEqual(self.evidence["passage_text"][start:end], result["supporting_quotation"])
        self.assertEqual(result["candidate_obligation"], "The bank shall\nmaintain records.")

    def test_canonically_equivalent_unicode_quote_maps_to_original_source(self):
        self.evidence["passage_text"] = "Caf\u00e9 shall apply."
        self.valid["candidate_obligation"] = "Cafe\u0301 shall apply."
        self.valid["supporting_quotation"] = "Cafe\u0301 shall apply."

        result = validate_extraction_response(json.dumps(self.valid), self.evidence)

        self.assertEqual(result["supporting_quotation"], "Caf\u00e9 shall apply.")
        start, end = result["source_quote_start"], result["source_quote_end"]
        self.assertEqual(self.evidence["passage_text"][start:end], "Caf\u00e9 shall apply.")

    def test_missing_interword_space_is_not_treated_as_whitespace_normalization(self):
        self.evidence["passage_text"] = "Review data privacy safeguards."
        self.valid["candidate_obligation"] = "Review data privacy safeguards."
        self.valid["supporting_quotation"] = "Review dataprivacy safeguards."

        with self.assertRaises(ExtractionError) as caught:
            validate_extraction_response(json.dumps(self.valid), self.evidence)
        self.assertEqual(caught.exception.code, "ungrounded_quotation")

    def test_unicode_punctuation_variants_are_not_silently_normalized(self):
        self.evidence["passage_text"] = "The borrower’s account must be reviewed."
        self.valid["candidate_obligation"] = "The borrower's account must be reviewed."
        self.valid["supporting_quotation"] = "The borrower's account must be reviewed."

        with self.assertRaises(ExtractionError) as caught:
            validate_extraction_response(json.dumps(self.valid), self.evidence)
        self.assertEqual(caught.exception.code, "ungrounded_quotation")

    def test_explicit_candidate_must_map_and_is_preserved_separately_from_source_derivation(self):
        self.evidence["passage_text"] = "The bank shall maintain data privacy records."
        self.valid["supporting_quotation"] = self.evidence["passage_text"]
        self.valid["candidate_obligation"] = "The bank shall maintain dataprivacy records."

        with self.assertRaises(ExtractionError) as caught:
            validate_extraction_response(json.dumps(self.valid), self.evidence)
        self.assertEqual(caught.exception.code, "unsupported_candidate")

    def test_explicit_candidate_whitespace_variant_retains_model_text_and_source_offsets(self):
        self.evidence["passage_text"] = "The bank shall\nmaintain records."
        self.valid["candidate_obligation"] = "The bank shall maintain records."
        self.valid["supporting_quotation"] = "The bank shall maintain records."

        result = validate_extraction_response(json.dumps(self.valid), self.evidence)

        self.assertEqual(result["model_candidate_obligation"], "The bank shall maintain records.")
        self.assertEqual(result["candidate_obligation"], "The bank shall\nmaintain records.")
        start, end = result["candidate_source_start"], result["candidate_source_end"]
        self.assertEqual(self.evidence["passage_text"][start:end], result["candidate_obligation"])

    def test_unicode_normalization_does_not_match_partial_source_character(self):
        self.evidence["passage_text"] = "Caf\u00e9 shall apply."
        self.valid["candidate_obligation"] = "Cafe"
        self.valid["supporting_quotation"] = "e"

        with self.assertRaises(ExtractionError) as caught:
            validate_extraction_response(json.dumps(self.valid), self.evidence)
        self.assertEqual(caught.exception.code, "ungrounded_quotation")

    def test_paraphrase_or_invented_quote_is_rejected(self):
        self.valid["supporting_quotation"] = "A bank must preserve all documents."
        self.valid["candidate_obligation"] = "A bank must preserve all documents."

        with self.assertRaises(ExtractionError) as caught:
            validate_extraction_response(json.dumps(self.valid), self.evidence)
        self.assertEqual(caught.exception.code, "ungrounded_quotation")

    def test_wrong_evidence_mapping_is_rejected_before_quote_matching(self):
        payload = dict(self.valid, evidence_id="EVID-2")

        with self.assertRaises(ExtractionError) as caught:
            validate_extraction_response(json.dumps(payload), self.evidence)
        self.assertEqual(caught.exception.code, "reference_mismatch")

    def test_malformed_json_rejected(self):
        with self.assertRaisesRegex(ExtractionError, "valid JSON"):
            validate_extraction_response("{bad", self.evidence)

    def test_missing_fields_rejected(self):
        with self.assertRaisesRegex(ExtractionError, "exactly"):
            validate_extraction_response(json.dumps({"document_id": "DOC-CB-2025"}), self.evidence)

    def test_reference_page_confidence_and_review_are_validated(self):
        for key, value, code in (
            ("document_id", "DOC-DL-2022", "reference_mismatch"),
            ("page", 19, "page_mismatch"),
            ("confidence", 1.1, "invalid_confidence"),
            ("requires_human_review", False, "human_review_required"),
        ):
            with self.subTest(field=key):
                payload = dict(self.valid)
                payload[key] = value
                with self.assertRaises(ExtractionError) as caught:
                    validate_extraction_response(json.dumps(payload), self.evidence)
                self.assertEqual(caught.exception.code, code)

    def test_extraction_uses_validated_phase2_evidence_and_does_not_write_obligations(self):
        from backend.services.structured_extraction import PROCESSED

        with (PROCESSED / "evidence_passages.csv").open(encoding="utf-8-sig", newline="") as stream:
            evidence = next(row for row in csv.DictReader(stream) if row["passage_text"].strip())
        page = int(evidence["printed_page_number"] or evidence["page_number"])
        payload = {
            "document_id": evidence["document_id"],
            "evidence_id": evidence["evidence_id"],
            "page": page,
            "candidate_obligation": "",
            "candidate_type": "no_candidate",
            "supporting_quotation": "",
            "uncertainty": "The selected passage is a heading.",
            "confidence": 0.1,
            "reasoning": "The selected passage is not a standalone obligation.",
            "requires_human_review": True,
        }
        client = Mock()
        client.complete.return_value = LLMResponse(json.dumps(payload), "test/model", None, 1.0, 0)
        obligations_path = PROCESSED / "obligations.csv"
        before = obligations_path.read_bytes()
        result = extract_candidate_obligation(evidence["evidence_id"], client=client)
        self.assertTrue(result["requires_human_review"])
        self.assertEqual(obligations_path.read_bytes(), before)

    def test_null_or_token_limited_extraction_is_incomplete_and_ignores_reasoning(self):
        from backend.services.structured_extraction import PROCESSED

        with (PROCESSED / "evidence_passages.csv").open(encoding="utf-8-sig", newline="") as stream:
            evidence = next(row for row in csv.DictReader(stream) if row["passage_text"].strip())
        for response in (
            LLMResponse(None, "test/model", None, 1.0, 0, "length"),
            LLMResponse("{", "test/model", None, 1.0, 0, "length"),
        ):
            client = Mock()
            client.complete.return_value = response
            with self.subTest(finish_reason=response.finish_reason, content=response.content):
                with self.assertRaises(ExtractionError) as caught:
                    extract_candidate_obligation(evidence["evidence_id"], client=client)
                self.assertEqual(caught.exception.code, "incomplete_completion")

    def test_extraction_timeout_is_reported_with_sanitized_code(self):
        from backend.services.structured_extraction import PROCESSED

        with (PROCESSED / "evidence_passages.csv").open(encoding="utf-8-sig", newline="") as stream:
            evidence = next(row for row in csv.DictReader(stream) if row["passage_text"].strip())
        client = Mock()
        client.complete.side_effect = LLMError("timeout", "NVIDIA request timed out")
        with self.assertRaises(ExtractionError) as caught:
            extract_candidate_obligation(evidence["evidence_id"], client=client)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertNotIn("secret", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
