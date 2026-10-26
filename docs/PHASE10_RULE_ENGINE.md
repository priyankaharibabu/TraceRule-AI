# Phase 10: Deterministic Rule Engineering and Verification

## Existing rule model

`backend/schemas/rule_schema.json` is the versioned JSON Schema contract (schema version `1.0.0`). Rules use semver `rule_version`, a restricted JSON condition tree, a constrained action type, status, approval marker, and source-obligation metadata. `rule_validator.py` validates this format and rejects unknown fields and executable-code-like payloads. `rule_engine.py` remains the deterministic Python outcome evaluator and calls the existing real-source eligibility gate before evaluating non-synthetic mappings.

## Phase 10 verification route

`POST /phase10/synthetic/verify` accepts only a fixed synthetic rule ID and fixed synthetic test-case ID. It loads those IDs from the checked-in synthetic fixtures; it does not accept rule definitions, transaction inputs, or source metadata from the caller. The endpoint is restricted to loopback clients and does not persist results. The Analyzer & synthetic demonstrations page exposes a small form for these fixed fixture IDs and displays validation, OPA, Z3, and Python evaluation outcomes separately.

The workflow reports four independent results:

1. Restricted schema validation.
2. OPA/Rego eligibility for an explicitly labelled synthetic `test` rule and synthetic case.
3. Z3 satisfiability of the rule condition (and the service exposes a separate condition-overlap helper).
4. The existing deterministic Python rule outcome for the fixture's inputs.

OPA is an eligibility boundary only. It does not interpret a rule outcome or establish policy truth. Z3 satisfiability and overlap indicate logical properties only; they are not evidence of compliance, legal conflict, duplication, or regulatory validity. The action result is a simulated outcome from the existing Python evaluator. No approvals, activations, candidate transitions, or audit records are created.

The OPA CLI is located by `TRACERULE_OPA_BIN` or `PATH`. Missing OPA, execution errors, timeouts, and malformed output fail closed. The OPA binary used for local validation was downloaded to the OS temporary directory, checksum verified against the official checksum endpoint, and is not part of the project. `z3-solver==4.16.0.0` was installed into the existing `.venv` following operator approval; dependency manifests were not changed. Z3 is imported lazily; errors do not imply a simulated solver result.

## Verification

Validation performed on 2026-10-26:

- `& "$env:TEMP\tracerule-phase10-engines\opa.exe" check --strict backend\policies\phase10.rego`: passed with OPA 1.21.4.
- `z3-solver` 4.16.0.0 import and actual solver checks: passed.
- `\.venv\Scripts\python.exe -m pytest backend\tests -q`: **234 passed, 48 subtests passed**.
- `\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v`: **147 passed**. The unittest and pytest sets overlap; do not add counts.
- `npm.cmd run build`: passed; Vite transformed 1,375 modules.
- Chrome DevTools Protocol against the local app: all primary pages plus Bank Policies and Analyzer rendered; Phase 5 redirected to Candidates; proposed-rule upload/extract/compare returned HTTP 200 using a temporary synthetic non-regulatory PDF; Phase 8 upload returned HTTP 200 with NVIDIA opt-in unchecked; Phase 9 BM25-only retrieval returned HTTP 200 with source page 1; Phase 10 UI returned HTTP 200 with OPA allowed, Z3 `sat`, and separate simulated Python outcome; malformed JSON policy preview returned HTTP 400 and did not expose commit. No console exceptions were observed. The invalid policy preview's HTTP 400 was expected. A missing `/favicon.ico` request returned 404.
- `node --test tests/fetchWithTimeout.test.js`: **1 passed**. The frontend has no `npm test` script; this existing Node test was run directly.
- Persistent `data/` integrity: all **26** files matched their pre-test SHA-256 hashes after normalizing the baseline's UTF-8 BOM and digest case; no files were missing or new. Registry statuses remained `DOC-CB-2025=VERIFY_CURRENT_STATUS`, `DOC-DL-2025=HISTORICAL_STATUS_REQUIRES_REVIEW`, and `DOC-DL-2022=HISTORICAL_STATUS_REQUIRES_REVIEW`; 77 candidate queue rows, 0 verified obligations, and 0 bank-policy records remained.

The route reads only fixed fixture IDs and produces no persistent writes. NVIDIA was not called. The browser test PDF was written only to the OS temporary directory and contained fictional training text, not regulatory or policy content.

No genuine human-verified obligations, current source-authority determination, genuine bank policies, or transaction dataset are available. Therefore there are no real regulatory rules to validate or execute. Existing source-scope checks continue to block unverified candidates, superseded `DOC-DL-2022`, legal-review-pending `DOC-DL-2025`, and unresolved-currentness `DOC-CB-2025` mappings. Human approval and production enforcement are outside this prototype route.

## Security and production limits

This remains a loopback-only unauthenticated prototype. Do not expose the API to a network. `TRACERULE_OPA_BIN` must point to a trusted, locally controlled binary. Production use needs authenticated identities, role-based approvals, durable tamper-evident audit records, authorized policy data, deployment controls, and independent legal/compliance validation. None of these results constitute regulatory advice or production approval.
