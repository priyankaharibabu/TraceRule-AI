"""Evidence-aware comparison for reviewed structured rules.

Text overlap is reported separately from condition/action comparison. No
example transactions or synthetic rule files are loaded here.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from backend.services.rule_engine import _eval_condition
from backend.services.rule_validator import validate_rule

INCOMPATIBLE_ACTION_PAIRS = frozenset({("allow", "deny"), ("deny", "allow"), ("set_limit", "deny"), ("deny", "set_limit")})


@dataclass(frozen=True)
class RuleReference:
    rule_id: str | None
    rule_version: str | None
    source_category: str
    source_document: str | None
    policy_id: str | None
    clause_id: str | None
    source_text: str
    verification_status: str
    lifecycle_status: str
    source_status: str = "unknown"
    effective_from: str | None = None
    effective_to: str | None = None
    evidence_reference: str | None = None
    rule_definition: dict[str, Any] | None = None
    execution_capability: str = "not_available"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _tokens(text: str) -> set[str]:
    ignored = {"the", "and", "for", "from", "that", "this", "with", "shall", "must", "may", "will", "their", "under", "where", "such", "each", "into", "between", "have", "has", "are", "was", "were", "not", "any", "all", "bank", "banks"}
    return {t for t in re.findall(r"[a-z0-9]+", text.casefold()) if len(t) > 2 and t not in ignored}


def _text_screening(left: RuleReference, right: RuleReference) -> dict[str, Any]:
    a = re.sub(r"\s+", " ", left.source_text).strip().casefold()
    b = re.sub(r"\s+", " ", right.source_text).strip().casefold()
    wa, wb = _tokens(a), _tokens(b)
    strength = len(wa & wb) / max(1, min(len(wa), len(wb)))
    exact = bool(a) and a == b
    return {
        "category": "possible_duplicate_wording" if exact else "related_source_passage" if strength >= 0.35 else "no_related_wording_found",
        "match_strength": round(strength, 3),
        "basis": "exact normalized wording" if exact else "shared-token screening" if strength >= 0.35 else "no material token overlap",
        "warning": "Wording similarity is a screening signal only; it does not establish legal equivalence or conflict.",
    }


def _atomic_conditions_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    if a.get("type") != "comparison" or b.get("type") != "comparison":
        return None
    for node in (a, b):
        value = node.get("value")
        values = value if isinstance(value, list) else [value]
        if node.get("operator") in {"lt", "lte", "gt", "gte"} and any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(float(v)) for v in values):
            return None
    if a.get("field") != b.get("field"):
        return _atomic_condition_satisfiable(a) and _atomic_condition_satisfiable(b)
    field = a["field"]
    nodes = (a, b)
    values: set[Any] = set()
    try:
        for node in nodes:
            value = node.get("value")
            values.update(value if isinstance(value, list) else [value])
    except TypeError:
        return None
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    if any(node.get("operator") in {"lt", "lte", "gt", "gte"} for node in nodes) and not numeric:
        return None
    candidates: set[Any] = set(values)
    if numeric and values:
        ordered = sorted(float(v) for v in values)
        candidates.update({ordered[0] - 1.0, ordered[-1] + 1.0})
        for item in ordered:
            candidates.update({item - 0.5, item + 0.5})
        for lo, hi in zip(ordered, ordered[1:]):
            candidates.add((lo + hi) / 2.0)
    elif values and all(isinstance(v, bool) for v in values):
        candidates.update({True, False})
    else:
        candidates.add("__OTHER_VALUE_NOT_IN_SOURCE__")
    for candidate in candidates:
        sample = {field: candidate}
        trace_a: list[str] = []
        trace_b: list[str] = []
        missing_a: list[str] = []
        missing_b: list[str] = []
        if _eval_condition(a, sample, trace_a, missing_a) and _eval_condition(b, sample, trace_b, missing_b):
            return True
    return False


def _atomic_condition_candidates(node: dict[str, Any]) -> set[Any]:
    value = node.get("value")
    try:
        values = set(value if isinstance(value, list) else [value])
    except TypeError:
        return set()
    numeric = bool(values) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    candidates: set[Any] = set(values)
    if numeric:
        ordered = sorted(float(v) for v in values)
        candidates.update({ordered[0] - 1.0, ordered[-1] + 1.0})
        for item in ordered:
            candidates.update({item - 0.5, item + 0.5})
        candidates.update((lo + hi) / 2.0 for lo, hi in zip(ordered, ordered[1:]))
    elif values and all(isinstance(v, bool) for v in values):
        candidates.update({True, False})
    else:
        candidates.add("__OTHER_VALUE_NOT_IN_SOURCE__")
    return candidates


def _atomic_condition_satisfiable(node: dict[str, Any]) -> bool:
    if node.get("operator") == "in" and not node.get("value"):
        return False
    field = node.get("field")
    if node.get("operator") in {"lt", "lte", "gt", "gte"}:
        try:
            if not isinstance(node.get("value"), (int, float)) or isinstance(node.get("value"), bool) or not math.isfinite(float(node["value"])):
                return False
        except (TypeError, ValueError):
            return False
    for candidate in _atomic_condition_candidates(node):
        trace: list[str] = []
        missing: list[str] = []
        if _eval_condition(node, {field: candidate}, trace, missing):
            return True
    return False


def _conditions_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    if _canonical(a) == _canonical(b):
        return True
    return _atomic_conditions_overlap(a, b)


def _actions_incompatible(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    pair = (a.get("action_type"), b.get("action_type"))
    if pair in INCOMPATIBLE_ACTION_PAIRS:
        return True
    if pair == ("set_limit", "set_limit"):
        return a.get("parameters", {}).get("max_amount") != b.get("parameters", {}).get("max_amount")
    if _canonical(a) == _canonical(b):
        return False
    return None


def _existing_is_current_and_approved(source: RuleReference, today: date | None = None) -> tuple[bool, list[str]]:
    current_day = today or date.today()
    missing = []
    if source.verification_status != "verified":
        missing.append("source and rule verification is not confirmed")
    if source.source_status != "verified_current":
        missing.append("source currentness is not verified")
    if source.lifecycle_status != "approved":
        missing.append("source rule is not approved")
    if not source.source_document:
        missing.append("source document reference is missing")
    if source.source_category == "bank_policy":
        if not source.policy_id:
            missing.append("policy ID is missing")
        if not source.clause_id:
            missing.append("policy clause reference is missing")
    try:
        start = date.fromisoformat(source.effective_from) if source.effective_from else None
        end = date.fromisoformat(source.effective_to) if source.effective_to else None
    except ValueError:
        start, end = None, None
        missing.append("effective date is malformed")
    if not start:
        missing.append("effective-from date is missing")
    elif start > current_day:
        missing.append("rule is not yet effective")
    if end and end < current_day:
        missing.append("rule has expired")
    if source.source_category == "RBI" and source.evidence_reference is None:
        missing.append("RBI evidence reference is missing")
    return not missing, missing


def compare_rule_pair(
    existing: RuleReference,
    proposed: RuleReference,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Compare one proposed definition with one evidence-bound existing rule.

    This function deliberately does not execute rules. Execution additionally
    requires an engine-eligible source mapping and a genuine authorized input set.
    """
    wording = _text_screening(existing, proposed)
    source_eligible, source_gaps = _existing_is_current_and_approved(existing, today)
    definition = existing.rule_definition
    proposed_definition = proposed.rule_definition
    if not source_eligible:
        structural = {
            "category": "insufficient_evidence",
            "condition_overlap": "not_assessed",
            "explanation": "The existing rule is not verified, approved, current, and source-traceable enough for rule comparison.",
            "missing_information": source_gaps,
        }
    elif not isinstance(definition, dict) or not isinstance(proposed_definition, dict):
        structural = {
            "category": "insufficient_evidence",
            "condition_overlap": "not_assessed",
            "explanation": "A reviewed structured definition is missing for one or both clauses; natural-language text is not executable rule logic.",
            "missing_information": ["reviewed condition and action definitions are required for both rules"],
        }
    else:
        errors = validate_rule(definition) + validate_rule(proposed_definition)
        if errors:
            structural = {
                "category": "insufficient_evidence",
                "condition_overlap": "not_assessed",
                "explanation": "One or both structured definitions failed rule-schema validation.",
                "missing_information": errors,
            }
        else:
            same_condition = _canonical(definition["condition"]) == _canonical(proposed_definition["condition"])
            same_action = _canonical(definition["action"]) == _canonical(proposed_definition["action"])
            overlap = _conditions_overlap(definition["condition"], proposed_definition["condition"])
            incompatible = _actions_incompatible(definition["action"], proposed_definition["action"])
            if same_condition and same_action:
                category = "potentially_equivalent_requirements"
            elif overlap is True and incompatible:
                category = "possible_conflict_requiring_review"
            elif overlap is False:
                category = "no_conflict_detected_in_available_verified_rules"
            elif overlap is True and incompatible is False:
                category = "no_conflict_detected_in_available_verified_rules"
            else:
                category = "insufficient_evidence"
            structural = {
                "category": category,
                "condition_overlap": "overlap" if overlap is True else "disjoint" if overlap is False else "unknown",
                "conditions_equivalent": same_condition,
                "actions_equivalent": same_action,
                "actions_incompatible": incompatible if incompatible is not None else "unknown",
                "explanation": "Structured condition/action analysis; confirm applicability and source context with a reviewer.",
                "missing_information": (["condition form is outside the supported overlap analysis"] if overlap is None else []) + (["action compatibility is not established for this action combination"] if incompatible is None else []),
            }

    return {
        "existing_rule": asdict(existing) | {"rule_definition": None},
        "proposed_rule": asdict(proposed) | {"rule_definition": None},
        "wording_screening": wording,
        "structured_comparison": structural,
        "execution": {
            "status": "not_executed",
            "inputs_identical": False,
            "existing_output": None,
            "proposed_output": None,
            "reason": "Real transaction impact is not measured by this rule-only comparison; an engine-eligible reviewed mapping and genuine authorized transaction records are required.",
        },
    }
