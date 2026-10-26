"""Bounded LangGraph workflow for grounded, provisional regulatory candidates."""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.services.nvidia_llm import LLMClient
from backend.services.structured_extraction import (
    ExtractionError,
    _find_source_quote,
    extract_candidate_obligation,
)
from backend.services.validate_evidence_dataset import PROCESSED, validate as validate_evidence_dataset

PROJECT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = "document_registry.csv"
EVIDENCE_FILE = "evidence_passages.csv"
OBLIGATIONS_FILE = "obligations.csv"
SUPERSEDED_DOCUMENTS = frozenset({"DOC-DL-2022"})
MAX_EVIDENCE_PER_RUN = 3
WORKFLOW_VERSION = "1.0.0"
DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 60
MAX_EXTRACTION_TIMEOUT_SECONDS = 120
DEFAULT_EXTRACTION_MAX_TOKENS = 1024


class AnalysisState(TypedDict, total=False):
    run_id: str
    document_id: str
    evidence_ids: list[str]
    data_dir: Path
    status: str
    source_status: str | None
    evidence: list[dict[str, str]]
    extraction_results: list[dict[str, Any]]
    validated_extractions: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    validation_errors: list[dict[str, str]]
    completed_nodes: list[str]
    result: dict[str, Any]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _error(code: str, message: str, *, evidence_id: str = "") -> dict[str, str]:
    item = {"code": code, "message": message}
    if evidence_id:
        item["evidence_id"] = evidence_id
    return item


def _append_node(state: AnalysisState, name: str) -> list[str]:
    return [*state.get("completed_nodes", []), name]


def _load_context(state: AnalysisState) -> dict[str, Any]:
    document_id = state.get("document_id", "")
    evidence_ids = state.get("evidence_ids", [])
    data_dir = state.get("data_dir", PROCESSED)
    errors: list[dict[str, str]] = []
    if Path(data_dir).resolve() != PROCESSED.resolve():
        errors.append(_error("unsupported_data_directory", "Workflow is restricted to the project's validated Phase 2 dataset"))
    if not isinstance(document_id, str) or not document_id:
        errors.append(_error("invalid_document_id", "A registered document ID is required"))
    if not isinstance(evidence_ids, list) or not evidence_ids:
        errors.append(_error("invalid_evidence_ids", "At least one evidence ID is required"))
    elif len(evidence_ids) > MAX_EVIDENCE_PER_RUN:
        errors.append(_error("evidence_limit_exceeded", f"A run may include at most {MAX_EVIDENCE_PER_RUN} evidence passages"))
    elif any(not isinstance(item, str) or not item for item in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids):
        errors.append(_error("invalid_evidence_ids", "Evidence IDs must be non-empty unique strings"))
    if errors:
        return {"status": "failed", "validation_errors": errors, "completed_nodes": _append_node(state, "context_loader")}

    if document_id in SUPERSEDED_DOCUMENTS:
        return {
            "status": "blocked_superseded",
            "validation_errors": [_error("superseded_document", "Superseded DOC-DL-2022 cannot generate obligation candidates")],
            "completed_nodes": _append_node(state, "context_loader"),
        }

    dataset_errors, _warnings, _counts = validate_evidence_dataset(data_dir)
    if dataset_errors:
        return {
            "status": "failed",
            "validation_errors": [_error("invalid_evidence_dataset", "Phase 2 evidence dataset failed validation")],
            "completed_nodes": _append_node(state, "context_loader"),
        }

    registry = {row.get("document_id"): row for row in _read_csv(data_dir / REGISTRY_FILE)}
    registry_row = registry.get(document_id)
    if not registry_row:
        return {
            "status": "failed",
            "validation_errors": [_error("unregistered_document", "Document ID is not in the local document registry")],
            "completed_nodes": _append_node(state, "context_loader"),
        }
    source_filename = registry_row.get("filename", "")
    if not source_filename or Path(source_filename).name != source_filename:
        return {
            "status": "failed",
            "validation_errors": [_error("invalid_registry_source", "Registry source filename is invalid")],
            "completed_nodes": _append_node(state, "context_loader"),
        }
    source_exists = any(
        (PROJECT / "data" / "raw" / area / source_filename).is_file()
        for area in ("current", "historical")
    )
    if not source_exists:
        return {
            "status": "failed",
            "validation_errors": [_error("local_source_missing", "Registered source document is not available locally")],
            "completed_nodes": _append_node(state, "context_loader"),
        }
    all_evidence = {row.get("evidence_id"): row for row in _read_csv(data_dir / EVIDENCE_FILE)}
    selected: list[dict[str, str]] = []
    for evidence_id in evidence_ids:
        row = all_evidence.get(evidence_id)
        if not row:
            errors.append(_error("unsupported_evidence", "Evidence ID is not in the validated local evidence dataset", evidence_id=evidence_id))
        elif row.get("document_id") != document_id:
            errors.append(_error("evidence_document_mismatch", "Evidence does not belong to the requested document", evidence_id=evidence_id))
        else:
            selected.append(row)
    return {
        "status": "failed" if errors else "ready",
        "source_status": registry_row.get("document_status", ""),
        "evidence": selected,
        "validation_errors": errors,
        "completed_nodes": _append_node(state, "context_loader"),
    }


def _extract_evidence(state: AnalysisState, client: LLMClient) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for evidence in state.get("evidence", []):
        evidence_id = evidence["evidence_id"]
        try:
            settings = client.settings
            timeout_seconds = min(
                MAX_EXTRACTION_TIMEOUT_SECONDS,
                getattr(settings, "extraction_timeout_seconds", DEFAULT_EXTRACTION_TIMEOUT_SECONDS),
            )
            max_tokens = getattr(settings, "extraction_max_tokens", DEFAULT_EXTRACTION_MAX_TOKENS)
            extracted = extract_candidate_obligation(
                evidence_id,
                client=client,
                data_dir=state.get("data_dir", PROCESSED),
                timeout_seconds=timeout_seconds,
                max_tokens=max_tokens,
                retry_count=0,
                reasoning_effort="low",
            )
            results.append({"evidence_id": evidence_id, "output": extracted})
        except ExtractionError as exc:
            results.append({"evidence_id": evidence_id, "error": _error(exc.code, exc.message, evidence_id=evidence_id)})
        except Exception:
            # Keep errors safe: never include prompts, response bodies, or credentials.
            results.append({
                "evidence_id": evidence_id,
                "error": _error("extraction_failed", "Candidate extraction failed", evidence_id=evidence_id),
            })
    return {"extraction_results": results, "completed_nodes": _append_node(state, "evidence_extraction_agent")}


def _validate_extractions(state: AnalysisState) -> dict[str, Any]:
    evidence_by_id = {row["evidence_id"]: row for row in state.get("evidence", [])}
    valid: list[dict[str, Any]] = []
    errors = list(state.get("validation_errors", []))
    for item in state.get("extraction_results", []):
        evidence_id = item["evidence_id"]
        if "error" in item:
            errors.append(item["error"])
            continue
        output = item.get("output", {})
        source = evidence_by_id.get(evidence_id)
        if not source:
            errors.append(_error("unsupported_evidence", "Extraction has no loaded source evidence", evidence_id=evidence_id))
            continue
        try:
            source_page = int(source.get("printed_page_number") or source.get("page_number") or "0")
        except ValueError:
            source_page = 0
        source_text = source.get("passage_text", "")
        # Independent verification — NOT a tautology.
        #
        # Previously this checked ``output["supporting_quotation"] in source_text``
        # but by this point supporting_quotation has already been replaced with a
        # slice of source_text, so that check was always true.  Instead we now:
        #   1. Verify the MODEL's original quote maps to the source passage.
        #   2. Verify the recorded offsets reconstruct the exact source slice.
        #   3. For explicit_requirement, verify the obligation offsets too.
        checks_list: list[tuple[bool, str]] = [
            (output.get("document_id") == source.get("document_id"), "document reference mismatch"),
            (output.get("evidence_id") == evidence_id, "evidence reference mismatch"),
            (output.get("page") == source_page and source_page > 0, "page reference mismatch"),
            (output.get("requires_human_review") is True, "human review is required"),
        ]
        # Check 1: model's original quote independently grounds in source.
        model_quote = output.get("model_supporting_quotation")
        if not isinstance(model_quote, str) or not model_quote.strip():
            checks_list.append((False, "model supporting quotation is missing"))
        elif _find_source_quote(source_text, model_quote) is None:
            checks_list.append((False, "model supporting quotation is not grounded in source evidence"))
        # Check 2: offsets reconstruct the exact source slice.
        qs = output.get("source_quote_start")
        qe = output.get("source_quote_end")
        grounded_quote = output.get("supporting_quotation", "")
        if not isinstance(qs, int) or not isinstance(qe, int):
            checks_list.append((False, "source quote offsets are missing or invalid"))
        elif not isinstance(grounded_quote, str) or not grounded_quote:
            checks_list.append((False, "supporting quotation is empty"))
        elif source_text[qs:qe] != grounded_quote:
            checks_list.append((False, "source quote offsets do not reconstruct the supporting quotation"))
        # Check 3: for explicit_requirement, obligation must be grounded too.
        if output.get("candidate_type") == "explicit_requirement":
            cs = output.get("candidate_source_start")
            ce = output.get("candidate_source_end")
            grounded_obligation = output.get("candidate_obligation", "")
            if not isinstance(cs, int) or not isinstance(ce, int):
                checks_list.append((False, "candidate obligation offsets are missing or invalid"))
            elif not isinstance(grounded_obligation, str) or not grounded_obligation:
                checks_list.append((False, "candidate obligation is empty"))
            elif source_text[cs:ce] != grounded_obligation:
                checks_list.append((False, "candidate obligation offsets do not reconstruct the proposed obligation"))
        # Check 4: interpretation candidates must be explicitly flagged ungrounded.
        if output.get("candidate_type") == "interpretation":
            if output.get("candidate_obligation_grounded") is not False:
                checks_list.append((False, "interpretation candidate must be flagged as ungrounded"))
        failed = [message for okay, message in checks_list if not okay]
        if failed:
            errors.append(_error("validation_failed", "; ".join(failed), evidence_id=evidence_id))
            continue
        valid.append({"output": output, "source": source})
    return {
        "validated_extractions": valid,
        "validation_errors": errors,
        "completed_nodes": _append_node(state, "validation_agent"),
    }


def _build_candidates(state: AnalysisState) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(state.get("validated_extractions", []), start=1):
        output = item["output"]
        source = item["source"]
        if output["candidate_type"] == "no_candidate":
            continue
        candidates.append({
            "candidate_id": f"WF-{state['run_id'][:8]}-{index:02d}",
            "document_id": source["document_id"],
            "evidence_id": source["evidence_id"],
            "source_page": source.get("printed_page_number") or source.get("page_number"),
            "paragraph_reference": source.get("paragraph_reference", ""),
            "supporting_quotation": output["supporting_quotation"],
            "model_supporting_quotation": output.get("model_supporting_quotation", output["supporting_quotation"]),
            "source_quote_start": output.get("source_quote_start"),
            "source_quote_end": output.get("source_quote_end"),
            "candidate_source_start": output.get("candidate_source_start"),
            "candidate_source_end": output.get("candidate_source_end"),
            "source_wording": output["supporting_quotation"],
            "proposed_obligation": output["candidate_obligation"],
            "model_proposed_obligation": output.get("model_candidate_obligation", output["candidate_obligation"]),
            "candidate_type": output["candidate_type"],
            "candidate_obligation_grounded": output.get("candidate_obligation_grounded", False),
            "uncertainty": output["uncertainty"],
            "extraction_confidence": output["confidence"],
            "review_status": "pending_human_review",
            "verification_status": "unverified_candidate",
            "activation_status": "inactive",
        })
    return {"candidates": candidates, "completed_nodes": _append_node(state, "obligation_candidate_agent")}


def _review_gate(state: AnalysisState) -> dict[str, Any]:
    # A graph run cannot approve, persist, publish, or activate candidate records.
    gated = []
    for candidate in state.get("candidates", []):
        gated.append({
            **candidate,
            "review_status": "pending_human_review",
            "verification_status": "unverified_candidate",
            "activation_status": "inactive",
        })
    return {"candidates": gated, "completed_nodes": _append_node(state, "review_gate")}


def _audit_result(state: AnalysisState, client: LLMClient) -> dict[str, Any]:
    errors = state.get("validation_errors", [])
    status = state.get("status", "failed")
    if status == "ready":
        status = "pending_human_review" if state.get("candidates") else (
            "failed" if errors and not state.get("validated_extractions") else "complete_no_candidates"
        )
    try:
        settings = client.settings
        model = settings.model
        timeout_seconds = min(
            MAX_EXTRACTION_TIMEOUT_SECONDS,
            getattr(settings, "extraction_timeout_seconds", DEFAULT_EXTRACTION_TIMEOUT_SECONDS),
        )
        config = {
            "timeout_seconds": timeout_seconds,
            "max_tokens": getattr(settings, "extraction_max_tokens", DEFAULT_EXTRACTION_MAX_TOKENS),
            "retry_count": 0,
            "json_mode": True,
            "reasoning_effort": "low",
        }
    except Exception:
        model = None
        config = {
            "timeout_seconds": DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
            "max_tokens": DEFAULT_EXTRACTION_MAX_TOKENS,
            "retry_count": 0,
            "json_mode": True,
        }
    document_status = state.get("source_status")
    audit = {
        "run_id": state.get("run_id"),
        "workflow_version": WORKFLOW_VERSION,
        "workflow_status": status,
        "document_id": state.get("document_id"),
        "document_status": document_status,
        "model": model,
        "model_parameters": config,
        "requested_evidence_count": len(state.get("evidence_ids", [])),
        "loaded_evidence_count": len(state.get("evidence") or []),
        "candidate_count": len(state.get("candidates", [])),
        "validation_errors": errors,
        "review_status": "pending_human_review" if state.get("candidates") else "not_applicable",
        "activation_status": "inactive",
        "completed_nodes": _append_node(state, "audit_result"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    result = {
        "workflow_status": status,
        "run_id": state.get("run_id"),
        "document_id": state.get("document_id"),
        "document_status": document_status,
        "candidates": state.get("candidates", []),
        "validation_errors": errors,
        "audit": audit,
    }
    return {"status": status, "result": result, "completed_nodes": audit["completed_nodes"]}


def build_phase5_graph(*, client: LLMClient | None = None, data_dir: Path = PROCESSED):
    """Build a finite six-node StateGraph. Model client is injectable for tests."""
    llm_client = client or LLMClient()
    graph = StateGraph(AnalysisState)
    graph.add_node("context_loader", _load_context)
    graph.add_node("evidence_extraction_agent", lambda state: _extract_evidence(state, llm_client))
    graph.add_node("validation_agent", _validate_extractions)
    graph.add_node("obligation_candidate_agent", _build_candidates)
    graph.add_node("review_gate", _review_gate)
    graph.add_node("audit_result", lambda state: _audit_result(state, llm_client))
    graph.add_edge(START, "context_loader")
    graph.add_conditional_edges(
        "context_loader",
        lambda state: "extract" if state.get("status") == "ready" else "audit",
        {"extract": "evidence_extraction_agent", "audit": "audit_result"},
    )
    graph.add_edge("evidence_extraction_agent", "validation_agent")
    graph.add_edge("validation_agent", "obligation_candidate_agent")
    graph.add_edge("obligation_candidate_agent", "review_gate")
    graph.add_edge("review_gate", "audit_result")
    graph.add_edge("audit_result", END)
    return graph.compile()


def run_phase5_analysis(
    document_id: str,
    evidence_ids: list[str],
    *,
    client: LLMClient | None = None,
    data_dir: Path = PROCESSED,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    graph = build_phase5_graph(client=client, data_dir=data_dir)
    state = graph.invoke({
        "run_id": run_id,
        "document_id": document_id,
        "evidence_ids": evidence_ids,
        "data_dir": data_dir,
        "completed_nodes": [],
        "validation_errors": [],
        "candidates": [],
    }, config={"recursion_limit": 10})
    return state["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--evidence-id", required=True, action="append", dest="evidence_ids")
    args = parser.parse_args()
    try:
        result = run_phase5_analysis(args.document_id, args.evidence_ids)
    except Exception:
        print(json.dumps({"workflow_status": "failed", "error": {"code": "workflow_failed", "message": "Analysis workflow failed"}}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["workflow_status"] not in {"failed", "blocked_superseded"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
