# Bank Policy Repository (Prototype)

This repository is an empty, local JSON store at `data/bank_policies/policy_repository.json`. JSON follows the schema in `backend/schemas/bank_policy_repository.schema.json`. JSON was selected because the project already stores versioned rule and evidence artifacts as JSON and has no database, migrations, or ORM. Writes use a temporary file and atomic replace. This is suitable for a single-user local prototype; it does not provide concurrent multi-user transactions, tamper resistance, encryption, or production access control.

## Import format

The local-only import API accepts UTF-8 CSV and JSON files up to 10 MB. JSON has this shape:

```json
{
  "policies": [
    {
      "policy_id": "provided-by-source",
      "policy_title": "provided-by-source",
      "policy_category": "provided-by-source",
      "owning_organization": "optional source value",
      "source_document_reference": "provided source reference",
      "versions": [
        {
          "version": "provided version",
          "effective_from": "YYYY-MM-DD",
          "effective_to": null,
          "status": "draft | approved | retired",
          "approval_details": [],
          "clauses": [
            {
              "clause_id": "provided-by-source",
              "clause_title": null,
              "clause_text": "exact source wording",
              "section": null,
              "paragraph": null,
              "page": null,
              "source_document_reference": "provided source reference",
              "executable_rule_definition": {}
            }
          ]
        }
      ]
    }
  ]
}
```

CSV is one row per clause. Required columns are `policy_id`, `version`, `status`, `clause_id`, and `clause_text`; recommended columns include `policy_title`, `policy_category`, `owning_organization`, `effective_from`, `effective_to`, `source_document_reference`, `clause_title`, `section`, `paragraph`, and `page`. Optional approval columns are `approval_reference`, `approver`, `approval_date`, and `approval_decision`.

Preview reports accepted, incomplete, rejected, and duplicate records without writing data. A separate commit request saves the accepted and incomplete versions; rejected records are not stored. Existing policy/version pairs are never overwritten. A repeated policy ID is allowed across distinct versions, but its title/category identity must remain consistent. Duplicate clause IDs within one policy version are rejected. Dates use ISO `YYYY-MM-DD`; effective-to cannot precede effective-from.

Missing source and approval information remains explicit. The importer records filename, file SHA-256, import time, and `unverified_import` provenance. Any source-provided verification field is retained as a source claim only; importing cannot verify it or grant approval. Consequently, imported clauses are screening-only and cannot be presented as active requirements. Approved/effective comparisons require independently verified provenance and approval events, which this unauthenticated prototype cannot establish.

## Local API and safeguards

- `GET /bank-policies`: list repository records and empty/populated state.
- `POST /bank-policies/import/preview`: validate a CSV/JSON upload; no persistence.
- `POST /bank-policies/import/commit`: persist a prior preview token.

These routes reject non-loopback connections. Loopback restriction is not authentication: do not expose the backend to a network with confidential policy records. Production requires organizational authentication/authorization, verified source ingestion, durable transactional storage, protected audit logs, and approved data-handling controls.

Proposed-rule review searches imported clauses for text similarity and labels matches as screening-only while provenance is unverified. It selects active clause candidates only when the version is approved, effective today, has verifiable approval details, the source and clause are independently marked verified, and a clause source reference exists. Text similarity itself never establishes equivalence or conflict. The current deterministic engine's real-rule gate maps rules through verified RBI obligations and regulatory scope; it is not a bank-policy execution interface. The repository therefore does not turn natural-language clauses into rules. A reviewed executable mapping and genuine authorized transaction dataset are still required before real transaction impact can be measured. Synthetic fixtures remain separate and are never imported into this repository.

## Phase 4 comparison behavior

The proposed-rule response separates wording screening, structured condition/action comparison, source sufficiency, and transaction execution. RBI passage matches are evidence-bound wording leads; they are not executable obligations. Bank imports may include an `executable_rule_definition` claim, but import records it as `executable_rule_definition_claim` with `executable_rule_verification_status: unverified_import`. It is never exposed to comparison or execution as a reviewed rule definition. Independent review and verified provenance are required before a structured bank mapping can be used.

The comparison logic recognizes identical structured requirements, some supported single-field comparison overlaps, disjoint conditions, and incompatible `allow`/`deny` or `set_limit`/`deny` actions. Unsupported compound-condition overlap is reported as unknown. These are preliminary deterministic findings for reviewer follow-up, not legal conclusions. The current project has no verified obligation rows, no imported bank-policy versions, and no genuine authorized transaction dataset. As a result, actual same-input rule-engine comparisons and real transaction impact are unavailable; no synthetic fallback is used. The feature reports null execution outputs and `not_executed` explicitly.
