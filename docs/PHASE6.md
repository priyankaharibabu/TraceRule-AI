# Phase 6 — Rule Lifecycle Governance

Phase 6 implements a local rule lifecycle prototype with confidence gates, an approval workflow, a CSV event log, and rollback backups. It is not a production authorization or tamper-resistant audit system.

## Architecture

### Components

1. **Governance Service** (`backend/services/governance.py`): Core lifecycle management with:
   - Status transitions (draft ↔ test ↔ pending_approval ↔ approved ↔ retired)
   - Confidence and evidence gate validation
   - Human approval workflow with reviewer tracking
   - Official verification gate (mocks external system)
   - CSV event log that appends rows during normal operation (without tamper protection)
   - Safe rollback with backup preservation

2. **Rule Status Lifecycle**:
   ```
   draft → test → pending_approval → approved → retired
          ↑         ↓          ↑         ↑
          └─────────┼──────────┘         └── rollback
   ```

3. **Data Files**:
   - `governance_audit.csv`: CSV governance event log
   - `verified_rules.json`: Current rule state with governance metadata
   - `rule_backups/`: Safe rollback backups with timestamps

### Gates and Validation

#### Confidence Gates
- **draft → test**: Minimum 0.7 confidence
- **test → pending_approval**: Minimum 0.85 confidence
- All stages: At least one evidence reference required

#### Evidence Gates
- Source obligation must be verified (`verification_status="verified"`)
- `dataset_kind` must be "verified" (not synthetic)
- Valid `obligation_id` must be present

### Human Approval Workflow

1. **Submission**: Caller supplies a submitter ID when submitting a test rule
2. **Review**: Reviewer examines the rule and evidence
3. **Decision**: Approve (promote to approved) or Reject (send back to draft)
4. **Verification**: Optional verification check (currently mocked)

The service compares normalized caller-supplied submitter and reviewer IDs to prevent self-approval. It does not authenticate those IDs.

### Audit Trail

Governance events are appended to `governance_audit.csv` by the service:
- `event_id`: Unique identifier
- `timestamp`: ISO 8601 timestamp
- `rule_id`: Affected rule
- `event_type`: status_change, approval_submitted, approval_decided, verification, rollback
- `user_id`: User performing action
- `details`: JSON with event-specific details

This is a plain CSV file. The implementation does not prevent editing or deletion, provide cryptographic integrity checks, or guarantee that every state change and event write succeeds atomically.

### Safe Rollback

Rules can be safely rolled back from `approved` or `pending_approval` states to `draft` or `test`:
1. Creates timestamped backup of current rule state
2. Updates rule status with rollback metadata
3. Clears approval fields if rolling back from approved
4. Records rollback event in audit trail

## API Usage

```python
from backend.services.governance import RuleGovernance

gov = RuleGovernance()

# Promote rule from draft to test
gov.promote_to_test("RULE-001", "user123")

# Submit for approval
gov.submit_for_approval("RULE-001", "user123")

# Approve/reject
gov.approve_rule("RULE-001", "reviewer456", "Looks good")
gov.reject_rule("RULE-001", "reviewer456", "Needs more work")

# Official verification
gov.verify_rule("RULE-001", "verifier789")

# Rollback
gov.rollback_rule("RULE-001", "draft", "Bug found", "admin")

# Get lifecycle history
lifecycle = gov.get_rule_lifecycle("RULE-001")
```

## Security and Constraints

### Authorization
- No authentication, session, or role-management provider is integrated in this repository.
- Governance methods accept caller-supplied identity strings; role permissions are not checked.
- Self-approval prevention compares normalized supplied IDs only and does not establish real identity.

### Data Integrity
- The CSV event log is append-oriented but is not tamper-proof.
- Rule backups preserve copies of prior rule state when a rule file is saved.
- All transitions validated against allowed state machine
- Confidence and evidence gates enforce quality standards

### Integration Points
- Uses verified-obligation references from the Phase 2 dataset; there is no direct Phase 5 workflow integration.
- Compatible with Phase 3 rule engine (`EVALUABLE_STATUSES` includes "test", "pending_approval", "approved")
- Audit trail can feed into compliance reporting systems

## Testing

Run governance tests:
```powershell
python -m pytest backend/tests/test_phase6_governance.py -v
```

Test coverage includes:
- Status transition validation
- Confidence and evidence gate checks
- Approval workflow
- Audit trail integrity
- Rollback safety
- Invalid state transitions and approval-gate failures; authorization is not implemented or tested

## Phase 6 + Phase 5 Text Integrity Integration

Phase 6 governance works with Phase 5's enhanced text integrity validation:

1. **Model Quote Verification**: Phase 5 validates extracted quotes against source passages before returning candidates.
2. **Source Text Integrity**: Phase 5 includes a warning system for possible merged words in extracted text.
3. **Interpretation Grounding**: Phase 5 flags interpretation candidates as ungrounded.
4. **Governance Boundary**: The governance service does not rerun these text-integrity checks or record their results in its audit log.

These Phase 5 checks do not establish legal meaning, source currency, or regulatory compliance.

## Limitations

- **Verification Gate**: Current implementation mocks external verification; production would integrate with actual verification systems
- **Identity and authorization**: No trusted identity source is integrated. Supplied IDs are audit labels, not authenticated identities; a future integration must obtain identity from an established authentication/authorization layer before governance actions.
- **Performance**: Audit trail grows linearly; may need archiving for large-scale deployments
- **Concurrency**: File writes use no locking or transaction mechanism and are not suitable for concurrent production use.
- **Audit integrity**: CSV audit entries and rule files are locally editable; use protected, access-controlled, tamper-evident storage before production use.