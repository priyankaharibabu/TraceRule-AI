"""API tests for fixed, isolated synthetic demo scenarios."""

from fastapi.testclient import TestClient

from backend.services import document_analyzer
from backend.services.rule_engine import SYNTHETIC_BANNER
from backend.services.synthetic_demo import PROCESSED_DATA_DIR


client = TestClient(document_analyzer.app)

REGULATORY_CSV_NAMES = (
    "document_registry.csv",
    "evidence_passages.csv",
    "obligation_candidates.csv",
    "obligation_review_queue.csv",
    "obligations.csv",
)


def _snapshot_regulatory_csvs():
    return {
        name: (PROCESSED_DATA_DIR / name).read_bytes()
        for name in REGULATORY_CSV_NAMES
    }


def test_clean_synthetic_demo_endpoint_returns_gate_ready_results():
    response = client.get("/demo/synthetic/clean")

    assert response.status_code == 200
    result = response.json()
    assert result["label"] == SYNTHETIC_BANNER
    assert result["scenario_id"] == "DEMO-SCENARIO-CLEAN"
    assert result["validation_result"] == {"passed": True, "errors": []}
    assert result["conflict_findings"] == []
    assert result["simulation_summary"]["simulation_type"] == "baseline_vs_proposed"
    assert result["simulation_summary"]["case_count"] == 400
    assert result["approval_gate_outcome"] == {"status": "passed", "error_code": None}
    assert result["approval_status"] == "not_performed"
    assert result["activation_status"] == "inactive"


def test_conflict_synthetic_demo_endpoint_returns_actual_blocking_finding():
    response = client.get("/demo/synthetic/conflict")

    assert response.status_code == 200
    result = response.json()
    assert result["label"] == SYNTHETIC_BANNER
    assert result["scenario_id"] == "DEMO-SCENARIO-CONFLICT"
    assert result["validation_result"] == {"passed": True, "errors": []}
    assert any(
        finding["finding_type"] == "identical_condition_incompatible_actions"
        for finding in result["conflict_findings"]
    )
    assert result["simulation_summary"]["case_count"] == 400
    assert result["approval_gate_outcome"] == {
        "status": "blocked",
        "error_code": "approval_gate_failed",
    }
    assert result["approval_status"] == "not_performed"
    assert result["activation_status"] == "inactive"


def test_unknown_demo_scenario_route_is_not_exposed():
    response = client.get("/demo/synthetic/unknown")

    assert response.status_code == 404


def test_demo_endpoints_do_not_accept_mutations_or_external_state_inputs():
    assert client.post("/demo/synthetic/clean").status_code == 405
    response = client.get(
        "/demo/synthetic/clean",
        params={
            "rules": '[{"rule_id":"ATTACK"}]',
            "evidence": "real-evidence",
            "file_path": "data/processed/obligations.csv",
            "reviewer_id": "untrusted-user",
            "action": "approve",
        },
    )

    assert response.status_code == 200
    assert response.json()["scenario_id"] == "DEMO-SCENARIO-CLEAN"
    assert response.json()["approval_status"] == "not_performed"


def test_api_scenarios_leave_regulatory_csvs_unchanged(monkeypatch):
    from backend.services import review_obligation_candidates

    before = _snapshot_regulatory_csvs()

    def forbidden_review(*args, **kwargs):
        raise AssertionError("Demo API must not call candidate-review persistence")

    monkeypatch.setattr(review_obligation_candidates, "review_candidate", forbidden_review)
    assert client.get("/demo/synthetic/clean").status_code == 200
    assert client.get("/demo/synthetic/conflict").status_code == 200

    assert _snapshot_regulatory_csvs() == before
