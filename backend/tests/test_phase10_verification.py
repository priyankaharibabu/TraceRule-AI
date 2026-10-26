from __future__ import annotations

import os
import shutil
from copy import deepcopy

import pytest
from backend.services import phase10_verification as phase10
from backend.services.rule_engine import evaluate_rule
from backend.services.generate_synthetic_data import build_baseline_rules
from backend.services.document_analyzer import app
from fastapi.testclient import TestClient


def _opa_binary() -> str | None:
    return os.environ.get("TRACERULE_OPA_BIN") or shutil.which("opa")


def test_z3_detects_satisfiable_and_contradictory_conditions():
    satisfiable = {"type": "comparison", "field": "loan_amount", "operator": "gte", "value": 50000}
    contradictory = {"type": "logical", "operator": "and", "operands": [
        {"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 50000},
        {"type": "comparison", "field": "loan_amount", "operator": "lte", "value": 50000},
    ]}
    assert phase10.analyze_satisfiability(satisfiable)["result"] == "sat"
    assert phase10.analyze_satisfiability(contradictory)["result"] == "unsat"


def test_z3_overlap_is_logical_only():
    left = {"type": "comparison", "field": "loan_amount", "operator": "gt", "value": 50000}
    right = {"type": "comparison", "field": "loan_amount", "operator": "lt", "value": 40000}
    result = phase10.analyze_overlap(left, right)
    assert result["result"] == "unsat"
    assert "not a legal conflict" in result["meaning"]


def test_z3_membership_boundaries_match_empty_and_boolean_sets():
    empty_in = {"type": "comparison", "field": "channel", "operator": "in", "value": []}
    bool_in = {"type": "comparison", "field": "has_lsp", "operator": "in", "value": [True]}
    assert phase10.analyze_satisfiability(empty_in)["result"] == "unsat"
    assert phase10.analyze_satisfiability(bool_in)["result"] == "sat"


def test_z3_rejects_unsupported_missing_node():
    with pytest.raises(phase10.VerificationError, match="unsupported"):
        phase10.analyze_satisfiability({"type": "missing"})


@pytest.mark.skipif(not _opa_binary(), reason="OPA CLI unavailable")
def test_opa_allows_only_synthetic_test_with_explicit_provenance():
    rule = build_baseline_rules()[0]
    payload = {
        "rule": rule,
        "case": {"case_id": "SYN-CASE-1", "dataset_kind": "synthetic"},
        "test_provenance": {"kind": "synthetic", "case_id": "SYN-CASE-1"},
    }
    assert phase10._opa_allows(payload, opa_bin=_opa_binary())
    blocked = deepcopy(payload)
    blocked["rule"]["status"] = "approved"
    assert not phase10._opa_allows(blocked, opa_bin=_opa_binary())
    blocked = deepcopy(payload)
    blocked["rule"]["source_obligation"]["dataset_kind"] = "unverified_candidate"
    assert not phase10._opa_allows(blocked, opa_bin=_opa_binary())
    blocked = deepcopy(payload)
    blocked["rule"]["source_obligation"]["dataset_kind"] = "verified"
    assert not phase10._opa_allows(blocked, opa_bin=_opa_binary())


@pytest.mark.skipif(not _opa_binary(), reason="OPA CLI unavailable")
def test_synthetic_verification_returns_separate_gates_and_no_persistence():
    result = phase10.verify_synthetic_case(
        "SYN-RULE-001", "SYN-CASE-ANCHOR-001", opa_bin=_opa_binary()
    )
    assert result["validation"]["valid"] is True
    assert result["opa_eligibility"]["allowed"] is True
    assert result["satisfiability"]["engine"] == "Z3"
    assert result["evaluation"]["engine"] == "TraceRule deterministic Python evaluator"
    assert result["persistence"] == "none"
    assert result["approval_or_activation"] == "not performed"


@pytest.mark.skipif(not _opa_binary(), reason="OPA CLI unavailable")
def test_synthetic_nonmatching_case_is_a_deterministic_deny_outcome():
    result = phase10.verify_synthetic_case(
        "SYN-RULE-001", "SYN-CASE-0001", opa_bin=_opa_binary()
    )
    assert result["opa_eligibility"]["allowed"] is True
    assert result["evaluation"]["matched"] is False
    assert result["evaluation"]["action"] is None


def test_existing_python_engine_keeps_unverified_obligations_blocked():
    rule = deepcopy(build_baseline_rules()[0])
    rule["source_obligation"]["dataset_kind"] = "unverified_candidate"
    outcome = evaluate_rule(rule, {"disbursement_route": "direct"})
    assert outcome.action is None
    assert outcome.validation_errors


@pytest.mark.skipif(not _opa_binary(), reason="OPA CLI unavailable")
def test_phase10_api_accepts_only_fixed_synthetic_identifiers_and_is_read_only(monkeypatch):
    from backend.services import document_analyzer
    client = TestClient(app, client=("127.0.0.1", 54321))
    before = {p.name: p.read_bytes() for p in (phase10.PROJECT / "data" / "processed").glob("*.csv")}
    monkeypatch.setenv("TRACERULE_OPA_BIN", _opa_binary())
    response = client.post("/phase10/synthetic/verify", json={
        "rule_id": "SYN-RULE-001", "case_id": "SYN-CASE-ANCHOR-001"
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["persistence"] == "none"
    assert payload["approval_or_activation"] == "not performed"
    assert client.post("/phase10/synthetic/verify", json={
        "rule_id": "SYN-RULE-001", "case_id": "SYN-CASE-ANCHOR-001", "rule": {}
    }).status_code == 422
    assert client.post("/phase10/synthetic/verify", json={
        "rule_id": "REAL-RULE-001", "case_id": "SYN-CASE-ANCHOR-001"
    }).status_code == 422
    assert {p.name: p.read_bytes() for p in (phase10.PROJECT / "data" / "processed").glob("*.csv")} == before


def test_phase10_api_rejects_non_loopback_requests():
    remote = TestClient(app, client=("192.0.2.10", 54321))
    response = remote.post("/phase10/synthetic/verify", json={
        "rule_id": "SYN-RULE-001", "case_id": "SYN-CASE-ANCHOR-001"
    })
    assert response.status_code == 403
