"""Phase 6: Rule Lifecycle Governance Service

Implements candidate lifecycle, human approval, audit trail, and rollback.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from backend.services.baseline_simulation import run_simulation
from backend.services.conflict_detector import detect_conflicts
from backend.services.rule_engine import load_test_cases
from backend.services.rule_validator import validate_rule
from backend.services.scope_governance import check_real_rule_mapping_eligibility

PROJECT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT / "data" / "processed"
AUDIT_FILE = PROCESSED / "governance_audit.csv"
RULES_FILE = PROCESSED / "verified_rules.json"
STATUS_FILE = PROCESSED / "rule_status.json"
SYNTHETIC_CASES_FILE = PROJECT / "data" / "synthetic" / "synthetic_test_cases.json"


class RuleStatus(str, Enum):
    """Valid rule lifecycle states."""
    DRAFT = "draft"
    TEST = "test"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RETIRED = "retired"


class GovernanceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


@dataclass
class ApprovalRecord:
    rule_id: str
    reviewer_id: str
    decision: str  # "approved", "rejected", "sent_back"
    reason: str
    timestamp: str
    previous_status: str
    new_status: str


@dataclass
class GovernanceEvent:
    event_id: str
    rule_id: str
    event_type: str  # "status_change", "approval_submitted", "approval_decided", "verification", "rollback"
    user_id: str
    timestamp: str
    details: dict[str, Any]


class RuleGovernance:
    """Manages rule lifecycle, approval workflow, audit trail, and rollback."""
    
    def __init__(self, data_dir: Path = PROCESSED):
        self.data_dir = data_dir
        self.audit_path = data_dir / AUDIT_FILE.name
        self.rules_path = data_dir / RULES_FILE.name
        self.status_path = data_dir / STATUS_FILE.name
        
    # ---------- Status Transitions ----------
    
    _VALID_TRANSITIONS = {
        RuleStatus.DRAFT: {RuleStatus.TEST, RuleStatus.RETIRED},
        RuleStatus.TEST: {RuleStatus.PENDING_APPROVAL, RuleStatus.DRAFT, RuleStatus.RETIRED},
        RuleStatus.PENDING_APPROVAL: {RuleStatus.APPROVED, RuleStatus.DRAFT, RuleStatus.RETIRED},
        RuleStatus.APPROVED: {RuleStatus.RETIRED, RuleStatus.TEST, RuleStatus.DRAFT},  # can demote for re-testing or rollback
        RuleStatus.RETIRED: {RuleStatus.DRAFT},  # can be revived as new draft
    }
    
    @classmethod
    def validate_transition(cls, from_status: str, to_status: str) -> bool:
        """Check if a status transition is valid."""
        try:
            from_enum = RuleStatus(from_status.lower())
            to_enum = RuleStatus(to_status.lower())
        except ValueError:
            return False
        return to_enum in cls._VALID_TRANSITIONS.get(from_enum, set())
    
    # ---------- Confidence and Evidence Gates ----------
    
    @staticmethod
    def check_confidence_gate(rule: dict[str, Any]) -> list[str]:
        """Check if rule meets confidence thresholds for promotion."""
        errors = []
        confidence = rule.get("confidence", 0)
        
        # Draft → Test: Medium confidence required
        if confidence < 0.7:
            errors.append(f"Confidence {confidence} < 0.7 required for testing")
        
        # Test → Pending Approval: High confidence required
        if rule.get("status") == "test" and confidence < 0.85:
            errors.append(f"Confidence {confidence} < 0.85 required for approval submission")
        
        evidence_count = len(rule.get("source_obligation", {}).get("evidence_ids", []))
        if evidence_count < 1:
            errors.append("At least one evidence reference required")
            
        return errors
    
    @staticmethod
    def check_evidence_gate(rule: dict[str, Any]) -> list[str]:
        """Check evidence requirements for promotion."""
        errors = []
        source = rule.get("source_obligation", {})
        
        if source.get("dataset_kind") != "verified":
            errors.append("Only verified obligations can enter governance workflow")
        
        if not source.get("obligation_id"):
            errors.append("Missing obligation_id in source")
            
        verification_status = source.get("verification_status")
        if verification_status != "verified":
            errors.append(f"Source obligation verification_status must be 'verified', got '{verification_status}'")
        
        return errors

    @staticmethod
    def _normalize_identity(identity: Any, field_name: str) -> str:
        if not isinstance(identity, str) or not identity.strip():
            raise GovernanceError("invalid_identity", f"{field_name} is required")
        return identity.strip().casefold()
    
    # ---------- Human Approval Service ----------
    
    def submit_for_approval(self, rule_id: str, submitter_id: str) -> ApprovalRecord:
        """Submit a rule for human approval."""
        self._normalize_identity(submitter_id, "submitter_id")
        rules = self._load_rules()
        rule = next((r for r in rules if r["rule_id"] == rule_id), None)
        if not rule:
            raise GovernanceError("rule_not_found", f"Rule {rule_id} not found")
        
        current_status = rule.get("status", "").lower()
        if current_status != "test":
            raise GovernanceError("invalid_status", 
                f"Rule must be in 'test' status to submit for approval, got '{current_status}'")
        
        # Check gates
        confidence_errors = self.check_confidence_gate(rule)
        evidence_errors = self.check_evidence_gate(rule)
        if confidence_errors or evidence_errors:
            raise GovernanceError("gate_check_failed", 
                f"Gates not met: {confidence_errors + evidence_errors}")
        
        # Update status
        rule["status"] = "pending_approval"
        rule["submitted_for_approval_at"] = datetime.now(timezone.utc).isoformat()
        rule["submitted_by"] = submitter_id.strip()
        
        self._save_rules(rules)
        
        # Record approval submission
        record = ApprovalRecord(
            rule_id=rule_id,
            reviewer_id=submitter_id,
            decision="submitted",
            reason="Submitted for approval",
            timestamp=datetime.now(timezone.utc).isoformat(),
            previous_status="test",
            new_status="pending_approval"
        )
        
        self._log_event(
            rule_id=rule_id,
            event_type="approval_submitted",
            user_id=submitter_id,
            details={"reason": "Submitted for approval"}
        )
        
        return record
    
    def approve_rule(self, rule_id: str, reviewer_id: str, reason: str) -> ApprovalRecord:
        """Approve a pending rule."""
        return self._process_approval(rule_id, reviewer_id, "approved", reason)
    
    def reject_rule(self, rule_id: str, reviewer_id: str, reason: str) -> ApprovalRecord:
        """Reject a pending rule (send back to draft)."""
        return self._process_approval(rule_id, reviewer_id, "rejected", reason)
    
    def _process_approval(self, rule_id: str, reviewer_id: str, decision: str, reason: str) -> ApprovalRecord:
        """Process an approval decision."""
        normalized_reviewer_id = (
            self._normalize_identity(reviewer_id, "reviewer_id")
            if decision == "approved"
            else ""
        )
        rules = self._load_rules()
        rule = next((r for r in rules if r["rule_id"] == rule_id), None)
        if not rule:
            raise GovernanceError("rule_not_found", f"Rule {rule_id} not found")
        
        current_status = rule.get("status", "").lower()
        if current_status != "pending_approval":
            raise GovernanceError("invalid_status", 
                f"Rule must be in 'pending_approval' status, got '{current_status}'")

        if decision == "approved":
            submitter_id = self._normalize_identity(rule.get("submitted_by"), "submitted_by")
            if normalized_reviewer_id == submitter_id:
                raise GovernanceError(
                    "self_approval_forbidden",
                    "The rule submitter cannot approve their own rule",
                )

        if decision == "approved":
            self._check_approval_gates(rule, rules)
        
        previous_status = current_status
        
        if decision == "approved":
            new_status = "approved"
            rule["approved_by"] = reviewer_id
            rule["approved_at"] = datetime.now(timezone.utc).isoformat()
            rule["approval_reason"] = reason
        elif decision == "rejected":
            new_status = "draft"  # Send back to draft
            rule["rejected_by"] = reviewer_id
            rule["rejected_at"] = datetime.now(timezone.utc).isoformat()
            rule["rejection_reason"] = reason
        else:
            raise GovernanceError("invalid_decision", f"Invalid decision: {decision}")
        
        rule["status"] = new_status
        self._save_rules(rules)
        
        record = ApprovalRecord(
            rule_id=rule_id,
            reviewer_id=reviewer_id,
            decision=decision,
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
            previous_status=previous_status,
            new_status=new_status
        )
        
        self._log_event(
            rule_id=rule_id,
            event_type="approval_decided",
            user_id=reviewer_id,
            details={"decision": decision, "reason": reason}
        )
        
        return record

    def _check_approval_gates(
        self,
        rule: dict[str, Any],
        rules: list[dict[str, Any]],
    ) -> None:
        """Require valid, conflict-free rules and a complete passing simulation."""
        workflow_fields = {"submitted_by", "submitted_for_approval_at"}
        rules_for_checks = [
            {key: value for key, value in item.items() if key not in workflow_fields}
            for item in rules
        ]
        rule_for_checks = next(
            item for item in rules_for_checks if item.get("rule_id") == rule.get("rule_id")
        )

        validation_errors = validate_rule(rule_for_checks)
        source = rule_for_checks.get("source_obligation") or {}
        if source.get("dataset_kind") != "synthetic":
            validation_errors.extend(
                check_real_rule_mapping_eligibility(rule_for_checks, data_dir=self.data_dir)
            )
        if validation_errors:
            raise GovernanceError(
                "approval_gate_failed",
                f"Rule validation failed: {validation_errors}",
            )

        try:
            findings = detect_conflicts(rules_for_checks)
        except Exception as exc:
            raise GovernanceError(
                "approval_gate_failed",
                f"Conflict detection failed ({type(exc).__name__})",
            ) from exc
        if findings:
            summary = [
                {
                    "rule_ids": finding.rule_ids,
                    "finding_type": finding.finding_type,
                    "severity": finding.severity,
                }
                for finding in findings
            ]
            raise GovernanceError(
                "approval_gate_failed",
                f"Rule conflicts require resolution: {summary}",
            )

        try:
            cases_payload = json.loads(SYNTHETIC_CASES_FILE.read_text(encoding="utf-8"))
            test_cases = load_test_cases(SYNTHETIC_CASES_FILE)
            baseline_rules = [
                item for item in rules_for_checks
                if item.get("rule_id") != rule.get("rule_id")
            ]
            report = run_simulation(
                baseline_rules,
                rules_for_checks,
                test_cases,
                baseline_version="pre-approval",
                proposed_version=str(rule.get("rule_version", "")),
                cases_version=str(cases_payload.get("dataset_version", "")),
                seed=cases_payload.get("seed"),
            )
        except Exception as exc:
            raise GovernanceError(
                "approval_gate_failed",
                f"Required rule simulation failed ({type(exc).__name__})",
            ) from exc

        comparisons = report.get("comparisons") if isinstance(report, dict) else None
        case_count = report.get("case_count") if isinstance(report, dict) else None
        changed_outcomes = report.get("changed_outcomes") if isinstance(report, dict) else None
        if (
            not isinstance(report, dict)
            or report.get("simulation_type") != "baseline_vs_proposed"
            or isinstance(case_count, bool)
            or not isinstance(case_count, int)
            or case_count < 1
            or isinstance(changed_outcomes, bool)
            or not isinstance(changed_outcomes, int)
            or not 0 <= changed_outcomes <= case_count
            or not isinstance(comparisons, list)
            or len(comparisons) != case_count
        ):
            raise GovernanceError(
                "approval_gate_failed",
                "Required simulation comparison results are missing or incomplete",
            )

        for comparison in comparisons:
            if (
                not isinstance(comparison, dict)
                or not isinstance(comparison.get("case_id"), str)
                or not isinstance(comparison.get("outcome_changed"), bool)
            ):
                raise GovernanceError(
                    "approval_gate_failed",
                    "Required simulation comparison results are malformed",
                )
            for result_key in ("baseline", "proposed"):
                result = comparison.get(result_key)
                if (
                    not isinstance(result, dict)
                    or not isinstance(result.get("validation_errors"), list)
                    or not isinstance(result.get("fired_actions"), list)
                ):
                    raise GovernanceError(
                        "approval_gate_failed",
                        "Simulation omitted required per-case outcomes",
                    )
                if result["validation_errors"]:
                    raise GovernanceError(
                        "approval_gate_failed",
                        f"Simulation validation failed for {comparison['case_id']} ({result_key})",
                    )
    
    # ---------- Official Verification Gate ----------
    
    def verify_rule(self, rule_id: str, verifier_id: str) -> bool:
        """Perform official verification check (mocks external system)."""
        rules = self._load_rules()
        rule = next((r for r in rules if r["rule_id"] == rule_id), None)
        if not rule:
            raise GovernanceError("rule_not_found", f"Rule {rule_id} not found")
        
        current_status = rule.get("status", "").lower()
        if current_status != "approved":
            raise GovernanceError("invalid_status", 
                f"Rule must be in 'approved' status for verification, got '{current_status}'")
        
        # Mock verification logic
        # In production, this would call external verification system
        source = rule.get("source_obligation", {})
        verified = (
            source.get("dataset_kind") == "verified" and
            source.get("verification_status") == "verified" and
            rule.get("approved_by") is not None
        )
        
        if verified:
            rule["officially_verified"] = True
            rule["verified_by"] = verifier_id
            rule["verified_at"] = datetime.now(timezone.utc).isoformat()
            self._save_rules(rules)
            
            self._log_event(
                rule_id=rule_id,
                event_type="verification",
                user_id=verifier_id,
                details={"verified": True}
            )
            
        return verified
    
    # ---------- Append-only Audit Trail ----------
    
    def _log_event(self, rule_id: str, event_type: str, user_id: str, details: dict[str, Any]) -> None:
        """Log a governance event to the append-only audit trail."""
        event = GovernanceEvent(
            event_id=str(uuid.uuid4()),
            rule_id=rule_id,
            event_type=event_type,
            user_id=user_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            details=details
        )
        
        # Create CSV if doesn't exist
        if not self.audit_path.exists():
            with self.audit_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["event_id", "timestamp", "rule_id", "event_type", "user_id", "details"])
        
        # Append event
        with self.audit_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                event.event_id,
                event.timestamp,
                event.rule_id,
                event.event_type,
                event.user_id,
                json.dumps(event.details, ensure_ascii=False)
            ])
    
    def get_audit_trail(self, rule_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve audit trail entries, optionally filtered by rule_id."""
        if not self.audit_path.exists():
            return []
        
        events = []
        with self.audit_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if rule_id and row["rule_id"] != rule_id:
                    continue
                try:
                    details = json.loads(row["details"])
                except json.JSONDecodeError:
                    details = {"error": "Invalid JSON"}
                
                events.append({
                    "event_id": row["event_id"],
                    "timestamp": row["timestamp"],
                    "rule_id": row["rule_id"],
                    "event_type": row["event_type"],
                    "user_id": row["user_id"],
                    "details": details
                })
        
        # Sort by timestamp descending
        events.sort(key=lambda x: x["timestamp"], reverse=True)
        return events[:limit]
    
    # ---------- Safe Rollback ----------
    
    def rollback_rule(self, rule_id: str, rollback_to_status: str, reason: str, user_id: str) -> dict[str, Any]:
        """Rollback a rule to a previous safe state."""
        rules = self._load_rules()
        rule = next((r for r in rules if r["rule_id"] == rule_id), None)
        if not rule:
            raise GovernanceError("rule_not_found", f"Rule {rule_id} not found")
        
        current_status = rule.get("status", "").lower()
        
        # Only allow rollback from approved or pending_approval states
        if current_status not in {"approved", "pending_approval"}:
            raise GovernanceError("invalid_rollback", 
                f"Cannot rollback from status '{current_status}'")
        
        # Only allow rollback to draft or test
        if rollback_to_status not in {"draft", "test"}:
            raise GovernanceError("invalid_target", 
                f"Can only rollback to 'draft' or 'test', got '{rollback_to_status}'")
        
        # Validate transition
        if not self.validate_transition(current_status, rollback_to_status):
            raise GovernanceError("invalid_transition",
                f"Cannot transition from '{current_status}' to '{rollback_to_status}'")
        
        # Create backup of current rule state
        backup_id = f"backup_{rule_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        backup_path = self.data_dir / f"rule_backups/{backup_id}.json"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        
        with backup_path.open("w", encoding="utf-8") as f:
            json.dump(rule, f, indent=2, ensure_ascii=False)
        
        # Update rule status
        rule["status"] = rollback_to_status
        rule["previous_status"] = current_status
        rule["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        rule["rolled_back_by"] = user_id
        rule["rollback_reason"] = reason
        rule["backup_id"] = backup_id
        
        # Clear approval fields if rolling back from approved
        if current_status == "approved":
            for field in ["approved_by", "approved_at", "approval_reason", "officially_verified"]:
                rule.pop(field, None)
        
        self._save_rules(rules)
        
        self._log_event(
            rule_id=rule_id,
            event_type="rollback",
            user_id=user_id,
            details={
                "from_status": current_status,
                "to_status": rollback_to_status,
                "reason": reason,
                "backup_id": backup_id
            }
        )
        
        return {
            "rule_id": rule_id,
            "from_status": current_status,
            "to_status": rollback_to_status,
            "backup_id": backup_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    # ---------- Persistence ----------
    
    def _load_rules(self) -> list[dict[str, Any]]:
        """Load rules from JSON file."""
        if not self.rules_path.exists():
            return []
        
        with self.rules_path.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict) and "rules" in data:
                    return data["rules"]
                else:
                    return []
            except json.JSONDecodeError:
                return []
    
    def _save_rules(self, rules: list[dict[str, Any]]) -> None:
        """Save rules to JSON file."""
        # Create backup
        if self.rules_path.exists():
            import shutil
            backup_path = self.rules_path.with_suffix(f".{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.bak")
            # Use shutil.copy2 to avoid rename issues on Windows
            shutil.copy2(self.rules_path, backup_path)
        
        with self.rules_path.open("w", encoding="utf-8") as f:
            json.dump({"rules": rules, "version": "1.0", "updated_at": datetime.now(timezone.utc).isoformat()}, 
                     f, indent=2, ensure_ascii=False)
    
    # ---------- Public API ----------
    
    def promote_to_test(self, rule_id: str, user_id: str) -> bool:
        """Promote draft rule to test status."""
        return self._change_status(rule_id, "draft", "test", user_id, "Promoted to test")
    
    def retire_rule(self, rule_id: str, user_id: str, reason: str) -> bool:
        """Retire a rule (mark as retired)."""
        return self._change_status(rule_id, None, "retired", user_id, reason)
    
    def _change_status(self, rule_id: str, expected_current: str | None, new_status: str, 
                       user_id: str, reason: str) -> bool:
        """Internal helper to change rule status."""
        rules = self._load_rules()
        rule = next((r for r in rules if r["rule_id"] == rule_id), None)
        if not rule:
            raise GovernanceError("rule_not_found", f"Rule {rule_id} not found")
        
        current_status = rule.get("status", "").lower()
        if expected_current and current_status != expected_current:
            raise GovernanceError("invalid_status",
                f"Expected status '{expected_current}', got '{current_status}'")
        
        if not self.validate_transition(current_status, new_status):
            raise GovernanceError("invalid_transition",
                f"Cannot transition from '{current_status}' to '{new_status}'")
        
        rule["status"] = new_status
        rule[f"changed_to_{new_status}_at"] = datetime.now(timezone.utc).isoformat()
        rule[f"changed_to_{new_status}_by"] = user_id
        rule[f"change_to_{new_status}_reason"] = reason
        
        self._save_rules(rules)
        
        self._log_event(
            rule_id=rule_id,
            event_type="status_change",
            user_id=user_id,
            details={
                "from_status": current_status,
                "to_status": new_status,
                "reason": reason
            }
        )
        
        return True
    
    def get_rule_lifecycle(self, rule_id: str) -> dict[str, Any]:
        """Get complete lifecycle history for a rule."""
        rule = next((r for r in self._load_rules() if r["rule_id"] == rule_id), None)
        if not rule:
            raise GovernanceError("rule_not_found", f"Rule {rule_id} not found")
        
        audit_events = self.get_audit_trail(rule_id=rule_id)
        
        return {
            "rule": rule,
            "audit_trail": audit_events,
            "current_status": rule.get("status"),
            "confidence_gates": self.check_confidence_gate(rule),
            "evidence_gates": self.check_evidence_gate(rule)
        }