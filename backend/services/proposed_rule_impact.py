"""Evidence-grounded impact analysis for bounded proposed-rule sets."""

from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.services.baseline_simulation import run_simulation
from backend.services.conflict_detector import detect_conflicts
from backend.services.rule_engine import SYNTHETIC_BANNER, load_test_cases
from backend.services.rule_validator import validate_rule

PROJECT = Path(__file__).resolve().parents[2]
IMPACT_FIXTURE = PROJECT / "data" / "demo" / "proposed_rule_impact_fixture.json"
SYNTHETIC_CASES_FILE = PROJECT / "data" / "synthetic" / "synthetic_test_cases.json"
MAX_RULES = 50
MAX_SOURCES = 100
MAX_INPUT_BYTES = 262_144
SOURCE_CATEGORIES = frozenset({"rbi_requirement", "bank_policy", "other_rule"})
SOURCE_STATUSES = frozenset({"synthetic_unverified", "verified", "historical", "unresolved"})


class ImpactAnalysisError(ValueError):
    """Raised when an impact-analysis request or fixed fixture is invalid."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _rule_summary(rule: dict[str, Any]) -> str:
    return str(rule.get("rule_name") or rule.get("rule_id") or "Unnamed rule")


def _condition_summary(rule: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(rule.get("condition"))


def _action_summary(rule: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(rule.get("action"))


def _source_reference(
    rule: dict[str, Any],
    source_records: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    record = source_records.get(str(rule.get("obligation_id", "")))
    if record is None:
        return None
    source = rule.get("source_obligation") or {}
    if source.get("dataset_kind") != record["dataset_kind"]:
        return None
    for field in ("document_id", "evidence_id"):
        if source.get(field) and source.get(field) != record.get(field):
            return None
    return {
        "source_category": record["source_category"],
        "dataset_kind": record["dataset_kind"],
        "document_id": record.get("document_id"),
        "section": record.get("section"),
        "paragraph": record.get("paragraph"),
        "page": record.get("page"),
        "evidence_id": record.get("evidence_id"),
        "source_status": record["source_status"],
        "verification_status": record.get("verification_status", "unverified"),
        "quote": record.get("quote"),
        "source_label": record["source_label"],
        "rule_dataset_kind": source.get("dataset_kind"),
    }


def _validate_source_records(
    source_records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(source_records, list) or len(source_records) > MAX_SOURCES:
        raise ImpactAnalysisError(f"source_records must be a list of at most {MAX_SOURCES} items")
    indexed: dict[str, dict[str, Any]] = {}
    for record in source_records:
        if not isinstance(record, dict):
            raise ImpactAnalysisError("Each source record must be an object")
        obligation_id = record.get("obligation_id")
        if not isinstance(obligation_id, str) or not obligation_id:
            raise ImpactAnalysisError("Source records require an obligation_id")
        if obligation_id in indexed:
            raise ImpactAnalysisError(f"Duplicate source record for obligation {obligation_id}")
        if record.get("source_category") not in SOURCE_CATEGORIES:
            raise ImpactAnalysisError(f"Unsupported source category for {obligation_id}")
        if record.get("dataset_kind") not in {"synthetic", "verified", "unverified_candidate"}:
            raise ImpactAnalysisError(f"Unsupported dataset kind for {obligation_id}")
        if record.get("source_status") not in SOURCE_STATUSES:
            raise ImpactAnalysisError(f"Unsupported source status for {obligation_id}")
        if not isinstance(record.get("source_label"), str) or not record["source_label"]:
            raise ImpactAnalysisError(f"Source records require a source_label for {obligation_id}")
        for field in ("document_id", "section", "paragraph", "page", "evidence_id", "quote"):
            value = record.get(field)
            if value is not None and not isinstance(value, str):
                raise ImpactAnalysisError(f"Source field {field} must be a string or null")
        if record["dataset_kind"] == "synthetic":
            if (
                not obligation_id.startswith("DEMO-")
                or not record["source_label"].startswith(SYNTHETIC_BANNER)
                or record["source_status"] == "verified"
                or record.get("verification_status") == "verified"
            ):
                raise ImpactAnalysisError("Synthetic source records cannot claim verified or real status")
            indexed[obligation_id] = record
        elif record["dataset_kind"] == "unverified_candidate":
            if (
                not obligation_id.startswith("DEMO-")
                or not record["source_label"].startswith(SYNTHETIC_BANNER)
                or record["source_status"] == "verified"
                or record.get("verification_status") == "verified"
            ):
                raise ImpactAnalysisError("Unverified candidate source records must be explicitly synthetic")
            indexed[obligation_id] = record
        elif record["dataset_kind"] == "verified":
            corroborated = _load_verified_source_record(obligation_id)
            if corroborated is not None:
                indexed[obligation_id] = corroborated
    return indexed


def _load_verified_source_record(obligation_id: str) -> dict[str, Any] | None:
    """Return source metadata only when the verified obligation and evidence are stored."""
    processed = PROJECT / "data" / "processed"
    obligations_path = processed / "obligations.csv"
    evidence_path = processed / "evidence_passages.csv"
    registry_path = processed / "document_registry.csv"
    if not all(path.is_file() for path in (obligations_path, evidence_path, registry_path)):
        return None

    def rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    obligation = next(
        (row for row in rows(obligations_path) if row.get("obligation_id") == obligation_id),
        None,
    )
    if not obligation or obligation.get("verification_status") != "verified":
        return None
    evidence_id = obligation.get("evidence_id", "")
    document_id = obligation.get("document_id", "")
    evidence = next(
        (
            row for row in rows(evidence_path)
            if row.get("evidence_id") == evidence_id
            and row.get("document_id") == document_id
        ),
        None,
    )
    if not evidence:
        return None
    registry = next(
        (row for row in rows(registry_path) if row.get("document_id") == document_id),
        None,
    )
    if not registry:
        return None
    document_status = registry.get("document_status", "")
    source_status = "historical" if document_status == "SUPERSEDED" else (
        "verified" if document_status == "ACTIVE" else "unresolved"
    )
    return {
        "obligation_id": obligation_id,
        "source_category": "rbi_requirement",
        "dataset_kind": "verified",
        "source_status": source_status,
        "verification_status": "verified",
        "source_label": f"Stored verified obligation {obligation_id}",
        "document_id": document_id,
        "section": evidence.get("section_heading") or evidence.get("section_hint"),
        "paragraph": evidence.get("paragraph_reference"),
        "page": evidence.get("printed_page_number") or evidence.get("pdf_page_index"),
        "evidence_id": evidence_id,
        "quote": evidence.get("passage_text") or evidence.get("original_text"),
    }


def _condition_is_ambiguous(condition: Any) -> bool:
    if not isinstance(condition, dict):
        return True
    return condition.get("type") != "comparison"


def _conditions_may_overlap_for_near_duplicate(
    proposed_condition: dict[str, Any],
    existing_condition: dict[str, Any],
) -> bool:
    if (
        proposed_condition.get("type") != "comparison"
        or existing_condition.get("type") != "comparison"
        or proposed_condition.get("field") != existing_condition.get("field")
        or proposed_condition.get("operator") != existing_condition.get("operator")
    ):
        return False
    operator = proposed_condition.get("operator")
    left = proposed_condition.get("value")
    right = existing_condition.get("value")
    if operator in {"gt", "gte", "lt", "lte"}:
        return (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        )
    if operator in {"in", "not_in"} and isinstance(left, list) and isinstance(right, list):
        if operator == "in":
            return any(item_left == item_right for item_left in left for item_right in right)
        return False
    return False


def _classify_pair(
    proposed: dict[str, Any],
    existing: dict[str, Any],
    conflict_findings: list[Any],
) -> list[dict[str, Any]]:
    proposed_id = proposed["rule_id"]
    existing_id = existing["rule_id"]
    proposed_condition = proposed["condition"]
    existing_condition = existing["condition"]
    proposed_action = proposed["action"]
    existing_action = existing["action"]
    same_condition = _canonical(proposed_condition) == _canonical(existing_condition)
    same_action = (
        proposed_action.get("action_type") == existing_action.get("action_type")
        and _canonical(proposed_action.get("parameters", {}))
        == _canonical(existing_action.get("parameters", {}))
    )
    categories: list[tuple[str, str, str, dict[str, Any], bool, bool]] = []

    if same_condition and same_action:
        categories.append((
            "exact_duplicate",
            "medium",
            "The condition and action parameters match exactly; rule names and wording do not determine this finding.",
            {"condition_match": True, "action_match": True},
            True,
            False,
        ))
    elif (
        not same_condition
        and same_action
        and proposed_condition.get("type") == "comparison"
        and existing_condition.get("type") == "comparison"
        and proposed_condition.get("field") == existing_condition.get("field")
        and proposed_condition.get("operator") == existing_condition.get("operator")
        and proposed_condition.get("value") != existing_condition.get("value")
        and _conditions_may_overlap_for_near_duplicate(proposed_condition, existing_condition)
    ):
        categories.append((
            "possible_near_duplicate",
            "low",
            "Both rules use the same field, operator, and action but different values; semantic equivalence is not established.",
            {},
            False,
            True,
        ))

    detector_categories = set()
    for finding in conflict_findings:
        kind = finding.finding_type
        if kind in {"identical_condition_incompatible_actions", "overlapping_incompatible_actions"}:
            if _condition_is_ambiguous(proposed_condition) or _condition_is_ambiguous(existing_condition):
                category = "ambiguous_conditions"
            else:
                category = "direct_contradiction" if same_condition else "conditional_contradiction"
            detector_categories.add(category)
            categories.append((
                category,
                finding.severity,
                finding.explanation,
                finding.evidence,
                finding.overlap_established,
                finding.requires_human_review,
            ))
        elif kind == "ambiguous_overlap_requires_review":
            detector_categories.add("ambiguous_conditions")
            categories.append((
                "ambiguous_conditions",
                finding.severity,
                finding.explanation,
                finding.evidence,
                finding.overlap_established,
                finding.requires_human_review,
            ))
        elif kind in {"missing_or_incomplete_condition", "overly_broad_condition"}:
            detector_categories.add("ambiguous_conditions")
            categories.append((
                "ambiguous_conditions",
                finding.severity,
                finding.explanation,
                finding.evidence,
                finding.overlap_established,
                finding.requires_human_review,
            ))

    if not detector_categories and not same_action:
        categories.append((
            "action_compatibility_unresolved",
            "medium",
            "The current detector does not resolve this condition overlap and action pair; review is required before concluding there is no impact.",
            {},
            same_condition,
            True,
        ))

    return [
        {
            "category": category,
            "severity": severity,
            "explanation": explanation,
            "rule_ids": [proposed_id, existing_id],
            "evidence": evidence,
            "overlap_established": overlap_established,
            "requires_human_review": requires_human_review,
        }
        for category, severity, explanation, evidence, overlap_established, requires_human_review
        in categories
    ]


def analyze_proposed_rule(
    proposed_rule: dict[str, Any],
    existing_rules: list[dict[str, Any]],
    *,
    source_records: list[dict[str, Any]] | None = None,
    ruleset_complete: bool = False,
    test_cases_path: Path = SYNTHETIC_CASES_FILE,
) -> dict[str, Any]:
    """Analyze one proposed rule against a bounded, explicitly supplied ruleset."""
    if not isinstance(proposed_rule, dict):
        raise ImpactAnalysisError("proposed_rule must be an object")
    if not isinstance(existing_rules, list) or len(existing_rules) > MAX_RULES:
        raise ImpactAnalysisError(f"existing_rules must be a list of at most {MAX_RULES} items")
    if any(not isinstance(rule, dict) for rule in existing_rules):
        raise ImpactAnalysisError("Every existing rule must be an object")
    if not isinstance(ruleset_complete, bool):
        raise ImpactAnalysisError("ruleset_complete must be a boolean")
    try:
        input_size = len(
            json.dumps(
                {
                    "proposed_rule": proposed_rule,
                    "existing_rules": existing_rules,
                    "source_records": source_records or [],
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ImpactAnalysisError("Impact inputs must be finite JSON-compatible values") from exc
    if input_size > MAX_INPUT_BYTES:
        raise ImpactAnalysisError(f"Impact inputs must not exceed {MAX_INPUT_BYTES} bytes")

    proposed = deepcopy(proposed_rule)
    existing = deepcopy(existing_rules)
    rules = [*existing, proposed]
    rule_ids = [rule.get("rule_id") for rule in rules]
    duplicate_ids = {item for item in rule_ids if rule_ids.count(item) > 1}
    validation = [
        {"rule_id": str(rule.get("rule_id", "")), "errors": validate_rule(rule)}
        for rule in rules
    ]
    if proposed.get("status") not in {"draft", "test", "pending_approval"}:
        validation[-1]["errors"].append(
            "Proposed impact rule must be pre-approval (draft, test, or pending_approval)"
        )
    validation.extend(
        {
            "rule_id": str(rule.get("rule_id", "")),
            "errors": ["rule_id must be unique within the assessed ruleset"],
        }
        for rule in rules
        if rule.get("rule_id") in duplicate_ids
    )
    source_index = _validate_source_records(source_records or [])
    source_reference = _source_reference(proposed, source_index)
    missing_evidence: list[str] = []
    uncertainty_warnings: list[str] = []
    findings: list[dict[str, Any]] = []

    for item in validation:
        if item["errors"]:
            findings.append({
                "category": "invalid_rule_schema",
                "severity": "high",
                "existing_rule": None,
                "source_category": None,
                "source_reference": None,
                "proposed_condition": _condition_summary(proposed),
                "existing_condition": None,
                "proposed_action": _action_summary(proposed),
                "existing_action": None,
                "explanation": f"Rule {item['rule_id']} failed restricted schema validation.",
                "validation_errors": item["errors"],
                "recommended_next_action": "Correct the rule schema before assessing its impact.",
            })

    proposed_ambiguous = _condition_is_ambiguous(proposed.get("condition"))
    if proposed_ambiguous:
        uncertainty_warnings.append("The proposed rule has missing, broad, or unsupported condition semantics.")
        findings.append({
            "category": "ambiguous_conditions",
            "severity": "medium",
            "existing_rule": None,
            "source_category": None,
            "source_reference": source_reference,
            "proposed_condition": _condition_summary(proposed),
            "existing_condition": None,
            "proposed_action": _action_summary(proposed),
            "existing_action": None,
            "explanation": "The proposed condition cannot be assessed precisely with the available condition semantics.",
            "recommended_next_action": "Clarify and narrow the condition before relying on this analysis.",
        })

    if source_reference is None:
        missing_evidence.append(f"No mapped source record was supplied for obligation {proposed.get('obligation_id', '')}.")
        uncertainty_warnings.append("No exact supporting evidence reference is available for the proposed rule.")
    elif source_reference["dataset_kind"] != "verified":
        missing_evidence.append("The proposed rule source is not a verified obligation.")
    if source_reference and source_reference["source_status"] in {"historical", "unresolved"}:
        uncertainty_warnings.append(
            f"Source status is {source_reference['source_status']}; current applicability is not established."
        )
        findings.append({
            "category": "historical_or_unresolved_source",
            "severity": "medium",
            "existing_rule": None,
            "source_category": source_reference["source_category"],
            "source_reference": source_reference,
            "proposed_condition": _condition_summary(proposed),
            "existing_condition": None,
            "proposed_action": _action_summary(proposed),
            "existing_action": None,
            "explanation": "The mapped source is historical or has unresolved status; no current legal conclusion can be drawn.",
            "recommended_next_action": "Verify source currency and applicability with an authorized reviewer.",
        })

    for other in existing:
        pair_is_valid = (
            not validate_rule(proposed)
            and not validate_rule(other)
            and proposed.get("status") in {"draft", "test", "pending_approval"}
        )
        if not pair_is_valid:
            continue
        pair_conflicts = detect_conflicts([proposed, other])
        pair_findings = _classify_pair(proposed, other, pair_conflicts)
        if (
            _condition_is_ambiguous(other.get("condition"))
            and not any(item["category"] == "ambiguous_conditions" for item in pair_findings)
        ):
            pair_findings.append({
                "category": "ambiguous_conditions",
                "severity": "medium",
                "explanation": "The existing rule condition is outside the conflict detector's reliable comparison subset.",
                "rule_ids": [proposed["rule_id"], other["rule_id"]],
                "evidence": {},
                "overlap_established": False,
                "requires_human_review": True,
            })
        reference = _source_reference(other, source_index)
        if reference and reference["dataset_kind"] != "verified":
            missing_evidence.append(
                f"Existing rule {other.get('rule_id', '')} maps to a non-verified source."
            )
        if reference and reference["source_status"] in {"historical", "unresolved"}:
            warning = (
                f"Existing rule {other.get('rule_id', '')} source status is "
                f"{reference['source_status']}; current applicability is not established."
            )
            uncertainty_warnings.append(warning)
            findings.append({
                "category": "historical_or_unresolved_source",
                "severity": "medium",
                "existing_rule": {
                    "rule_id": other.get("rule_id"),
                    "summary": _rule_summary(other),
                },
                "source_category": reference["source_category"],
                "source_reference": reference,
                "proposed_condition": _condition_summary(proposed),
                "existing_condition": _condition_summary(other),
                "proposed_action": _action_summary(proposed),
                "existing_action": _action_summary(other),
                "explanation": "The existing rule's mapped source is historical or unresolved; current applicability is not established.",
                "recommended_next_action": "Verify the source status and applicability before relying on this mapping.",
            })
        for pair in pair_findings:
            source_category = reference["source_category"] if reference else "other_rule"
            explanation = pair["explanation"]
            if (
                pair["category"] == "conditional_contradiction"
                and pair["overlap_established"]
            ):
                explanation = (
                    f"{explanation} The existing detector established overlap using its bounded sample inputs; "
                    "confirm applicability with a reviewer."
                )
            findings.append({
                "category": pair["category"],
                "severity": pair["severity"],
                "existing_rule": {
                    "rule_id": other.get("rule_id"),
                    "summary": _rule_summary(other),
                },
                "source_category": source_category,
                "source_reference": reference,
                "proposed_condition": _condition_summary(proposed),
                "existing_condition": _condition_summary(other),
                "proposed_action": _action_summary(proposed),
                "existing_action": _action_summary(other),
                "explanation": explanation,
                "detector_evidence": pair["evidence"],
                "overlap_established": pair["overlap_established"],
                "requires_human_review": pair["requires_human_review"],
                "recommended_next_action": "Review the overlap and resolve or document the relationship before governance approval.",
            })
            if reference and reference["source_category"] in {"rbi_requirement", "bank_policy"}:
                category = (
                    "potential_regulatory_impact"
                    if reference["source_category"] == "rbi_requirement"
                    else "potential_bank_policy_impact"
                )
                status_warning = (
                    "This source is synthetic or unverified; this is not a regulatory or policy conclusion."
                    if reference["dataset_kind"] != "verified"
                    or reference["verification_status"] != "verified"
                    or reference["source_status"] != "verified"
                    else "Potential mapped impact identified; this is not a compliance determination."
                )
                findings.append({
                    "category": category,
                    "severity": pair["severity"],
                    "existing_rule": {
                        "rule_id": other.get("rule_id"),
                        "summary": _rule_summary(other),
                    },
                    "source_category": reference["source_category"],
                    "source_reference": reference,
                    "proposed_condition": _condition_summary(proposed),
                    "existing_condition": _condition_summary(other),
                    "proposed_action": _action_summary(proposed),
                    "existing_action": _action_summary(other),
                    "explanation": f"{pair['explanation']} {status_warning}",
                    "recommended_next_action": "Verify the mapped source and have a qualified reviewer assess applicability.",
                })

    simulation_result: dict[str, Any] | None = None
    if not any(item["errors"] for item in validation):
        try:
            cases_payload = json.loads(Path(test_cases_path).read_text(encoding="utf-8"))
            cases = load_test_cases(Path(test_cases_path))
            simulation_result = run_simulation(
                existing,
                rules,
                cases,
                baseline_version="bounded-impact-baseline",
                proposed_version=str(proposed.get("rule_version", "")),
                cases_version=str(cases_payload.get("dataset_version", "unknown")),
                seed=cases_payload.get("seed"),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            uncertainty_warnings.append(f"Synthetic comparison simulation could not run: {type(exc).__name__}.")
            findings.append({
                "category": "insufficient_evidence",
                "severity": "medium",
                "existing_rule": None,
                "source_category": None,
                "source_reference": None,
                "proposed_condition": _condition_summary(proposed),
                "existing_condition": None,
                "proposed_action": _action_summary(proposed),
                "existing_action": None,
                "explanation": "The deterministic comparison simulation did not complete.",
                "recommended_next_action": "Check the synthetic test-case dataset and rerun the analysis.",
            })

    conflict_categories = {
        "exact_duplicate",
        "possible_near_duplicate",
        "direct_contradiction",
        "conditional_contradiction",
        "ambiguous_conditions",
        "action_compatibility_unresolved",
    }
    if (
        not proposed_ambiguous
        and not any(item["errors"] for item in validation)
        and not any(finding["category"] in conflict_categories for finding in findings)
    ):
        findings.append({
            "category": "no_conflict_detected_within_scope",
            "severity": "info",
            "existing_rule": None,
            "source_category": "other_rule",
            "source_reference": None,
            "proposed_condition": _condition_summary(proposed),
            "existing_condition": None,
            "proposed_action": _action_summary(proposed),
            "existing_action": None,
            "explanation": "No conflict was detected within the explicitly supplied ruleset; this does not establish universal compliance.",
            "recommended_next_action": "Confirm the ruleset is complete and verify all supporting sources.",
        })

    categories = {finding["category"] for finding in findings}
    if any(item["errors"] for item in validation):
        assessment_status = "invalid_proposal"
    elif any(category in categories for category in ("direct_contradiction", "conditional_contradiction")):
        assessment_status = "conflict_detected"
    elif proposed_ambiguous or missing_evidence or "historical_or_unresolved_source" in categories:
        assessment_status = "review_required"
    elif any(category in categories for category in ("potential_regulatory_impact", "potential_bank_policy_impact")):
        assessment_status = "potential_impact"
    elif "possible_near_duplicate" in categories:
        assessment_status = "review_required"
    else:
        assessment_status = "no_conflict_within_checked_scope"

    return {
        "label": SYNTHETIC_BANNER,
        "proposed_rule": {
            "rule_id": proposed.get("rule_id"),
            "summary": _rule_summary(proposed),
            "condition": _condition_summary(proposed),
            "action": _action_summary(proposed),
        },
        "assessment_status": assessment_status,
        "scope": {
            "existing_rule_count": len(existing),
            "complete_ruleset_attested": ruleset_complete,
            "coverage_warning": "Findings are limited to the explicitly supplied bounded ruleset and source records.",
        },
        "findings": findings,
        "missing_evidence": missing_evidence,
        "uncertainty_warnings": uncertainty_warnings,
        "validation_result": {
            "passed": not any(item["errors"] for item in validation),
            "results": validation,
        },
        "simulation_result": (
            {
                "simulation_type": simulation_result["simulation_type"],
                "case_count": simulation_result["case_count"],
                "changed_outcomes": simulation_result["changed_outcomes"],
                "dataset_version": simulation_result["test_case_dataset_version"],
                "seed": simulation_result["random_seed"],
            }
            if simulation_result is not None
            else None
        ),
        "approval_status": "not_performed",
        "activation_status": "inactive",
    }


def assess_fixed_impact_scenario(scenario_id: str) -> dict[str, Any]:
    """Assess one scenario from the checked-in synthetic fixture."""
    try:
        fixture = json.loads(IMPACT_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImpactAnalysisError("Synthetic impact fixture could not be loaded") from exc
    if not isinstance(fixture, dict) or fixture.get("label") != SYNTHETIC_BANNER:
        raise ImpactAnalysisError("Synthetic impact fixture is missing its synthetic-data label")
    scenarios = fixture.get("scenarios")
    if not isinstance(scenarios, list):
        raise ImpactAnalysisError("Synthetic impact fixture scenarios are invalid")
    scenario = next(
        (item for item in scenarios if item.get("scenario_id") == scenario_id),
        None,
    )
    if scenario is None:
        raise ImpactAnalysisError(f"Unknown synthetic impact scenario: {scenario_id}")
    if not isinstance(scenario.get("proposed_rule"), dict) or not isinstance(
        scenario.get("existing_rules"), list
    ):
        raise ImpactAnalysisError("Synthetic impact scenario has an invalid ruleset")
    for rule in [*scenario["existing_rules"], scenario["proposed_rule"]]:
        source = rule.get("source_obligation") or {}
        if (
            not str(rule.get("rule_id", "")).startswith("DEMO-")
            or source.get("dataset_kind") != "synthetic"
            or not str(source.get("label", "")).startswith(SYNTHETIC_BANNER)
        ):
            raise ImpactAnalysisError("Fixed impact scenarios must contain only labelled synthetic rules")
    for record in scenario.get("source_records", []):
        if (
            record.get("dataset_kind") != "synthetic"
            or not str(record.get("obligation_id", "")).startswith("DEMO-")
            or not str(record.get("source_label", "")).startswith(SYNTHETIC_BANNER)
        ):
            raise ImpactAnalysisError("Fixed impact sources must be explicitly labelled synthetic")
    result = analyze_proposed_rule(
        scenario.get("proposed_rule"),
        scenario.get("existing_rules"),
        source_records=scenario.get("source_records", []),
        ruleset_complete=scenario.get("ruleset_complete", False),
    )
    result["scenario_id"] = scenario_id
    result["scenario_description"] = scenario.get("description", "")
    result["label"] = fixture["label"]
    return result
