"""Phase 6 governance tests: lifecycle, approval, audit, rollback."""

import json
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.conflict_detector import ConflictFinding
from backend.services.generate_synthetic_data import build_baseline_rules
from backend.services.governance import (
    GovernanceError,
    RuleGovernance,
    RuleStatus,
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def governance(temp_dir):
    return RuleGovernance(data_dir=temp_dir)


@pytest.fixture
def sample_rule():
    return {
        "rule_id": "RULE-001",
        "name": "Test Rule",
        "status": "draft",
        "confidence": 0.75,
        "condition": {"type": "comparison", "field": "amount", "operator": "gt", "value": 1000},
        "action": {"action_type": "alert", "message": "High amount detected"},
        "source_obligation": {
            "dataset_kind": "verified",
            "obligation_id": "OBL-001",
            "verification_status": "verified",
            "evidence_ids": ["EVID-001"]
        }
    }


@pytest.fixture
def sample_rules(sample_rule):
    return [sample_rule]


def test_validate_transitions():
    """Test status transition validation."""
    gov = RuleGovernance()
    
    # Valid transitions
    assert gov.validate_transition("draft", "test")
    assert gov.validate_transition("test", "pending_approval")
    assert gov.validate_transition("pending_approval", "approved")
    assert gov.validate_transition("approved", "retired")
    assert gov.validate_transition("retired", "draft")
    assert gov.validate_transition("test", "draft")  # Can go back to draft
    assert gov.validate_transition("approved", "test")  # Can demote for retesting
    
    # Invalid transitions
    assert not gov.validate_transition("draft", "approved")  # Can't skip test
    # test -> retired is actually allowed (direct retirement)
    # assert not gov.validate_transition("test", "retired")  # Can't directly retire from test
    # approved -> draft is allowed for rollback
    # assert not gov.validate_transition("approved", "draft")  # Must go through test or retired
    assert not gov.validate_transition("pending_approval", "test")  # Can't go back directly


def test_confidence_gate_check(governance, sample_rule):
    """Test confidence gate validation."""
    # Test with low confidence
    low_conf_rule = sample_rule.copy()
    low_conf_rule["confidence"] = 0.6
    low_conf_rule["status"] = "draft"
    
    errors = governance.check_confidence_gate(low_conf_rule)
    assert "Confidence 0.6 < 0.7 required for testing" in errors
    
    # Test with sufficient confidence for draft -> test
    ok_conf_rule = sample_rule.copy()
    ok_conf_rule["confidence"] = 0.75
    ok_conf_rule["status"] = "draft"
    
    errors = governance.check_confidence_gate(ok_conf_rule)
    assert not errors
    
    # Test with insufficient confidence for test -> pending_approval
    test_rule = sample_rule.copy()
    test_rule["confidence"] = 0.8
    test_rule["status"] = "test"
    
    errors = governance.check_confidence_gate(test_rule)
    assert "Confidence 0.8 < 0.85 required for approval submission" in errors
    
    # Test with sufficient confidence for approval
    high_conf_rule = sample_rule.copy()
    high_conf_rule["confidence"] = 0.9
    high_conf_rule["status"] = "test"
    
    errors = governance.check_confidence_gate(high_conf_rule)
    assert not errors


def test_evidence_gate_check(governance, sample_rule):
    """Test evidence gate validation."""
    # Valid verified rule
    errors = governance.check_evidence_gate(sample_rule)
    assert not errors
    
    # Invalid: unverified obligation
    unverified_rule = sample_rule.copy()
    unverified_rule["source_obligation"]["verification_status"] = "pending"
    
    errors = governance.check_evidence_gate(unverified_rule)
    assert any("Source obligation verification_status must be 'verified'" in err for err in errors)
    
    # Invalid: synthetic dataset
    synthetic_rule = sample_rule.copy()
    synthetic_rule["source_obligation"]["dataset_kind"] = "synthetic"
    
    errors = governance.check_evidence_gate(synthetic_rule)
    assert "Only verified obligations can enter governance workflow" in errors
    
    # Invalid: missing obligation_id
    no_id_rule = sample_rule.copy()
    no_id_rule["source_obligation"].pop("obligation_id")
    
    errors = governance.check_evidence_gate(no_id_rule)
    assert "Missing obligation_id in source" in errors


def test_promote_to_test(governance, temp_dir, sample_rule):
    """Test promoting a draft rule to test status."""
    # Create rules file
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [sample_rule]}, f, indent=2)
    
    # Promote to test
    result = governance.promote_to_test("RULE-001", "user123")
    assert result
    
    # Verify status changed
    updated_rules = governance._load_rules()
    updated_rule = next(r for r in updated_rules if r["rule_id"] == "RULE-001")
    assert updated_rule["status"] == "test"
    assert "changed_to_test_at" in updated_rule
    assert "changed_to_test_by" in updated_rule
    
    # Check audit trail
    audit = governance.get_audit_trail("RULE-001")
    assert len(audit) == 1
    assert audit[0]["event_type"] == "status_change"
    assert audit[0]["details"]["to_status"] == "test"


def test_submit_for_approval_success(governance, temp_dir, sample_rule):
    """Test successful submission for approval."""
    # Create rule in test status with high confidence
    test_rule = sample_rule.copy()
    test_rule["status"] = "test"
    test_rule["confidence"] = 0.9
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [test_rule]}, f, indent=2)
    
    # Submit for approval
    record = governance.submit_for_approval("RULE-001", "user123")
    
    assert record.rule_id == "RULE-001"
    assert record.new_status == "pending_approval"
    assert record.previous_status == "test"
    
    # Verify rule updated
    updated_rules = governance._load_rules()
    updated_rule = next(r for r in updated_rules if r["rule_id"] == "RULE-001")
    assert updated_rule["status"] == "pending_approval"
    assert "submitted_for_approval_at" in updated_rule
    assert updated_rule["submitted_by"] == "user123"


def test_submit_for_approval_requires_submitter_identity(governance, temp_dir, sample_rule):
    test_rule = sample_rule.copy()
    test_rule["status"] = "test"
    test_rule["confidence"] = 0.9
    with (temp_dir / "verified_rules.json").open("w", encoding="utf-8") as stream:
        json.dump({"rules": [test_rule]}, stream)

    with pytest.raises(GovernanceError, match="submitter_id is required"):
        governance.submit_for_approval("RULE-001", "  ")

    assert governance._load_rules()[0]["status"] == "test"
    assert not governance.audit_path.exists()


def test_submit_for_approval_fails_gates(governance, temp_dir, sample_rule):
    """Test submission fails when gates not met."""
    # Create rule in test status with LOW confidence
    test_rule = sample_rule.copy()
    test_rule["status"] = "test"
    test_rule["confidence"] = 0.8  # Below 0.85 threshold
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [test_rule]}, f, indent=2)
    
    # Should fail
    with pytest.raises(GovernanceError) as exc:
        governance.submit_for_approval("RULE-001", "user123")
    
    assert "gate_check_failed" in str(exc.value)
    assert "Confidence 0.8 < 0.85" in str(exc.value)


def test_approve_rule(governance, temp_dir):
    """Test approving a synthetic rule after validation, conflict and simulation gates."""
    pending_rule = deepcopy(build_baseline_rules()[0])
    pending_rule["status"] = "pending_approval"
    pending_rule["submitted_by"] = "proposer-001"
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [pending_rule]}, f, indent=2)
    
    # Approve the rule
    record = governance.approve_rule(pending_rule["rule_id"], "reviewer-002", "Synthetic test approval")

    assert record.rule_id == pending_rule["rule_id"]
    assert record.decision == "approved"
    assert record.new_status == "approved"
    assert record.reviewer_id == "reviewer-002"

    # Verify rule updated
    updated_rules = governance._load_rules()
    updated_rule = next(r for r in updated_rules if r["rule_id"] == pending_rule["rule_id"])
    assert updated_rule["status"] == "approved"
    assert updated_rule["approved_by"] == "reviewer-002"
    assert "approved_at" in updated_rule
    assert updated_rule["approval_reason"] == "Synthetic test approval"

    # Check audit trail
    audit = governance.get_audit_trail(pending_rule["rule_id"])
    approval_events = [e for e in audit if e["event_type"] == "approval_decided"]
    assert len(approval_events) == 1
    assert approval_events[0]["details"]["decision"] == "approved"


def _write_pending_synthetic_rule(temp_dir):
    rule = deepcopy(build_baseline_rules()[0])
    rule["status"] = "pending_approval"
    rule["submitted_by"] = "proposer-001"
    with (temp_dir / "verified_rules.json").open("w", encoding="utf-8") as stream:
        json.dump({"rules": [rule]}, stream)
    return rule


def _assert_approval_unchanged(governance, rule_id):
    stored = next(item for item in governance._load_rules() if item["rule_id"] == rule_id)
    assert stored["status"] == "pending_approval"
    assert not governance.audit_path.exists()


def test_self_approval_is_rejected_without_status_or_audit_change(governance, temp_dir):
    rule = _write_pending_synthetic_rule(temp_dir)
    with pytest.raises(GovernanceError, match="cannot approve their own"):
        governance.approve_rule(rule["rule_id"], "  PROPOSER-001  ", "Synthetic test")
    _assert_approval_unchanged(governance, rule["rule_id"])


def test_approval_requires_reviewer_and_submitter_identity(governance, temp_dir):
    rule = _write_pending_synthetic_rule(temp_dir)
    with (temp_dir / "verified_rules.json").open("r", encoding="utf-8") as stream:
        rules = json.load(stream)["rules"]
    rules[0].pop("submitted_by")
    with (temp_dir / "verified_rules.json").open("w", encoding="utf-8") as stream:
        json.dump({"rules": rules}, stream)

    with pytest.raises(GovernanceError, match="submitted_by is required"):
        governance.approve_rule(rule["rule_id"], "reviewer-002", "Synthetic test")
    _assert_approval_unchanged(governance, rule["rule_id"])

    rules[0]["submitted_by"] = "proposer-001"
    with (temp_dir / "verified_rules.json").open("w", encoding="utf-8") as stream:
        json.dump({"rules": rules}, stream)
    with pytest.raises(GovernanceError, match="reviewer_id is required"):
        governance.approve_rule(rule["rule_id"], "  ", "Synthetic test")
    _assert_approval_unchanged(governance, rule["rule_id"])


def test_detected_conflict_blocks_approval_without_status_or_audit_change(governance, temp_dir):
    rule = _write_pending_synthetic_rule(temp_dir)
    finding = ConflictFinding(
        rule_ids=[rule["rule_id"]],
        finding_type="overlapping_incompatible_actions",
        severity="high",
        explanation="Synthetic conflict fixture.",
        overlap_established=True,
        requires_human_review=True,
    )
    with patch("backend.services.governance.detect_conflicts", return_value=[finding]):
        with pytest.raises(GovernanceError, match="conflicts require resolution"):
            governance.approve_rule(rule["rule_id"], "reviewer456", "Synthetic test")
    _assert_approval_unchanged(governance, rule["rule_id"])


def test_failed_simulation_blocks_approval_without_status_or_audit_change(governance, temp_dir):
    rule = _write_pending_synthetic_rule(temp_dir)
    with patch("backend.services.governance.run_simulation", side_effect=RuntimeError("synthetic failure")):
        with pytest.raises(GovernanceError, match="simulation failed"):
            governance.approve_rule(rule["rule_id"], "reviewer456", "Synthetic test")
    _assert_approval_unchanged(governance, rule["rule_id"])


def test_missing_simulation_comparisons_block_approval(governance, temp_dir):
    rule = _write_pending_synthetic_rule(temp_dir)
    report = {"simulation_type": "baseline_vs_proposed", "case_count": 1, "comparisons": []}
    with patch("backend.services.governance.run_simulation", return_value=report):
        with pytest.raises(GovernanceError, match="missing or incomplete"):
            governance.approve_rule(rule["rule_id"], "reviewer456", "Synthetic test")
    _assert_approval_unchanged(governance, rule["rule_id"])


def test_simulation_validation_errors_block_approval(governance, temp_dir):
    rule = _write_pending_synthetic_rule(temp_dir)
    report = {
        "simulation_type": "baseline_vs_proposed",
        "case_count": 1,
        "changed_outcomes": 0,
        "comparisons": [{
            "case_id": "SYN-CASE-001",
            "outcome_changed": False,
            "baseline": {"fired_actions": [], "validation_errors": []},
            "proposed": {"fired_actions": [], "validation_errors": ["invalid synthetic rule"]},
        }],
    }
    with patch("backend.services.governance.run_simulation", return_value=report):
        with pytest.raises(GovernanceError, match="Simulation validation failed"):
            governance.approve_rule(rule["rule_id"], "reviewer456", "Synthetic test")
    _assert_approval_unchanged(governance, rule["rule_id"])


def test_rule_validation_failure_blocks_approval_before_conflict_or_simulation(governance, temp_dir):
    rule = _write_pending_synthetic_rule(temp_dir)
    stored_rules = governance._load_rules()
    stored_rules[0]["condition"]["field"] = "unsupported_field"
    with (temp_dir / "verified_rules.json").open("w", encoding="utf-8") as stream:
        json.dump({"rules": stored_rules}, stream)

    with (
        patch("backend.services.governance.detect_conflicts") as detect,
        patch("backend.services.governance.run_simulation") as simulate,
    ):
        with pytest.raises(GovernanceError, match="Rule validation failed"):
            governance.approve_rule(rule["rule_id"], "reviewer456", "Synthetic test")
    detect.assert_not_called()
    simulate.assert_not_called()
    _assert_approval_unchanged(governance, rule["rule_id"])


def test_reject_rule(governance, temp_dir, sample_rule):
    """Test rejecting a pending rule."""
    # Create rule in pending_approval status
    pending_rule = sample_rule.copy()
    pending_rule["status"] = "pending_approval"
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [pending_rule]}, f, indent=2)
    
    # Reject the rule
    record = governance.reject_rule("RULE-001", "reviewer456", "Needs more work")
    
    assert record.rule_id == "RULE-001"
    assert record.decision == "rejected"
    assert record.new_status == "draft"  # Goes back to draft
    assert record.reviewer_id == "reviewer456"
    
    # Verify rule updated
    updated_rules = governance._load_rules()
    updated_rule = next(r for r in updated_rules if r["rule_id"] == "RULE-001")
    assert updated_rule["status"] == "draft"
    assert updated_rule["rejected_by"] == "reviewer456"
    assert "rejected_at" in updated_rule
    assert updated_rule["rejection_reason"] == "Needs more work"


def test_verify_rule(governance, temp_dir, sample_rule):
    """Test official verification."""
    # Create approved rule
    approved_rule = sample_rule.copy()
    approved_rule["status"] = "approved"
    approved_rule["approved_by"] = "reviewer456"
    approved_rule["approved_at"] = datetime.now(timezone.utc).isoformat()
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [approved_rule]}, f, indent=2)
    
    # Verify the rule
    result = governance.verify_rule("RULE-001", "verifier789")
    assert result
    
    # Verify rule updated
    updated_rules = governance._load_rules()
    updated_rule = next(r for r in updated_rules if r["rule_id"] == "RULE-001")
    assert updated_rule["officially_verified"] is True
    assert updated_rule["verified_by"] == "verifier789"
    assert "verified_at" in updated_rule


def test_verify_rule_fails_not_approved(governance, temp_dir, sample_rule):
    """Test verification fails for non-approved rules."""
    # Create rule NOT in approved status
    test_rule = sample_rule.copy()
    test_rule["status"] = "test"
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [test_rule]}, f, indent=2)
    
    # Should fail
    with pytest.raises(GovernanceError) as exc:
        governance.verify_rule("RULE-001", "verifier789")
    
    assert "invalid_status" in str(exc.value)


def test_append_only_audit_trail(governance, temp_dir, sample_rule):
    """Test audit trail append-only behavior."""
    # Create initial rule with high confidence
    high_conf_rule = sample_rule.copy()
    high_conf_rule["confidence"] = 0.9
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [high_conf_rule]}, f, indent=2)
    
    # Perform multiple operations
    governance.promote_to_test("RULE-001", "user123")
    
    governance.submit_for_approval("RULE-001", "user123")
    
    # Check audit trail
    audit = governance.get_audit_trail("RULE-001")
    assert len(audit) == 2
    
    event_types = [e["event_type"] for e in audit]
    assert "status_change" in event_types
    assert "approval_submitted" in event_types
    
    # Verify audit file exists
    assert governance.audit_path.exists()
    
    # Count lines in audit file (should have 3: header + 2 events)
    with governance.audit_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 3  # header + 2 events


def test_rollback_rule(governance, temp_dir, sample_rule):
    """Test rolling back an approved rule."""
    # Create approved rule
    approved_rule = sample_rule.copy()
    approved_rule["status"] = "approved"
    approved_rule["approved_by"] = "reviewer456"
    approved_rule["approved_at"] = datetime.now(timezone.utc).isoformat()
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [approved_rule]}, f, indent=2)
    
    # Rollback to draft
    result = governance.rollback_rule("RULE-001", "draft", "Bug found", "admin")
    
    assert result["rule_id"] == "RULE-001"
    assert result["from_status"] == "approved"
    assert result["to_status"] == "draft"
    assert "backup_id" in result
    
    # Verify rule updated
    updated_rules = governance._load_rules()
    updated_rule = next(r for r in updated_rules if r["rule_id"] == "RULE-001")
    assert updated_rule["status"] == "draft"
    assert updated_rule["previous_status"] == "approved"
    assert "rolled_back_at" in updated_rule
    assert updated_rule["rolled_back_by"] == "admin"
    assert updated_rule["rollback_reason"] == "Bug found"
    
    # Verify approval fields cleared
    assert "approved_by" not in updated_rule
    assert "approved_at" not in updated_rule
    
    # Verify backup created
    backup_dir = temp_dir / "rule_backups"
    assert backup_dir.exists()
    backup_files = list(backup_dir.glob("*.json"))
    assert len(backup_files) == 1


def test_rollback_invalid_target(governance, temp_dir, sample_rule):
    """Test rollback with invalid target status."""
    # Create approved rule
    approved_rule = sample_rule.copy()
    approved_rule["status"] = "approved"
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [approved_rule]}, f, indent=2)
    
    # Try invalid rollback target
    with pytest.raises(GovernanceError) as exc:
        governance.rollback_rule("RULE-001", "pending_approval", "Test", "admin")
    
    assert "invalid_target" in str(exc.value)


def test_retire_rule(governance, temp_dir, sample_rule):
    """Test retiring a rule."""
    # Create rule in test status
    test_rule = sample_rule.copy()
    test_rule["status"] = "test"
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [test_rule]}, f, indent=2)
    
    # Retire the rule
    result = governance.retire_rule("RULE-001", "user123", "Obsolete")
    assert result
    
    # Verify rule updated
    updated_rules = governance._load_rules()
    updated_rule = next(r for r in updated_rules if r["rule_id"] == "RULE-001")
    assert updated_rule["status"] == "retired"


def test_get_rule_lifecycle(governance, temp_dir, sample_rule):
    """Test retrieving complete lifecycle information."""
    # Create rule and perform operations
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [sample_rule]}, f, indent=2)
    
    governance.promote_to_test("RULE-001", "user123")
    
    # Get lifecycle
    lifecycle = governance.get_rule_lifecycle("RULE-001")
    
    assert "rule" in lifecycle
    assert "audit_trail" in lifecycle
    assert "current_status" in lifecycle
    assert "confidence_gates" in lifecycle
    assert "evidence_gates" in lifecycle
    
    assert lifecycle["rule"]["rule_id"] == "RULE-001"
    assert len(lifecycle["audit_trail"]) == 1
    assert lifecycle["current_status"] == "test"


def test_unauthorized_approval_attempt(governance, temp_dir, sample_rule):
    """Test that non-pending rules cannot be approved."""
    # Create rule in test status (not pending_approval)
    test_rule = sample_rule.copy()
    test_rule["status"] = "test"
    
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": [test_rule]}, f, indent=2)
    
    # Try to approve - should fail
    with pytest.raises(GovernanceError) as exc:
        governance.approve_rule("RULE-001", "user123", "Test")
    
    assert "invalid_status" in str(exc.value)


def test_rule_not_found_errors(governance, temp_dir):
    """Test operations on non-existent rules."""
    # Create empty rules file
    rules_file = temp_dir / "verified_rules.json"
    with rules_file.open("w", encoding="utf-8") as f:
        json.dump({"rules": []}, f, indent=2)
    
    # All operations should fail with rule_not_found
    with pytest.raises(GovernanceError) as exc:
        governance.promote_to_test("NONEXISTENT", "user123")
    assert "rule_not_found" in str(exc.value)
    
    with pytest.raises(GovernanceError) as exc:
        governance.submit_for_approval("NONEXISTENT", "user123")
    assert "rule_not_found" in str(exc.value)
    
    with pytest.raises(GovernanceError) as exc:
        governance.get_rule_lifecycle("NONEXISTENT")
    assert "rule_not_found" in str(exc.value)