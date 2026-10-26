"""Option A scope gates for real (non-synthetic) regulatory rule mapping."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT / "data" / "processed"
SCOPE_FILE = PROCESSED / "rule_derivation_scope.json"
REGISTRY_FILE = PROCESSED / "document_registry.csv"
OBLIGATIONS_FILE = PROCESSED / "obligations.csv"
EVIDENCE_FILE = PROCESSED / "evidence_passages.csv"

BLOCKED_ACTIVE_DOCUMENTS = frozenset({"DOC-DL-2022"})
LEGAL_REVIEW_DOCUMENTS = frozenset({"DOC-DL-2025"})
ALLOWED_REAL_MAPPING_DOCUMENT = "DOC-CB-2025"
PARAGRAPH_MIN = 6
PARAGRAPH_MAX = 31
UNRESOLVED_DOCUMENT_STATUS = "VERIFY_CURRENT_STATUS"
ALLOWED_RULE_STATUSES_WITHOUT_APPROVAL = frozenset({"draft", "test"})


@dataclass
class ScopeRecord:
    raw: dict[str, Any]

    @property
    def scope_id(self) -> str:
        return str(self.raw.get("scope_id", ""))


def load_scope(path: Path = SCOPE_FILE) -> ScopeRecord:
    with path.open(encoding="utf-8") as stream:
        return ScopeRecord(json.load(stream))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _registry_status(document_id: str, data_dir: Path = PROCESSED) -> str | None:
    for row in _read_csv(data_dir / REGISTRY_FILE.name):
        if row.get("document_id") == document_id:
            return row.get("document_status")
    return None


def _obligation_index(data_dir: Path = PROCESSED) -> dict[str, dict[str, str]]:
    path = data_dir / OBLIGATIONS_FILE.name
    if not path.exists():
        return {}
    return {row["obligation_id"]: row for row in _read_csv(path) if row.get("obligation_id")}


def _evidence_index(data_dir: Path = PROCESSED) -> dict[str, dict[str, str]]:
    return {row["evidence_id"]: row for row in _read_csv(data_dir / EVIDENCE_FILE.name) if row.get("evidence_id")}


def paragraph_in_scope(paragraph_reference: str) -> bool:
    if not paragraph_reference or not str(paragraph_reference).isdigit():
        return False
    value = int(paragraph_reference)
    return PARAGRAPH_MIN <= value <= PARAGRAPH_MAX


def check_real_rule_mapping_eligibility(
    rule: dict[str, Any],
    *,
    data_dir: Path = PROCESSED,
) -> list[str]:
    """Return blocking errors for verified regulatory mapping; empty list means structurally eligible."""
    errors: list[str] = []
    source = rule.get("source_obligation") or {}
    kind = source.get("dataset_kind")
    if kind == "synthetic":
        return []
    if kind != "verified":
        if kind == "unverified_candidate":
            errors.append("unverified_candidate cannot be mapped to executable verified rules")
        return errors

    status = rule.get("status")
    if status not in ALLOWED_RULE_STATUSES_WITHOUT_APPROVAL:
        errors.append(
            f"real rules must stay draft or test until human approval (got status={status!r})"
        )
    if rule.get("approved_by"):
        errors.append("approved_by must be null until explicit human approval workflow")

    document_id = source.get("document_id") or rule.get("document_id", "")
    if document_id in BLOCKED_ACTIVE_DOCUMENTS:
        errors.append(f"{document_id} is superseded; active derivation forbidden")
    if document_id in LEGAL_REVIEW_DOCUMENTS:
        errors.append(f"{document_id} requires legal review before verified mapping to Option A scope")
    if document_id and document_id != ALLOWED_REAL_MAPPING_DOCUMENT:
        errors.append(f"Option A allows verified mapping only from {ALLOWED_REAL_MAPPING_DOCUMENT}")

    registry_status = _registry_status(document_id, data_dir) if document_id else None
    if registry_status == UNRESOLVED_DOCUMENT_STATUS:
        errors.append(
            "DOC-CB-2025 document_status is VERIFY_CURRENT_STATUS; legal currency not established for mapping"
        )

    obligation_id = rule.get("obligation_id", "")
    obligations = _obligation_index(data_dir)
    obligation = obligations.get(obligation_id)
    if not obligation:
        errors.append(f"obligation_id {obligation_id!r} not found in verified obligations.csv")
        return errors
    if obligation.get("verification_status") != "verified":
        errors.append(f"obligation {obligation_id} is not human-verified")
    if obligation.get("document_id") != ALLOWED_REAL_MAPPING_DOCUMENT:
        errors.append("verified obligation must belong to DOC-CB-2025 for Option A")

    evidence_id = obligation.get("evidence_id") or source.get("evidence_id", "")
    evidence = _evidence_index(data_dir).get(evidence_id)
    if not evidence:
        errors.append(f"missing evidence_id {evidence_id!r} for traceability")
    else:
        paragraph = evidence.get("paragraph_reference", "")
        if not paragraph_in_scope(paragraph):
            errors.append(
                f"evidence paragraph {paragraph!r} outside Option A range {PARAGRAPH_MIN}–{PARAGRAPH_MAX}"
            )
        if not evidence.get("pdf_page_index") and not evidence.get("printed_page_number"):
            errors.append("evidence lacks page-level reference")

    return errors


def summarize_scope_for_report(data_dir: Path = PROCESSED) -> dict[str, Any]:
    scope = load_scope(data_dir / "rule_derivation_scope.json" if (data_dir / "rule_derivation_scope.json").exists() else SCOPE_FILE)
    obligations = _obligation_index(data_dir)
    verified = [row for row in obligations.values() if row.get("verification_status") == "verified"]
    return {
        "scope_id": scope.scope_id,
        "verified_obligation_count": len(verified),
        "real_rule_mapping_allowed": len(verified) > 0,
        "document_status_cb_2025": _registry_status(ALLOWED_REAL_MAPPING_DOCUMENT, data_dir),
    }
