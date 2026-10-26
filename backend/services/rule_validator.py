"""Validate rules against the restricted rule schema without arbitrary code execution."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "rule_schema.json"

FORBIDDEN_CONDITION_KEYS = frozenset({"eval", "exec", "code", "python", "script", "lambda"})
RULE_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
ALLOWED_STATUSES = frozenset({"draft", "test", "pending_approval", "approved", "retired"})
ALLOWED_ACTION_TYPES = frozenset(
    {"allow", "deny", "require_review", "flag", "set_limit", "require_disclosure"}
)
ALLOWED_FIELDS = frozenset(
    {
        "customer_type",
        "loan_product",
        "loan_amount",
        "loan_tenor_months",
        "apr_percent",
        "channel",
        "has_lsp",
        "lsp_count",
        "credit_limit_increase_requested",
        "disbursement_route",
        "repayment_route",
        "data_storage_region",
        "dlg_cap_percent",
        "borrower_age",
        "income_verified",
        "jurisdiction",
    }
)
COMPARISON_OPS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in"})
LOGICAL_OPS = frozenset({"and", "or"})
AUTO_ACTIVATION_STATUSES = frozenset({"approved"})


class RuleValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


def _reject_unknown_keys(obj: dict[str, Any], allowed: frozenset[str], path: str, errors: list[str]) -> None:
    for key in obj:
        if key not in allowed:
            errors.append(f"{path}: unknown field '{key}'")
        if key in FORBIDDEN_CONDITION_KEYS:
            errors.append(f"{path}: forbidden key '{key}'")


def _validate_condition(node: Any, path: str, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append(f"{path}: condition must be an object")
        return
    _reject_unknown_keys(
        node,
        frozenset({"type", "field", "operator", "value", "operands", "operand", "reason"}),
        path,
        errors,
    )
    node_type = node.get("type")
    if node_type == "comparison":
        field = node.get("field")
        if field not in ALLOWED_FIELDS:
            errors.append(f"{path}: unsupported condition field '{field}'")
        op = node.get("operator")
        if op not in COMPARISON_OPS:
            errors.append(f"{path}: unsupported operator '{op}'")
        if "value" not in node:
            errors.append(f"{path}: comparison requires value")
        elif op in {"in", "not_in"} and not isinstance(node.get("value"), list):
            errors.append(f"{path}: operator '{op}' requires list value")
    elif node_type == "logical":
        op = node.get("operator")
        if op not in LOGICAL_OPS:
            errors.append(f"{path}: unsupported logical operator '{op}'")
        operands = node.get("operands")
        if not isinstance(operands, list) or not operands:
            errors.append(f"{path}: logical condition requires non-empty operands")
        elif isinstance(operands, list):
            for index, child in enumerate(operands):
                _validate_condition(child, f"{path}.operands[{index}]", errors)
    elif node_type == "not":
        if "operand" not in node:
            errors.append(f"{path}: not condition requires operand")
        else:
            _validate_condition(node["operand"], f"{path}.operand", errors)
    elif node_type in {"missing", "broad"}:
        pass
    else:
        errors.append(f"{path}: unsupported condition type '{node_type}'")


def _validate_action(action: Any, path: str, errors: list[str]) -> None:
    if not isinstance(action, dict):
        errors.append(f"{path}: action must be an object")
        return
    _reject_unknown_keys(action, frozenset({"action_type", "parameters", "message"}), path, errors)
    action_type = action.get("action_type")
    if action_type not in ALLOWED_ACTION_TYPES:
        errors.append(f"{path}: invalid action_type '{action_type}'")
    params = action.get("parameters")
    if not isinstance(params, dict):
        errors.append(f"{path}: action.parameters must be an object")
    elif isinstance(params, dict):
        for key, value in params.items():
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                errors.append(f"{path}.parameters.{key}: unsupported parameter type")


def validate_rule(rule: dict[str, Any], *, allow_approved: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(rule, dict):
        return ["rule must be an object"]
    allowed_top = frozenset(
        {
            "rule_id",
            "obligation_id",
            "rule_name",
            "condition",
            "action",
            "source_obligation",
            "rule_version",
            "status",
            "created_at",
            "approved_by",
            "notes",
        }
    )
    _reject_unknown_keys(rule, allowed_top, "rule", errors)

    rule_id = rule.get("rule_id", "")
    if not isinstance(rule_id, str) or not RULE_ID_PATTERN.match(rule_id):
        errors.append("rule_id: invalid format")

    status = rule.get("status")
    if status not in ALLOWED_STATUSES:
        errors.append(f"status: must be one of {sorted(ALLOWED_STATUSES)}")
    if status == "approved" and not allow_approved:
        if not rule.get("approved_by"):
            errors.append("status approved requires approved_by (human workflow)")
    if status in {"draft", "test", "pending_approval"} and rule.get("approved_by"):
        errors.append("approved_by must be null until human approval")

    version = rule.get("rule_version", "")
    if not isinstance(version, str) or not VERSION_PATTERN.match(version):
        errors.append("rule_version: expected semver like 1.0.0")

    created_at = rule.get("created_at", "")
    if isinstance(created_at, str):
        try:
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("created_at: invalid ISO-8601 timestamp")
    else:
        errors.append("created_at: must be a string")

    source = rule.get("source_obligation")
    if not isinstance(source, dict):
        errors.append("source_obligation: required object")
    else:
        _reject_unknown_keys(
            source,
            frozenset({"label", "dataset_kind", "document_id", "evidence_id", "source_page"}),
            "source_obligation",
            errors,
        )
        kind = source.get("dataset_kind")
        if kind not in {"synthetic", "verified", "unverified_candidate"}:
            errors.append("source_obligation.dataset_kind: invalid value")

    _validate_condition(rule.get("condition"), "condition", errors)
    _validate_action(rule.get("action"), "action", errors)

    raw = json.dumps(rule)
    if any(token in raw.lower() for token in ("__import__", "eval(", "exec(", "compile(")):
        errors.append("rule contains forbidden code-like tokens")

    return errors


def validate_rules(rules: list[dict[str, Any]]) -> None:
    errors: list[str] = []
    for index, rule in enumerate(rules):
        errors.extend(f"rules[{index}].{err}" for err in validate_rule(rule))
    if errors:
        raise RuleValidationError(errors)
