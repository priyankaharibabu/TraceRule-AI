# Phase 8 — Evidence-Grounded Proposed Rule Impact Analysis

Phase 8 adds a bounded, deterministic impact report for a proposed rule against an explicitly supplied ruleset. It reuses the restricted rule validator, structural conflict detector, and baseline/proposed simulator. Analysis is read-only: it does not approve, activate, or persist rules.

## Local synthetic demonstration

The active standalone page in `frontend/index.html` offers four fixed examples through `GET /demo/impact/{scenario_id}`:

- `DEMO-IMPACT-REGULATORY`: a synthetic RBI-like source and a direct rule contradiction.
- `DEMO-IMPACT-BANK-POLICY`: a synthetic bank policy and a direct rule contradiction.
- `DEMO-IMPACT-CLEAN-SCOPE`: no conflict detected within a small bounded ruleset; source status is unresolved.
- `DEMO-IMPACT-AMBIGUOUS`: an intentionally broad condition requiring review.

The fixtures and references are stored in `data/demo/proposed_rule_impact_fixture.json`, separate from regulatory records. Their clauses, rule IDs, documents, paragraphs, and evidence IDs are synthetic. They are not actual RBI requirements or bank policies.

## Report semantics and limits

The service reports exact duplicates from matching conditions and action parameters. A possible near duplicate is only a review prompt based on a shared field, operator, and action with a different value; it is not a semantic-equivalence claim. Direct and conditional contradictions use the existing conflict detector. Conditional overlap uses that detector's bounded sample inputs and requires reviewer confirmation. Action pairs without an explicit compatibility result are marked unresolved rather than conflict-free.

`no_conflict_detected_within_scope` means only that no supported conflict was found in the supplied ruleset. It is not proof of regulatory compliance or absence of conflicts outside that scope. Missing evidence, ambiguous conditions, unverified sources, and historical or unresolved source status are reported as limitations. A claimed verified source is cited only when the obligation, evidence passage, and document registry entry can be corroborated from stored processed data.

The current dataset contains zero verified obligations, so this phase does not provide verified RBI citations or current legal conclusions. No complete set of real bank policies is available. Synthetic analysis results always report `approval_status: not_performed` and `activation_status: inactive`.

## Validation

Run the focused impact tests with:

```powershell
python -m pytest -q backend\tests\test_proposed_rule_impact.py
```

The fixed scenarios do not accept caller-supplied rules, evidence, paths, reviewer identities, or mutations. The impact endpoint is separate from the existing clean/conflict synthetic demo and document-analysis routes.
