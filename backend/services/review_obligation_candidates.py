"""Human-in-the-loop CLI for reviewing provisional obligation candidates.

A candidate enters obligations.csv only through an explicit ``review --decision
verified`` action with all reviewed fields supplied by a person.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT / "data" / "processed"
CANDIDATE_FILE = "obligation_candidates.csv"
EVIDENCE_FILE = "evidence_passages.csv"
REGISTRY_FILE = "document_registry.csv"
QUEUE_FILE = "obligation_review_queue.csv"
OBLIGATIONS_FILE = "obligations.csv"
CANDIDATE_AUDIT_FILE = "candidate_review_audit.csv"
DECISIONS = {"pending", "verified", "rejected"}
QUEUE_FIELDS = [
    "candidate_id", "candidate_text", "provisional_category", "evidence_id",
    "document_id", "paragraph_reference", "pdf_page_index",
    "printed_page_number", "source_passage_text", "review_decision",
    "reviewer_notes", "reviewed_requirement_text", "reviewed_actor",
    "reviewed_action", "reviewed_conditions", "reviewed_exceptions",
    "applicability_notes",
]
OBLIGATION_FIELDS = [
    "obligation_id", "document_id", "evidence_id", "source_page",
    "source_section", "original_text", "normalized_requirement",
    "responsible_party", "applicability", "conditions", "exceptions",
    "related_obligation_ids", "verification_status", "review_notes",
    "reviewed_action",
]
REQUIRED_VERIFIED_FIELDS = (
    "reviewer_notes",
    "reviewed_requirement_text", "reviewed_actor", "reviewed_action",
    "reviewed_conditions", "reviewed_exceptions", "applicability_notes",
)
CANDIDATE_AUDIT_FIELDS = [
    "event_id", "candidate_id", "evidence_id", "document_id",
    "reviewer_id", "decision", "reason", "timestamp",
]


class ReviewError(ValueError):
    """Raised for invalid or untraceable review operations."""


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise ReviewError(f"Required source file is missing: {path.name}")
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ReviewError(f"CSV has no header: {path.name}")
        return list(reader), reader.fieldnames


def write_csv(path: Path, rows: Sequence[dict[str, str]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def backup_once(path: Path) -> None:
    """Keep the first pre-change copy; never overwrite an existing backup."""
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            shutil.copy2(path, backup)


def atomic_write(path: Path, rows: Sequence[dict[str, str]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        write_csv(temp_path, rows, fields)
        backup_once(path)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def load_source(data_dir: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    candidates, candidate_headers = read_csv(data_dir / CANDIDATE_FILE)
    evidence_rows, evidence_headers = read_csv(data_dir / EVIDENCE_FILE)
    registry_rows, registry_headers = read_csv(data_dir / REGISTRY_FILE)
    required = {
        CANDIDATE_FILE: {"candidate_id", "candidate_text", "candidate_type", "evidence_id", "document_id", "paragraph_reference"},
        EVIDENCE_FILE: {"evidence_id", "document_id", "pdf_page_index", "printed_page_number", "passage_text"},
        REGISTRY_FILE: {"document_id", "filename"},
    }
    for name, headers in ((CANDIDATE_FILE, candidate_headers), (EVIDENCE_FILE, evidence_headers), (REGISTRY_FILE, registry_headers)):
        missing = sorted(required[name].difference(headers))
        if missing:
            raise ReviewError(f"{name} missing required columns: {', '.join(missing)}")
    candidates_by_id = _unique_index(candidates, "candidate_id", "candidate")
    evidence_by_id = _unique_index(evidence_rows, "evidence_id", "evidence")
    registry_by_id = _unique_index(registry_rows, "document_id", "document")
    for candidate in candidates:
        validate_candidate_references(candidate, evidence_by_id, registry_by_id)
    return candidates, evidence_by_id, registry_by_id


def _unique_index(rows: list[dict[str, str]], key: str, label: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        value = row.get(key, "").strip()
        if not value:
            raise ReviewError(f"A {label} row has a blank {key}")
        if value in result:
            raise ReviewError(f"Duplicate {label} ID: {value}")
        result[value] = row
    return result


def validate_candidate_source(
    candidate: dict[str, str],
    evidence_by_id: dict[str, dict[str, str]],
    registry_by_id: dict[str, dict[str, str]],
) -> dict[str, str]:
    evidence = validate_candidate_references(candidate, evidence_by_id, registry_by_id)
    candidate_id = candidate.get("candidate_id", "")
    source_text = evidence.get("passage_text", "")
    candidate_text = candidate.get("candidate_text", "")
    normalized_source = " ".join(source_text.split())
    normalized_candidate = " ".join(candidate_text.split())
    if not normalized_candidate or normalized_candidate not in normalized_source:
        raise ReviewError(f"Candidate {candidate_id} text does not match its source evidence")
    return evidence


def validate_candidate_references(
    candidate: dict[str, str],
    evidence_by_id: dict[str, dict[str, str]],
    registry_by_id: dict[str, dict[str, str]],
) -> dict[str, str]:
    candidate_id = candidate.get("candidate_id", "")
    evidence_id = candidate.get("evidence_id", "")
    document_id = candidate.get("document_id", "")
    if evidence_id not in evidence_by_id:
        raise ReviewError(f"Candidate {candidate_id} references missing evidence {evidence_id!r}")
    if document_id not in registry_by_id:
        raise ReviewError(f"Candidate {candidate_id} references unregistered document {document_id!r}")
    evidence = evidence_by_id[evidence_id]
    if evidence.get("document_id") != document_id:
        raise ReviewError(f"Candidate {candidate_id} document_id does not match its evidence")
    return evidence


def load_queue(data_dir: Path) -> list[dict[str, str]]:
    path = data_dir / QUEUE_FILE
    if not path.exists():
        return []
    rows, headers = read_csv(path)
    missing = sorted(set(QUEUE_FIELDS).difference(headers))
    if missing:
        raise ReviewError(f"{QUEUE_FILE} missing required columns: {', '.join(missing)}")
    _unique_index(rows, "candidate_id", "review queue candidate")
    for row in rows:
        if row.get("review_decision") not in DECISIONS:
            raise ReviewError(f"Candidate {row.get('candidate_id')} has an invalid review decision")
    return rows


def export_review_queue(data_dir: Path = DEFAULT_DATA_DIR) -> list[dict[str, str]]:
    candidates, evidence_by_id, _registry_by_id = load_source(data_dir)
    existing_rows = load_queue(data_dir)
    existing_by_id = {row["candidate_id"]: row for row in existing_rows}
    rows = []
    for candidate in candidates:
        evidence = validate_candidate_references(candidate, evidence_by_id, _registry_by_id)
        previous = existing_by_id.get(candidate["candidate_id"], {})
        record = {
            "candidate_id": candidate["candidate_id"],
            "candidate_text": candidate["candidate_text"],
            "provisional_category": candidate["candidate_type"],
            "evidence_id": candidate["evidence_id"],
            "document_id": candidate["document_id"],
            "paragraph_reference": candidate["paragraph_reference"],
            "pdf_page_index": evidence.get("pdf_page_index", ""),
            "printed_page_number": evidence.get("printed_page_number", ""),
            "source_passage_text": evidence["passage_text"],
            "review_decision": previous.get("review_decision", "pending"),
        }
        for field in QUEUE_FIELDS:
            if field in record:
                continue
            record[field] = previous.get(field, "")
        if record["review_decision"] not in DECISIONS:
            raise ReviewError(f"Candidate {candidate['candidate_id']} has an invalid review decision")
        rows.append(record)
    # If a candidate disappeared from its source file, do not silently discard its review history.
    current_ids = {row["candidate_id"] for row in rows}
    orphan_ids = set(existing_by_id).difference(current_ids)
    if orphan_ids:
        raise ReviewError(f"Review queue contains candidate IDs missing from source CSV: {', '.join(sorted(orphan_ids))}")
    atomic_write(data_dir / QUEUE_FILE, rows, QUEUE_FIELDS)
    return rows


def _obligation_from_review(row: dict[str, str]) -> dict[str, str]:
    return {
        "obligation_id": f"OBL-{row['candidate_id']}",
        "document_id": row["document_id"],
        "evidence_id": row["evidence_id"],
        "source_page": row["printed_page_number"],
        "source_section": row["paragraph_reference"],
        "original_text": row["candidate_text"],
        "normalized_requirement": row["reviewed_requirement_text"],
        "responsible_party": row["reviewed_actor"],
        "reviewed_action": row["reviewed_action"],
        "applicability": row["applicability_notes"],
        "conditions": row["reviewed_conditions"],
        "exceptions": row["reviewed_exceptions"],
        "related_obligation_ids": "",
        "verification_status": "verified",
        "review_notes": row["reviewer_notes"],
    }


def review_candidate(
    candidate_id: str,
    decision: str,
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    reviewer_id: str = "",
    reviewer_notes: str = "",
    reviewed_requirement_text: str = "",
    reviewed_actor: str = "",
    reviewed_action: str = "",
    reviewed_conditions: str = "",
    reviewed_exceptions: str = "",
    applicability_notes: str = "",
) -> dict[str, str]:
    if decision not in DECISIONS:
        raise ReviewError(f"Invalid review decision {decision!r}; choose pending, verified, or rejected")
    if decision in {"verified", "rejected"} and not reviewer_id.strip():
        raise ReviewError("Verified or rejected decisions require reviewer_id")
    candidates, evidence_by_id, registry_by_id = load_source(data_dir)
    candidate_by_id = _unique_index(candidates, "candidate_id", "candidate")
    if candidate_id not in candidate_by_id:
        raise ReviewError(f"Unknown candidate ID: {candidate_id}")
    candidate = candidate_by_id[candidate_id]
    evidence = validate_candidate_source(candidate, evidence_by_id, registry_by_id)
    queue = load_queue(data_dir)
    if not queue:
        queue = export_review_queue(data_dir)
    queue_by_id = _unique_index(queue, "candidate_id", "review queue candidate")
    if candidate_id not in queue_by_id:
        raise ReviewError(f"Candidate {candidate_id} is absent from the review queue")
    current = queue_by_id[candidate_id]
    if current["review_decision"] != "pending":
        raise ReviewError(
            f"Candidate {candidate_id} was already reviewed as {current['review_decision']}; duplicate reviews are not allowed"
        )
    if decision == "rejected" and not reviewer_notes.strip():
        raise ReviewError("A rejected decision requires reviewer_notes explaining the rejection")
    supplied = {
        "reviewer_notes": reviewer_notes,
        "reviewed_requirement_text": reviewed_requirement_text,
        "reviewed_actor": reviewed_actor,
        "reviewed_action": reviewed_action,
        "reviewed_conditions": reviewed_conditions,
        "reviewed_exceptions": reviewed_exceptions,
        "applicability_notes": applicability_notes,
    }
    if decision == "verified":
        missing = [field for field in REQUIRED_VERIFIED_FIELDS if not supplied[field].strip()]
        if not reviewed_action.strip():
            missing.append("reviewed_action")
        if missing:
            raise ReviewError(
                "Verified decisions require human-entered fields: " + ", ".join(dict.fromkeys(missing))
            )
    for name, value in supplied.items():
        current[name] = value
    current["review_decision"] = decision
    current["reviewer_notes"] = reviewer_notes

    obligations_path = data_dir / OBLIGATIONS_FILE
    obligations, headers = read_csv(obligations_path)
    legacy_obligation_fields = set(OBLIGATION_FIELDS).difference({"reviewed_action"})
    missing_headers = sorted(legacy_obligation_fields.difference(headers))
    if missing_headers:
        raise ReviewError(f"{OBLIGATIONS_FILE} missing required columns: {', '.join(missing_headers)}")
    _unique_index(obligations, "obligation_id", "verified obligation")
    for existing in obligations:
        if existing.get("verification_status", "").lower() != "verified":
            raise ReviewError(
                f"{OBLIGATIONS_FILE} contains a non-verified record {existing.get('obligation_id', '')!r}"
            )
        if existing.get("evidence_id", "") not in evidence_by_id:
            raise ReviewError(
                f"Verified obligation {existing.get('obligation_id', '')} references missing evidence"
            )
    existing_obligation_ids = {row.get("obligation_id", "") for row in obligations}
    existing_source_candidates = {
        row.get("obligation_id", "").removeprefix("OBL-") for row in obligations
    }
    if candidate_id in existing_source_candidates:
        raise ReviewError(f"Candidate {candidate_id} already has a verified obligation record")
    if decision == "verified":
        obligation = _obligation_from_review(current)
        if obligation["obligation_id"] in existing_obligation_ids:
            raise ReviewError(f"Duplicate verified obligation ID: {obligation['obligation_id']}")
        obligations.append(obligation)

    audit_path = data_dir / CANDIDATE_AUDIT_FILE
    audit_rows: list[dict[str, str]] | None = None
    if decision in {"verified", "rejected"}:
        if audit_path.exists():
            audit_rows, audit_headers = read_csv(audit_path)
            missing_audit_headers = sorted(set(CANDIDATE_AUDIT_FIELDS).difference(audit_headers))
            if missing_audit_headers:
                raise ReviewError(
                    f"{CANDIDATE_AUDIT_FILE} missing required columns: "
                    f"{', '.join(missing_audit_headers)}"
                )
        else:
            audit_rows = []
        audit_rows.append({
            "event_id": str(uuid.uuid4()),
            "candidate_id": candidate_id,
            "evidence_id": candidate["evidence_id"],
            "document_id": candidate["document_id"],
            "reviewer_id": reviewer_id.strip(),
            "decision": decision,
            "reason": reviewer_notes.strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # Prepare the review queue for every decision. Only verified decisions modify
    # obligations.csv; the action field is an additive extension to its prior schema.
    staged: list[tuple[Path, Path]] = []
    try:
        outputs = [(data_dir / QUEUE_FILE, queue, QUEUE_FIELDS)]
        if decision == "verified":
            obligation_fields = list(headers)
            if "reviewed_action" not in obligation_fields:
                obligation_fields.append("reviewed_action")
            outputs.append((obligations_path, obligations, obligation_fields))
        if audit_rows is not None:
            outputs.append((audit_path, audit_rows, CANDIDATE_AUDIT_FIELDS))
        for target, rows, fields in outputs:
            descriptor, temp_name = tempfile.mkstemp(prefix=f"{target.stem}-", suffix=".tmp", dir=data_dir)
            os.close(descriptor)
            temp_path = Path(temp_name)
            write_csv(temp_path, rows, fields)
            staged.append((target, temp_path))
        for target, temp_path in staged:
            backup_once(target)
            os.replace(temp_path, target)
    finally:
        for _target, temp_path in staged:
            temp_path.unlink(missing_ok=True)
    return current


def list_candidates(data_dir: Path = DEFAULT_DATA_DIR, decision: str | None = None) -> list[dict[str, str]]:
    if decision is not None and decision not in DECISIONS:
        raise ReviewError(f"Invalid review decision filter {decision!r}")
    queue = load_queue(data_dir) if (data_dir / QUEUE_FILE).exists() else export_review_queue(data_dir)
    if decision is not None:
        queue = [row for row in queue if row["review_decision"] == decision]
    return queue


def list_candidate_records(data_dir: Path = DEFAULT_DATA_DIR) -> list[dict[str, str]]:
    """Return candidates joined to their existing review state without writing files."""
    candidates, evidence_by_id, _registry_by_id = load_source(data_dir)
    if (data_dir / QUEUE_FILE).exists():
        queue = load_queue(data_dir)
    else:
        queue = []
    queue_by_id = _unique_index(queue, "candidate_id", "review queue candidate")
    records = []
    for candidate in candidates:
        evidence = validate_candidate_references(candidate, evidence_by_id, _registry_by_id)
        normalized_source = " ".join(evidence.get("passage_text", "").split())
        normalized_candidate = " ".join(candidate.get("candidate_text", "").split())
        source_match = bool(normalized_candidate) and normalized_candidate in normalized_source
        review = queue_by_id.get(candidate["candidate_id"], {})
        records.append({
            **candidate,
            "review_decision": review.get("review_decision", "pending"),
            "reviewer_notes": review.get("reviewer_notes", ""),
            "source_passage_text": evidence.get("passage_text", ""),
            "printed_page_number": evidence.get("printed_page_number", ""),
            "source_match_status": "matched" if source_match else "requires_source_review",
            "source_match_warning": "Candidate wording does not appear as a contiguous span in the exact indexed passage; verification is blocked until its source grounding is reviewed." if not source_match else "Candidate wording is present in its indexed source passage; legal interpretation still requires human review.",
        })
    return records


def append_extracted_candidates(candidates: Sequence[dict], data_dir: Path = DEFAULT_DATA_DIR) -> list[str]:
    """Append grounded workflow candidates to the existing CSV review store."""
    source_candidates, evidence_by_id, registry_by_id = load_source(data_dir)
    existing_ids = {row["candidate_id"] for row in source_candidates}
    evidence_headers = set(evidence_by_id)
    registry_headers = set(registry_by_id)
    candidate_path = data_dir / CANDIDATE_FILE
    rows, fields = read_csv(candidate_path)
    added_ids: list[str] = []
    for item in candidates:
        candidate_id = str(item.get("candidate_id", ""))
        evidence_id = str(item.get("evidence_id", ""))
        document_id = str(item.get("document_id", ""))
        text = str(item.get("proposed_obligation", ""))
        candidate_type = str(item.get("candidate_type", ""))
        if not candidate_id or candidate_id in existing_ids:
            raise ReviewError("Extracted candidate ID is missing or already exists")
        if evidence_id not in evidence_headers or document_id not in registry_headers:
            raise ReviewError("Extracted candidate source is not in the registered evidence dataset")
        if evidence_by_id[evidence_id].get("document_id") != document_id:
            raise ReviewError("Extracted candidate document does not match its evidence source")
        if candidate_type == "no_candidate" or item.get("candidate_obligation_grounded") is not True:
            raise ReviewError("Only source-grounded candidate extractions can enter the review queue")
        evidence = evidence_by_id[evidence_id]
        normalized_source = " ".join(evidence.get("passage_text", "").split())
        normalized_text = " ".join(text.split())
        if not normalized_text or normalized_text not in normalized_source:
            raise ReviewError("Extracted candidate wording does not match its source evidence")
        registry = registry_by_id[document_id]
        row = {field: "" for field in fields}
        row.update({
            "candidate_id": candidate_id,
            "evidence_id": evidence_id,
            "document_id": document_id,
            "source_filename": registry.get("filename", ""),
            "source_page": str(item.get("source_page", "")),
            "source_section_hint": str(item.get("paragraph_reference", "")),
            "original_text": text,
            "matched_language": candidate_type,
            "candidate_reason": str(item.get("uncertainty", "")),
            "normalized_requirement": "",
            "responsible_party": "",
            "applicability": "",
            "conditions": "",
            "exceptions": "",
            "verification_status": "unverified_candidate",
            "review_notes": "Requires human review; extraction output is provisional.",
            "paragraph_reference": str(item.get("paragraph_reference", "")),
            "candidate_text": text,
            "candidate_type": candidate_type,
            "review_status": "pending_human_review",
        })
        rows.append(row)
        existing_ids.add(candidate_id)
        added_ids.append(candidate_id)
    if rows and added_ids:
        atomic_write(candidate_path, rows, fields)
        export_review_queue(data_dir)
    return added_ids


def show_candidate(candidate_id: str, data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, str]:
    candidates, evidence_by_id, registry_by_id = load_source(data_dir)
    candidate = _unique_index(candidates, "candidate_id", "candidate").get(candidate_id)
    if candidate is None:
        raise ReviewError(f"Unknown candidate ID: {candidate_id}")
    evidence = validate_candidate_references(candidate, evidence_by_id, registry_by_id)
    queue = load_queue(data_dir) if (data_dir / QUEUE_FILE).exists() else export_review_queue(data_dir)
    queue_row = _unique_index(queue, "candidate_id", "review queue candidate").get(candidate_id, {})
    return {"candidate": candidate, "evidence": evidence, "review": queue_row}


def print_candidate(row: dict[str, str]) -> None:
    print(
        f"{row['candidate_id']} | {row['review_decision']} | {row['provisional_category']} | "
        f"{row['document_id']} | {row['paragraph_reference']} | evidence={row['evidence_id']}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Processed data directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("export", help="Create/update the review queue while preserving prior human decisions")
    list_parser = commands.add_parser("list", help="List candidate IDs and statuses")
    list_parser.add_argument("--decision", choices=sorted(DECISIONS))
    list_parser.add_argument("--limit", type=int)
    show_parser = commands.add_parser("show", help="Display candidate text and its source passage")
    show_parser.add_argument("candidate_id")
    review_parser = commands.add_parser(
        "review",
        help="Record a human decision for one candidate",
        description=(
            "Verified requires human-entered requirement, actor, action, conditions, "
            "exceptions, and applicability notes. Use an explicit note such as "
            "'None identified after review' when a field does not apply."
        ),
    )
    review_parser.add_argument("candidate_id")
    review_parser.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    review_parser.add_argument("--reviewer-id", default="", help="Human reviewer identifier (required for verified/rejected)")
    review_parser.add_argument("--reviewer-notes", default="")
    review_parser.add_argument("--requirement", dest="reviewed_requirement_text", default="")
    review_parser.add_argument("--actor", dest="reviewed_actor", default="")
    review_parser.add_argument("--action", dest="reviewed_action", default="")
    review_parser.add_argument("--conditions", dest="reviewed_conditions", default="")
    review_parser.add_argument("--exceptions", dest="reviewed_exceptions", default="")
    review_parser.add_argument("--applicability-notes", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.resolve()
    try:
        if args.command == "export":
            rows = export_review_queue(data_dir)
            print(f"Review queue ready: {len(rows)} candidates at {data_dir / QUEUE_FILE}")
        elif args.command == "list":
            rows = list_candidates(data_dir, args.decision)
            if args.limit is not None:
                rows = rows[: max(args.limit, 0)]
            for row in rows:
                print_candidate(row)
            print(f"Listed {len(rows)} candidate(s).")
        elif args.command == "show":
            result = show_candidate(args.candidate_id, data_dir)
            candidate = result["candidate"]
            evidence = result["evidence"]
            review = result["review"]
            print(f"Candidate: {candidate['candidate_id']} ({candidate['candidate_type']})")
            print(f"Decision: {review.get('review_decision', 'pending')}")
            print(f"Document: {candidate['document_id']} | Evidence: {candidate['evidence_id']}")
            print(f"Paragraph: {candidate['paragraph_reference']} | PDF page index: {evidence.get('pdf_page_index', '')} | printed page: {evidence.get('printed_page_number', '')}")
            print("Candidate text:\n" + candidate["candidate_text"])
            print("\nSource passage:\n" + evidence["passage_text"])
        elif args.command == "review":
            reviewed = review_candidate(
                args.candidate_id,
                args.decision,
                data_dir,
                reviewer_id=args.reviewer_id,
                reviewer_notes=args.reviewer_notes,
                reviewed_requirement_text=args.reviewed_requirement_text,
                reviewed_actor=args.reviewed_actor,
                reviewed_action=args.reviewed_action,
                reviewed_conditions=args.reviewed_conditions,
                reviewed_exceptions=args.reviewed_exceptions,
                applicability_notes=args.applicability_notes,
            )
            print(f"Recorded {reviewed['review_decision']} for {reviewed['candidate_id']}.")
            if reviewed["review_decision"] == "verified":
                print(f"Added verified obligation OBL-{reviewed['candidate_id']} to {data_dir / OBLIGATIONS_FILE}.")
        return 0
    except (ReviewError, OSError, csv.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
