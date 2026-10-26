# Phase 3 — Deterministic Rule Engine

**SIMULATED INTERNAL CONTROLS — NOT ACTUAL BANK POLICIES**

## Scope status

Option A is the selected technical scope: DOC-CB-2025, Chapter III, paragraphs 6–31. Real regulatory rule derivation remains **blocked** pending official current-status and amendment verification, provision-level applicability and legal review, and human-verified obligations. The local CB-2025 PDF is not asserted to incorporate 2026 amendments. Phase 3 currently ships schema validation, synthetic controls, evaluation, conflict detection, and baseline-vs-proposed simulation only. Synthetic controls are not actual bank policies.

## Rule schema

- File: `backend/schemas/rule_schema.json` (schema version `1.0.0`)
- Condition nodes: `comparison`, `logical` (`and`/`or`), `not`, `missing`, `broad`
- Comparison operators: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `not_in`
- Action types: `allow`, `deny`, `require_review`, `flag`, `set_limit`, `require_disclosure`
- Status lifecycle: new rules stay `draft` or `test`; `approved` requires non-null `approved_by` (human workflow outside the engine)

## Synthetic vs verified data

| Dataset | Location |
| --- | --- |
| Synthetic baseline rules | `data/synthetic/synthetic_rules_baseline.json` |
| Synthetic proposed rules | `data/synthetic/synthetic_rules_proposed.json` |
| Synthetic test cases | `data/synthetic/synthetic_test_cases.json` |
| Conflict report | `data/synthetic/synthetic_conflict_report.json` |
| Simulation output | `data/processed/simulation_baseline_vs_proposed.json` |
| Verified obligations | `data/processed/obligations.csv` (human-verified only) |

The offline demonstration uses a separate `data/demo/synthetic_demo_fixture.json` fixture and writes demo runtime state only under `data/demo/`. It uses the deterministic mock extractor and the existing synthetic rule, conflict, simulation, and approval-gate services. Demo candidates remain pending and unverified; scenario checks do not approve or activate rules and cannot write to the Phase 2 review queue or verified-obligations dataset.

## Commands (PowerShell)

```powershell
cd C:\Users\priya\Downloads\TraceRule-AI\TraceRule-AI
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path

# Phase 2 validation
.\.venv\Scripts\python.exe backend\services\validate_evidence_dataset.py

# Phase 1 smoke (imports + PDF module)
.\.venv\Scripts\python.exe -c "import fitz; from backend.services import pdf_extractor; print('Phase 1 import OK')"

# Generate synthetic artifacts
.\.venv\Scripts\python.exe backend\services\generate_synthetic_data.py

# Baseline vs proposed simulation
.\.venv\Scripts\python.exe backend\services\baseline_simulation.py

# Tests
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py" -v
```

## Known limitations

- Condition overlap uses sampled inputs; ambiguous cases report `requires_review` instead of asserting conflict.
- Coverage gaps are relative to the synthetic rule set only, not regulatory completeness.
- DOC-DL-2022 must not be used for active obligations (superseded; historical comparison only).
- No rules are auto-approved or activated.
