"""Focused unit tests for evidence-gated structured rule comparison."""

from __future__ import annotations

import unittest
from datetime import date

from backend.services.rule_comparison import RuleReference, compare_rule_pair


def ref(rule_id, *, condition, action, text="A bank shall review the application.", status="verified", lifecycle="approved", source_status="verified_current", effective_from="2024-01-01", effective_to=None):
    # The embedded definition is a test-only schema object, not project policy data.
    rule = {
        "rule_id": rule_id,
        "obligation_id": "TEST-OBLIGATION",
        "rule_name": "Test-only rule definition",
        "condition": condition,
        "action": {"action_type": action, "parameters": {}, "message": "Test-only"},
        "source_obligation": {"label": "Test-only definition", "dataset_kind": "synthetic"},
        "rule_version": "1.0.0",
        "status": "test",
        "created_at": "2025-01-01T00:00:00+00:00",
        "approved_by": None,
    }
    return RuleReference(
        rule_id=rule_id,
        rule_version="1.0.0",
        source_category="bank_policy",
        source_document="test-only source reference",
        policy_id="TEST-POLICY",
        clause_id=rule_id + "-CLAUSE",
        source_text=text,
        verification_status=status,
        lifecycle_status=lifecycle,
        source_status=source_status,
        effective_from=effective_from,
        effective_to=effective_to,
        evidence_reference="test-only evidence reference",
        rule_definition=rule,
        execution_capability="test-only definition",
    )


class RuleComparisonTests(unittest.TestCase):
    def compare(self, existing, proposed):
        return compare_rule_pair(existing, proposed, today=date(2026, 1, 1))

    def test_identical_condition_and_action_is_potentially_equivalent(self):
        condition = {"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 1000}
        result = self.compare(ref("EXIST-1", condition=condition, action="deny"), ref("PROP-1", condition=condition, action="deny"))
        self.assertEqual(result["structured_comparison"]["category"], "potentially_equivalent_requirements")
        self.assertEqual(result["wording_screening"]["category"], "possible_duplicate_wording")
        self.assertEqual(result["execution"]["status"], "not_executed")

    def test_overlapping_conditions_with_incompatible_actions_need_review(self):
        existing = ref("EXIST-2", condition={"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 1000}, action="deny")
        proposed = ref("PROP-2", condition={"type": "comparison", "field": "loan_amount", "operator": "lt", "value": 2000}, action="allow")
        result = self.compare(existing, proposed)
        self.assertEqual(result["structured_comparison"]["condition_overlap"], "overlap")
        self.assertEqual(result["structured_comparison"]["category"], "possible_conflict_requiring_review")

    def test_disjoint_conditions_do_not_report_conflict(self):
        existing = ref("EXIST-3", condition={"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 2000}, action="deny")
        proposed = ref("PROP-3", condition={"type": "comparison", "field": "loan_amount", "operator": "lt", "value": 1000}, action="allow")
        result = self.compare(existing, proposed)
        self.assertEqual(result["structured_comparison"]["condition_overlap"], "disjoint")
        self.assertEqual(result["structured_comparison"]["category"], "no_conflict_detected_in_available_verified_rules")

    def test_unknown_action_semantics_do_not_claim_no_conflict(self):
        condition = {"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 1000}
        existing = ref("EXIST-7", condition=condition, action="allow")
        proposed = ref("PROP-7", condition=condition, action="require_review")
        result = self.compare(existing, proposed)
        self.assertEqual(result["structured_comparison"]["category"], "insufficient_evidence")
        self.assertEqual(result["structured_comparison"]["actions_incompatible"], "unknown")

    def test_text_similarity_without_structured_definition_is_not_a_conflict(self):
        existing = ref("EXIST-4", condition={"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 1000}, action="deny", text="A bank shall review the application.")
        existing = existing.__class__(**(existing.__dict__ | {"rule_definition": None}))
        proposed = ref("PROP-4", condition={"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 1000}, action="allow", text="A bank shall review the application.")
        result = self.compare(existing, proposed)
        self.assertEqual(result["wording_screening"]["category"], "possible_duplicate_wording")
        self.assertEqual(result["structured_comparison"]["category"], "insufficient_evidence")

    def test_unresolved_historical_and_retired_sources_are_blocked(self):
        condition = {"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 100}
        proposed = ref("PROP-5", condition=condition, action="deny")
        for status, lifecycle, source_status in (("unverified", "approved", "verified_current"), ("verified", "retired", "verified_current"), ("verified", "approved", "HISTORICAL_STATUS_REQUIRES_REVIEW")):
            existing = ref("EXIST-5", condition=condition, action="deny", status=status, lifecycle=lifecycle, source_status=source_status)
            result = self.compare(existing, proposed)
            self.assertEqual(result["structured_comparison"]["category"], "insufficient_evidence")

    def test_missing_executable_definition_and_no_transaction_data_are_explicit(self):
        condition = {"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 100}
        existing = ref("EXIST-6", condition=condition, action="deny")
        proposed = ref("PROP-6", condition=condition, action="deny")
        proposed = proposed.__class__(**(proposed.__dict__ | {"rule_definition": None}))
        result = self.compare(existing, proposed)
        self.assertEqual(result["structured_comparison"]["category"], "insufficient_evidence")
        self.assertIsNone(result["execution"]["existing_output"])
        self.assertIsNone(result["execution"]["proposed_output"])


if __name__ == "__main__":
    unittest.main()
