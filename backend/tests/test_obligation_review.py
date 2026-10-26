from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from backend.services.review_obligation_candidates import (
    CANDIDATE_AUDIT_FILE,
    CANDIDATE_FILE,
    EVIDENCE_FILE,
    OBLIGATIONS_FILE,
    QUEUE_FILE,
    REGISTRY_FILE,
    QUEUE_FIELDS,
    ReviewError,
    append_extracted_candidates,
    export_review_queue,
    list_candidate_records,
    review_candidate,
)


class ObligationReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.candidate = {
            "candidate_id": "CAND-TEST-0001",
            "evidence_id": "EVID-TEST-0001",
            "document_id": "DOC-TEST",
            "source_filename": "sample.pdf",
            "source_page": "8",
            "source_section_hint": "A. Sample",
            "original_text": "Complete source text with a requirement.",
            "matched_language": "shall",
            "candidate_reason": "Test only.",
            "normalized_requirement": "",
            "responsible_party": "",
            "applicability": "",
            "conditions": "",
            "exceptions": "",
            "verification_status": "pending_review",
            "review_notes": "",
            "paragraph_reference": "6(1)",
            "candidate_text": "A bank shall perform the reviewed action.",
            "candidate_type": "mandatory_requirement_candidate",
            "review_status": "needs_review",
        }
        self.evidence = {
            "evidence_id": "EVID-TEST-0001",
            "document_id": "DOC-TEST",
            "pdf_page_index": "7",
            "printed_page_number": "8",
            "passage_text": "6. Sample\nA bank shall perform the reviewed action.\nAssociated context.",
        }
        self.registry = {"document_id": "DOC-TEST", "filename": "sample.pdf"}
        self._write(CANDIDATE_FILE, [self.candidate], list(self.candidate))
        self._write(EVIDENCE_FILE, [self.evidence], list(self.evidence))
        self._write(REGISTRY_FILE, [self.registry], list(self.registry))
        self._write(
            OBLIGATIONS_FILE,
            [],
            [
                "obligation_id", "document_id", "evidence_id", "source_page",
                "source_section", "original_text", "normalized_requirement",
                "responsible_party", "applicability", "conditions", "exceptions",
                "related_obligation_ids", "verification_status", "review_notes",
            ],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, name: str, rows: list[dict[str, str]], fields: list[str]) -> None:
        with (self.data_dir / name).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def _read(self, name: str) -> list[dict[str, str]]:
        with (self.data_dir / name).open("r", newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream))

    def _verified_fields(self) -> dict[str, str]:
        return {
            "reviewer_notes": "Checked against source text and applicability.",
            "reviewed_requirement_text": "The bank must perform the described action.",
            "reviewed_actor": "A bank",
            "reviewed_action": "perform the described action",
            "reviewed_conditions": "No additional conditions identified.",
            "reviewed_exceptions": "No exceptions identified.",
            "applicability_notes": "Applicability reviewed for this source; current status not inferred.",
        }

    def test_pending_review_stays_out_of_verified_dataset(self) -> None:
        rows = export_review_queue(self.data_dir)
        self.assertEqual(rows[0]["review_decision"], "pending")
        review_candidate(self.candidate["candidate_id"], "pending", self.data_dir)
        self.assertEqual(len(self._read(OBLIGATIONS_FILE)), 0)

    def test_verified_review_copies_human_reviewed_fields(self) -> None:
        review_candidate(
            self.candidate["candidate_id"], "verified", self.data_dir,
            reviewer_id="reviewer-test-001",
            **self._verified_fields(),
        )
        obligations = self._read(OBLIGATIONS_FILE)
        self.assertEqual(len(obligations), 1)
        self.assertEqual(obligations[0]["verification_status"], "verified")
        self.assertEqual(obligations[0]["original_text"], self.candidate["candidate_text"])
        self.assertEqual(obligations[0]["normalized_requirement"], "The bank must perform the described action.")
        self.assertEqual(obligations[0]["reviewed_action"], "perform the described action")
        self.assertEqual(self._read(QUEUE_FILE)[0]["review_decision"], "verified")
        self.assertTrue((self.data_dir / f"{OBLIGATIONS_FILE}.bak").is_file())
        self.assertTrue((self.data_dir / f"{QUEUE_FILE}.bak").is_file())
        audit = self._read(CANDIDATE_AUDIT_FILE)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["reviewer_id"], "reviewer-test-001")
        self.assertEqual(audit[0]["decision"], "verified")
        self.assertEqual(audit[0]["reason"], self._verified_fields()["reviewer_notes"])
        self.assertTrue(audit[0]["timestamp"])
        self.assertEqual(audit[0]["candidate_id"], self.candidate["candidate_id"])

    def test_rejected_candidate_never_enters_verified_dataset(self) -> None:
        review_candidate(
            self.candidate["candidate_id"], "rejected", self.data_dir,
            reviewer_id="reviewer-test-002",
            reviewer_notes="Not a standalone obligation.",
        )
        self.assertEqual(len(self._read(OBLIGATIONS_FILE)), 0)
        self.assertEqual(self._read(QUEUE_FILE)[0]["review_decision"], "rejected")
        audit = self._read(CANDIDATE_AUDIT_FILE)
        self.assertEqual(audit[0]["reviewer_id"], "reviewer-test-002")
        self.assertEqual(audit[0]["decision"], "rejected")
        self.assertEqual(audit[0]["reason"], "Not a standalone obligation.")

    def test_missing_source_evidence_is_rejected(self) -> None:
        self._write(EVIDENCE_FILE, [], list(self.evidence))
        with self.assertRaisesRegex(ReviewError, "missing evidence"):
            export_review_queue(self.data_dir)

    def test_invalid_decision_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReviewError, "Invalid review decision"):
            review_candidate(self.candidate["candidate_id"], "approved", self.data_dir)

    def test_verified_decision_requires_all_review_fields(self) -> None:
        with self.assertRaisesRegex(ReviewError, "require human-entered fields"):
            review_candidate(
                self.candidate["candidate_id"], "verified", self.data_dir,
                reviewer_id="reviewer-test-001",
            )
        self.assertEqual(len(self._read(OBLIGATIONS_FILE)), 0)

    def test_verified_or_rejected_decision_requires_reviewer_identity(self) -> None:
        obligations_before = (self.data_dir / OBLIGATIONS_FILE).read_bytes()
        for decision, notes, fields in (
            ("verified", "", self._verified_fields()),
            ("rejected", "Not supported.", {}),
        ):
            with self.subTest(decision=decision):
                arguments = {**fields, "reviewer_id": "  ", "reviewer_notes": notes}
                with self.assertRaisesRegex(ReviewError, "require reviewer_id"):
                    review_candidate(
                        self.candidate["candidate_id"],
                        decision,
                        self.data_dir,
                        **arguments,
                    )
        self.assertFalse((self.data_dir / QUEUE_FILE).exists())
        self.assertEqual((self.data_dir / OBLIGATIONS_FILE).read_bytes(), obligations_before)
        self.assertFalse((self.data_dir / CANDIDATE_AUDIT_FILE).exists())

    def test_duplicate_review_is_rejected(self) -> None:
        review_candidate(
            self.candidate["candidate_id"], "verified", self.data_dir,
            reviewer_id="reviewer-test-001",
            **self._verified_fields(),
        )
        with self.assertRaisesRegex(ReviewError, "already reviewed"):
            review_candidate(
                self.candidate["candidate_id"], "rejected", self.data_dir,
                reviewer_id="reviewer-test-002",
                reviewer_notes="Duplicate attempt.",
            )
        self.assertEqual(len(self._read(OBLIGATIONS_FILE)), 1)
        self.assertEqual(len(self._read(CANDIDATE_AUDIT_FILE)), 1)

    def test_duplicate_queue_candidate_id_is_rejected(self) -> None:
        export_review_queue(self.data_dir)
        duplicate = self._read(QUEUE_FILE)[0]
        queue_rows = [duplicate, duplicate]
        self._write(QUEUE_FILE, queue_rows, QUEUE_FIELDS)
        with self.assertRaisesRegex(ReviewError, "Duplicate review queue candidate ID"):
            review_candidate(self.candidate["candidate_id"], "pending", self.data_dir)

    def test_grounded_extraction_appends_to_existing_review_store(self) -> None:
        export_review_queue(self.data_dir)
        new_candidate = {
            "candidate_id": "WF-TEST-0001",
            "document_id": self.evidence["document_id"],
            "evidence_id": self.evidence["evidence_id"],
            "source_page": 8,
            "paragraph_reference": "6(1)",
            "proposed_obligation": "perform the reviewed action",
            "candidate_type": "explicit_requirement",
            "candidate_obligation_grounded": True,
            "uncertainty": "Test extraction only.",
        }
        added = append_extracted_candidates([new_candidate], self.data_dir)
        records = list_candidate_records(self.data_dir)
        self.assertEqual(added, ["WF-TEST-0001"])
        self.assertEqual(len(records), 2)
        submitted = next(row for row in records if row["candidate_id"] == "WF-TEST-0001")
        self.assertEqual(submitted["review_decision"], "pending")
        self.assertEqual(submitted["verification_status"], "unverified_candidate")
        self.assertEqual(submitted["candidate_text"], "perform the reviewed action")

    def test_ungrounded_extraction_cannot_enter_candidate_review_store(self) -> None:
        before = (self.data_dir / CANDIDATE_FILE).read_bytes()
        with self.assertRaisesRegex(ReviewError, "Only source-grounded"):
            append_extracted_candidates([{
                "candidate_id": "WF-TEST-0002",
                "document_id": self.evidence["document_id"],
                "evidence_id": self.evidence["evidence_id"],
                "proposed_obligation": "invented wording",
                "candidate_type": "interpretation",
                "candidate_obligation_grounded": False,
            }], self.data_dir)
        self.assertEqual((self.data_dir / CANDIDATE_FILE).read_bytes(), before)

    def test_existing_unmatched_candidate_is_listed_but_cannot_be_verified(self) -> None:
        changed = {**self.candidate, "candidate_text": "Text absent from the indexed source."}
        self._write(CANDIDATE_FILE, [changed], list(changed))
        export_review_queue(self.data_dir)
        listed = list_candidate_records(self.data_dir)[0]
        self.assertEqual(listed["source_match_status"], "requires_source_review")
        with self.assertRaisesRegex(ReviewError, "does not match its source evidence"):
            review_candidate(
                changed["candidate_id"], "verified", self.data_dir,
                reviewer_id="reviewer-test-003",
                **self._verified_fields(),
            )
        self.assertEqual(len(self._read(OBLIGATIONS_FILE)), 0)


if __name__ == "__main__":
    unittest.main()
