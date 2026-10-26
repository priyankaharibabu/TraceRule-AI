"""Structural conflict detection for restricted rule conditions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from backend.services.rule_engine import _eval_condition
from backend.services.rule_validator import validate_rule

INCOMPATIBLE_ACTION_PAIRS = frozenset(
    {
        ("allow", "deny"),
        ("deny", "allow"),
        ("set_limit", "deny"),
    }
)


@dataclass
class ConflictFinding:
    rule_ids: list[str]
    finding_type: str
    severity: str
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)
    overlap_established: bool = False
    requires_human_review: bool = False


def _condition_fingerprint(condition: dict[str, Any]) -> str:
    normalized = json.dumps(condition, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _actions_incompatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    left = a.get("action_type")
    right = b.get("action_type")
    if (left, right) in INCOMPATIBLE_ACTION_PAIRS:
        return True
    if left == right == "set_limit":
        return a.get("parameters", {}).get("max_amount") != b.get("parameters", {}).get("max_amount")
    return False


def _sample_inputs_for_fields(fields: set[str]) -> list[dict[str, Any]]:
    base = {
        "customer_type": "retail",
        "loan_product": "personal",
        "loan_amount": 50000,
        "loan_tenor_months": 12,
        "apr_percent": 18.0,
        "channel": "digital",
        "has_lsp": True,
        "lsp_count": 1,
        "credit_limit_increase_requested": False,
        "disbursement_route": "direct",
        "repayment_route": "direct",
        "data_storage_region": "IN",
        "dlg_cap_percent": 5,
        "borrower_age": 30,
        "income_verified": True,
        "jurisdiction": "IN",
    }
    samples = [dict(base)]
    for field_name in fields:
        variant = dict(base)
        if field_name == "loan_amount":
            variant[field_name] = 1000
        elif field_name == "has_lsp":
            variant[field_name] = False
        elif field_name == "channel":
            variant[field_name] = "branch"
        else:
            variant[field_name] = None
        samples.append(variant)
    return samples


def _conditions_overlap(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, bool]:
    """Return (overlap_established, possibly_overlaps)."""
    if a.get("type") == "broad" or b.get("type") == "broad":
        return True, True
    if a.get("type") == "missing" or b.get("type") == "missing":
        return False, True

    fields = set()
    for node in (a, b):
        if node.get("type") == "comparison":
            fields.add(node.get("field", ""))
    samples = _sample_inputs_for_fields(fields)
    for sample in samples:
        reasoning_a: list[str] = []
        reasoning_b: list[str] = []
        missing_a: list[str] = []
        missing_b: list[str] = []
        match_a = _eval_condition(a, sample, reasoning_a, missing_a)
        match_b = _eval_condition(b, sample, reasoning_b, missing_b)
        if match_a and match_b:
            return True, True
    same = _condition_fingerprint(a) == _condition_fingerprint(b)
    if same:
        return True, True
    return False, False


def detect_conflicts(rules: list[dict[str, Any]]) -> list[ConflictFinding]:
    findings: list[ConflictFinding] = []
    valid_rules: list[dict[str, Any]] = []
    for rule in rules:
        if validate_rule(rule):
            findings.append(
                ConflictFinding(
                    rule_ids=[str(rule.get("rule_id", ""))],
                    finding_type="invalid_rule",
                    severity="high",
                    explanation="Rule failed schema validation",
                    evidence={"errors": validate_rule(rule)},
                    requires_human_review=True,
                )
            )
            continue
        valid_rules.append(rule)

    by_fingerprint: dict[str, list[dict[str, Any]]] = {}
    for rule in valid_rules:
        fp = _condition_fingerprint(rule["condition"])
        by_fingerprint.setdefault(fp, []).append(rule)

    for fp, group in by_fingerprint.items():
        if len(group) > 1:
            actions = [item["action"] for item in group]
            incompatible = any(
                _actions_incompatible(actions[i], actions[j])
                for i in range(len(actions))
                for j in range(i + 1, len(actions))
            )
            findings.append(
                ConflictFinding(
                    rule_ids=[r["rule_id"] for r in group],
                    finding_type="duplicate_or_near_duplicate",
                    severity="medium" if not incompatible else "high",
                    explanation="Identical condition fingerprints across multiple rules",
                    evidence={"fingerprint": fp, "count": len(group), "incompatible_actions": incompatible},
                    overlap_established=True,
                    requires_human_review=incompatible,
                )
            )
            if incompatible:
                findings.append(
                    ConflictFinding(
                        rule_ids=[r["rule_id"] for r in group],
                        finding_type="identical_condition_incompatible_actions",
                        severity="high",
                        explanation="Same condition with mutually incompatible actions",
                        evidence={"fingerprint": fp},
                        overlap_established=True,
                        requires_human_review=True,
                    )
                )

    for i, left in enumerate(valid_rules):
        for right in valid_rules[i + 1 :]:
            overlap, ambiguous = _conditions_overlap(left["condition"], right["condition"])
            if not overlap and not ambiguous:
                continue
            if _actions_incompatible(left["action"], right["action"]):
                if overlap:
                    findings.append(
                        ConflictFinding(
                            rule_ids=[left["rule_id"], right["rule_id"]],
                            finding_type="overlapping_incompatible_actions",
                            severity="high",
                            explanation="Overlapping conditions with incompatible actions",
                            evidence={"overlap_established": True},
                            overlap_established=True,
                            requires_human_review=True,
                        )
                    )
                else:
                    findings.append(
                        ConflictFinding(
                            rule_ids=[left["rule_id"], right["rule_id"]],
                            finding_type="ambiguous_overlap_requires_review",
                            severity="medium",
                            explanation="Potential overlap could not be established reliably",
                            evidence={"overlap_established": False},
                            overlap_established=False,
                            requires_human_review=True,
                        )
                    )

    for rule in valid_rules:
        condition = rule["condition"]
        if condition.get("type") == "missing":
            findings.append(
                ConflictFinding(
                    rule_ids=[rule["rule_id"]],
                    finding_type="missing_or_incomplete_condition",
                    severity="medium",
                    explanation="Rule declares a missing condition",
                    evidence={"reason": condition.get("reason", "")},
                    overlap_established=False,
                    requires_human_review=True,
                )
            )
        if condition.get("type") == "broad":
            findings.append(
                ConflictFinding(
                    rule_ids=[rule["rule_id"]],
                    finding_type="overly_broad_applicability",
                    severity="medium",
                    explanation="Rule uses a broad applicability marker",
                    evidence={"reason": condition.get("reason", "")},
                    overlap_established=True,
                    requires_human_review=True,
                )
            )

    return findings


def summarize_findings(findings: list[ConflictFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in findings:
        counts[item.finding_type] = counts.get(item.finding_type, 0) + 1
    return counts
