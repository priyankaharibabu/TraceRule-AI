"""Deterministic tests for proposed-rule intake, comparison, and local decisions."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from backend.services.document_analyzer import app
from backend.services.proposed_rule_analysis import record_local_decision
from backend.services import bank_policy_repository as bank_repo

PROJECT = Path(__file__).resolve().parents[2]


def make_pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(35, 35, 560, 760), text, fontsize=8)
    payload = document.tobytes()
    document.close()
    return payload


class ProposedRuleAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, client=("127.0.0.1", 54321))
        self.pdf = make_pdf(
            "A proposed lending rule states that transactions using the direct route shall be reviewed before processing."
        )

    def extract(self, payload: bytes | None = None) -> dict:
        response = self.client.post(
            "/proposed-rules/extract",
            files={"file": ("proposal.pdf", payload if payload is not None else self.pdf, "application/pdf")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def compare(self, extraction: dict, *, field="disbursement_route", operator="eq", value="direct", action="deny") -> dict:
        statement = extraction["statements"][0]
        response = self.client.post(
            "/proposed-rules/compare",
            json={
                "analysis_id": extraction["analysis_id"],
                "statement_id": statement["statement_id"],
                "rule_name": "Proposed route review",
                "purpose": "Review the route before processing.",
                "condition_field": field,
                "condition_operator": operator,
                "condition_value": value,
                "action_type": action,
                "action_message": "Review this sample only.",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_pdf_upload_extracts_verbatim_clause_and_page_reference(self) -> None:
        result = self.extract()
        self.assertEqual(result["pages_processed"], 1)
        self.assertTrue(result["statements"])
        self.assertIn("shall be reviewed", result["statements"][0]["quote"])
        self.assertEqual(result["statements"][0]["page"], 1)
        self.assertIsNone(result["statements"][0]["text_signals"]["possible_purpose_text"])

    def test_invalid_and_unreadable_pdfs_return_clear_errors(self) -> None:
        invalid = self.client.post("/proposed-rules/extract", files={"file": ("broken.pdf", b"not a pdf", "application/pdf")})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("could not be read", invalid.json()["detail"])
        blank_pdf = make_pdf("")
        unreadable = self.client.post("/proposed-rules/extract", files={"file": ("scan.pdf", blank_pdf, "application/pdf")})
        self.assertEqual(unreadable.status_code, 400)
        self.assertIn("No readable text", unreadable.json()["detail"])

    def test_exact_rbi_passage_is_only_possible_duplicate_wording(self) -> None:
        with (PROJECT / "data" / "processed" / "evidence_passages.csv").open(encoding="utf-8-sig", newline="") as stream:
            evidence = next(row for row in csv.DictReader(stream) if row["evidence_id"] == "DOC-CB-2025-CH3-P015")
        result = self.compare(self.extract(make_pdf(evidence["passage_text"])), field="loan_amount", value="50000", action="flag")
        exact = [match for match in result["rbi_matches"] if match["evidence_id"] == evidence["evidence_id"]]
        self.assertTrue(exact)
        self.assertEqual(exact[0]["classification"], "possible_duplicate_wording")
        self.assertIn("does not establish legal identity", exact[0]["why_related"])
        self.assertEqual(result["conflict_detection"]["status"], "cannot_determine")
        self.assertEqual(exact[0]["document_id"], "DOC-CB-2025")
        self.assertEqual(exact[0]["document_status"], "VERIFY_CURRENT_STATUS")
        self.assertEqual(result["comparison_questions"]["condition_action_equivalence"]["status"], "insufficient_evidence")
        self.assertEqual(result["comparison_questions"]["execution"]["status"], "unavailable")
        self.assertTrue(any(pair["wording_screening"]["category"] == "possible_duplicate_wording" for pair in result["rule_comparisons"]))
        self.assertTrue(all(pair["structured_comparison"]["category"] == "insufficient_evidence" for pair in result["rule_comparisons"]))
        self.assertEqual(result["activation_status"], "inactive")

    def test_keyword_overlap_is_needs_review_not_a_conflict_claim(self) -> None:
        with (PROJECT / "data" / "processed" / "evidence_passages.csv").open(encoding="utf-8-sig", newline="") as stream:
            source_quote = next(row["passage_text"] for row in csv.DictReader(stream) if row["evidence_id"] == "DOC-CB-2025-CH3-P015")
        related_but_not_exact = source_quote.replace("A bank and LSPs", "A lender and LSPs", 1)
        result = self.compare(self.extract(make_pdf(related_but_not_exact)))
        self.assertTrue(all(item["classification"] == "related_source_passage" for item in result["rbi_matches"]))
        self.assertIn("text overlap alone", " ".join(item["why_related"] for item in result["rbi_matches"]))
        self.assertEqual(result["conflict_detection"]["status"], "cannot_determine")

    def test_bank_rule_gap_is_explicit_and_no_match_does_not_claim_safety(self) -> None:
        unrelated = make_pdf("When an institution reviews a cedar shipment, it shall document the cedar grade.")
        result = self.compare(self.extract(unrelated), field="borrower_age", operator="gt", value="120", action="flag")
        self.assertFalse(result["bank_rules"]["available"])
        self.assertEqual(result["bank_rules"]["status"], "no_imported_policies")
        self.assertEqual(result["bank_rules"]["message"], "No bank policies have been imported yet. Internal policy comparison is unavailable.")
        self.assertEqual(result["assessment_status"], "comparison_unavailable")
        self.assertEqual(result["conflict_detection"]["status"], "cannot_determine")
        self.assertEqual(result["approval_status"], "not_performed")
        self.assertEqual(result["activation_status"], "inactive")

    def test_imported_unverified_clause_is_shown_as_screening_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository_path = Path(temp_dir) / "policy_repository.json"
            payload = {"policies": [{
                "policy_id": "POL-LOCAL-1", "policy_title": "Locally imported policy", "policy_category": "Operations",
                "source_document_reference": "policy-source.pdf#4", "versions": [{
                    "version": "1.0", "effective_from": "2025-01-01", "status": "approved", "approval_details": [
                        {"approval_reference": "APR-1", "approver": "source claim", "approval_date": "2025-01-01", "decision": "approved"}
                    ], "clauses": [{"clause_id": "CLAUSE-1", "clause_text": "A proposed lending rule states that transactions using the direct route shall be reviewed before processing."}]
                }]
            }]}
            preview = bank_repo.prepare_import("source-export.json", json.dumps(payload).encode(), repository_path)
            bank_repo.commit_import(preview["preview_id"], repository_path)

            def match_from_temp(query: str, exact_text: str = ""):
                return bank_repo.policy_clause_matches(query, exact_text=exact_text, path=repository_path)

            with patch("backend.services.proposed_rule_analysis.policy_clause_matches", side_effect=match_from_temp):
                result = self.compare(self.extract())
        self.assertEqual(len(result["bank_rules"]["screening_matches"]), 1)
        self.assertFalse(result["bank_rules"]["available"])
        self.assertEqual(result["bank_rules"]["screening_matches"][0]["policy_id"], "POL-LOCAL-1")
        self.assertEqual(result["bank_rules"]["screening_matches"][0]["comparison_eligibility"], "screening_only_unverified_or_inactive")
        self.assertEqual(result["transaction_comparison"]["available"], False)

    def test_real_analysis_does_not_read_synthetic_controls_or_claim_transaction_results(self) -> None:
        with patch("backend.services.nvidia_llm.LLMClient.complete", side_effect=AssertionError("live model call attempted")) as complete:
            with patch("backend.services.proposed_rule_analysis.load_rules", side_effect=AssertionError("synthetic rules accessed"), create=True):
                result = self.compare(self.extract())
        complete.assert_not_called()
        self.assertNotIn("synthetic_comparison", result)
        self.assertEqual(result["verified_rule_comparison"]["verified_obligation_count"], 0)
        self.assertEqual(result["verified_rule_comparison"]["executable_rule_count"], 0)
        self.assertEqual(result["verified_rule_comparison"]["status"], "no_verified_obligations")
        self.assertFalse(result["transaction_comparison"]["available"])
        self.assertEqual(result["transaction_comparison"]["transaction_count"], 0)
        self.assertIn("No transaction outcomes were generated", result["transaction_comparison"]["message"])

    def test_local_decision_records_notes_without_identity_or_activation(self) -> None:
        result = self.compare(self.extract())
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "decisions.jsonl"
            response = record_local_decision(
                analysis_id=result["analysis_id"],
                decision="approve_for_next_stage",
                notes="Review source status before any further step.",
                path=path,
            )
            saved = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(response["identity_status"], "not_authenticated")
        self.assertEqual(response["approval_status"], "not_performed")
        self.assertEqual(response["activation_status"], "inactive")
        self.assertIsNone(saved["authority_identity"])
        self.assertEqual(saved["authority_notes"], "Review source status before any further step.")
        self.assertEqual(saved["record_type"], "local_decision_only")
        self.assertEqual(saved["proposal_source"]["page"], 1)
        self.assertTrue(saved["proposal_source"]["quote_sha256"])
        self.assertEqual(saved["compared_rule_versions"], [])
        self.assertIsNone(saved["simulation_id"])
        self.assertIsNone(saved["simulation_summary"])
        self.assertNotIn("synthetic_rule_ids", saved)

    def test_unknown_decisions_are_rejected_and_proposal_never_persisted_as_rule(self) -> None:
        result = self.compare(self.extract())
        decisions_path = PROJECT / "data" / "processed" / "proposed_rule_decisions.jsonl"
        before = decisions_path.read_bytes() if decisions_path.exists() else None
        response = self.client.post(
            "/proposed-rules/decisions",
            json={"analysis_id": result["analysis_id"], "decision": "activate", "authority_notes": "", "authority_identity": "untrusted"},
        )
        self.assertEqual(response.status_code, 422)
        after = decisions_path.read_bytes() if decisions_path.exists() else None
        self.assertEqual(after, before, "An invalid decision must not create or alter local decision records")

    def test_decision_endpoint_rejects_non_local_connections(self) -> None:
        remote_client = TestClient(app, client=("192.0.2.10", 54321))
        response = remote_client.post(
            "/proposed-rules/decisions",
            json={"analysis_id": "not-a-session", "decision": "reject", "authority_notes": ""},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
