"""Generate synthetic rules and reproducible test cases for Phase 3."""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from backend.services.conflict_detector import detect_conflicts, summarize_findings
from backend.services.rule_engine import SYNTHETIC_BANNER
from backend.services.rule_validator import validate_rules

PROJECT = Path(__file__).resolve().parents[2]
SYNTHETIC_DIR = PROJECT / "data" / "synthetic"
SEED = 42


def _src(label: str) -> dict[str, str]:
    return {
        "label": label,
        "dataset_kind": "synthetic",
    }


def _cmp(field: str, operator: str, value) -> dict:
    return {"type": "comparison", "field": field, "operator": operator, "value": value}


def _rule(
    rule_id: str,
    obligation_id: str,
    name: str,
    condition: dict,
    action_type: str,
    parameters: dict | None = None,
    *,
    status: str = "test",
    version: str = "1.0.0",
    notes: str = "",
) -> dict:
    return {
        "rule_id": rule_id,
        "obligation_id": obligation_id,
        "rule_name": name,
        "condition": condition,
        "action": {
            "action_type": action_type,
            "parameters": parameters or {},
            "message": f"{SYNTHETIC_BANNER}: {name}",
        },
        "source_obligation": _src(obligation_id),
        "rule_version": version,
        "status": status,
        "created_at": "2026-01-15T10:00:00+00:00",
        "approved_by": None,
        "notes": notes or SYNTHETIC_BANNER,
    }


def build_baseline_rules() -> list[dict]:
    return [
        _rule(
            "SYN-RULE-001",
            "SYN-OBL-001",
            "Allow direct retail digital disbursement",
            _cmp("disbursement_route", "eq", "direct"),
            "allow",
            {"channel_scope": "digital"},
        ),
        _rule(
            "SYN-RULE-002",
            "SYN-OBL-002",
            "Deny pool-account disbursement",
            _cmp("disbursement_route", "eq", "pool"),
            "deny",
            {"reason_code": "POOL_ROUTE"},
        ),
        _rule(
            "SYN-RULE-003",
            "SYN-OBL-003",
            "Flag high APR personal loans",
            {
                "type": "logical",
                "operator": "and",
                "operands": [
                    _cmp("loan_product", "eq", "personal"),
                    _cmp("apr_percent", "gt", 24.0),
                ],
            },
            "flag",
            {"severity": "medium"},
        ),
        _rule(
            "SYN-RULE-004",
            "SYN-OBL-004",
            "Require review for LSP digital channel",
            {
                "type": "logical",
                "operator": "and",
                "operands": [
                    _cmp("channel", "eq", "digital"),
                    _cmp("has_lsp", "eq", True),
                ],
            },
            "require_review",
        ),
        _rule(
            "SYN-RULE-005",
            "SYN-OBL-005",
            "Set limit for unsecured micro loans",
            _cmp("loan_amount", "lte", 25000),
            "set_limit",
            {"max_amount": 25000},
        ),
        _rule(
            "SYN-RULE-006",
            "SYN-OBL-006",
            "Require disclosure for limit increase requests",
            _cmp("credit_limit_increase_requested", "eq", True),
            "require_disclosure",
            {"artifact": "KFS"},
        ),
        _rule(
            "SYN-RULE-007",
            "SYN-OBL-007",
            "Deny data storage outside India",
            _cmp("data_storage_region", "ne", "IN"),
            "deny",
            {"reason_code": "DATA_LOCALITY"},
        ),
        _rule(
            "SYN-RULE-008",
            "SYN-OBL-008",
            "Allow verified income retail",
            {
                "type": "logical",
                "operator": "and",
                "operands": [
                    _cmp("customer_type", "eq", "retail"),
                    _cmp("income_verified", "eq", True),
                ],
            },
            "allow",
        ),
        _rule(
            "SYN-RULE-009",
            "SYN-OBL-009",
            "Duplicate of direct disbursement (intentional)",
            _cmp("disbursement_route", "eq", "direct"),
            "allow",
            {"channel_scope": "digital"},
        ),
        _rule(
            "SYN-RULE-010",
            "SYN-OBL-010",
            "Identical condition incompatible action",
            _cmp("disbursement_route", "eq", "direct"),
            "deny",
            {"reason_code": "CONFLICT_TEST"},
        ),
        _rule(
            "SYN-RULE-011",
            "SYN-OBL-011",
            "Overlapping tenor boundary A",
            _cmp("loan_tenor_months", "lte", 12),
            "set_limit",
            {"max_amount": 100000},
        ),
        _rule(
            "SYN-RULE-012",
            "SYN-OBL-012",
            "Overlapping tenor boundary B",
            _cmp("loan_tenor_months", "gte", 12),
            "deny",
            {"reason_code": "TENOR_CONFLICT"},
        ),
        _rule(
            "SYN-RULE-013",
            "SYN-OBL-013",
            "Missing condition placeholder",
            {"type": "missing", "reason": "Condition not finalized"},
            "require_review",
        ),
        _rule(
            "SYN-RULE-014",
            "SYN-OBL-014",
            "Overly broad applicability",
            {"type": "broad", "reason": "Applies to all products pending scoping"},
            "flag",
            {"severity": "low"},
        ),
        _rule(
            "SYN-RULE-015",
            "SYN-OBL-015",
            "DLG cap boundary allow",
            _cmp("dlg_cap_percent", "lte", 5),
            "allow",
        ),
        _rule(
            "SYN-RULE-016",
            "SYN-OBL-016",
            "DLG cap boundary deny",
            _cmp("dlg_cap_percent", "gt", 5),
            "deny",
            {"reason_code": "DLG_CAP"},
        ),
        _rule(
            "SYN-RULE-017",
            "SYN-OBL-017",
            "Minor borrower review",
            _cmp("borrower_age", "lt", 18),
            "require_review",
        ),
        _rule(
            "SYN-RULE-018",
            "SYN-OBL-018",
            "Draft rule not evaluated",
            _cmp("channel", "eq", "branch"),
            "allow",
            status="draft",
        ),
        _rule(
            "SYN-RULE-019",
            "SYN-OBL-019",
            "Multi-lender count review",
            _cmp("lsp_count", "gt", 1),
            "require_review",
        ),
        _rule(
            "SYN-RULE-020",
            "SYN-OBL-020",
            "Jurisdiction membership check",
            _cmp("jurisdiction", "in", ["IN", "SG"]),
            "allow",
        ),
    ]


def build_proposed_rules(baseline: list[dict]) -> list[dict]:
    proposed = json.loads(json.dumps(baseline))
    for rule in proposed:
        if rule["rule_id"] == "SYN-RULE-005":
            rule["rule_version"] = "1.1.0"
            rule["action"]["parameters"]["max_amount"] = 30000
        if rule["rule_id"] == "SYN-RULE-010":
            rule["status"] = "retired"
        if rule["rule_id"] == "SYN-RULE-003":
            rule["condition"]["operands"][1]["value"] = 22.0
    proposed.append(
        _rule(
            "SYN-RULE-021",
            "SYN-OBL-021",
            "Proposed only: flag indirect repayment",
            _cmp("repayment_route", "eq", "indirect"),
            "flag",
            {"severity": "high"},
            version="1.0.0",
        )
    )
    return proposed


def generate_test_cases(rules: list[dict], count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    customer_types = ["retail", "sme", "corporate"]
    products = ["personal", "business", "gold"]
    channels = ["digital", "branch", "partner"]
    routes = ["direct", "pool", "indirect"]

    cases: list[dict] = []
    purposes = [
        "normal_path",
        "boundary_value",
        "missing_field",
        "null_field",
        "invalid_type_coerced",
        "duplicate_rules",
        "conflict_path",
        "non_conflict_overlap",
        "disjoint_conditions",
        "broad_condition",
        "missing_condition_rule",
        "empty_rule_set",
        "baseline_vs_proposed",
    ]

    for index in range(count):
        purpose = purposes[index % len(purposes)]
        inputs: dict = {
            "customer_type": rng.choice(customer_types),
            "loan_product": rng.choice(products),
            "loan_amount": rng.choice([0, 1, 24999, 25000, 25001, 100000, 500000]),
            "loan_tenor_months": rng.choice([6, 11, 12, 13, 24, 36]),
            "apr_percent": round(rng.uniform(8, 36), 2),
            "channel": rng.choice(channels),
            "has_lsp": rng.choice([True, False]),
            "lsp_count": rng.choice([0, 1, 2, 3]),
            "credit_limit_increase_requested": rng.choice([True, False]),
            "disbursement_route": rng.choice(routes),
            "repayment_route": rng.choice(routes),
            "data_storage_region": rng.choice(["IN", "US", "EU"]),
            "dlg_cap_percent": rng.choice([4, 5, 6, 10]),
            "borrower_age": rng.choice([16, 17, 18, 25, 60]),
            "income_verified": rng.choice([True, False]),
            "jurisdiction": rng.choice(["IN", "SG", "US"]),
        }
        expected: dict = {"purpose": purpose}
        if purpose == "missing_field":
            inputs.pop("loan_amount", None)
            expected["expect_missing"] = True
        if purpose == "null_field":
            inputs["loan_amount"] = None
            expected["expect_missing"] = True
        if purpose == "invalid_type_coerced":
            inputs["loan_amount"] = "not-a-number"
            expected["expect_type_issue"] = True
        if purpose == "empty_rule_set":
            expected["use_empty_rules"] = True

        cases.append(
            {
                "case_id": f"SYN-CASE-{index + 1:04d}",
                "purpose": purpose,
                "inputs": inputs,
                "expected": expected,
                "exercises": f"Covers {purpose} behavior for deterministic engine",
            }
        )

    # Anchor cases with explicit expectations
    anchors = [
        {
            "case_id": "SYN-CASE-ANCHOR-001",
            "purpose": "conflict_path",
            "inputs": {
                "customer_type": "retail",
                "loan_product": "personal",
                "loan_amount": 10000,
                "loan_tenor_months": 12,
                "apr_percent": 15,
                "channel": "digital",
                "has_lsp": False,
                "lsp_count": 0,
                "credit_limit_increase_requested": False,
                "disbursement_route": "direct",
                "repayment_route": "direct",
                "data_storage_region": "IN",
                "dlg_cap_percent": 4,
                "borrower_age": 30,
                "income_verified": True,
                "jurisdiction": "IN",
            },
            "expected": {"expect_multiple_allow_deny_on_direct": True},
            "exercises": "Identical disbursement_route with allow and deny rules",
        },
        {
            "case_id": "SYN-CASE-ANCHOR-002",
            "purpose": "boundary_value",
            "inputs": {
                "customer_type": "retail",
                "loan_product": "personal",
                "loan_amount": 25000,
                "loan_tenor_months": 12,
                "apr_percent": 24.0,
                "channel": "digital",
                "has_lsp": True,
                "lsp_count": 1,
                "credit_limit_increase_requested": False,
                "disbursement_route": "direct",
                "repayment_route": "direct",
                "data_storage_region": "IN",
                "dlg_cap_percent": 5,
                "borrower_age": 18,
                "income_verified": True,
                "jurisdiction": "IN",
            },
            "expected": {"expect_boundary_hits": True},
            "exercises": "Boundary values for amount, APR, age, DLG cap",
        },
    ]
    cases = anchors + cases[: count - len(anchors)]
    return cases


def write_outputs() -> dict[str, int | str]:
    SYNTHETIC_DIR.mkdir(parents=True, exist_ok=True)
    baseline = build_baseline_rules()
    proposed = build_proposed_rules(baseline)
    validate_rules(baseline)
    validate_rules(proposed)

    cases = generate_test_cases(baseline, 400, SEED)
    findings = detect_conflicts(baseline)

    baseline_doc = {
        "label": SYNTHETIC_BANNER,
        "ruleset_version": "1.0.0",
        "ruleset_kind": "synthetic_baseline",
        "rules": baseline,
    }
    proposed_doc = {
        "label": SYNTHETIC_BANNER,
        "ruleset_version": "1.1.0",
        "ruleset_kind": "synthetic_proposed",
        "rules": proposed,
    }
    cases_doc = {
        "label": SYNTHETIC_BANNER,
        "dataset_version": "1.0.0",
        "seed": SEED,
        "test_cases": cases,
    }
    conflict_doc = {
        "label": SYNTHETIC_BANNER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "findings": [
            {
                "rule_ids": item.rule_ids,
                "finding_type": item.finding_type,
                "severity": item.severity,
                "explanation": item.explanation,
                "evidence": item.evidence,
                "overlap_established": item.overlap_established,
                "requires_human_review": item.requires_human_review,
            }
            for item in findings
        ],
        "summary": summarize_findings(findings),
    }

    (SYNTHETIC_DIR / "synthetic_rules_baseline.json").write_text(
        json.dumps(baseline_doc, indent=2), encoding="utf-8"
    )
    (SYNTHETIC_DIR / "synthetic_rules_proposed.json").write_text(
        json.dumps(proposed_doc, indent=2), encoding="utf-8"
    )
    (SYNTHETIC_DIR / "synthetic_test_cases.json").write_text(
        json.dumps(cases_doc, indent=2), encoding="utf-8"
    )
    (SYNTHETIC_DIR / "synthetic_conflict_report.json").write_text(
        json.dumps(conflict_doc, indent=2), encoding="utf-8"
    )
    return {
        "baseline_rules": len(baseline),
        "proposed_rules": len(proposed),
        "test_cases": len(cases),
        "conflict_findings": len(findings),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    stats = write_outputs()
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
