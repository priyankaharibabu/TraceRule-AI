from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from backend.services.baseline_simulation import run_simulation
from backend.services.conflict_detector import detect_conflicts
from backend.services.generate_synthetic_data import build_baseline_rules, build_proposed_rules, generate_test_cases
from backend.services.rule_engine import SYNTHETIC_BANNER, evaluate_case, evaluate_rule, load_rules
from backend.services.rule_validator import RuleValidationError, validate_rule, validate_rules

PROJECT = Path(__file__).resolve().parents[2]
SYNTHETIC = PROJECT / "data" / "synthetic"


class RuleSchemaTests(unittest.TestCase):
    def test_valid_synthetic_rule_passes(self) -> None:
        rule = build_baseline_rules()[0]
        self.assertEqual(validate_rule(rule), [])

    def test_unknown_field_rejected(self) -> None:
        rule = deepcopy(build_baseline_rules()[0])
        rule["eval"] = "1+1"
        errors = validate_rule(rule)
        self.assertTrue(any("unknown field" in err for err in errors))

    def test_forbidden_code_token_rejected(self) -> None:
        rule = deepcopy(build_baseline_rules()[0])
        rule["rule_name"] = "eval(1)"
        errors = validate_rule(rule)
        self.assertTrue(any("forbidden code-like" in err for err in errors))

    def test_invalid_action_type_rejected(self) -> None:
        rule = deepcopy(build_baseline_rules()[0])
        rule["action"]["action_type"] = "execute_python"
        errors = validate_rule(rule)
        self.assertTrue(any("invalid action_type" in err for err in errors))

    def test_draft_rule_not_evaluated(self) -> None:
        draft = next(r for r in build_baseline_rules() if r["rule_id"] == "SYN-RULE-018")
        outcome = evaluate_rule(draft, {"channel": "branch"})
        self.assertFalse(outcome.matched)
        self.assertIn("draft", " ".join(outcome.reasoning))

    def test_unverified_candidate_cannot_fire(self) -> None:
        candidate = deepcopy(build_baseline_rules()[0])
        candidate["source_obligation"] = {
            "label": "Provisional candidate",
            "dataset_kind": "unverified_candidate",
            "document_id": "DOC-CB-2025",
            "evidence_id": "EVID-CANDIDATE",
            "source_page": "8",
        }
        outcome = evaluate_rule(candidate, {"disbursement_route": "direct"})
        self.assertFalse(outcome.matched)
        self.assertIsNone(outcome.action)
        self.assertTrue(any("unverified_candidate" in error for error in outcome.validation_errors))

    def test_verified_rule_blocked_while_document_status_unresolved(self) -> None:
        rule = deepcopy(build_baseline_rules()[0])
        rule["obligation_id"] = "OBL-CB-UNVERIFIED"
        rule["source_obligation"] = {
            "label": "Proposed mapped obligation",
            "dataset_kind": "verified",
            "document_id": "DOC-CB-2025",
            "evidence_id": "EVID-CB-UNVERIFIED",
            "source_page": "8",
        }
        outcome = evaluate_rule(rule, {"disbursement_route": "direct"})
        self.assertFalse(outcome.matched)
        self.assertIsNone(outcome.action)
        self.assertTrue(any("VERIFY_CURRENT_STATUS" in error for error in outcome.validation_errors))

    def test_superseded_document_rule_cannot_fire(self) -> None:
        rule = deepcopy(build_baseline_rules()[0])
        rule["obligation_id"] = "OBL-DL-2022-OLD"
        rule["source_obligation"] = {
            "label": "Historical source",
            "dataset_kind": "verified",
            "document_id": "DOC-DL-2022",
            "evidence_id": "EVID-DL-2022-OLD",
            "source_page": "8",
        }
        outcome = evaluate_rule(rule, {"disbursement_route": "direct"})
        self.assertFalse(outcome.matched)
        self.assertIsNone(outcome.action)
        self.assertTrue(any("superseded" in error for error in outcome.validation_errors))


class RuleEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = build_baseline_rules()

    def test_comparison_eq_and_allow(self) -> None:
        rule = next(r for r in self.rules if r["rule_id"] == "SYN-RULE-001")
        outcome = evaluate_rule(rule, {"disbursement_route": "direct"})
        self.assertTrue(outcome.matched)
        self.assertEqual(outcome.action["action_type"], "allow")

    def test_missing_input_reported(self) -> None:
        rule = next(r for r in self.rules if r["rule_id"] == "SYN-RULE-005")
        outcome = evaluate_rule(rule, {})
        self.assertFalse(outcome.matched)
        self.assertIn("loan_amount", outcome.missing_inputs)

    def test_null_input_treated_as_missing(self) -> None:
        rule = next(r for r in self.rules if r["rule_id"] == "SYN-RULE-005")
        outcome = evaluate_rule(rule, {"loan_amount": None})
        self.assertIn("loan_amount", outcome.missing_inputs)

    def test_logical_and_boundary(self) -> None:
        rule = next(r for r in self.rules if r["rule_id"] == "SYN-RULE-003")
        outcome = evaluate_rule(
            rule,
            {"loan_product": "personal", "apr_percent": 25.0},
        )
        self.assertTrue(outcome.matched)

    def test_in_operator(self) -> None:
        rule = next(r for r in self.rules if r["rule_id"] == "SYN-RULE-020")
        outcome = evaluate_rule(rule, {"jurisdiction": "IN"})
        self.assertTrue(outcome.matched)

    def test_deterministic_repeatable(self) -> None:
        inputs = {"disbursement_route": "direct", "loan_amount": 1000}
        first = evaluate_case("CASE-1", inputs, self.rules)
        second = evaluate_case("CASE-1", inputs, self.rules)
        self.assertEqual(
            [item.rule_id for item in first.outcomes if item.matched],
            [item.rule_id for item in second.outcomes if item.matched],
        )


class ConflictDetectionTests(unittest.TestCase):
    def test_duplicate_detection(self) -> None:
        findings = detect_conflicts(build_baseline_rules())
        types = {item.finding_type for item in findings}
        self.assertIn("duplicate_or_near_duplicate", types)
        self.assertIn("identical_condition_incompatible_actions", types)

    def test_missing_and_broad_findings(self) -> None:
        findings = detect_conflicts(build_baseline_rules())
        types = {item.finding_type for item in findings}
        self.assertIn("missing_or_incomplete_condition", types)
        self.assertIn("overly_broad_applicability", types)


class SimulationTests(unittest.TestCase):
    def test_baseline_vs_proposed_reproducible(self) -> None:
        baseline = build_baseline_rules()
        proposed = build_proposed_rules(baseline)
        cases = generate_test_cases(baseline, 50, 42)
        first = run_simulation(
            baseline,
            proposed,
            cases,
            baseline_version="1.0.0",
            proposed_version="1.1.0",
            cases_version="1.0.0",
            seed=42,
        )
        second = run_simulation(
            baseline,
            proposed,
            cases,
            baseline_version="1.0.0",
            proposed_version="1.1.0",
            cases_version="1.0.0",
            seed=42,
        )
        self.assertEqual(first["changed_outcomes"], second["changed_outcomes"])
        self.assertEqual(first["case_count"], second["case_count"])

    def test_simulation_includes_validation_errors(self) -> None:
        baseline = build_baseline_rules()
        proposed = build_proposed_rules(baseline)
        bad_case = {
            "case_id": "BAD-1",
            "purpose": "invalid_rules",
            "inputs": {},
            "expected": {},
        }
        invalid_rules = [{"rule_id": "BAD"}]
        report = run_simulation(
            invalid_rules,
            proposed,
            [bad_case],
            baseline_version="x",
            proposed_version="y",
            cases_version="z",
            seed=1,
        )
        self.assertGreaterEqual(len(report["comparisons"]), 1)


class SyntheticDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (SYNTHETIC / "synthetic_rules_baseline.json").exists():
            from backend.services.generate_synthetic_data import write_outputs

            write_outputs()

    def test_synthetic_files_labeled(self) -> None:
        payload = json.loads((SYNTHETIC / "synthetic_rules_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["label"], SYNTHETIC_BANNER)

    def test_no_verified_obligation_ids_in_synthetic_rules(self) -> None:
        rules = load_rules(SYNTHETIC / "synthetic_rules_baseline.json")
        for rule in rules:
            self.assertTrue(rule["obligation_id"].startswith("SYN-OBL-"))

    def test_obligations_csv_still_empty(self) -> None:
        obligations = (PROJECT / "data" / "processed" / "obligations.csv").read_text(encoding="utf-8-sig")
        self.assertEqual(len(obligations.strip().splitlines()), 1)


class NegativeValidationTests(unittest.TestCase):
    def test_validate_rules_raises(self) -> None:
        with self.assertRaises(RuleValidationError):
            validate_rules([{"rule_id": "BAD"}])


if __name__ == "__main__":
    unittest.main()
