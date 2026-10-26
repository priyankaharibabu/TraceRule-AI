"""Coverage for bounded, evidence-grounded synthetic impact analysis."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.services import document_analyzer
from backend.services.proposed_rule_impact import (
    IMPACT_FIXTURE,
    ImpactAnalysisError,
    analyze_proposed_rule,
    assess_fixed_impact_scenario,
)
from backend.services.rule_engine import SYNTHETIC_BANNER
from backend.services.synthetic_demo import PROCESSED_DATA_DIR

client = TestClient(document_analyzer.app)


def make_rule(
    rule_id="DEMO-RULE-001",
    *,
    condition=None,
    action="allow",
    obligation_id="DEMO-OBL-001",
):
    return {
        "rule_id": rule_id,
        "obligation_id": obligation_id,
        "rule_name": f"Synthetic {rule_id}",
        "condition": condition or {
            "type": "comparison",
            "field": "loan_product",
            "operator": "eq",
            "value": "personal",
        },
        "action": {"action_type": action, "parameters": {}},
        "source_obligation": {
            "label": f"{SYNTHETIC_BANNER}: {obligation_id}",
            "dataset_kind": "synthetic",
        },
        "rule_version": "1.0.0",
        "status": "test",
        "created_at": "2026-01-15T10:00:00+00:00",
        "approved_by": None,
        "notes": "Synthetic test fixture only.",
    }


def source_record(
    obligation_id="DEMO-OBL-001",
    *,
    category="rbi_requirement",
    source_status="synthetic_unverified",
    verification_status="unverified",
    dataset_kind="synthetic",
):
    return {
        "obligation_id": obligation_id,
        "source_category": category,
        "dataset_kind": dataset_kind,
        "source_status": source_status,
        "verification_status": verification_status,
        "source_label": f"{SYNTHETIC_BANNER}: synthetic source",
        "document_id": "DEMO-DOC-001",
        "section": "Synthetic section",
        "paragraph": "DEMO-PARA-001",
        "evidence_id": "DEMO-EVIDENCE-001",
        "quote": "SYNTHETIC TEXT: example clause, not an actual RBI or bank policy.",
    }


def categories(report):
    return {finding["category"] for finding in report["findings"]}


def test_exact_duplicate_compares_conditions_and_action_parameters():
    proposed = make_rule("DEMO-RULE-PROPOSED")
    existing = make_rule("DEMO-RULE-EXISTING")

    report = analyze_proposed_rule(proposed, [existing])

    assert "exact_duplicate" in categories(report)
    assert report["validation_result"]["passed"]
    assert report["approval_status"] == "not_performed"
    assert report["activation_status"] == "inactive"


def test_possible_near_duplicate_requires_review_not_semantic_claim():
    proposed = make_rule(
        "DEMO-RULE-PROPOSED",
        condition={"type": "comparison", "field": "loan_amount", "operator": "gte", "value": 50000},
    )
    existing = make_rule(
        "DEMO-RULE-EXISTING",
        condition={"type": "comparison", "field": "loan_amount", "operator": "gte", "value": 50001},
    )

    report = analyze_proposed_rule(proposed, [existing])

    finding = next(item for item in report["findings"] if item["category"] == "possible_near_duplicate")
    assert "not established" in finding["explanation"]
    assert report["assessment_status"] == "review_required"


def test_direct_contradiction_uses_existing_conflict_detector():
    proposed = make_rule("DEMO-RULE-PROPOSED", action="deny")
    existing = make_rule("DEMO-RULE-EXISTING", action="allow")

    report = analyze_proposed_rule(proposed, [existing])

    assert "direct_contradiction" in categories(report)
    assert report["assessment_status"] == "conflict_detected"


def test_conditional_contradiction_uses_overlapping_different_conditions():
    proposed = make_rule("DEMO-RULE-PROPOSED", action="deny")
    existing = make_rule(
        "DEMO-RULE-EXISTING",
        action="allow",
        condition={"type": "comparison", "field": "jurisdiction", "operator": "eq", "value": "IN"},
    )

    report = analyze_proposed_rule(proposed, [existing])

    assert "conditional_contradiction" in categories(report)
    finding = next(item for item in report["findings"] if item["category"] == "conditional_contradiction")
    assert finding["proposed_condition"] != finding["existing_condition"]


def test_mapped_synthetic_regulatory_source_is_potential_impact_not_a_legal_conclusion():
    proposed = make_rule("DEMO-RULE-PROPOSED", action="deny")
    existing = make_rule("DEMO-RULE-EXISTING", action="allow")
    report = analyze_proposed_rule(
        proposed,
        [existing],
        source_records=[source_record(existing["obligation_id"])],
    )

    finding = next(item for item in report["findings"] if item["category"] == "potential_regulatory_impact")
    assert finding["source_reference"]["dataset_kind"] == "synthetic"
    assert finding["source_reference"]["verification_status"] == "unverified"
    assert finding["source_reference"]["quote"].startswith("SYNTHETIC TEXT:")
    assert "not a regulatory or policy conclusion" in finding["explanation"]


def test_synthetic_bank_policy_impact_is_distinguished_from_regulatory_impact():
    proposed = make_rule("DEMO-RULE-PROPOSED", action="deny")
    existing = make_rule("DEMO-RULE-EXISTING", action="allow")
    report = analyze_proposed_rule(
        proposed,
        [existing],
        source_records=[source_record(existing["obligation_id"], category="bank_policy")],
    )

    assert "potential_bank_policy_impact" in categories(report)
    assert "potential_regulatory_impact" not in categories(report)


def test_unstored_claim_of_verified_source_is_not_cited():
    report = analyze_proposed_rule(
        make_rule(),
        [],
        source_records=[
            source_record(
                dataset_kind="verified",
                source_status="verified",
                verification_status="verified",
            )
        ],
    )

    assert report["findings"][0]["source_reference"] is None
    assert any("No exact supporting evidence" in warning for warning in report["uncertainty_warnings"])
    assert any("No mapped source record" in message for message in report["missing_evidence"])


def test_undefined_action_compatibility_is_not_reported_as_no_conflict():
    proposed = make_rule("DEMO-RULE-PROPOSED", action="flag")
    existing = make_rule("DEMO-RULE-EXISTING", action="allow")

    report = analyze_proposed_rule(proposed, [existing])

    assert "action_compatibility_unresolved" in categories(report)
    assert "no_conflict_detected_within_scope" not in categories(report)


def test_no_conflict_is_scoped_and_does_not_claim_compliance():
    proposed = make_rule("DEMO-RULE-PROPOSED")
    existing = make_rule(
        "DEMO-RULE-EXISTING",
        condition={"type": "comparison", "field": "jurisdiction", "operator": "eq", "value": "IN"},
    )

    report = analyze_proposed_rule(proposed, [existing], ruleset_complete=True)

    assert "no_conflict_detected_within_scope" in categories(report)
    assert "possible_near_duplicate" not in categories(report)
    assert report["scope"]["complete_ruleset_attested"] is True
    assert "does not establish universal compliance" in next(
        item["explanation"] for item in report["findings"]
        if item["category"] == "no_conflict_detected_within_scope"
    )


@pytest.mark.parametrize(
    ("records", "expected_missing"),
    [
        ([], "No mapped source record"),
        ([source_record(dataset_kind="unverified_candidate")], "not a verified obligation"),
    ],
)
def test_missing_or_unverified_evidence_requires_review(records, expected_missing):
    proposed = make_rule()
    if records:
        proposed["source_obligation"]["dataset_kind"] = "unverified_candidate"
    report = analyze_proposed_rule(proposed, [], source_records=records)

    assert report["assessment_status"] == "review_required"
    assert any(expected_missing in message for message in report["missing_evidence"])


@pytest.mark.parametrize("source_status", ["historical", "unresolved"])
def test_historical_or_unresolved_source_status_is_reported(source_status):
    report = analyze_proposed_rule(
        make_rule(),
        [],
        source_records=[source_record(source_status=source_status)],
    )

    assert "historical_or_unresolved_source" in categories(report)
    assert any(source_status in warning for warning in report["uncertainty_warnings"])
    assert report["assessment_status"] == "review_required"


@pytest.mark.parametrize(
    "condition",
    [
        {"type": "broad", "reason": "Synthetic broad condition"},
        {
            "type": "logical",
            "operator": "and",
            "operands": [
                {"type": "comparison", "field": "loan_product", "operator": "eq", "value": "personal"}
            ],
        },
        {"type": "missing", "reason": "Synthetic incomplete condition"},
    ],
)
def test_ambiguous_condition_is_not_reported_as_clear(condition):
    report = analyze_proposed_rule(
        make_rule(condition=condition),
        [],
    )

    assert "ambiguous_conditions" in categories(report)
    assert report["assessment_status"] == "review_required"


def test_invalid_rule_schema_blocks_simulation():
    proposed = make_rule()
    proposed["action"]["action_type"] = "execute_code"

    report = analyze_proposed_rule(proposed, [])

    assert not report["validation_result"]["passed"]
    assert report["simulation_result"] is None
    assert report["assessment_status"] == "invalid_proposal"
    assert "invalid_rule_schema" in categories(report)


def test_already_approved_proposal_is_not_accepted_for_impact_analysis():
    proposed = make_rule()
    proposed["status"] = "approved"
    proposed["approved_by"] = "caller-supplied"

    report = analyze_proposed_rule(proposed, [])

    assert not report["validation_result"]["passed"]
    assert report["simulation_result"] is None
    assert report["approval_status"] == "not_performed"
    assert report["activation_status"] == "inactive"


def test_invalid_existing_rule_is_reported_without_attempting_pair_comparison():
    existing = make_rule("DEMO-RULE-EXISTING")
    existing.pop("condition")

    report = analyze_proposed_rule(make_rule("DEMO-RULE-PROPOSED"), [existing])

    assert not report["validation_result"]["passed"]
    assert any(
        finding["category"] == "invalid_rule_schema"
        and finding["existing_rule"] is None
        for finding in report["findings"]
    )
    assert report["simulation_result"] is None


def test_ruleset_size_is_bounded():
    proposed = make_rule("DEMO-RULE-PROPOSED")
    rules = [make_rule(f"DEMO-RULE-{index:03d}") for index in range(51)]

    with pytest.raises(ImpactAnalysisError, match="at most 50"):
        analyze_proposed_rule(proposed, rules)


def test_fixed_fixture_has_only_labelled_synthetic_rules_and_sources():
    fixture = json.loads(IMPACT_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["label"] == SYNTHETIC_BANNER
    for scenario in fixture["scenarios"]:
        for rule in [scenario["proposed_rule"], *scenario["existing_rules"]]:
            assert rule["rule_id"].startswith("DEMO-")
            assert rule["source_obligation"]["dataset_kind"] == "synthetic"
            assert rule["source_obligation"]["label"].startswith(SYNTHETIC_BANNER)
        for source in scenario["source_records"]:
            assert source["dataset_kind"] == "synthetic"
            assert source["source_label"].startswith(SYNTHETIC_BANNER)


def test_fixed_scenarios_are_bounded_labelled_and_never_approve_or_activate():
    for scenario_id in (
        "DEMO-IMPACT-REGULATORY",
        "DEMO-IMPACT-BANK-POLICY",
        "DEMO-IMPACT-CLEAN-SCOPE",
        "DEMO-IMPACT-AMBIGUOUS",
    ):
        result = assess_fixed_impact_scenario(scenario_id)
        assert result["label"] == SYNTHETIC_BANNER
        assert result["approval_status"] == "not_performed"
        assert result["activation_status"] == "inactive"
        assert result["simulation_result"] is not None

    with pytest.raises(ImpactAnalysisError, match="Unknown synthetic impact scenario"):
        assess_fixed_impact_scenario("DEMO-IMPACT-UNKNOWN")


def test_impact_api_is_fixed_get_only_and_does_not_mutate_regulatory_csvs():
    names = (
        "document_registry.csv",
        "evidence_passages.csv",
        "obligation_candidates.csv",
        "obligation_review_queue.csv",
        "obligations.csv",
    )
    before = {name: (PROCESSED_DATA_DIR / name).read_bytes() for name in names}

    response = client.get(
        "/demo/impact/DEMO-IMPACT-REGULATORY",
        params={
            "rules": '[{"rule_id":"ATTACK"}]',
            "file_path": "data/processed/obligations.csv",
            "reviewer_id": "untrusted",
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["label"] == SYNTHETIC_BANNER
    assert "potential_regulatory_impact" in {item["category"] for item in result["findings"]}
    assert result["approval_status"] == "not_performed"
    assert result["activation_status"] == "inactive"
    assert client.post("/demo/impact/DEMO-IMPACT-REGULATORY").status_code == 405
    assert client.get("/demo/impact/unknown").status_code == 404
    assert {name: (PROCESSED_DATA_DIR / name).read_bytes() for name in names} == before
