"""Local, evidence-limited screening of user-proposed rules.

This module never calls an AI provider and never writes or activates a rule.
RBI passage similarity is only a review aid. This project has no verified
executable obligations, bank rules, or genuine transaction dataset to compare.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.services.rule_validator import ALLOWED_ACTION_TYPES, ALLOWED_FIELDS, COMPARISON_OPS, validate_rule
from backend.services.bank_policy_repository import policy_clause_matches
from backend.services.rule_comparison import RuleReference, compare_rule_pair

PROJECT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT / "data" / "processed"
REGISTRY_FILE = PROCESSED / "document_registry.csv"
EVIDENCE_FILE = PROCESSED / "evidence_passages.csv"
OBLIGATIONS_FILE = PROCESSED / "obligations.csv"
DECISIONS_FILE = PROCESSED / "proposed_rule_decisions.jsonl"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PAGES = 400
MAX_TEXT_CHARS = 2_000_000
MAX_STATEMENTS = 40
DECISIONS = frozenset({"approve_for_next_stage", "request_changes", "reject"})
NUMERIC_FIELDS = frozenset({"loan_amount", "loan_tenor_months", "apr_percent", "lsp_count", "dlg_cap_percent", "borrower_age"})
BOOLEAN_FIELDS = frozenset({"has_lsp", "credit_limit_increase_requested", "income_verified"})
_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSION_LOCK = threading.RLock()
_DECISION_LOCK = threading.Lock()


class ProposedRuleError(ValueError):
    """A safe, user-displayable proposed-rule validation error."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def _words(value: str) -> set[str]:
    ignored = {"the", "and", "for", "from", "that", "this", "with", "shall", "must", "may", "will", "their", "under", "where", "such", "each", "into", "between", "have", "has", "are", "was", "were", "not", "any", "all", "bank", "banks"}
    return {word for word in re.findall(r"[a-z0-9]+", value.casefold()) if len(word) > 2 and word not in ignored}


def _extract_statements(pages: dict[int, str]) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    obligation_language = re.compile(r"\b(shall|must|may not|must not|required to|is required|are required|prohibited|not permitted|should)\b", re.IGNORECASE)
    section_pattern = re.compile(r"\b(?:section|paragraph|clause|para\.?|§)\s+([0-9]+(?:\.[0-9]+)*(?:\([0-9A-Za-z]+\))*)", re.IGNORECASE)
    numbered_section_pattern = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)*(?:\([0-9A-Za-z]+\))*)\s*[.):]", re.MULTILINE)
    action_patterns = (
        (re.compile(r"\b(prohibit(?:ed)?|may not|must not|shall not|deny|reject)\b", re.IGNORECASE), "deny"),
        (re.compile(r"\b(allow(?:ed)?|permit(?:ted)?|may)\b", re.IGNORECASE), "allow"),
        (re.compile(r"\b(review|approval|verify|verification)\b", re.IGNORECASE), "require_review"),
    )
    for page_number, page_text in pages.items():
        for block in re.split(r"\n\s*\n+", page_text):
            quote = block.strip()
            if len(quote) < 30 or not obligation_language.search(quote):
                continue
            section = section_pattern.search(quote)
            if not section:
                section = numbered_section_pattern.search(quote)
            action = next((label for pattern, label in action_patterns if pattern.search(quote)), None)
            limit_clues = re.findall(
                r"[^.\n]{0,100}(?:limit|cap|maximum|minimum|at most|not exceed|up to|₹|INR|percent|%)[^.\n]{0,100}",
                quote,
                flags=re.IGNORECASE,
            )
            exception_clues = re.findall(
                r"[^.\n]{0,100}(?:except|unless|notwithstanding|provided that|exempt(?:ion)?)[^.\n]{0,100}",
                quote,
                flags=re.IGNORECASE,
            )
            purpose_clue = quote if re.search(r"\b(purpose|objective|aim|intended to|designed to)\b", quote, re.IGNORECASE) else None
            condition_clue = quote if re.search(r"\b(if|when|where|unless|subject to|provided that|condition)\b", quote, re.IGNORECASE) else None
            mentioned_fields = [
                field for field, terms in {
                    "loan_amount": ("loan amount", "amount", "rupee", "inr"),
                    "loan_tenor_months": ("tenor", "tenure", "months"),
                    "apr_percent": ("annual percentage", "apr", "interest rate"),
                    "channel": ("digital channel", "branch", "channel"),
                    "has_lsp": ("lsp", "loan service provider"),
                    "disbursement_route": ("disbursement", "directly to the borrower"),
                    "repayment_route": ("repayment", "repay"),
                    "data_storage_region": ("data storage", "data stored", "server location"),
                    "borrower_age": ("borrower age", "age of the borrower"),
                    "income_verified": ("income verification", "income verified"),
                }.items() if any(term in quote.casefold() for term in terms)
            ]
            statements.append({
                "statement_id": f"p{page_number}-s{len(statements) + 1}",
                "page": page_number,
                "section_reference": section.group(1) if section else None,
                "quote": quote[:6000],
                "text_signals": {
                    "possible_action": action,
                    "possible_transaction_fields": mentioned_fields,
                    "possible_condition_text": condition_clue,
                    "possible_limit_text": limit_clues[:5],
                    "possible_exception_text": exception_clues[:5],
                    "possible_purpose_text": purpose_clue,
                },
            })
            if len(statements) >= MAX_STATEMENTS:
                return statements
    return statements


def _store_session(session_id: str, value: dict[str, Any]) -> None:
    with _SESSION_LOCK:
        _SESSIONS[session_id] = value
        while len(_SESSIONS) > 100:
            _SESSIONS.pop(next(iter(_SESSIONS)))


def extract_proposed_document(
    file_name: str,
    file_bytes: bytes,
    extract_pdf: Callable[[bytes], dict[int, str]],
) -> dict[str, Any]:
    if not file_name.casefold().endswith(".pdf"):
        raise ProposedRuleError("Upload a PDF file to analyze a proposed rule.")
    if not file_bytes:
        raise ProposedRuleError("The selected PDF is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ProposedRuleError("The PDF is too large. The maximum upload size is 15 MB.")
    try:
        pages = extract_pdf(file_bytes)
    except Exception as exc:
        raise ProposedRuleError("The PDF could not be read. It may be damaged, encrypted, or unsupported.") from exc
    if not pages or not any(text.strip() for text in pages.values()):
        raise ProposedRuleError("No readable text was found. This PDF may be scanned or encrypted.")
    if len(pages) > MAX_PAGES:
        raise ProposedRuleError("The PDF has more than 400 pages; split it into smaller files and try again.")
    total_chars = sum(len(text) for text in pages.values())
    if total_chars > MAX_TEXT_CHARS:
        raise ProposedRuleError("The extracted PDF text is too large to review in one analysis.")
    statements = _extract_statements(pages)
    analysis_id = str(uuid.uuid4())
    proposal_id = f"PR-{hashlib.sha256(file_bytes).hexdigest()[:12].upper()}"
    safe_name = Path(file_name.replace("\\", "/")).name[:200] or "proposed-rule.pdf"
    result = {
        "analysis_id": analysis_id,
        "proposal_id": proposal_id,
        "file_name": safe_name,
        "file_sha256": hashlib.sha256(file_bytes).hexdigest(),
        "pages_processed": len(pages),
        "text_length": total_chars,
        "statements": statements,
        "extraction_warning": None if statements else "Readable text was found, but no requirement-style clauses were identified. Review the PDF manually; no rule details were inferred.",
    }
    # Keep only bounded source excerpts in memory; never retain the uploaded PDF/pages.
    _store_session(analysis_id, {**result, "comparison": None})
    return result


def _parse_condition_value(field: str, operator: str, raw_value: str) -> Any:
    if field not in ALLOWED_FIELDS:
        raise ProposedRuleError("Choose a supported transaction field.")
    if operator not in COMPARISON_OPS:
        raise ProposedRuleError("Choose a supported comparison.")
    if not raw_value.strip():
        raise ProposedRuleError("Enter the value for the proposed condition.")
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    if operator in {"in", "not_in"}:
        if not values:
            raise ProposedRuleError("Enter at least one value.")
        return [_convert_field_value(field, item) for item in values]
    return _convert_field_value(field, raw_value.strip())


def _convert_field_value(field: str, value: str) -> Any:
    if field in NUMERIC_FIELDS:
        try:
            number = float(value)
        except ValueError as exc:
            raise ProposedRuleError("Enter a number for this transaction field.") from exc
        if not math.isfinite(number):
            raise ProposedRuleError("Enter a finite number for this transaction field.")
        if not number.is_integer() and field != "apr_percent":
            raise ProposedRuleError("Use a whole number for this transaction field.")
        return number if field == "apr_percent" or not number.is_integer() else int(number)
    if field in BOOLEAN_FIELDS:
        if value.casefold() not in {"true", "false"}:
            raise ProposedRuleError("Use true or false for this transaction field.")
        return value.casefold() == "true"
    return value


def _match_rbi_passages(query: str, *, exact_text: str = "") -> list[dict[str, Any]]:
    registry = {row.get("document_id", ""): row for row in _read_csv(REGISTRY_FILE)}
    query_words = _words(query)
    if not query_words:
        return []
    matches = []
    for passage in _read_csv(EVIDENCE_FILE):
        document_id = passage.get("document_id", "")
        document = registry.get(document_id)
        if not document or "reserve bank of india" not in document.get("issuer", "").casefold():
            continue
        quote = passage.get("passage_text") or passage.get("original_text") or ""
        passage_words = _words(quote)
        if not passage_words:
            continue
        shared = query_words.intersection(passage_words)
        overlap = len(shared) / max(1, min(len(query_words), len(passage_words)))
        exact = bool(exact_text) and _normalized_text(exact_text) == _normalized_text(quote)
        if not exact and overlap < 0.35:
            continue
        matches.append({
            "source_category": "RBI",
            "document_id": document_id,
            "document_name": document.get("document_title") or document.get("filename"),
            "document_status": document.get("document_status", "status unavailable"),
            "source_url": document.get("source_url") or None,
            "evidence_id": passage.get("evidence_id"),
            "section": passage.get("section_heading") or passage.get("section_hint") or None,
            "paragraph": passage.get("paragraph_reference") or None,
            "page": passage.get("printed_page_number") or passage.get("page_number") or passage.get("pdf_page_index") or None,
            "quote": quote,
            "classification": "possible_duplicate_wording" if exact else "related_source_passage",
            "match_strength": round(overlap, 3),
            "why_related": "The wording matches an indexed passage after whitespace normalization. This is a wording match only; it does not establish legal identity, applicability, or conflict." if exact else "Some words overlap with this indexed RBI passage; text overlap alone cannot establish duplication or conflict.",
            "uncertainty": "Verify the original source, current status, applicability, and full context with a qualified reviewer.",
        })
    matches.sort(key=lambda item: (item["classification"] != "possible_duplicate_wording", -item["match_strength"]))
    return matches[:8]


def _compare_source_findings(
    proposal: dict[str, Any],
    statement: dict[str, Any],
    rbi_matches: list[dict[str, Any]],
    bank_matches: dict[str, Any],
) -> list[dict[str, Any]]:
    """Run structured comparisons only when a source actually supplies a reviewed rule."""
    proposed_ref = RuleReference(
        rule_id=proposal["rule_id"],
        rule_version=proposal["rule_version"],
        source_category="proposed",
        source_document=proposal["uploaded_file_name"],
        policy_id=None,
        clause_id=statement["statement_id"],
        source_text=statement["quote"],
        verification_status="unverified_candidate",
        lifecycle_status="draft",
        source_status="unknown",
        evidence_reference=proposal["uploaded_file_sha256"],
        rule_definition={key: proposal[key] for key in ("rule_id", "obligation_id", "rule_name", "condition", "action", "source_obligation", "rule_version", "status", "created_at", "approved_by", "notes")},
        execution_capability="candidate_definition_only",
    )
    comparisons = []
    for match in rbi_matches:
        existing_ref = RuleReference(
            rule_id=None,
            rule_version=None,
            source_category="RBI",
            source_document=match.get("document_id"),
            policy_id=None,
            clause_id=None,
            source_text=match.get("quote", ""),
            verification_status="unverified_source_passage",
            lifecycle_status=match.get("document_status", "unknown"),
            source_status="unknown",
            evidence_reference=match.get("evidence_id"),
            rule_definition=None,
            execution_capability="source_text_only",
        )
        comparisons.append(compare_rule_pair(existing_ref, proposed_ref))
    for match in bank_matches["screening_matches"]:
        existing_ref = RuleReference(
            rule_id=None,
            rule_version=match.get("policy_version"),
            source_category="bank_policy",
            source_document=match.get("source_document_reference"),
            policy_id=match.get("policy_id"),
            clause_id=match.get("clause_id"),
            source_text=match.get("clause_text", ""),
            verification_status=match.get("policy_verification_status", "unknown"),
            lifecycle_status=match.get("lifecycle_status", "unknown"),
            source_status="verified_current" if match.get("comparison_eligibility") == "eligible_approved_effective_verified" else "unknown",
            effective_from=match.get("effective_from"),
            effective_to=match.get("effective_to"),
            evidence_reference=match.get("source_document_reference"),
            rule_definition=match.get("executable_rule_definition"),
            execution_capability="reviewed_structured_definition" if match.get("executable_rule_definition") else "natural_language_clause_only",
        )
        comparisons.append(compare_rule_pair(existing_ref, proposed_ref))
    return comparisons


def compare_proposed_rule(
    *,
    analysis_id: str,
    statement_id: str,
    rule_name: str,
    purpose: str,
    condition_field: str,
    condition_operator: str,
    condition_value: str,
    action_type: str,
    action_message: str,
    action_max_amount: float | None = None,
    limit_notes: str = "",
    exception_notes: str = "",
    affected_transaction_notes: str = "",
) -> dict[str, Any]:
    with _SESSION_LOCK:
        session = _SESSIONS.get(analysis_id)
    if session is None:
        raise ProposedRuleError("This analysis has expired. Upload the PDF again and restart the review.")
    statement = next((item for item in session["statements"] if item["statement_id"] == statement_id), None)
    if statement is None:
        raise ProposedRuleError("Choose a source statement extracted from this uploaded PDF.")
    if not rule_name.strip() or len(rule_name) > 256:
        raise ProposedRuleError("Enter a rule name of up to 256 characters.")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ProposedRuleError("Choose a supported expected action.")
    if len(purpose) > 1000 or len(action_message) > 500 or len(limit_notes) > 1000 or len(exception_notes) > 1000 or len(affected_transaction_notes) > 1000:
        raise ProposedRuleError("One or more review fields are too long.")
    if action_type == "set_limit" and (action_max_amount is None or not math.isfinite(action_max_amount) or action_max_amount < 0):
        raise ProposedRuleError("Enter a non-negative limit for the set-limit action.")
    condition_value_parsed = _parse_condition_value(condition_field, condition_operator, condition_value)
    rule_id = f"PR-{uuid.uuid4().hex[:12].upper()}"
    obligation_id = f"PROPOSAL-{uuid.uuid4().hex[:12].upper()}"
    proposal = {
        "rule_id": rule_id,
        "obligation_id": obligation_id,
        "rule_name": rule_name.strip(),
        "condition": {"type": "comparison", "field": condition_field, "operator": condition_operator, "value": condition_value_parsed},
        "action": {
            "action_type": action_type,
            "parameters": {"max_amount": action_max_amount} if action_type == "set_limit" else {},
            "message": action_message.strip() or "Proposed action entered for review.",
        },
        "source_obligation": {
            "label": f"Uploaded proposal {session['file_name']} — unverified source statement",
            "dataset_kind": "unverified_candidate",
        },
        "rule_version": "0.1.0",
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": None,
        "notes": (purpose.strip() + (" | Limits: " + limit_notes.strip() if limit_notes.strip() else "") + (" | Exceptions: " + exception_notes.strip() if exception_notes.strip() else ""))[:2000],
    }
    validation_errors = validate_rule(proposal)
    if validation_errors:
        raise ProposedRuleError("The rule fields need correction before this proposal can be screened.")

    query = " ".join((statement["quote"], rule_name, purpose, limit_notes, exception_notes, affected_transaction_notes))
    rbi_matches = _match_rbi_passages(query, exact_text=statement["quote"])
    bank_matches = policy_clause_matches(query, exact_text=statement["quote"])
    # The installed data contains RBI evidence passages but no verified obligations
    # or bank-rule repository. A passage overlap is never upgraded to a conflict.
    verified_obligations = [row for row in _read_csv(OBLIGATIONS_FILE) if row.get("verification_status") == "verified"]
    comparison_pairs = _compare_source_findings(
        {**proposal, "uploaded_file_name": session["file_name"], "uploaded_file_sha256": session["file_sha256"]},
        statement,
        rbi_matches,
        bank_matches,
    )
    assessed_pairs = [pair for pair in comparison_pairs if pair["structured_comparison"]["category"] != "insufficient_evidence"]
    overlap_pairs = [pair for pair in comparison_pairs if pair["structured_comparison"]["condition_overlap"] in {"overlap", "disjoint"}]
    exact_duplicates = [item for item in rbi_matches if item["classification"] == "possible_duplicate_wording"]
    if exact_duplicates:
        status = "possible_duplicate_wording"
        status_message = "Possible duplicate wording was found in the indexed passages. This is not a verified duplicate or a conflict finding."
    elif rbi_matches:
        status = "related_source_passages_found"
        status_message = "Related RBI source passages were found. Text overlap does not establish duplication, legal effect, applicability, or conflict."
    else:
        status = "comparison_unavailable"
        status_message = "No related passage was found in the available index. A conflict determination is unavailable because verified executable rules and bank rules are not available."
    result = {
        "analysis_id": analysis_id,
        "proposal_id": session["proposal_id"],
        "proposal": {
            "rule_id": rule_id,
            "rule_version": proposal["rule_version"],
            "name": proposal["rule_name"],
            "purpose": purpose.strip(),
            "condition": proposal["condition"],
            "action": proposal["action"],
            "source_statement": statement,
            "affected_transaction_notes": affected_transaction_notes.strip(),
            "limit_notes": limit_notes.strip(),
            "exception_notes": exception_notes.strip(),
            "verification_status": "unverified_candidate",
            "uploaded_file_name": session["file_name"],
            "uploaded_file_sha256": session["file_sha256"],
        },
        "assessment_status": status,
        "status_message": status_message,
        "rbi_matches": rbi_matches,
        "rule_comparisons": comparison_pairs,
        "comparison_questions": {
            "wording": {"status": "screened", "match_count": len(rbi_matches) + len(bank_matches["screening_matches"]), "message": "Wording findings are preliminary text-screening signals only."},
            "condition_action_equivalence": {"status": "assessed" if assessed_pairs else "insufficient_evidence", "message": "Reviewed structured rule definitions were compared where available." if assessed_pairs else "No verified existing executable rule definitions are available to compare conditions and actions."},
            "condition_overlap": {"status": "assessed" if overlap_pairs else "insufficient_evidence", "message": "Supported condition overlap was assessed on compared structured rules." if overlap_pairs else "Condition overlap cannot be assessed against verified existing structured rules in the current data."},
            "execution": {"status": "unavailable", "message": "No verified existing executable rules and genuine authorized transaction inputs are available."},
            "evidence_sufficiency": {"status": "insufficient_evidence", "message": "Indexed source text alone cannot establish equivalence, conflict, or legal safety."},
        },
        "verified_rbi_obligation_count": len(verified_obligations),
        "verified_rule_comparison": {
            "available": False,
            "status": "no_verified_obligations" if not verified_obligations else "no_executable_rule_mapping",
            "verified_obligation_count": len(verified_obligations),
            "executable_rule_count": 0,
            "message": "No verified regulatory obligations are available; no rule comparison was run." if not verified_obligations else "Verified obligation records exist, but no reviewed executable rule mapping is available for comparison.",
        },
        "bank_rules": {
            "available": bool(bank_matches["active_verified_matches"]),
            "status": "available" if bank_matches["active_verified_matches"] else ("no_imported_policies" if bank_matches["policy_version_count"] == 0 else "no_verified_active_policies"),
            "message": "No bank policies have been imported yet. Internal policy comparison is unavailable." if bank_matches["policy_version_count"] == 0 else ("No imported policy versions meet the verified, approved, and effective comparison requirements. Similar text is shown as screening only." if not bank_matches["active_verified_matches"] else "Verified, approved, effective policy clauses are available for review."),
            "screening_matches": bank_matches["screening_matches"],
            "active_verified_matches": bank_matches["active_verified_matches"],
            "policy_version_count": bank_matches["policy_version_count"],
        },
        "transaction_comparison": {"available": False, "status": "data_unavailable", "transaction_count": 0, "executable_rule_count": 0, "message": "Real transaction impact cannot be measured: no genuine authorized transaction dataset is present, and policy clauses do not yet have reviewed executable rule definitions. No transaction outcomes were generated."},
        "conflict_detection": {"available": False, "status": "cannot_determine", "message": "A conflict cannot be determined from text similarity. No verified current executable RBI rules or independently verified bank policy rule definitions are available."},
        "scope_warning": "The RBI check compares text with indexed source passages; it cannot confirm completeness, legal effect, or current applicability. No verified executable RBI rules or bank rules are present in the available dataset.",
        "approval_status": "not_performed",
        "activation_status": "inactive",
        "authority_authentication": "not_available_in_this_prototype",
    }
    with _SESSION_LOCK:
        session["comparison"] = result
    return result


def record_local_decision(
    *,
    analysis_id: str,
    decision: str,
    notes: str,
    path: Path = DECISIONS_FILE,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ProposedRuleError("Choose approve for next stage, request changes, or reject.")
    if len(notes) > 2000:
        raise ProposedRuleError("Authority notes must be 2,000 characters or fewer.")
    with _SESSION_LOCK:
        session = _SESSIONS.get(analysis_id)
        comparison = session.get("comparison") if session else None
    if comparison is None:
        raise ProposedRuleError("Run the analysis before recording a decision.")
    source_ids = [item.get("evidence_id") for item in comparison["rbi_matches"] if item.get("evidence_id")]
    record = {
        "decision_id": str(uuid.uuid4()),
        "proposal_id": comparison["proposal_id"],
        "analysis_id": analysis_id,
        "proposed_rule_id": comparison["proposal"]["rule_id"],
        "proposed_rule_version": comparison["proposal"]["rule_version"],
        "decision": decision,
        "decision_time": datetime.now(timezone.utc).isoformat(),
        "authority_identity": None,
        "identity_status": "not_authenticated",
        "authority_notes": notes.strip(),
        "compared_source_ids": source_ids,
        "compared_rule_versions": [],
        "proposal_source": {
            "file_name": comparison["proposal"]["uploaded_file_name"],
            "file_sha256": comparison["proposal"]["uploaded_file_sha256"],
            "statement_id": comparison["proposal"]["source_statement"]["statement_id"],
            "page": comparison["proposal"]["source_statement"]["page"],
            "section_reference": comparison["proposal"]["source_statement"]["section_reference"],
            "quote_sha256": hashlib.sha256(comparison["proposal"]["source_statement"]["quote"].encode("utf-8")).hexdigest(),
        },
        "simulation_id": None,
        "simulation_summary": None,
        "record_type": "local_decision_only",
        "approval_status": "not_performed",
        "activation_status": "inactive",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with _DECISION_LOCK, path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {
        "decision_id": record["decision_id"],
        "decision": decision,
        "identity_status": record["identity_status"],
        "message": "Decision saved locally. This prototype cannot authenticate the authority; the rule remains inactive and is not approved for deployment.",
        "approval_status": "not_performed",
        "activation_status": "inactive",
    }
