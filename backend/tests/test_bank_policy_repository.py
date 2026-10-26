"""Tests for the local, unverified bank-policy import repository."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.services import bank_policy_repository as repo
from backend.services.document_analyzer import app


def policy_export(*, policy_id="POL-001", version="1.0", status="draft", effective_from="2025-01-01", include_fields=True):
    policy = {"policy_id": policy_id}
    if include_fields:
        policy.update({"policy_title": "Source supplied title", "policy_category": "Source supplied category", "owning_organization": "Source supplied unit", "source_document_reference": "source.pdf#section-2"})
    policy["versions"] = [{
        "version": version,
        "effective_from": effective_from,
        "effective_to": None,
        "status": status,
        "approval_details": [],
        "clauses": [{"clause_id": "CL-001", "clause_title": "Clause heading", "clause_text": "A bank must review this policy clause before processing.", "section": "2", "paragraph": "2.1", "page": "4", "source_document_reference": "source.pdf#section-2"}],
    }]
    return {"policies": [policy]}


class BankPolicyRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "policy_repository.json"

    def tearDown(self):
        self.temp.cleanup()

    def import_json(self, payload):
        return repo.prepare_import("bank-export.json", json.dumps(payload).encode(), self.path)

    def test_missing_repository_is_empty_and_has_no_fake_records(self):
        result = repo.list_policy_records(self.path)
        self.assertEqual(result["repository_status"], "empty")
        self.assertEqual(result["policies"], [])
        self.assertIn("No bank policies have been imported yet", result["message"])
        self.assertFalse(self.path.exists())

    def test_preview_does_not_write_and_commit_keeps_source_claims_unverified(self):
        payload = policy_export()
        payload["policies"][0]["versions"][0]["verification_status"] = "verified"
        payload["policies"][0]["versions"][0]["clauses"][0]["verification_status"] = "verified"
        source = json.dumps(payload).encode()
        before = source[:]
        preview = repo.prepare_import("authoritative-export.json", source, self.path)
        self.assertEqual(source, before)
        self.assertFalse(self.path.exists())
        self.assertEqual(preview["summary"]["accepted_records"], 1)
        saved = repo.commit_import(preview["preview_id"], self.path)
        self.assertTrue(saved["saved"])
        record = repo.load_repository(self.path)["policies"][0]
        version = record["versions"][0]
        self.assertEqual(version["provenance"]["verification_status"], "unverified_import")
        self.assertEqual(version["provenance"]["source_claimed_verification_status"], "verified")
        self.assertEqual(version["provenance"]["source_filename"], "authoritative-export.json")
        self.assertEqual(version["clauses"][0]["verification_status"], "unverified_import")
        self.assertEqual(version["clauses"][0]["source_claimed_verification_status"], "verified")
        self.assertEqual(version["clauses"][0]["clause_text"], policy_export()["policies"][0]["versions"][0]["clauses"][0]["clause_text"])

    def test_missing_metadata_is_reported_incomplete_not_invented(self):
        preview = self.import_json(policy_export(include_fields=False, effective_from=None))
        self.assertEqual(preview["summary"]["incomplete_policy_versions"], 1)
        self.assertEqual(preview["summary"]["rejected_records"], 0)
        self.assertIn("policy_title missing", preview["incomplete"][0]["missing_information"])
        self.assertIn("effective_from missing", preview["incomplete"][0]["missing_information"])
        self.assertIsNone(preview["incomplete"][0]["policy_title"])

    def test_malformed_identifiers_status_and_date_are_rejected(self):
        malformed = policy_export()
        malformed["policies"][0]["versions"][0]["status"] = "active-ish"
        result = self.import_json(malformed)
        self.assertEqual(result["summary"]["rejected_records"], 1)
        self.assertEqual(result["summary"]["accepted_records"], 0)
        self.assertTrue(any("status must be" in e["message"] for e in result["errors"]))

    def test_malformed_date_and_invalid_effective_range_are_rejected(self):
        malformed = policy_export()
        malformed["policies"][0]["versions"][0]["effective_from"] = "31/01/2025"
        result = self.import_json(malformed)
        self.assertEqual(result["summary"]["rejected_records"], 1)
        self.assertTrue(any("YYYY-MM-DD" in e["message"] for e in result["errors"]))
        malformed["policies"][0]["versions"][0]["effective_from"] = "2025-02-02"
        malformed["policies"][0]["versions"][0]["effective_to"] = "2025-02-01"
        result = self.import_json(malformed)
        self.assertTrue(any("on or after" in e["message"] for e in result["errors"]))

    def test_duplicate_clause_and_existing_version_are_not_overwritten(self):
        payload = policy_export()
        payload["policies"][0]["versions"][0]["clauses"].append(dict(payload["policies"][0]["versions"][0]["clauses"][0]))
        preview = self.import_json(payload)
        self.assertEqual(preview["summary"]["duplicate_records"], 1)
        self.assertEqual(preview["summary"]["rejected_policy_versions"], 1)
        clean = self.import_json(policy_export())
        repo.commit_import(clean["preview_id"], self.path)
        duplicate_version = self.import_json(policy_export())
        self.assertEqual(duplicate_version["summary"]["duplicate_records"], 1)
        self.assertEqual(duplicate_version["summary"]["accepted_records"], 0)
        self.assertEqual(len(repo.load_repository(self.path)["policies"][0]["versions"]), 1)

    def test_distinct_versions_are_preserved_and_identity_mismatch_rejected(self):
        first = self.import_json(policy_export(version="1.0"))
        repo.commit_import(first["preview_id"], self.path)
        second = self.import_json(policy_export(version="2.0", effective_from="2026-01-01"))
        repo.commit_import(second["preview_id"], self.path)
        self.assertEqual([v["version"] for v in repo.load_repository(self.path)["policies"][0]["versions"]], ["1.0", "2.0"])
        changed = policy_export(version="3.0")
        changed["policies"][0]["policy_title"] = "Different title for same stable ID"
        result = self.import_json(changed)
        self.assertEqual(result["summary"]["rejected_records"], 1)
        self.assertIn("differs from the existing stable policy identity", result["errors"][0]["message"])

    def test_approved_source_claim_stays_unverified_and_never_becomes_active(self):
        payload = policy_export(status="approved")
        version = payload["policies"][0]["versions"][0]
        version["approval_details"] = [{"approval_reference": "APR-SOURCE-1", "approver": "Source supplied approver", "approval_date": "2025-01-01", "decision": "approved"}]
        version["clauses"][0]["executable_rule_definition"] = {"condition": {"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 10}, "action": {"action_type": "deny"}}
        payload["policies"][0]["source_verification_status"] = "verified"
        preview = self.import_json(payload)
        repo.commit_import(preview["preview_id"], self.path)
        match = repo.policy_clause_matches("A bank must review this policy clause before processing.", exact_text="A bank must review this policy clause before processing.", path=self.path)
        self.assertEqual(len(match["screening_matches"]), 1)
        self.assertEqual(match["active_verified_matches"], [])
        self.assertEqual(match["screening_matches"][0]["comparison_eligibility"], "screening_only_unverified_or_inactive")
        self.assertIsNone(match["screening_matches"][0]["executable_rule_definition"])
        self.assertEqual(match["screening_matches"][0]["executable_rule_verification_status"], "unverified_import")

    def test_verified_approved_effective_version_is_selected_only_after_review_metadata(self):
        payload = policy_export(status="approved")
        version = payload["policies"][0]["versions"][0]
        version["approval_details"] = [{"approval_reference": "APR-1", "approver": "reviewer", "approval_date": "2025-01-01", "decision": "approved"}]
        staged = self.import_json(payload)
        repo.commit_import(staged["preview_id"], self.path)
        stored = repo.load_repository(self.path)
        policy_version = stored["policies"][0]["versions"][0]
        policy_version["provenance"]["verification_status"] = "verified"
        policy_version["approval_details"][0]["provenance_status"] = "verified"
        policy_version["clauses"][0]["verification_status"] = "verified"
        self.path.write_text(json.dumps(stored), encoding="utf-8")
        match = repo.policy_clause_matches("A bank must review this policy clause before processing.", exact_text="A bank must review this policy clause before processing.", path=self.path)
        self.assertEqual(len(match["active_verified_matches"]), 1)
        self.assertEqual(match["active_verified_matches"][0]["policy_id"], "POL-001")

    def test_csv_import_and_malformed_file_handling(self):
        csv_text = "policy_id,version,status,clause_id,clause_text,policy_title,policy_category,effective_from,source_document_reference\nPOL-CSV,1,draft,CL-CSV,The policy shall be recorded.,CSV policy,Operations,2025-01-01,manual.pdf#1\n"
        result = repo.prepare_import("export.csv", csv_text.encode(), self.path)
        self.assertEqual(result["summary"]["accepted_records"], 1)
        with self.assertRaises(repo.BankPolicyRepositoryError):
            repo.prepare_import("broken.json", b"{oops", self.path)
        with self.assertRaises(repo.BankPolicyRepositoryError):
            repo.prepare_import("export.xlsx", b"not supported", self.path)

    def test_failed_commit_does_not_overwrite_a_version_added_after_preview(self):
        staged = self.import_json(policy_export())
        other = self.import_json(policy_export())
        repo.commit_import(other["preview_id"], self.path)
        with self.assertRaisesRegex(repo.BankPolicyRepositoryError, "never overwritten"):
            repo.commit_import(staged["preview_id"], self.path)
        self.assertEqual(len(repo.load_repository(self.path)["policies"][0]["versions"]), 1)

    def test_loopback_api_empty_state_and_remote_import_block(self):
        local = TestClient(app, client=("127.0.0.1", 54321))
        self.assertEqual(local.get("/bank-policies").json()["policy_count"], 0)
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "api-repository.json"
            source = json.dumps(policy_export()).encode()
            with patch("backend.services.document_analyzer.preview_bank_policy_import", side_effect=lambda name, data: repo.prepare_import(name, data, target)), patch("backend.services.document_analyzer.commit_bank_policy_import", side_effect=lambda token: repo.commit_import(token, target)):
                preview = local.post("/bank-policies/import/preview", files={"file": ("bank.csv.json", source, "application/json")})
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertFalse(target.exists())
                committed = local.post("/bank-policies/import/commit", json={"preview_id": preview.json()["preview_id"]})
                self.assertEqual(committed.status_code, 200, committed.text)
                self.assertTrue(target.exists())
        remote = TestClient(app, client=("192.0.2.8", 54321))
        blocked = remote.get("/bank-policies")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(remote.post("/bank-policies/import/preview", files={"file": ("empty.csv", b"a", "text/csv")}).status_code, 403)

    def test_policy_fixture_data_is_not_migrated_into_repository(self):
        self.assertEqual(repo.load_repository(self.path)["policies"], [])
        self.assertFalse(any(p.get("policy_id", "").startswith("SYN") for p in repo.load_repository(self.path)["policies"]))


if __name__ == "__main__":
    unittest.main()
