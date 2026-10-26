from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from backend.services.nvidia_llm import LLMError, LLMResponse
from backend.services.regulatory_analysis_workflow import run_phase5_analysis
from backend.services.structured_extraction import PROCESSED


def _evidence_row(evidence_id="DOC-CB-2025-CH3-P006"):
    with (PROCESSED / "evidence_passages.csv").open(encoding="utf-8-sig", newline="") as stream:
        return next(row for row in csv.DictReader(stream) if row["evidence_id"] == evidence_id)


def _output_for(evidence):
    quote = evidence["passage_text"][: min(120, len(evidence["passage_text"]))]
    return {
        "document_id": evidence["document_id"],
        "evidence_id": evidence["evidence_id"],
        "page": int(evidence["printed_page_number"] or evidence["page_number"]),
        "candidate_obligation": quote,
        "candidate_type": "explicit_requirement",
        "supporting_quotation": quote,
        "uncertainty": "Current legal applicability has not been reviewed.",
        "confidence": 0.8,
        "reasoning": "The selected wording is copied from the source passage.",
        "requires_human_review": True,
    }


def _client(output=None, *, side_effect=None):
    client = Mock()
    client.settings = SimpleNamespace(
        model="test/model",
        timeout_seconds=20,
        retry_count=2,
        extraction_timeout_seconds=60,
        extraction_max_tokens=1024,
        api_key="must-not-appear-in-audit",
    )
    if isinstance(side_effect, BaseException):
        client.complete.side_effect = side_effect
    elif side_effect is not None:
        client.complete.return_value = side_effect
    else:
        client.complete.return_value = LLMResponse(json.dumps(output), "test/model", {"total_tokens": 10}, 12.0, 0, "stop")
    return client


class Phase5WorkflowTests(unittest.TestCase):
    def test_success_returns_grounded_pending_candidate_without_writing_obligations(self):
        evidence = _evidence_row()
        client = _client(_output_for(evidence))
        obligations = PROCESSED / "obligations.csv"
        before = obligations.read_bytes()

        result = run_phase5_analysis("DOC-CB-2025", [evidence["evidence_id"]], client=client)

        self.assertEqual(result["workflow_status"], "pending_human_review")
        self.assertEqual(result["document_status"], "VERIFY_CURRENT_STATUS")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["supporting_quotation"], _output_for(evidence)["supporting_quotation"])
        self.assertEqual(candidate["candidate_type"], "explicit_requirement")
        self.assertEqual(candidate["model_proposed_obligation"], _output_for(evidence)["candidate_obligation"])
        self.assertEqual(candidate["review_status"], "pending_human_review")
        self.assertEqual(candidate["verification_status"], "unverified_candidate")
        self.assertEqual(candidate["activation_status"], "inactive")
        self.assertNotIn("must-not-appear", json.dumps(result))
        self.assertEqual(obligations.read_bytes(), before)
        self.assertEqual(client.complete.call_count, 1)
        self.assertEqual(client.complete.call_args.kwargs["timeout_seconds"], 60)
        self.assertEqual(client.complete.call_args.kwargs["max_tokens"], 1024)
        self.assertEqual(client.complete.call_args.kwargs["retry_count"], 0)
        self.assertEqual(client.complete.call_args.kwargs["reasoning_effort"], "low")

    def test_p006_linewrapped_model_quote_is_returned_as_exact_source_slice(self):
        evidence = _evidence_row()
        source = evidence["passage_text"]
        quote_start = source.index("A bank shall conduct enhanced due diligence")
        quote_end = source.index("\n(3)", quote_start)
        source_quote = source[quote_start:quote_end].rstrip()
        model_quote = " ".join(source_quote.split())
        output = _output_for(evidence)
        output.update({
            "candidate_obligation": model_quote,
            "supporting_quotation": model_quote,
        })
        client = _client(output)

        result = run_phase5_analysis("DOC-CB-2025", [evidence["evidence_id"]], client=client)

        self.assertEqual(result["workflow_status"], "pending_human_review")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["supporting_quotation"], source_quote)
        self.assertEqual(candidate["source_wording"], source_quote)
        self.assertEqual(candidate["model_supporting_quotation"], model_quote)
        self.assertEqual(source[candidate["source_quote_start"]:candidate["source_quote_end"]], source_quote)
        self.assertEqual(candidate["proposed_obligation"], source_quote)
        self.assertEqual(candidate["model_proposed_obligation"], model_quote)
        self.assertEqual(source[candidate["candidate_source_start"]:candidate["candidate_source_end"]], source_quote)
        self.assertEqual(candidate["activation_status"], "inactive")

    def test_extraction_timeout_is_configurable_and_hard_capped(self):
        evidence = _evidence_row()
        client = _client(_output_for(evidence))
        client.settings.extraction_timeout_seconds = 180

        result = run_phase5_analysis("DOC-CB-2025", [evidence["evidence_id"]], client=client)

        self.assertEqual(result["workflow_status"], "pending_human_review")
        self.assertEqual(client.complete.call_args.kwargs["timeout_seconds"], 120)
        self.assertEqual(result["audit"]["model_parameters"]["timeout_seconds"], 120)

    def test_malformed_output_is_rejected(self):
        evidence = _evidence_row()
        client = _client(side_effect=LLMResponse("not JSON", "test/model", None, 1.0, 0))

        result = run_phase5_analysis("DOC-CB-2025", [evidence["evidence_id"]], client=client)

        self.assertEqual(result["workflow_status"], "failed")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any(error["code"] == "malformed_json" for error in result["validation_errors"]))

    def test_unsupported_evidence_is_rejected_before_model_call(self):
        client = _client(_output_for(_evidence_row()))

        result = run_phase5_analysis("DOC-CB-2025", ["NOT-IN-DATASET"], client=client)

        self.assertEqual(result["workflow_status"], "failed")
        self.assertTrue(any(error["code"] == "unsupported_evidence" for error in result["validation_errors"]))
        client.complete.assert_not_called()

    def test_timeout_is_captured_without_retry_or_sensitive_error_text(self):
        evidence = _evidence_row()
        client = _client(side_effect=LLMError("timeout", "NVIDIA request timed out", retryable=True))

        result = run_phase5_analysis("DOC-CB-2025", [evidence["evidence_id"]], client=client)

        self.assertEqual(result["workflow_status"], "failed")
        self.assertTrue(any(error["code"] == "timeout" for error in result["validation_errors"]))
        self.assertEqual(client.complete.call_count, 1)
        self.assertNotIn("api_key", json.dumps(result).lower())

    def test_token_limit_completion_is_incomplete_not_malformed_json(self):
        evidence = _evidence_row()
        client = _client(side_effect=LLMResponse(None, "test/model", None, 1.0, 0, "length"))

        result = run_phase5_analysis("DOC-CB-2025", [evidence["evidence_id"]], client=client)

        self.assertEqual(result["workflow_status"], "failed")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any(error["code"] == "incomplete_completion" for error in result["validation_errors"]))

    def test_superseded_document_is_blocked_before_model_call(self):
        client = _client(_output_for(_evidence_row()))

        result = run_phase5_analysis("DOC-DL-2022", ["any-id"], client=client)

        self.assertEqual(result["workflow_status"], "blocked_superseded")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["document_status"], None)
        client.complete.assert_not_called()

    def test_unverifiable_quote_is_rejected_and_candidate_never_passes_review_gate(self):
        evidence = _evidence_row()
        output = _output_for(evidence)
        output["supporting_quotation"] = "A quote that does not occur in the source."
        client = _client(output)

        result = run_phase5_analysis("DOC-CB-2025", [evidence["evidence_id"]], client=client)

        self.assertEqual(result["workflow_status"], "failed")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any(error["code"] == "ungrounded_quotation" for error in result["validation_errors"]))

    def test_evidence_count_is_bounded(self):
        client = _client(_output_for(_evidence_row()))

        result = run_phase5_analysis("DOC-CB-2025", ["a", "b", "c", "d"], client=client)

        self.assertEqual(result["workflow_status"], "failed")
        self.assertTrue(any(error["code"] == "evidence_limit_exceeded" for error in result["validation_errors"]))
        client.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
