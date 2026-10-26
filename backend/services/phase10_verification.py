"""Isolated deterministic rule verification using OPA gating and Z3 analysis."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.services.rule_engine import evaluate_rule
from backend.services.rule_validator import validate_rule

PROJECT = Path(__file__).resolve().parents[2]
POLICY_FILE = PROJECT / "backend" / "policies" / "phase10.rego"
RULES_FILE = PROJECT / "data" / "synthetic" / "synthetic_rules_baseline.json"
CASES_FILE = PROJECT / "data" / "synthetic" / "synthetic_test_cases.json"


class VerificationError(ValueError):
    """Safe, user-visible Phase 10 verification error."""


def _opa_path(explicit: str | Path | None = None) -> str:
    candidate = str(explicit or os.environ.get("TRACERULE_OPA_BIN", "") or shutil.which("opa") or "")
    if not candidate or not Path(candidate).is_file():
        raise VerificationError("OPA is unavailable; configure TRACERULE_OPA_BIN for synthetic sandbox verification")
    return candidate


def _opa_allows(payload: dict[str, Any], *, opa_bin: str | Path | None = None) -> bool:
    binary = _opa_path(opa_bin)
    with tempfile.TemporaryDirectory(prefix="tracerule-opa-") as temp_dir:
        input_path = Path(temp_dir) / "input.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            result = subprocess.run(
                [binary, "eval", "--format=json", "--input", str(input_path), "--data", str(POLICY_FILE), "data.tracerule.phase10.allow"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise VerificationError("OPA eligibility check timed out") from exc
        if result.returncode != 0:
            raise VerificationError("OPA eligibility policy failed to evaluate")
        try:
            parsed = json.loads(result.stdout)
            value = parsed["result"][0]["expressions"][0]["value"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise VerificationError("OPA returned a malformed eligibility result") from exc
        return value is True


def _load_synthetic_inputs(rule_id: str, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        rules = json.loads(RULES_FILE.read_text(encoding="utf-8"))["rules"]
        cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["test_cases"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise VerificationError("Checked-in synthetic verification fixtures are unavailable or malformed") from exc
    rule = next((item for item in rules if item.get("rule_id") == rule_id), None)
    case = next((item for item in cases if item.get("case_id") == case_id), None)
    if not rule or not case:
        raise VerificationError("Unknown synthetic rule or test case")
    return rule, case


def _z3_condition(condition: dict[str, Any], symbols: dict[str, Any]) -> Any:
    import z3

    kind = condition.get("type")
    if kind == "comparison":
        field, op, value = condition["field"], condition["operator"], condition["value"]
        if isinstance(value, bool):
            symbol = symbols.setdefault(field, z3.Bool(field))
            if not z3.is_bool(symbol):
                raise VerificationError("Z3 condition uses a field with inconsistent value types")
        elif isinstance(value, (int, float)):
            symbol = symbols.setdefault(field, z3.Real(field))
            if not z3.is_arith(symbol):
                raise VerificationError("Z3 condition uses a field with inconsistent value types")
            value = z3.RealVal(value)
        elif isinstance(value, str):
            symbol = symbols.setdefault(field, z3.String(field))
            if not z3.is_string(symbol):
                raise VerificationError("Z3 condition uses a field with inconsistent value types")
            value = z3.StringVal(value)
        elif op in {"in", "not_in"} and isinstance(value, list) and value:
            if all(isinstance(v, str) for v in value):
                symbol = symbols.setdefault(field, z3.String(field))
                if not z3.is_string(symbol):
                    raise VerificationError("Z3 condition uses a field with inconsistent value types")
                value = [z3.StringVal(v) for v in value]
            elif all(isinstance(v, bool) for v in value):
                symbol = symbols.setdefault(field, z3.Bool(field))
                if not z3.is_bool(symbol):
                    raise VerificationError("Z3 condition uses a field with inconsistent value types")
                value = [z3.BoolVal(v) for v in value]
            elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
                symbol = symbols.setdefault(field, z3.Real(field))
                if not z3.is_arith(symbol):
                    raise VerificationError("Z3 condition uses a field with inconsistent value types")
                value = [z3.RealVal(v) for v in value]
            else:
                raise VerificationError("Z3 cannot analyze mixed membership value types")
        elif op in {"in", "not_in"} and value == []:
            return z3.BoolVal(op == "not_in")
        else:
            raise VerificationError("Z3 cannot analyze this condition value type")
        if op == "eq": return symbol == value
        if op == "ne": return symbol != value
        if op in {"lt", "lte", "gt", "gte"} and not z3.is_arith(symbol):
            raise VerificationError("Z3 ordered comparisons are supported only for numeric values")
        if op == "lt": return symbol < value
        if op == "lte": return symbol <= value
        if op == "gt": return symbol > value
        if op == "gte": return symbol >= value
        if op == "in": return z3.Or(*[symbol == v for v in value])
        if op == "not_in": return z3.And(*[symbol != v for v in value])
        raise VerificationError("Z3 does not support this comparison operator")
    if kind == "logical":
        children = [_z3_condition(item, symbols) for item in condition["operands"]]
        return z3.And(*children) if condition["operator"] == "and" else z3.Or(*children)
    if kind == "not":
        return z3.Not(_z3_condition(condition["operand"], symbols))
    raise VerificationError(f"Z3 satisfiability is unsupported for condition type '{kind}'")


def analyze_satisfiability(condition: dict[str, Any], *, timeout_ms: int = 2000) -> dict[str, str]:
    """Return logical satisfiability only; this is not a legal or compliance finding."""
    import z3

    symbols: dict[str, Any] = {}
    expression = _z3_condition(condition, symbols)
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    solver.add(expression)
    result = solver.check()
    out = {"engine": "Z3", "result": str(result), "meaning": "logical satisfiability only; not compliance or legal validity"}
    if result == z3.sat:
        out["example_model"] = str(solver.model())
    elif result == z3.unknown:
        out["reason_unknown"] = solver.reason_unknown()
    return out


def analyze_overlap(left: dict[str, Any], right: dict[str, Any], *, timeout_ms: int = 2000) -> dict[str, str]:
    """Check whether two supported conditions can both hold; overlap is not a conflict."""
    import z3

    symbols: dict[str, Any] = {}
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    solver.add(_z3_condition(left, symbols), _z3_condition(right, symbols))
    result = solver.check()
    out = {"engine": "Z3", "result": str(result), "meaning": "condition overlap only; not a legal conflict"}
    if result == z3.sat:
        out["example_model"] = str(solver.model())
    elif result == z3.unknown:
        out["reason_unknown"] = solver.reason_unknown()
    return out


def verify_synthetic_case(rule_id: str, case_id: str, *, opa_bin: str | Path | None = None) -> dict[str, Any]:
    """Validate, sandbox-gate, logically analyze, then run one fixed synthetic case."""
    rule, case = _load_synthetic_inputs(rule_id, case_id)
    errors = validate_rule(rule)
    if errors:
        return {"rule_id": rule_id, "rule_version": rule.get("rule_version"), "validation": {"valid": False, "errors": errors}, "evaluation": None}
    provenance = {"kind": "synthetic", "fixture": RULES_FILE.name, "case_id": case_id}
    opa_input = {"rule": rule, "case": {"case_id": case_id, "dataset_kind": "synthetic"}, "test_provenance": provenance}
    if not _opa_allows(opa_input, opa_bin=opa_bin):
        raise VerificationError("OPA blocked this input from synthetic evaluation")
    satisfiability = analyze_satisfiability(rule["condition"])
    evaluation = evaluate_rule(rule, case.get("inputs", {}))
    return {
        "label": "SYNTHETIC TEST ONLY — NOT A BANK POLICY OR REGULATORY CONCLUSION",
        "rule_id": rule_id,
        "rule_version": rule["rule_version"],
        "inputs": case.get("inputs", {}),
        "test_provenance": provenance,
        "validation": {"valid": True, "errors": []},
        "satisfiability": satisfiability,
        "opa_eligibility": {"engine": "OPA/Rego", "allowed": True, "meaning": "synthetic test eligibility only"},
        "evaluation": {
            "engine": "TraceRule deterministic Python evaluator",
            "matched": evaluation.matched,
            "action": evaluation.action,
            "reasoning": evaluation.reasoning,
            "missing_inputs": evaluation.missing_inputs,
            "validation_errors": evaluation.validation_errors,
        },
        "persistence": "none",
        "approval_or_activation": "not performed",
    }
