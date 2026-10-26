"""
Real-time document analyzer API.
Accepts PDF/image uploads and finds relevant regulatory content.
"""

from __future__ import annotations

import csv
import json
import ipaddress
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from backend.services.proposed_rule_impact import (
    ImpactAnalysisError,
    assess_fixed_impact_scenario,
)
from backend.services.synthetic_demo import assess_demo_scenario
from backend.services.proposed_rule_analysis import (
    ProposedRuleError,
    compare_proposed_rule,
    extract_proposed_document,
    record_local_decision,
)
from backend.services.bank_policy_repository import (
    BankPolicyRepositoryError,
    commit_import as commit_bank_policy_import,
    list_policy_records,
    prepare_import as preview_bank_policy_import,
)
from backend.services.review_obligation_candidates import (
    ReviewError,
    append_extracted_candidates,
    list_candidate_records,
    review_candidate,
    show_candidate,
)
from backend.services.phase8_pipeline import (
    MAX_UPLOAD_BYTES as PHASE8_MAX_UPLOAD_BYTES,
    Phase8InputError,
    run_phase8_analysis,
)
from backend.services.phase9_pipeline import run_phase9_retrieval
from backend.services.governance import RuleGovernance
from backend.services.phase10_verification import VerificationError, verify_synthetic_case

import os
# Add project root to path
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in os.environ.get('PYTHONPATH', ''):
    os.environ['PYTHONPATH'] = str(project_root)

PROJECT = project_root
PROCESSED = PROJECT / "data" / "processed"
EVIDENCE_FILE = PROCESSED / "evidence_passages.csv"
REGISTRY_FILE = PROCESSED / "document_registry.csv"

app = FastAPI(title="TraceRule AI Document Analyzer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Try to import PyMuPDF for PDF extraction
try:
    import fitz  # PyMuPDF
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    print("WARNING: PyMuPDF not installed. PDF extraction will not work.")


class AnalysisResult(BaseModel):
    status: str
    file_name: str
    file_type: str
    pages_processed: int
    text_length: int
    matches: list[dict[str, Any]]
    total_matches: int
    processing_time: float


class ProposedRuleComparisonRequest(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=64)
    statement_id: str = Field(min_length=1, max_length=100)
    rule_name: str = Field(min_length=1, max_length=256)
    purpose: str = Field(default="", max_length=1000)
    condition_field: str = Field(min_length=1, max_length=100)
    condition_operator: str = Field(min_length=1, max_length=20)
    condition_value: str = Field(min_length=1, max_length=300)
    action_type: str = Field(min_length=1, max_length=100)
    action_message: str = Field(default="", max_length=500)
    action_max_amount: float | None = None
    limit_notes: str = Field(default="", max_length=1000)
    exception_notes: str = Field(default="", max_length=1000)
    affected_transaction_notes: str = Field(default="", max_length=1000)


class ProposedRuleDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(min_length=1, max_length=64)
    decision: str = Field(min_length=1, max_length=40)
    authority_notes: str = Field(default="", max_length=2000)


class BankPolicyImportCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=64)


class CandidateExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=100)
    evidence_ids: list[str] = Field(min_length=1, max_length=3)


class CandidateSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=64)
    candidate_ids: list[str] = Field(min_length=1, max_length=3)


class CandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(min_length=1, max_length=20)
    reviewer_id: str = Field(default="", max_length=200)
    reviewer_notes: str = Field(default="", max_length=2000)
    reviewed_requirement_text: str = Field(default="", max_length=2000)
    reviewed_actor: str = Field(default="", max_length=1000)
    reviewed_action: str = Field(default="", max_length=2000)
    reviewed_conditions: str = Field(default="", max_length=2000)
    reviewed_exceptions: str = Field(default="", max_length=2000)
    applicability_notes: str = Field(default="", max_length=2000)


class Phase10SyntheticVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str = Field(min_length=3, max_length=64, pattern=r"^SYN-RULE-[0-9]{3}$")
    case_id: str = Field(min_length=3, max_length=64, pattern=r"^SYN-CASE-[A-Z0-9-]+$")


_candidate_extraction_runs: dict[str, dict[str, Any]] = {}


def _require_local_connection(http_request: Request) -> None:
    peer = http_request.client.host if http_request.client else ""
    try:
        local_connection = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local_connection = False
    if not local_connection:
        raise HTTPException(status_code=403, detail="Bank policy repository access is restricted to a local connection in this unauthenticated prototype.")


def extract_text_from_pdf(file_bytes: bytes) -> dict[int, str]:
    """Extract text from PDF, returning dict of page_number -> text."""
    if not PDF_AVAILABLE:
        raise HTTPException(status_code=500, detail="PDF extraction not available. Install PyMuPDF: pip install pymupdf")
    
    pages: dict[int, str] = {}
    try:
        # Create a temporary file to save the PDF bytes
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        
        try:
            with fitz.open(tmp_path) as doc:
                for page_num, page in enumerate(doc, start=1):
                    text = page.get_text()
                    if text.strip():
                        pages[page_num] = text
        finally:
            # Clean up temp file
            os.unlink(tmp_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract PDF: {str(e)}")
    
    return pages


def extract_text_from_image(file_bytes: bytes, filename: str) -> str:
    """Extract text from image using OCR."""
    try:
        # Try pytesseract for OCR
        import pytesseract
        from PIL import Image
        import io
        
        image = Image.open(io.BytesIO(file_bytes))
        text = pytesseract.image_to_string(image)
        return text
    except ImportError:
        # If pytesseract not available, return placeholder
        return f"[Image file: {filename} - OCR not available. Install pytesseract]"
    except Exception as e:
        return f"[Could not process image: {str(e)}]"


def load_evidence_index() -> dict[str, dict[str, str]]:
    """Load evidence passages into a searchable index."""
    if not EVIDENCE_FILE.exists():
        return {}
    
    evidence: dict[str, dict[str, str]] = {}
    with EVIDENCE_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("evidence_id"):
                evidence[row["evidence_id"]] = row
    
    return evidence


def search_evidence(query: str, evidence_index: dict[str, dict[str, str]], max_results: int = 10) -> list[dict[str, Any]]:
    """Search evidence passages for matching content."""
    matches: list[dict[str, Any]] = []
    query_lower = query.lower()
    
    # Keywords to look for
    keywords = [
        "digital lending", "LSP", "loan service provider", "data privacy",
        "due diligence", "contractual agreement", "bank", "borrower",
        "financial institution", "credit", "lending", "interest rate",
        "recovery", "data protection", "security", "encryption"
    ]
    
    for evidence_id, row in evidence_index.items():
        passage_text = row.get("passage_text", "").lower()
        original_text = row.get("original_text", "").lower()
        
        # Calculate relevance score
        score = 0
        matched_keywords = []
        
        # Check for exact query match
        if query_lower in passage_text or query_lower in original_text:
            score += 100
        
        # Check for keyword matches
        for keyword in keywords:
            if keyword in query_lower:
                if keyword in passage_text or keyword in original_text:
                    score += 20
                    if keyword not in matched_keywords:
                        matched_keywords.append(keyword)
        
        # Bonus for longer matches (more context)
        if query_lower in passage_text:
            # Find the matching section
            idx = passage_text.find(query_lower)
            context_start = max(0, idx - 50)
            context_end = min(len(passage_text), idx + len(query_lower) + 50)
            context = passage_text[context_start:context_end]
            score += min(10, len(context) // 10)
        
        if score > 0:
            matches.append({
                "evidence_id": evidence_id,
                "document_id": row.get("document_id"),
                "paragraph_reference": row.get("paragraph_reference"),
                "page": row.get("printed_page_number") or row.get("page_number"),
                "passage_preview": row.get("passage_text", "")[:200] + "..." if len(row.get("passage_text", "")) > 200 else row.get("passage_text", ""),
                "score": score,
                "matched_keywords": matched_keywords[:5],
                "source_file": row.get("filename"),
            })
    
    # Sort by score descending
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:max_results]


@app.get("/")
async def root():
    return {
        "service": "TraceRule AI Document Analyzer",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "pdf_available": PDF_AVAILABLE,
        "evidence_count": len(load_evidence_index())
    }


@app.post("/phase8/analyze")
async def analyze_phase8_upload(
    http_request: Request,
    file: UploadFile = File(...),
    focus_query: str = Form("", max_length=500),
    use_nvidia: bool = Form(False),
):
    """Analyze an unregistered PDF in memory; no project data is written."""
    _require_local_connection(http_request)
    file_name = file.filename or "uploaded-document.pdf"
    if not file_name.casefold().endswith(".pdf"):
        await file.close()
        raise HTTPException(status_code=400, detail="Upload a PDF file.")
    file_bytes = await file.read(PHASE8_MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(file_bytes) > PHASE8_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The maximum PDF upload size is 15 MB.")
    try:
        return await run_in_threadpool(
            run_phase8_analysis,
            file_name,
            file_bytes,
            focus_query=focus_query,
            use_nvidia=use_nvidia,
        )
    except Phase8InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/phase9/retrieve")
async def retrieve_phase9_upload(
    http_request: Request,
    file: UploadFile = File(...),
    query: str = Form("", max_length=500),
    semantic_opt_in: bool = Form(False),
    rerank_opt_in: bool = Form(False),
):
    """Retrieve evidence from an unregistered PDF without persisting it."""
    _require_local_connection(http_request)
    file_name = file.filename or "uploaded-document.pdf"
    if not file_name.casefold().endswith(".pdf"):
        await file.close()
        raise HTTPException(status_code=400, detail="Upload a PDF file.")
    file_bytes = await file.read(PHASE8_MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(file_bytes) > PHASE8_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The maximum PDF upload size is 15 MB.")
    try:
        return await run_in_threadpool(
            run_phase9_retrieval,
            file_name,
            file_bytes,
            query,
            semantic_opt_in=semantic_opt_in,
            rerank_opt_in=rerank_opt_in,
        )
    except Phase8InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/analyze", response_model=AnalysisResult)
async def analyze_document(file: UploadFile = File(...)):
    """Analyze uploaded document and find regulatory content."""
    import time
    start_time = time.time()
    
    # Read file
    file_bytes = await file.read()
    file_name = file.filename or "unknown"
    
    # Determine file type
    file_type = "unknown"
    if file_name.lower().endswith(".pdf"):
        file_type = "pdf"
    elif file_name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff")):
        file_type = "image"
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload PDF or image.")
    
    # Extract text based on file type
    if file_type == "pdf":
        pages = extract_text_from_pdf(file_bytes)
        total_text = "\n\n".join(pages.values())
        pages_processed = len(pages)
    else:
        total_text = extract_text_from_image(file_bytes, file_name)
        # Assign to page 1 for images
        pages = {1: total_text}
        pages_processed = 1
    
    if not total_text.strip():
        raise HTTPException(status_code=400, detail="No text could be extracted from the document")
    
    # Load evidence index
    evidence_index = load_evidence_index()
    
    # If text is long, split into chunks and search
    # For PDF, search each page separately
    all_matches: list[dict[str, Any]] = []
    
    # Search the full text
    main_matches = search_evidence(total_text, evidence_index, max_results=15)
    for match in main_matches:
        match["matched_in"] = "full_document"
    all_matches.extend(main_matches)
    
    # Also search page by page for PDFs
    if file_type == "pdf":
        for page_num, page_text in pages.items():
            page_matches = search_evidence(page_text, evidence_index, max_results=5)
            for match in page_matches:
                match["matched_in"] = f"page_{page_num}"
                match["page_number"] = page_num
            all_matches.extend(page_matches)
    
    # Deduplicate and re-sort
    seen = set()
    unique_matches = []
    for match in all_matches:
        key = match["evidence_id"]
        if key not in seen:
            seen.add(key)
            unique_matches.append(match)
    
    # Re-sort by score
    unique_matches.sort(key=lambda x: x["score"], reverse=True)
    
    processing_time = time.time() - start_time
    
    return AnalysisResult(
        status="success",
        file_name=file_name,
        file_type=file_type,
        pages_processed=pages_processed,
        text_length=len(total_text),
        matches=unique_matches[:10],
        total_matches=len(unique_matches),
        processing_time=round(processing_time, 2)
    )


@app.get("/evidence")
async def get_evidence(document_id: str | None = None):
    """Get evidence passages with status joined from the authoritative registry."""
    evidence = load_evidence_index()
    
    if document_id:
        evidence = {k: v for k, v in evidence.items() if v.get("document_id") == document_id}

    document_statuses: dict[str, str] = {}
    if REGISTRY_FILE.exists():
        with REGISTRY_FILE.open("r", newline="", encoding="utf-8-sig") as stream:
            for document in csv.DictReader(stream):
                if document.get("document_id"):
                    document_statuses[document["document_id"]] = document.get("document_status", "")

    passages = [
        {**passage, "document_status": document_statuses.get(passage.get("document_id", ""), "")}
        for passage in evidence.values()
    ]
    
    return {
        "count": len(passages),
        "evidence": passages
    }


@app.get("/documents")
async def get_documents():
    """Get registered documents."""
    if not REGISTRY_FILE.exists():
        return {"documents": [], "count": 0}
    
    docs = []
    with REGISTRY_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("document_id"):
                docs.append(row)
    
    return {"documents": docs, "count": len(docs)}


@app.get("/candidates")
async def get_candidates(http_request: Request):
    _require_local_connection(http_request)
    try:
        records = list_candidate_records(PROCESSED)
    except ReviewError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"count": len(records), "candidates": records, "verified_obligation_count": _count_verified_obligations()}


def _count_verified_obligations() -> int:
    path = PROCESSED / "obligations.csv"
    if not path.is_file():
        return 0
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return sum(1 for row in csv.DictReader(stream) if row.get("verification_status") == "verified")


@app.post("/candidates/extract")
async def extract_candidates(request: CandidateExtractionRequest, http_request: Request):
    _require_local_connection(http_request)
    try:
        from backend.services.regulatory_analysis_workflow import run_phase5_analysis

        result = run_phase5_analysis(request.document_id, request.evidence_ids)
    except Exception as exc:
        # Do not return provider details, prompts, response bodies, or credentials.
        raise HTTPException(status_code=503, detail="The existing extraction workflow is unavailable or failed. Check local service configuration and try again.") from exc
    if result.get("workflow_status") in {"failed", "blocked_superseded"}:
        raise HTTPException(status_code=422, detail={
            "message": "The selected source could not enter extraction.",
            "workflow_status": result.get("workflow_status"),
            "validation_errors": result.get("validation_errors", []),
        })
    run_id = result.get("run_id")
    if run_id:
        _candidate_extraction_runs[run_id] = result
        while len(_candidate_extraction_runs) > 20:
            _candidate_extraction_runs.pop(next(iter(_candidate_extraction_runs)))
    return result


@app.post("/candidates/extract/submit")
async def submit_extracted_candidates(request: CandidateSubmissionRequest, http_request: Request):
    _require_local_connection(http_request)
    result = _candidate_extraction_runs.get(request.run_id)
    if result is None:
        raise HTTPException(status_code=410, detail="This extraction result expired. Run extraction again before submitting candidates.")
    candidates_by_id = {item.get("candidate_id"): item for item in result.get("candidates", [])}
    if len(set(request.candidate_ids)) != len(request.candidate_ids) or any(item not in candidates_by_id for item in request.candidate_ids):
        raise HTTPException(status_code=422, detail="Choose candidates returned by this extraction run.")
    try:
        selected = [candidates_by_id[item] for item in request.candidate_ids]
        stored = append_extracted_candidates(selected, PROCESSED)
    except ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _candidate_extraction_runs.pop(request.run_id, None)
    return {"submitted_candidate_ids": stored, "message": "Grounded candidates were added to the existing human review queue. They remain unverified and inactive."}


@app.post("/candidates/{candidate_id}/review")
async def review_candidate_route(candidate_id: str, request: CandidateReviewRequest, http_request: Request):
    _require_local_connection(http_request)
    try:
        result = review_candidate(
            candidate_id,
            request.decision,
            PROCESSED,
            reviewer_id=request.reviewer_id,
            reviewer_notes=request.reviewer_notes,
            reviewed_requirement_text=request.reviewed_requirement_text,
            reviewed_actor=request.reviewed_actor,
            reviewed_action=request.reviewed_action,
            reviewed_conditions=request.reviewed_conditions,
            reviewed_exceptions=request.reviewed_exceptions,
            applicability_notes=request.applicability_notes,
        )
    except ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"candidate_id": candidate_id, "review_decision": result["review_decision"], "message": "Review decision recorded locally. The reviewer identity is not authenticated."}


@app.get("/governance/overview")
async def get_governance_overview(http_request: Request):
    _require_local_connection(http_request)
    governance = RuleGovernance(PROCESSED)
    storage_available = governance.rules_path.is_file()
    try:
        if storage_available:
            with governance.rules_path.open("r", encoding="utf-8") as stream:
                stored = json.load(stream)
            rules = stored if isinstance(stored, list) else stored.get("rules") if isinstance(stored, dict) else None
            if not isinstance(rules, list):
                raise ValueError("invalid rules store shape")
        else:
            rules = []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="The local governance rules store could not be read safely.") from exc
    events = governance.get_audit_trail(limit=500)
    return {"rules": rules, "rule_count": len(rules), "events": events, "event_count": len(events), "storage_available": storage_available}


@app.get("/audit-trail")
async def get_audit_trail(http_request: Request):
    _require_local_connection(http_request)
    governance = RuleGovernance(PROCESSED)
    events = governance.get_audit_trail(limit=500)
    candidate_path = PROCESSED / "candidate_review_audit.csv"
    candidate_events = []
    if candidate_path.is_file():
        with candidate_path.open("r", newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                candidate_events.append({
                    "event_id": row.get("event_id", ""),
                    "timestamp": row.get("timestamp", ""),
                    "rule_id": row.get("candidate_id", ""),
                    "event_type": "candidate_review",
                    "user_id": row.get("reviewer_id", ""),
                    "details": {"decision": row.get("decision", ""), "reason": row.get("reason", ""), "evidence_id": row.get("evidence_id", ""), "document_id": row.get("document_id", ""), "identity_authenticated": False},
                })
    combined = sorted([*events, *candidate_events], key=lambda item: item.get("timestamp", ""), reverse=True)
    return {"events": combined[:500], "count": len(combined), "tamper_proof": False, "authenticated_identity": False}


@app.post("/proposed-rules/extract")
async def extract_proposed_rule_document(file: UploadFile = File(...)):
    """Extract source-grounded clause suggestions from a proposed-rule PDF."""
    file_bytes = await file.read(15 * 1024 * 1024 + 1)
    try:
        return extract_proposed_document(file.filename or "proposed-rule.pdf", file_bytes, extract_text_from_pdf)
    except ProposedRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/proposed-rules/compare")
async def compare_uploaded_proposed_rule(request: ProposedRuleComparisonRequest):
    """Screen an edited proposal against indexed RBI text and imported policy records."""
    try:
        return compare_proposed_rule(**request.model_dump())
    except ProposedRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/proposed-rules/decisions")
async def save_proposed_rule_decision(request: ProposedRuleDecisionRequest, http_request: Request):
    """Save a local decision record without claiming authenticated approval."""
    peer = http_request.client.host if http_request.client else ""
    try:
        local_connection = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local_connection = False
    if not local_connection:
        raise HTTPException(status_code=403, detail="Decision records are restricted to a local connection; authority identity still cannot be authenticated.")
    try:
        return record_local_decision(
            analysis_id=request.analysis_id,
            decision=request.decision,
            notes=request.authority_notes,
        )
    except ProposedRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/bank-policies")
async def get_bank_policy_repository(http_request: Request):
    _require_local_connection(http_request)
    try:
        return list_policy_records()
    except BankPolicyRepositoryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/bank-policies/import/preview")
async def preview_bank_policy_import_route(http_request: Request, file: UploadFile = File(...)):
    _require_local_connection(http_request)
    payload = await file.read(10 * 1024 * 1024 + 1)
    try:
        return preview_bank_policy_import(file.filename or "policy-import", payload)
    except BankPolicyRepositoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/bank-policies/import/commit")
async def commit_bank_policy_import_route(request: BankPolicyImportCommitRequest, http_request: Request):
    _require_local_connection(http_request)
    try:
        return commit_bank_policy_import(request.preview_id)
    except BankPolicyRepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _synthetic_demo_response(scenario_id: str) -> dict[str, Any]:
    result = assess_demo_scenario(scenario_id)
    simulation = result["simulation"]
    return {
        "label": result["label"],
        "scenario_id": result["scenario_id"],
        "validation_result": {
            "passed": not result["validation_errors"],
            "errors": result["validation_errors"],
        },
        "conflict_findings": result["conflicts"],
        "simulation_summary": {
            "simulation_type": simulation["simulation_type"],
            "case_count": simulation["case_count"],
            "changed_outcomes": simulation["changed_outcomes"],
            "dataset_version": simulation["test_case_dataset_version"],
            "seed": simulation["random_seed"],
        },
        "approval_gate_outcome": result["approval_gate"],
        "approval_status": result["approval_status"],
        "rule_status": result["rule_status"],
        "activation_status": result["activation_status"],
    }


@app.get("/demo/synthetic/clean")
async def synthetic_demo_clean():
    """Return the fixed clean synthetic approval-gate scenario."""
    return _synthetic_demo_response("DEMO-SCENARIO-CLEAN")


@app.post("/phase10/synthetic/verify")
async def phase10_synthetic_verify(request: Phase10SyntheticVerificationRequest, http_request: Request):
    """Run a fixed, non-persistent synthetic test case through OPA, Z3, and Python."""
    _require_local_connection(http_request)
    try:
        return verify_synthetic_case(request.rule_id, request.case_id)
    except VerificationError as exc:
        message = str(exc)
        status = 503 if "OPA is unavailable" in message else 404 if "Unknown synthetic" in message else 422
        raise HTTPException(status_code=status, detail=message) from exc


@app.get("/demo/synthetic/conflict")
async def synthetic_demo_conflict():
    """Return the fixed conflict-blocked synthetic scenario."""
    return _synthetic_demo_response("DEMO-SCENARIO-CONFLICT")


@app.get("/demo/impact/{scenario_id}")
async def proposed_rule_impact_demo(scenario_id: str):
    """Return an impact report for one checked-in synthetic scenario."""
    try:
        return assess_fixed_impact_scenario(scenario_id)
    except ImpactAnalysisError as exc:
        if str(exc).startswith("Unknown synthetic impact scenario:"):
            raise HTTPException(status_code=404, detail="Unknown synthetic impact scenario") from exc
        raise HTTPException(
            status_code=500,
            detail="Synthetic impact scenario could not be assessed safely",
        ) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
