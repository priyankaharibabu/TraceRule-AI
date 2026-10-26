# TraceRule AI

Regulatory circular → evidence → obligation review → deterministic rule engine (Phases 1–3) → governance lifecycle (Phases 4–6).

Validated Phase 2 evidence lives under `data/processed/`. Phase 3 synthetic controls live under `data/synthetic/` and are labelled **SIMULATED INTERNAL CONTROLS — NOT ACTUAL BANK POLICIES**.

See [docs/PHASE3.md](docs/PHASE3.md) for the deterministic engine, [docs/PHASE4.md](docs/PHASE4.md) for NVIDIA connectivity, [docs/PHASE5.md](docs/PHASE5.md) for the bounded document analysis workflow with text integrity validation, [docs/PHASE6.md](docs/PHASE6.md) for rule lifecycle governance, and [docs/PHASE8.md](docs/PHASE8.md) for bounded proposed-rule impact analysis.

## Quick start (Windows, Python 3.11.9)

```powershell
cd TraceRule-AI
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe backend\services\generate_synthetic_data.py
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py" -v
```

## Recent Updates

### Phase 5 Text Integrity Validation (Fixed)
- **Model Quote Verification**: Independent validation of model's original quote against source text
- **Source Text Integrity**: Warning system for merged-word detection in PDF extraction
- **Interpretation Grounding**: Proper flagging of interpretation candidates as ungrounded
- **All 69 Phase 1-5 tests passing** with enhanced validation

### Phase 6 Governance (Implemented)
- **Rule Lifecycle**: draft ↔ test ↔ pending_approval ↔ approved ↔ retired
- **Confidence Gates**: 0.7 for testing, 0.85 for approval submission
- **Human Approval Workflow**: Submission → Review → Decision → Verification
- **Governance Event Log**: Append-oriented CSV record; not tamper-proof or protected from local edits
- **Safe Rollback**: Create backups and restore known-good states
- **17 new governance tests** covering all lifecycle transitions

## Architecture Status

- ✅ **Phase 1**: Evidence extraction and validation
- ✅ **Phase 2**: Evidence dataset processing
- ✅ **Phase 3**: Deterministic rule engine with synthetic controls
- ✅ **Phase 4**: NVIDIA LLM integration with structured extraction
- ✅ **Phase 5**: Bounded workflow with enhanced text integrity validation
- ⚠️ **Phase 6**: Rule lifecycle prototype; trusted identity and tamper-resistant audit are not implemented
- ⚠️ **Phase 8**: Bounded impact-report prototype using synthetic fixtures; no verified obligations or complete bank-policy ruleset is available

Historical baseline: 86 tests (69 original + 17 governance tests); use the validation commands below to verify the current tree.

## Offline Synthetic Demo

The existing Document Analyzer page provides clean and conflict-blocked synthetic scenarios and a bounded proposed-rule impact demonstration using the local demo API. Results are labelled **SYNTHETIC — NOT ACTUAL BANK POLICIES** and do not establish RBI compliance. The demo calls only fixed local GET endpoints; it does not approve, activate, or change regulatory records.

Start the local backend and frontend in separate terminals:

```powershell
# From the project root
python -m uvicorn backend.services.document_analyzer:app --reload --port 8000

# From frontend/
npm run dev
```

The synthetic panel uses the same local backend base URL as document analysis. The existing Vite `/api` proxy is not used by these fixed demo requests; no production reverse-proxy configuration is supplied.

## Proposed Rule Review (Prototype)

The React dashboard includes a **Proposed Rule Review** page. It extracts text and requirement-style passages from a user-uploaded PDF, allows the reviewer to edit a restricted rule condition and action, and shows related indexed RBI evidence as review leads. The current dataset contains 3 registered RBI documents and source PDFs under `data/raw/current/` and `data/raw/historical/`, 33 indexed evidence passages, 77 unreviewed obligation candidates, and 0 verified obligations. The bank-policy repository starts empty; no genuine transaction dataset is available. See [the Bank Policy Repository guide](docs/BANK_POLICY_REPOSITORY.md) for local CSV/JSON import and its verification limits.

Text overlap is not a legal duplicate or conflict determination. The indexed RBI corpus is not a complete executable ruleset. The 20 baseline rules and 400 test cases under `data/synthetic/` are used only by the separate fixed Document Analyzer demo; they are never used by the proposed-rule review. Synthetic comparison results do not represent actual bank policy, customer behavior, or legal compliance. Decisions are appended to `data/processed/proposed_rule_decisions.jsonl` only when a user submits them from a loopback connection; the record has no authenticated authority identity and never approves or activates a production rule.

The **Bank Policies** page provides a local-only CSV/JSON import preview and explicit commit workflow. Imports are stored separately in `data/bank_policies/policy_repository.json`; each imported policy and approval claim remains unverified until independently verified. Imported clauses can appear as screening-only text matches. No import alone makes a policy an active requirement or grants approval.

For the React app, run the backend and Vite in separate terminals from the project root:

```powershell
# Terminal 1
.\.venv\Scripts\python.exe -m uvicorn backend.services.document_analyzer:app --reload --port 8000

# Terminal 2
Set-Location frontend
npm run dev
```

Open the Vite URL printed in terminal 2. The previous standalone analyzer has been retained at `/analyzer.html` and is available inside the React navigation under **Document Analyzer**.
