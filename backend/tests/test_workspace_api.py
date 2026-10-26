from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.services.document_analyzer import app

PROJECT = Path(__file__).resolve().parents[2]


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, client=("127.0.0.1", 54321))

    def test_registry_and_evidence_endpoints_return_source_backed_records(self):
        documents = self.client.get("/documents")
        evidence = self.client.get("/evidence")
        self.assertEqual(documents.status_code, 200)
        self.assertEqual(evidence.status_code, 200)
        self.assertTrue(all(row.get("document_id") for row in documents.json()["documents"]))
        self.assertTrue(all(row.get("evidence_id") and row.get("passage_text") for row in evidence.json()["evidence"]))
        statuses = {row["document_id"]: row.get("document_status", "") for row in documents.json()["documents"]}
        for passage in evidence.json()["evidence"]:
            with self.subTest(document_id=passage["document_id"], evidence_id=passage["evidence_id"]):
                self.assertEqual(passage.get("document_status", ""), statuses.get(passage["document_id"], ""))

    def test_candidate_listing_is_read_only_and_returns_review_status(self):
        queue = PROJECT / "data" / "processed" / "obligation_review_queue.csv"
        before = queue.read_bytes()
        response = self.client.get("/candidates")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["count"], len(payload["candidates"]))
        if payload["candidates"]:
            self.assertIn(payload["candidates"][0]["review_decision"], {"pending", "verified", "rejected"})
            self.assertTrue(payload["candidates"][0]["evidence_id"])
        self.assertEqual(queue.read_bytes(), before)

    def test_audit_and_governance_endpoints_report_authentication_limits(self):
        audit = self.client.get("/audit-trail")
        governance = self.client.get("/governance/overview")
        self.assertEqual(audit.status_code, 200)
        self.assertFalse(audit.json()["tamper_proof"])
        self.assertFalse(audit.json()["authenticated_identity"])
        self.assertEqual(governance.status_code, 200)
        self.assertEqual(governance.json()["rule_count"], len(governance.json()["rules"]))

    def test_local_data_routes_reject_non_loopback_requests(self):
        remote_client = TestClient(app, client=("192.0.2.10", 54321))
        for path in ("/candidates", "/audit-trail", "/governance/overview"):
            with self.subTest(path=path):
                self.assertEqual(remote_client.get(path).status_code, 403)

    def test_extraction_route_invokes_existing_workflow_without_persisting_results(self):
        fake_result = {
            "workflow_status": "pending_human_review",
            "run_id": "test-run",
            "document_id": "DOC-TEST",
            "document_status": "VERIFY_CURRENT_STATUS",
            "candidates": [],
            "validation_errors": [],
            "audit": {"run_id": "test-run", "candidate_count": 0},
        }
        with patch.dict("sys.modules", {"backend.services.regulatory_analysis_workflow": type("Workflow", (), {"run_phase5_analysis": staticmethod(lambda document_id, evidence_ids: fake_result)})}):
            response = self.client.post("/candidates/extract", json={"document_id": "DOC-TEST", "evidence_ids": ["EVID-TEST"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["run_id"], "test-run")
        self.assertEqual(response.json()["candidates"], [])


if __name__ == "__main__":
    unittest.main()
