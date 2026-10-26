# Option A — Proposed technical scope

**Recorded:** 2026-10-24 (owner confirmation)

## Scope definition

| Field | Value |
| --- | --- |
| Instrument | Reserve Bank of India (Commercial Banks – Credit Facilities) Directions, 2025 |
| Document ID | `DOC-CB-2025` |
| Chapter | **III — Digital Lending Guidelines** |
| Paragraph range | **6–31** (matches Phase 2 evidence boundary) |
| Machine-readable record | `data/processed/rule_derivation_scope.json` |

Phase 1 and Phase 2 validated outputs for this boundary are **unchanged**. Real regulatory rule mapping waits on human-verified obligations in `obligations.csv` (currently **0** rows).

## What Phase 3 continues to use

- **Synthetic rules** under `data/synthetic/` labelled **SIMULATED INTERNAL CONTROLS — NOT ACTUAL BANK POLICIES**
- Deterministic schema, engine, conflict detector, 400 seeded test cases, baseline-vs-proposed simulator
- All rules remain **`draft`** or **`test`** unless a future explicit human approval process authorises otherwise

## Governance (enforced in code)

`backend/services/scope_governance.py` blocks:

- Active derivation from `DOC-DL-2022`
- Verified (`dataset_kind: verified`) rules without a matching human-verified obligation and in-scope evidence
- Use of `DOC-DL-2025` for verified mapping until legal review flag is cleared
- Treating `VERIFY_CURRENT_STATUS` as resolved for legal applicability

## Unresolved legal questions (do not infer answers)

1. **CB-2025 currency:** Registry `document_status` remains `VERIFY_CURRENT_STATUS`. Official master-directions URL is recorded; whether the **local PDF byte-identical** to the live RBI consolidated file is **not** established.
2. **2026 amendments:** Local extracted text and RBI web copy both *display* “Updated as on October 1, 2026” and amendment footnotes. **Amendment incorporation into the local file is not assumed** without independent official verification (e.g. hash match to RBI PDF, or documented download provenance).
3. **Per-provision effective dates:** Footnotes cite differing effective dates (e.g. July 1, 2026; October 1, 2026). Which provisions are operative on a given date requires provision-level legal review.
4. **DL-2025 vs CB-2025 Ch. III:** For commercial banks, digital lending may appear in both the standalone Digital Lending Directions 2025 and Chapter III of Credit Facilities Directions 2025. **Which instrument governs which obligation is unresolved** — legal review required before any real cross-document mapping.
5. **DL-2022:** RBI notification states the September 2022 circular is **repealed** in favour of DL Directions 2025. Historical comparison only; **no active obligations**.
6. **Applicability of each Ch. III paragraph:** Phase 2 evidence supports text retrieval for ¶6–31; **regulatory applicability** (entity type, product, timing) is not verified until obligation review completes.

## Official sources (URL verified 2026-10-24)

- CB-2025: [Master Directions id=13156](https://rbi.org.in/SCRIPTs/BS_ViewMasDirections.aspx?id=13156) — RBI/DOR/2025-26/154
- DL-2025 (comparison / legal review only): [Master Directions id=12848](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12848)
- DL-2022 (historical): [Notification Id=12382](https://www.rbi.org.in/scripts/FS_Notification.aspx?Id=12382&Mode=0&fn=2)

## Next step when obligations are verified

Use `backend/services/prepare_verified_rule_draft.py` (validation-only) to check eligibility. It produces **draft** rule payloads only; it does not approve, activate, or write into synthetic datasets.
