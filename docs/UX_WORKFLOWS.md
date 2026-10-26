# TraceRule AI UX and workflow map

## Primary navigation

- **Dashboard** — counts from the local document registry, indexed evidence, candidate review queue, and verified-obligation CSV. Missing stores or failed requests appear as unavailable. Recent activity comes only from local audit records.
- **Documents** — the persistent registry in `data/processed/document_registry.csv`, with exact metadata and passage counts. Uploaded files used by document analysis are temporary and are not registered here.
- **Evidence Passages** — exact indexed text with evidence/document IDs and page references. “Extract candidate” opens the combined extraction workflow with the selected passage preselected.
- **Candidates & Extraction** — selects registered evidence, invokes the existing Phase 5 extraction workflow when explicitly requested, presents source and uncertainty, and lets the user add grounded outputs to the existing `obligation_candidates.csv` and `obligation_review_queue.csv` stores. Human review remains a separate step.
- **Proposed Rule Review** — temporary PDF intake, analyst-entered structured draft fields, separated evidence/wording/structured/execution tabs, and the existing local decision-record workflow.
- **Governance** — reports persisted rule-lifecycle records from the existing verified-rules store. The current project does not contain that store; demo rules are not shown as real governance records.
- **Audit Trail** — displays events found in governance and candidate-review audit files. It labels the unauthenticated and non-tamper-proof nature of local records.

**Policy Management** contains **Bank Policies**. Its preview/commit importer and unverified provenance rules are unchanged. **Tools & Demonstrations** contains the retained **Analyzer & synthetic demonstrations** page. The analyzer file and route are preserved, and its fictional scenarios are not included in regulatory metrics or proposed-rule findings.

The old `/phase5` route redirects to `/candidates?tab=extract`. Other existing routes remain available.

## Candidate source and review safeguards

The current dataset contains pre-existing candidate records whose candidate wording does not occur as a contiguous span in the associated exact evidence text. The Candidates page lists these with a source-review warning, displays their linked passage, and blocks verification/rejection actions for them. This preserves visibility without treating uncertain source mapping as grounded. New workflow candidates can be submitted only when the extraction service marks them grounded and their wording is present in the indexed passage. The existing review service still checks source grounding before writing a verified obligation.

Extraction is initiated only by an explicit user click and uses the project's existing configured provider. The workflow result remains temporary until the user selects grounded candidate(s) and submits them. Submitted rows are appended to the existing candidate CSV and review queue; they remain unverified and inactive. A verified candidate decision requires human-entered review fields and writes the existing obligations/audit records. Reviewer identifiers are local text and are not authenticated.

The workflow blocks the superseded `DOC-DL-2022` source before extraction, and extracted candidates remain provisional for unresolved source status. There is no automatic approval, rule activation, or synthetic fallback.

## Local API routes added for the consolidated views

- `GET /documents` and `GET /evidence` remain the source registry/index reads.
- `GET /candidates` returns candidate records joined to their existing review state without writing files.
- `POST /candidates/extract` calls the existing workflow for selected source IDs.
- `POST /candidates/extract/submit` appends selected grounded outputs to the existing candidate/review CSV stores.
- `POST /candidates/{candidate_id}/review` uses the existing human review service.
- `GET /governance/overview` and `GET /audit-trail` read actual local records.

New candidate and governance/audit routes are loopback-only. These controls do not provide authentication. Extraction results are held temporarily in process memory until submission; restarting the backend clears pending extraction sessions.

## Manual browser check

1. Start the existing backend from the project root: `\.venv\Scripts\python.exe -m uvicorn backend.services.document_analyzer:app --reload --port 8000`.
2. In a second terminal, run `cd frontend` and `npm run dev`.
3. Open the local Vite URL. Check that the sidebar shows the seven primary destinations, with Bank Policies and Analyzer & synthetic demonstrations in secondary groups.
4. Confirm Dashboard values load from the registry and local CSVs. Recent activity should be empty when no audit events exist; governance storage should be reported unavailable when no verified-rules file exists.
5. Open Documents and Evidence Passages. Verify registry values, exact evidence references, and that an “Extract candidate” link opens Candidates & Extraction with its source selected.
6. In Candidates & Extraction, select a registered document and one evidence passage. Extraction makes a request to the configured provider only after the button is clicked. Inspect source quote, status, and uncertainty; submit only a grounded result. Confirm it appears as pending in the existing queue.
7. Choose a source-grounded pending candidate to review. Verify requires all human-reviewed fields; rejected requires a reason. Confirm the UI states that reviewer identity is not authenticated. Do not treat the local decision as production approval.
8. Open Proposed Rule Review, upload a PDF, inspect the evidence tabs, and record a local decision. Verify that the decision button disables after success and no execution/activation result is shown.
9. Open Tools & Demonstrations to confirm synthetic scenarios are separately labeled. These scenarios are demonstrations only.

Automated checks do not replace this browser walkthrough; no browser automation was available for this change.
