"""Deterministic structural checks for the Phase 2 evidence CSVs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT / "data" / "processed"
JSON_FILES = {
    "DOC-CB-2025": "RBI_Commercial_Banks_Credit_Facilities_2025.json",
    "DOC-DL-2025": "RBI_Digital_Lending_Directions_May_2025.json",
    "DOC-DL-2022": "RBI_Digital_Lending_Guidelines_September_2022.json",
}
REQUIRED = {
    "document_registry.csv": {
        "document_id", "filename", "pdf_sha256", "extracted_text_sha256",
        "role", "document_status",
    },
    "evidence_passages.csv": {
        "evidence_id", "document_id", "source_filename", "source_sha256",
        "pdf_page_index", "printed_page_number", "section_heading",
        "paragraph_reference", "passage_text", "passage_type", "review_status",
        "extraction_method", "source_status", "source_page_indexes",
    },
    "obligation_candidates.csv": {
        "candidate_id", "evidence_id", "document_id", "paragraph_reference",
        "candidate_text", "candidate_type", "candidate_reason", "review_status",
    },
    "obligations.csv": {
        "obligation_id", "document_id", "evidence_id", "source_page",
        "original_text", "verification_status",
    },
}


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        return list(reader), reader.fieldnames or []


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate(data_dir: Path) -> tuple[list[str], list[str], dict[str, int]]:
    errors: list[str] = []
    warnings: list[str] = []
    rows: dict[str, list[dict[str, str]]] = {}
    for filename, required in REQUIRED.items():
        path = data_dir / filename
        if not path.exists():
            errors.append(f"Missing required CSV: {filename}")
            rows[filename] = []
            continue
        records, headers = read_csv(path)
        missing = sorted(required.difference(headers))
        if missing:
            errors.append(f"{filename} missing required columns: {', '.join(missing)}")
        rows[filename] = records

    registry_rows = rows["document_registry.csv"]
    evidence_rows = rows["evidence_passages.csv"]
    candidate_rows = rows["obligation_candidates.csv"]
    obligation_rows = rows["obligations.csv"]

    def unique(records: list[dict[str, str]], key: str, label: str) -> None:
        values = [row.get(key, "") for row in records]
        values = [value for value in values if value]
        duplicates = [value for value, count in Counter(values).items() if count > 1]
        if duplicates:
            errors.append(f"Duplicate {label}: {', '.join(duplicates[:5])}")

    unique(registry_rows, "document_id", "document_id values")
    unique(evidence_rows, "evidence_id", "evidence_id values")
    unique(candidate_rows, "candidate_id", "candidate_id values")
    registry = {row.get("document_id", ""): row for row in registry_rows}
    evidence = {row.get("evidence_id", ""): row for row in evidence_rows}

    for row in evidence_rows:
        evidence_id = row.get("evidence_id", "")
        doc_id = row.get("document_id", "")
        if doc_id not in registry:
            errors.append(f"Evidence {evidence_id} references unregistered document {doc_id!r}")
            continue
        if not row.get("passage_text", "").strip():
            errors.append(f"Evidence {evidence_id} has empty passage text")
        if not row.get("source_sha256", "") or row.get("source_sha256") != registry[doc_id].get("pdf_sha256"):
            errors.append(f"Evidence {evidence_id} source hash does not match registry")
        if not row.get("source_filename", "").strip():
            errors.append(f"Evidence {evidence_id} has no source filename")
        if not row.get("source_page_indexes", "").strip():
            warnings.append(f"Evidence {evidence_id} has no page index reference")
        if not row.get("printed_page_number", "").strip():
            warnings.append(f"Evidence {evidence_id} has no reliably parsed printed page number")
        if row.get("extraction_method") not in {
            "chapter_heading_and_numbered_paragraph_parser",
            "paragraph_aware_fallback",
        }:
            errors.append(f"Evidence {evidence_id} has an invalid extraction method")
        text = row.get("passage_text", "")
        if "Table of Contents" in text:
            errors.append(f"Evidence {evidence_id} contains a table-of-contents marker")
        if re.search(r"(?m)^\s*Chapter\s+IV\s*[-–:]", text, re.I):
            errors.append(f"Evidence {evidence_id} includes the unrelated Chapter IV heading")
        if len(text) > 5000:
            warnings.append(f"Evidence {evidence_id} is unusually large ({len(text)} characters); review segmentation")
        if "\ufffd" in text:
            warnings.append(f"Evidence {evidence_id} contains source-extraction replacement characters")

    duplicate_text = Counter(normalize(row.get("passage_text", "")) for row in evidence_rows)
    duplicates = [text for text, count in duplicate_text.items() if text and count > 1]
    if duplicates:
        warnings.append(f"Duplicate passage text found: {len(duplicates)} duplicate value(s)")

    for row in candidate_rows:
        candidate_id = row.get("candidate_id", "")
        evidence_id = row.get("evidence_id", "")
        if evidence_id not in evidence:
            errors.append(f"Candidate {candidate_id} references missing evidence {evidence_id!r}")
            continue
        source = evidence[evidence_id]
        if row.get("document_id") != source.get("document_id"):
            errors.append(f"Candidate {candidate_id} document_id disagrees with its evidence")
        candidate_lines = [
            normalize(line) for line in row.get("candidate_text", "").splitlines()
            if normalize(line)
        ]
        passage_text = normalize(source.get("passage_text", ""))
        if not candidate_lines or not all(line in passage_text for line in candidate_lines):
            errors.append(f"Candidate {candidate_id} text is not traceable to its evidence passage")
        if row.get("review_status") != "needs_review":
            warnings.append(f"Candidate {candidate_id} is not marked needs_review")
        if row.get("candidate_type") not in {
            "mandatory_requirement_candidate", "prohibition_candidate",
            "conditional_requirement_candidate", "definition_or_explanation",
            "exception_or_qualification", "uncertain_candidate",
        }:
            errors.append(f"Candidate {candidate_id} has an unrecognized provisional type")

    for row in obligation_rows:
        obligation_id = row.get("obligation_id", "")
        if row.get("evidence_id", "") not in evidence:
            errors.append(f"Verified obligation {obligation_id} has no valid evidence reference")
        if row.get("document_id", "") not in registry:
            errors.append(f"Verified obligation {obligation_id} has no registered document")
        if row.get("verification_status", "").lower() != "verified":
            errors.append(f"Obligation record {obligation_id} is not explicitly verified")

    if not obligation_rows:
        warnings.append("obligations.csv is header-only; accepted because manual review has not occurred")

    # Check the primary JSON metadata and that each passage's normalized source text
    # occurs on the source pages recorded by the extractor.
    for doc_id, json_filename in JSON_FILES.items():
        if doc_id not in registry:
            continue
        json_path = PROCESSED / json_filename
        if not json_path.exists():
            errors.append(f"Missing processed source JSON: {json_filename}")
            continue
        source = json.loads(json_path.read_text(encoding="utf-8"))
        if source.get("sha256", "") != registry[doc_id].get("pdf_sha256", ""):
            errors.append(f"Registry PDF hash differs from JSON metadata for {doc_id}")
        raw_matches = [
            root / registry[doc_id].get("filename", "")
            for root in (PROJECT / "data" / "raw" / "current", PROJECT / "data" / "raw" / "historical")
            if (root / registry[doc_id].get("filename", "")).is_file()
        ]
        if raw_matches:
            raw_hash = hashlib.sha256(raw_matches[0].read_bytes()).hexdigest()
            if raw_hash != registry[doc_id].get("pdf_sha256", ""):
                errors.append(f"Registry PDF hash differs from raw PDF for {doc_id}")
        full_hash = hashlib.sha256(
            "\n".join(page.get("text", "") for page in source.get("pages", [])).encode("utf-8")
        ).hexdigest()
        if full_hash != registry[doc_id].get("extracted_text_sha256", ""):
            errors.append(f"Registry extracted-text hash differs from JSON for {doc_id}")
        if doc_id != "DOC-CB-2025":
            continue
        source_pages = {str(page.get("page")): page.get("text", "") for page in source.get("pages", [])}
        for row in evidence_rows:
            if row.get("document_id") != doc_id:
                continue
            page_ids = [value for value in row.get("source_page_indexes", "").split(";") if value]
            page_values = [str(int(value) + 1) for value in page_ids]
            recorded_source = [normalize(source_pages.get(value, "")) for value in page_values]
            passage_lines = [
                normalize(line) for line in row.get("passage_text", "").splitlines()
                if normalize(line)
            ]
            # A paragraph may cross a PDF page boundary, where the PDF adds a
            # printed page header. Verify every extracted source line against its
            # recorded source pages instead of requiring a false cross-page substring.
            if not passage_lines or not all(
                any(line in source_text for source_text in recorded_source)
                for line in passage_lines
            ):
                errors.append(f"Evidence {row.get('evidence_id')} text cannot be located on its recorded JSON page(s)")

    paragraph_rows = [row for row in evidence_rows if row.get("paragraph_reference")]
    if not paragraph_rows or paragraph_rows[0].get("paragraph_reference") != "6":
        errors.append("Scope boundary check failed: first numbered evidence paragraph is not paragraph 6")
    if not paragraph_rows or paragraph_rows[-1].get("paragraph_reference") != "31":
        errors.append("Scope boundary check failed: final numbered evidence paragraph is not paragraph 31")
    if any(row.get("source_status") != "VERIFY_CURRENT_STATUS" for row in evidence_rows):
        errors.append("Primary evidence source status must remain VERIFY_CURRENT_STATUS")

    counts = Counter(row.get("candidate_type", "") for row in candidate_rows)
    return errors, warnings, dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=PROCESSED)
    args = parser.parse_args()
    errors, warnings, counts = validate(args.data_dir.resolve())
    print("Phase 2 evidence dataset validation")
    print(f"Evidence passages: {sum(1 for _ in csv.DictReader((args.data_dir / 'evidence_passages.csv').open(encoding='utf-8-sig')))}" if (args.data_dir / "evidence_passages.csv").exists() else "Evidence passages: unavailable")
    print(f"Obligation candidates by provisional category: {counts}")
    print(f"Verified obligation records: {sum(1 for _ in csv.DictReader((args.data_dir / 'obligations.csv').open(encoding='utf-8-sig')))}" if (args.data_dir / "obligations.csv").exists() else "Verified obligation records: unavailable")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"Result: {'PASS' if not errors else 'FAIL'} ({len(errors)} errors, {len(warnings)} warnings)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())