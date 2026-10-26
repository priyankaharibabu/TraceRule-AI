# Phase 8 AI Candidate Pipeline

## Data flow and status boundaries

`POST /phase8/analyze` accepts one PDF up to 15 MB from a loopback connection. PyMuPDF extracts text in memory, assigns deterministic upload and passage identifiers, and retains PDF page/source references. Scanned-only PDFs are rejected because this workflow does not perform OCR. The request is bounded to 400 pages and 2,000,000 extracted characters.

Local BM25 ranks at most three passages. Retrieval does not establish legal relevance, completeness, applicability, or conflict. With the NVIDIA option off (the default), the API returns extracted and retrieved passages without contacting an LLM.

When the caller explicitly opts in, the existing NVIDIA-compatible NIM client receives only the retrieved passages. A Pydantic schema rejects extra or malformed model fields. Code verifies that the model's quotation exists in the corresponding source passage and, for explicit requirements, that the proposed obligation is a verbatim span of that quotation. Failed output receives one correction attempt; it cannot enter the candidate list unless validation succeeds.

Every returned upload is marked `unverified_user_upload`; generated suggestions are `unverified_candidate`, `pending_human_review`, and `inactive`. The Phase 8 endpoint never writes the uploaded PDF, creates project evidence, or submits to the persistent regulatory candidate queue. Existing registered-source extraction and its explicit queue submission flow remain unchanged.

The endpoint is limited to loopback requests, upload size, page count, extracted text, retrieval count, provider timeout, and retries. Provider failures and invalid output are reported with sanitized errors. No tracing backend, checkpoint store, persistent vector index, LlamaIndex, embeddings, reranker, OPA, or Z3 was added because this workflow does not need them at the current project scale.

## Live provider operation

NVIDIA NIM integration uses the existing `NvidiaSettings` and `LLMClient`. The API key must be configured by the operator; its value is never returned. The browser checkbox is unchecked by default and explicitly warns that retrieved excerpts will be sent externally. Credential presence does not prove the key, model, account access, or endpoint works. Live inference was not invoked during implementation or automated tests; deterministic test providers were used instead.

## Test command

Run focused pipeline tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_phase8_pipeline -v
```

## Validation record (2026-10-26)

- The project `.venv` is Python 3.11.9. `pytest` was initially missing; after approval it was installed into this virtual environment with `.\.venv\Scripts\python.exe -m pip install pytest`.
- Full backend test suite, including pytest-dependent files: `.\.venv\Scripts\python.exe -m pytest backend\tests -q` — **224 passed, 48 subtests passed**.
- Phase 8 browser validation used installed Chrome over the local DevTools protocol and the running Vite/backend servers. A clearly marked temporary, non-regulatory PDF fixture exercised successful text extraction, page-linked passage display, default-disabled provider mode, invalid file rejection, and textless-PDF extraction errors. Both normal and pending loading labels were observed. Browser console exceptions: **0**; failed network requests: **0**.
- No NVIDIA NIM inference was called. Provider failure, malformed structured output, grounding, and inactive/human-review gates were exercised using deterministic mocks.
- SHA-256 comparison against the pre-test `data/` manifest: **26 files unchanged; none missing**. The 77 pending candidates and empty verified-obligations and bank-policy records remained unchanged.

The implementation was validated with local files and mocks. Live NVIDIA access and inference are still unverified. The browser check did not establish legal correctness or regulatory applicability.

