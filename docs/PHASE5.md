# Phase 5 — Bounded regulatory document analysis

Phase 5 adds a finite LangGraph `StateGraph` workflow on top of the existing Phase 2 evidence and Phase 4 NVIDIA extraction service. It proposes **in-memory, unverified candidates only**. It does not write to `obligations.csv`, alter review queues, create rules, publish findings, or activate obligations.

## Dependency and setup

The project uses Python 3.11.9. LangGraph was not previously installed. Its current compatible release line supports Python 3.10 and newer, so `requirements.txt` now declares `langgraph>=1.2,<2.0`. The workflow uses only `langgraph.graph.StateGraph`; it does not add LangChain model adapters or agent packages. Install dependencies with:

```powershell
cd C:\Users\priya\Downloads\TraceRule-AI\TraceRule-AI
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
```

The NVIDIA key remains in the existing ignored `.env` or process environment. It is never added to workflow state, audit output, or logs.

## Workflow

The graph runs these nodes once, in order, with no cycles:

1. `context_loader` validates the existing Phase 2 evidence dataset, requires a registered document and matching evidence IDs, and confirms the registered PDF exists in the local current or historical source directory. It accepts at most three unique evidence passages per run. `DOC-DL-2022` is blocked before any model call.
2. `evidence_extraction_agent` calls the existing NVIDIA wrapper through `extract_candidate_obligation` with `reasoning_effort=low`. Each structured extraction uses `NVIDIA_EXTRACTION_TIMEOUT_SECONDS` (default 60 seconds, hard maximum 120) and zero retries. Its separate output budget is `NVIDIA_EXTRACTION_MAX_TOKENS` (default 1024, maximum 4096). The health probe keeps its own shorter timeout and output settings. Null, empty, and token-limit-terminated completions are marked incomplete; reasoning content is never substituted for final content.
3. `validation_agent` checks document/evidence/page references, exact quotation grounding, and human-review requirements. Explicit requirement wording must be copied from the supporting quote. Invalid outputs are rejected.
4. `obligation_candidate_agent` creates transient candidates only for supported `explicit_requirement` or `interpretation` outputs. It preserves the exact supporting quotation separately from any proposed interpretation.
5. `review_gate` forces `review_status=pending_human_review`, `verification_status=unverified_candidate`, and `activation_status=inactive`.
6. `audit_result` returns workflow status, document/source status, model name and safe request settings, processed evidence IDs/counts, validation errors, review status, and completion time. It excludes passages, prompts, model response bodies, and credentials from the audit record.

`DOC-CB-2025` remains `VERIFY_CURRENT_STATUS` in the registry and is copied as-is into the result. Phase 5 does not assess currency, amendment incorporation, legal applicability, or whether an interpretation is legally correct. Every result remains subject to human review. The workflow does not read or modify synthetic Phase 3 rule sets and does not connect to the deterministic rule engine.

The extraction schema now requires `candidate_type` (`explicit_requirement`, `interpretation`, or `no_candidate`), `supporting_quotation`, and a non-empty `uncertainty` field in addition to the existing Phase 4 fields. Quotation validation first accepts an exact substring, then permits only Unicode canonical equivalence and folded whitespace runs; letters and punctuation are never removed or substituted, so joined words such as `dataprivacy` do not match `data privacy`. A successful normalized match is mapped back to the original passage and returned as its exact source slice, with zero-based, end-exclusive Python character offsets; the model's original quote is retained separately as `model_supporting_quotation`. For `explicit_requirement`, the model's proposed wording must itself map to the validated source quote. The workflow exposes that raw model wording as `model_proposed_obligation` and derives `proposed_obligation` from the exact mapped source slice. For `interpretation`, the model proposal is retained as an interpretation and is never labeled source wording. Paraphrased or invented quotes are rejected. The quotation contract cannot prove legal meaning, so interpretation candidates remain explicitly labeled and unverified.

## Run

With NVIDIA configuration available, invoke the workflow for one to three evidence IDs already present in `data/processed/evidence_passages.csv`:

```powershell
.\.venv\Scripts\python.exe -m backend.services.regulatory_analysis_workflow `
  --document-id DOC-CB-2025 `
  --evidence-id DOC-CB-2025-CH3-P006
```

Add another `--evidence-id` argument to include another passage, up to three. The CLI prints the in-memory result as JSON; it does not write a candidate or obligation file. Errors are returned without prompts, raw provider bodies, or secrets.

Run the mocked tests, without making NVIDIA calls, using:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py" -v
```

## Limitations

- Candidate generation still depends on model output. Exact quotation checks establish textual grounding only; they do not validate legal effect, scope, or completeness.
- Only the existing local evidence dataset is available. There is no scraping, live legal-source retrieval, document amendment reconciliation, or citation discovery.
- Historical/superseded `DOC-DL-2022` is blocked. Other registry statuses are carried through without resolving them.
- A graph failure or timeout produces an audit error and no candidate for that passage. Calls are sequential and bounded to three passages, each with the configured bounded extraction timeout and no retries.
- The extraction timeout and token budget can be changed in the project's ignored `.env` or process environment using `NVIDIA_EXTRACTION_TIMEOUT_SECONDS` (1–120) and `NVIDIA_EXTRACTION_MAX_TOKENS` (1–4096). The defaults allow longer structured output while keeping each call and the maximum three-call workflow bounded. No retry is performed by Phase 5.
- No human review UI or write-back path is included. A separate human review process remains necessary before any obligation could enter the verified dataset.
