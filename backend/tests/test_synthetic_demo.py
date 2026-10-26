"""Isolation and deterministic behavior tests for the offline synthetic demo."""

import json

import pytest

from backend.services.governance import GovernanceError, RuleGovernance
from backend.services.rule_engine import SYNTHETIC_BANNER
from backend.services import synthetic_demo
from backend.services.synthetic_demo import (
    DEMO_FIXTURE_FILE,
    PROCESSED_DATA_DIR,
    SyntheticDemoError,
    SyntheticDemoStore,
    assess_demo_scenario,
    load_demo_fixture,
)


def test_demo_state_is_separate_from_processed_obligations(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(synthetic_demo, "PROJECT", project_root)
    demo_dir = data_dir / "demo"
    processed_dir = project_root / "data" / "processed"
    processed_dir.mkdir(parents=True)
    obligations = processed_dir / "obligations.csv"
    obligations.write_text("obligation_id,verification_status\n", encoding="utf-8")
    before = obligations.read_bytes()

    state = SyntheticDemoStore(demo_dir=demo_dir).initialize()

    assert (demo_dir / "synthetic_demo_state.json").is_file()
    assert json.loads((demo_dir / "synthetic_demo_state.json").read_text(encoding="utf-8")) == state
    assert obligations.read_bytes() == before
    assert list(processed_dir.iterdir()) == [obligations]


def test_canonical_project_demo_directory_is_accepted():
    store = SyntheticDemoStore(demo_dir=DEMO_FIXTURE_FILE.parent)

    assert store.demo_dir == DEMO_FIXTURE_FILE.parent.resolve()


def test_external_data_demo_directory_is_rejected_without_creation(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    (project_root / "data").mkdir(parents=True)
    monkeypatch.setattr(synthetic_demo, "PROJECT", project_root)
    external_demo_dir = tmp_path / "external" / "data" / "demo"

    with pytest.raises(SyntheticDemoError, match="canonical data/demo directory"):
        SyntheticDemoStore(demo_dir=external_demo_dir)

    assert not external_demo_dir.exists()
    assert not external_demo_dir.parent.exists()


def test_mock_candidates_are_labelled_provisional_and_deterministic(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(synthetic_demo, "PROJECT", project_root)
    store = SyntheticDemoStore(demo_dir=data_dir / "demo")

    first = store.initialize()
    second = store.initialize()

    assert first == second
    assert first["label"] == SYNTHETIC_BANNER
    assert first["provider_kind"] == "mock"
    assert first["candidates"]
    for candidate in first["candidates"]:
        assert candidate["candidate_id"].startswith("DEMO-")
        assert candidate["document_id"].startswith("DEMO-")
        assert candidate["evidence_id"].startswith("DEMO-")
        assert candidate["provider_kind"] == "mock"
        assert candidate["label"] == SYNTHETIC_BANNER
        assert candidate["review_status"] == "pending_human_review"
        assert candidate["verification_status"] == "unverified_candidate"
        assert candidate["activation_status"] == "inactive"

    assert all(
        scenario["approval_status"] == "not_requested"
        and scenario["activation_status"] == "inactive"
        for scenario in first["scenarios"]
    )


def test_fixture_contains_synthetic_rules_and_namespaced_references():
    fixture = load_demo_fixture()

    assert fixture["label"] == SYNTHETIC_BANNER
    assert fixture["demo_id"].startswith("OFFLINE-SYNTHETIC-")
    assert all(
        rule["source_obligation"]["dataset_kind"] == "synthetic"
        and rule["rule_id"].startswith("DEMO-")
        for scenario in fixture["scenarios"]
        for rule in [*scenario["baseline_rules"], scenario["proposed_rule"]]
    )
    assert DEMO_FIXTURE_FILE.is_file()


def _scenario_signature(result):
    return {
        "scenario_id": result["scenario_id"],
        "validation_errors": result["validation_errors"],
        "conflicts": result["conflicts"],
        "approval_gate": result["approval_gate"],
        "approval_status": result["approval_status"],
        "rule_status": result["rule_status"],
        "activation_status": result["activation_status"],
        "simulation": {
            "case_count": result["simulation"]["case_count"],
            "changed_outcomes": result["simulation"]["changed_outcomes"],
            "comparisons": result["simulation"]["comparisons"],
        },
    }


def test_clean_scenario_is_gate_ready_and_repeatable():
    first = assess_demo_scenario("DEMO-SCENARIO-CLEAN")
    second = assess_demo_scenario("DEMO-SCENARIO-CLEAN")

    assert _scenario_signature(first) == _scenario_signature(second)
    assert first["validation_errors"] == []
    assert first["conflicts"] == []
    assert first["approval_gate"] == {"status": "passed", "error_code": None}
    assert first["simulation"]["case_count"] == 400
    assert first["approval_status"] == "not_performed"
    assert first["rule_status"] == "pending_approval"
    assert first["activation_status"] == "inactive"


def test_conflicting_scenario_is_blocked_by_existing_governance_gate():
    result = assess_demo_scenario("DEMO-SCENARIO-CONFLICT")
    fixture = load_demo_fixture()
    scenario = next(
        item for item in fixture["scenarios"]
        if item["scenario_id"] == "DEMO-SCENARIO-CONFLICT"
    )
    rules = [*scenario["baseline_rules"], scenario["proposed_rule"]]

    assert result["approval_gate"] == {
        "status": "blocked",
        "error_code": "approval_gate_failed",
    }
    assert any(
        finding["finding_type"] == "identical_condition_incompatible_actions"
        for finding in result["conflicts"]
    )
    assert result["simulation"]["case_count"] == 400
    with pytest.raises(GovernanceError, match="Rule conflicts require resolution"):
        RuleGovernance()._check_approval_gates(scenario["proposed_rule"], rules)
    assert result["approval_status"] == "not_performed"
    assert result["rule_status"] == "pending_approval"
    assert result["activation_status"] == "inactive"


def test_demo_runtime_does_not_use_candidate_review_persistence(tmp_path, monkeypatch):
    from backend.services import review_obligation_candidates

    project_root = tmp_path / "project"
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(synthetic_demo, "PROJECT", project_root)
    protected_files = [
        PROCESSED_DATA_DIR / name
        for name in (
            "document_registry.csv",
            "evidence_passages.csv",
            "obligation_candidates.csv",
            "obligation_review_queue.csv",
            "obligations.csv",
        )
    ]
    before = {path: path.read_bytes() for path in protected_files}

    def forbidden_review(*args, **kwargs):
        raise AssertionError("Synthetic demo must not call candidate-review persistence")

    monkeypatch.setattr(review_obligation_candidates, "review_candidate", forbidden_review)
    store = SyntheticDemoStore(demo_dir=data_dir / "demo")

    store.initialize()
    assess_demo_scenario("DEMO-SCENARIO-CLEAN", demo_dir=store.demo_dir)

    assert not (data_dir / "processed" / "obligations.csv").exists()
    assert {path: path.read_bytes() for path in protected_files} == before
