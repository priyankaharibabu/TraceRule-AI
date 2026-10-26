"""Deterministic evaluation for restricted TraceRule definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.services.rule_validator import validate_rule, validate_rules
from backend.services.scope_governance import check_real_rule_mapping_eligibility

SYNTHETIC_BANNER = "SIMULATED INTERNAL CONTROLS — NOT ACTUAL BANK POLICIES"
EVALUABLE_STATUSES = frozenset({"test", "pending_approval", "approved"})


@dataclass
class RuleOutcome:
    rule_id: str
    matched: bool
    action: dict[str, Any] | None
    reasoning: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    source_obligation: dict[str, Any] = field(default_factory=dict)
    rule_version: str = ""
    dataset_kind: str = "synthetic"


@dataclass
class EvaluationResult:
    case_id: str
    inputs: dict[str, Any]
    outcomes: list[RuleOutcome]
    fired_actions: list[dict[str, Any]]
    validation_errors: list[str]
    metadata: dict[str, Any]


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _field_value(inputs: dict[str, Any], field_name: str) -> tuple[Any, str | None]:
    if field_name not in inputs:
        return None, field_name
    value = inputs[field_name]
    if value is None:
        return None, field_name
    return value, None


def _eval_comparison(node: dict[str, Any], inputs: dict[str, Any], reasoning: list[str], missing: list[str]) -> bool:
    field_name = node["field"]
    op = node["operator"]
    expected = node["value"]
    actual, missing_field = _field_value(inputs, field_name)
    if missing_field:
        missing.append(missing_field)
        reasoning.append(f"field '{field_name}' is missing or null")
        return False

    if op in {"in", "not_in"}:
        if not isinstance(expected, list):
            reasoning.append(f"operator '{op}' misconfigured (expected list)")
            return False
        membership = actual in expected
        result = membership if op == "in" else not membership
        reasoning.append(f"{field_name} {op} {expected} => {result} (actual={actual!r})")
        return result

    if op in {"lt", "lte", "gt", "gte"}:
        left = _coerce_number(actual)
        right = _coerce_number(expected)
        if left is None or right is None:
            missing.append(field_name)
            reasoning.append(f"{field_name} or comparison value is not numeric")
            return False
        ops = {"lt": left < right, "lte": left <= right, "gt": left > right, "gte": left >= right}
        result = ops[op]
        reasoning.append(f"{field_name} {op} {right} => {result} (actual={left})")
        return result

    if op == "eq":
        result = actual == expected
    else:
        result = actual != expected
    reasoning.append(f"{field_name} {op} {expected!r} => {result} (actual={actual!r})")
    return result


def _eval_condition(node: dict[str, Any], inputs: dict[str, Any], reasoning: list[str], missing: list[str]) -> bool:
    node_type = node.get("type")
    if node_type == "comparison":
        return _eval_comparison(node, inputs, reasoning, missing)
    if node_type == "logical":
        op = node["operator"]
        results = [
            _eval_condition(child, inputs, reasoning, missing) for child in node.get("operands", [])
        ]
        value = all(results) if op == "and" else any(results)
        reasoning.append(f"logical {op} => {value}")
        return value
    if node_type == "not":
        value = not _eval_condition(node["operand"], inputs, reasoning, missing)
        reasoning.append(f"not => {value}")
        return value
    if node_type == "missing":
        reasoning.append("condition marked missing — treated as non-match")
        return False
    if node_type == "broad":
        reasoning.append("condition marked broad — matches all inputs for detection only")
        return True
    reasoning.append(f"unknown condition type '{node_type}'")
    return False


def evaluate_rule(rule: dict[str, Any], inputs: dict[str, Any]) -> RuleOutcome:
    errors = validate_rule(rule)
    rule_id = str(rule.get("rule_id", ""))
    source = rule.get("source_obligation") or {}
    if not errors and source.get("dataset_kind") != "synthetic":
        errors.extend(check_real_rule_mapping_eligibility(rule))
    if errors:
        return RuleOutcome(
            rule_id=rule_id,
            matched=False,
            action=None,
            validation_errors=errors,
            source_obligation=source,
            rule_version=str(rule.get("rule_version", "")),
            dataset_kind=str(source.get("dataset_kind", "")),
        )

    status = rule.get("status")
    reasoning: list[str] = []
    missing: list[str] = []
    if status not in EVALUABLE_STATUSES and status != "draft":
        reasoning.append(f"rule status '{status}' is not evaluated")
        return RuleOutcome(
            rule_id=rule_id,
            matched=False,
            action=None,
            reasoning=reasoning,
            source_obligation=source,
            rule_version=str(rule.get("rule_version", "")),
            dataset_kind=str(source.get("dataset_kind", "")),
        )
    if status == "draft":
        reasoning.append("draft rules are not evaluated")
        return RuleOutcome(
            rule_id=rule_id,
            matched=False,
            action=None,
            reasoning=reasoning,
            source_obligation=source,
            rule_version=str(rule.get("rule_version", "")),
            dataset_kind=str(source.get("dataset_kind", "")),
        )

    matched = _eval_condition(rule["condition"], inputs, reasoning, missing)
    action = rule.get("action") if matched else None
    return RuleOutcome(
        rule_id=rule_id,
        matched=matched,
        action=action,
        reasoning=reasoning,
        missing_inputs=sorted(set(missing)),
        source_obligation=source,
        rule_version=str(rule.get("rule_version", "")),
        dataset_kind=str(source.get("dataset_kind", "")),
    )


def evaluate_case(case_id: str, inputs: dict[str, Any], rules: list[dict[str, Any]]) -> EvaluationResult:
    validation_errors: list[str] = []
    try:
        validate_rules(rules)
    except Exception as exc:  # noqa: BLE001 — surfaced in result
        validation_errors.append(str(exc))

    outcomes: list[RuleOutcome] = []
    fired: list[dict[str, Any]] = []
    for rule in rules:
        outcome = evaluate_rule(rule, inputs)
        outcomes.append(outcome)
        if outcome.matched and outcome.action:
            fired.append(
                {
                    "rule_id": outcome.rule_id,
                    "action_type": outcome.action.get("action_type"),
                    "parameters": outcome.action.get("parameters", {}),
                    "message": outcome.action.get("message", ""),
                    "dataset_kind": outcome.dataset_kind,
                }
            )

    metadata = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_banner": SYNTHETIC_BANNER,
        "rules_evaluated": len(rules),
    }
    return EvaluationResult(
        case_id=case_id,
        inputs=inputs,
        outcomes=outcomes,
        fired_actions=fired,
        validation_errors=validation_errors,
        metadata=metadata,
    )


def load_rules(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    rules = payload.get("rules", payload if isinstance(payload, list) else [])
    if not isinstance(rules, list):
        raise ValueError("rules file must contain a list under 'rules'")
    validate_rules(rules)
    return rules


def load_test_cases(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    cases = payload.get("test_cases", [])
    if not isinstance(cases, list):
        raise ValueError("test case file must contain test_cases array")
    return cases
