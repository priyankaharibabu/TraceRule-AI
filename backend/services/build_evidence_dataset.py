"""Build a reviewable, Chapter III-only Phase 2 evidence dataset."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
PROCESSED = PROJECT / "data" / "processed"
VALIDATOR = Path(__file__).with_name("validate_evidence_dataset.py")

DOCUMENTS = [
    {
        "json": "RBI_Commercial_Banks_Credit_Facilities_2025.json",
        "document_id": "DOC-CB-2025",
        "role": "primary_scope_candidate",
        "status": "VERIFY_CURRENT_STATUS",
    },
    {
        "json": "RBI_Digital_Lending_Directions_May_2025.json",
        "document_id": "DOC-DL-2025",
        "role": "historical_comparison_candidate",
        "status": "HISTORICAL_STATUS_REQUIRES_REVIEW",
    },
    {
        "json": "RBI_Digital_Lending_Guidelines_September_2022.json",
        "document_id": "DOC-DL-2022",
        "role": "historical_comparison_candidate",
        "status": "HISTORICAL_STATUS_REQUIRES_REVIEW",
    },
]

REGISTRY_FIELDS = [
    "document_id", "filename", "document_title", "issuer", "issue_date",
    "updated_date", "page_count", "role", "document_status", "source_url",
    "pdf_sha256", "extracted_text_sha256", "verification_notes",
]
EVIDENCE_FIELDS = [
    "evidence_id", "document_id", "filename", "page_number",
    "passage_number", "section_hint", "original_text", "character_count",
    "contains_requirement_language", "review_status", "source_filename",
    "source_sha256", "pdf_page_index", "printed_page_number",
    "section_heading", "paragraph_reference", "passage_text", "passage_type",
    "extraction_method", "source_status", "source_page_indexes", "parent_reference",
    "split_order",
]
CANDIDATE_FIELDS = [
    "candidate_id", "evidence_id", "document_id", "source_filename",
    "source_page", "source_section_hint", "original_text", "matched_language",
    "candidate_reason", "normalized_requirement", "responsible_party",
    "applicability", "conditions", "exceptions", "verification_status",
    "review_notes", "paragraph_reference", "candidate_text", "candidate_type",
    "review_status",
]
OBLIGATION_FIELDS = [
    "obligation_id", "document_id", "evidence_id", "source_page",
    "source_section", "original_text", "normalized_requirement",
    "responsible_party", "applicability", "conditions", "exceptions",
    "related_obligation_ids", "verification_status", "review_notes",
]

CHAPTER_III = re.compile(
    r"^\s*Chapter\s+III\s*[-–:]\s*Digital Lending Guidelines\s*$",
    re.IGNORECASE,
)
CHAPTER_IV = re.compile(
    r"^\s*Chapter\s+IV\s*[-–:]\s*Lending against Gold and Silver Collateral\s*$",
    re.IGNORECASE,
)
SECTION_HEADING = re.compile(r"^\s*([A-F])\.\s+(.+?)\s*$")
PARAGRAPH_HEADING = re.compile(r"^\s*(\d{1,2})\.\s+\S")
CHILD_HEADING = re.compile(r"^\s*(\((?:\d+|[a-z]|[ivxlcdm]+)\))\s+", re.IGNORECASE)
DIRECTIVE = re.compile(
    r"\b(?:shall|must|is required to|are required to|prohibited from)\b",
    re.IGNORECASE,
)
PROHIBITION = re.compile(
    r"\b(?:shall not|must not|may not|prohibited from|not be permitted)\b",
    re.IGNORECASE,
)
CONDITION = re.compile(r"\b(?:if|where|when|unless|subject to|provided that)\b", re.I)
DEFINITION = re.compile(r"\b(?:shall mean|means|is defined as)\b", re.I)


def normalized(text: str) -> str:
    """Normalize whitespace only for comparisons; never replace source text."""
    return re.sub(r"\s+", " ", text).strip()


def printed_page_number(text: str) -> str:
    for line in text.splitlines()[:8]:
        value = line.strip()
        if re.fullmatch(r"\d{1,3}", value):
            return value
    return ""


def scope_chapter(pages: list[dict]) -> list[dict]:
    """Find the actual chapter heading, not its TOC occurrence, then cut at IV."""
    start = None
    for page_position, page in enumerate(pages):
        lines = page.get("text", "").replace("\r", "\n").splitlines()
        for line_position, line in enumerate(lines):
            if not CHAPTER_III.match(line):
                continue
            following = "\n".join(lines[line_position + 1 :])
            # Actual chapter text has section A and paragraph 6; the TOC does not.
            if (
                re.search(r"(?m)^\s*A\.\s+General Requirements for bank-LSP", following, re.I)
                and re.search(r"(?m)^\s*6\.\s+Due diligence requirements", following, re.I)
            ):
                start = (page_position, line_position)
                break
        if start:
            break
    if start is None:
        raise ValueError(
            "Could not establish Chapter III start from its actual heading, section A, and paragraph 6."
        )

    scoped: list[dict] = []
    start_page, start_line = start
    end_found = False
    for page_position in range(start_page, len(pages)):
        page = pages[page_position]
        lines = page.get("text", "").replace("\r", "\n").splitlines()
        first_line = start_line + 1 if page_position == start_page else 0
        end_line = len(lines)
        if page_position > start_page:
            for line_position, line in enumerate(lines):
                if CHAPTER_IV.match(line):
                    end_line = line_position
                    end_found = True
                    break
        page_lines = lines[first_line:end_line]
        meaningful_lines = [
            line for line in page_lines
            if line.strip() and not (
                page_position > start_page
                and line.strip() == str(page.get("page"))
                and len(page_lines) <= 8
            )
        ]
        if meaningful_lines:
            scoped.append({
                "page": page.get("page"),
                "text": "\n".join(page_lines),
                "printed_page_number": printed_page_number(page.get("text", "")),
            })
        if end_found:
            break

    if not end_found:
        raise ValueError("Could not establish the Chapter III end at the actual Chapter IV heading.")
    return scoped


def parse_passages(scoped_pages: list[dict], document_id: str, filename: str,
                   source_hash: str, source_status: str) -> list[dict]:
    """Group source lines into section headings and complete numbered paragraphs."""
    units: list[dict] = []
    current: dict | None = None
    expected_paragraph = 6

    def flush() -> None:
        nonlocal current
        if current and current["lines"]:
            text = "\n".join(current["lines"]).strip()
            if text:
                units.append({**current, "text": text})
        current = None

    def begin(kind: str, ref: str, heading: str, line: str, page: dict) -> None:
        nonlocal current
        flush()
        current = {
            "kind": kind,
            "reference": ref,
            "heading": heading,
            "lines": [line],
            "pages": [page["page"]],
            "printed_pages": [page["printed_page_number"]] if page["printed_page_number"] else [],
        }

    for page in scoped_pages:
        lines = page["text"].splitlines()
        page_text = page["text"]
        page_number = page.get("page")
        for line_position, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                if current and current["lines"] and current["lines"][-1] != "":
                    current["lines"].append("")
                continue
            # Remove only the page-number header, not numbered text in the body.
            if line_position < 8 and stripped == str(page_number):
                continue
            section_match = SECTION_HEADING.match(line)
            if section_match:
                label, title = section_match.groups()
                begin("heading", "", f"{label}. {title.strip()}", line.strip(), page)
                continue
            paragraph_match = PARAGRAPH_HEADING.match(line)
            if paragraph_match:
                number = int(paragraph_match.group(1))
                if number == expected_paragraph and 6 <= number <= 31:
                    begin("paragraph", str(number), "", line.strip(), page)
                    expected_paragraph += 1
                    continue
            if current:
                current["lines"].append(line.rstrip())
                if page_number not in current["pages"]:
                    current["pages"].append(page_number)
                    if page["printed_page_number"]:
                        current["printed_pages"].append(page["printed_page_number"])
        # A page boundary is retained as a line separator; paragraphs continue until
        # a real section or top-level paragraph heading ends them.
        if current and page_number not in current["pages"]:
            current["pages"].append(page_number)
            if page["printed_page_number"]:
                current["printed_pages"].append(page["printed_page_number"])
    flush()

    # The actual chapter heading is an evidence record, not extraction content.
    first_page = scoped_pages[0]
    chapter_heading = "Chapter III - Digital Lending Guidelines"
    units.insert(0, {
        "kind": "heading", "reference": "", "heading": chapter_heading,
        "text": chapter_heading, "pages": [first_page["page"]],
        "printed_pages": [first_page["printed_page_number"]] if first_page["printed_page_number"] else [],
    })

    passages = []
    active_section = ""
    passage_number = 0
    for unit in units:
        if unit["kind"] == "heading" and unit["reference"]:
            active_section = unit["heading"]
        passage_number += 1
        page_numbers = unit["pages"]
        start_page = page_numbers[0] if page_numbers else ""
        passage_text = unit["text"]
        passage_type = classify_passage(unit)
        evidence_id = (
            f"{document_id}-CH3-H-{unit['heading'][0]}"
            if unit["kind"] == "heading" and unit["heading"] != chapter_heading
            else f"{document_id}-CH3-P{unit['reference']:0>3}"
            if unit["kind"] == "paragraph"
            else f"{document_id}-CH3-H-III"
        )
        passages.append({
            "evidence_id": evidence_id,
            "document_id": document_id,
            "filename": filename,
            "page_number": start_page,
            "passage_number": passage_number,
            "section_hint": active_section or unit["heading"],
            "original_text": passage_text,
            "character_count": len(passage_text),
            "contains_requirement_language": bool(DIRECTIVE.search(passage_text)),
            "review_status": "needs_review",
            "source_filename": filename,
            "source_sha256": source_hash,
            "pdf_page_index": int(start_page) - 1 if str(start_page).isdigit() else "",
            "printed_page_number": unit["printed_pages"][0] if unit["printed_pages"] else "",
            "section_heading": active_section or unit["heading"],
            "paragraph_reference": unit["reference"],
            "passage_text": passage_text,
            "passage_type": passage_type,
            "extraction_method": "chapter_heading_and_numbered_paragraph_parser",
            "source_status": source_status,
            "source_page_indexes": ";".join(str(int(number) - 1) for number in page_numbers if str(number).isdigit()),
            "parent_reference": unit["reference"],
            "split_order": 1,
        })
    return passages


def classify_passage(unit: dict) -> str:
    if unit["kind"] == "heading":
        return "heading"
    text = unit["text"].strip()
    if re.match(r"^(?:Explanation|Clarification)\s*[:–-]", text, re.I):
        return "explanation"
    if unit["reference"] == "29" or re.search(r"\b(?:provided that|except|exception|unless)\b", text, re.I):
        return "exception"
    if DEFINITION.search(text):
        return "definition"
    return "unclassified"


def candidate_units(passage: dict) -> list[tuple[str, str]]:
    """Return independently reviewable child clauses, retaining parent context."""
    text = passage["passage_text"]
    if passage["passage_type"] in {"heading", "definition", "explanation"}:
        return []
    lines = text.splitlines()
    child_positions = [i for i, line in enumerate(lines) if CHILD_HEADING.match(line)]
    if not child_positions:
        return [(passage["paragraph_reference"], text)] if text else []

    parent_context = "\n".join(lines[:child_positions[0]]).strip()
    result = []
    for offset, position in enumerate(child_positions):
        end = child_positions[offset + 1] if offset + 1 < len(child_positions) else len(lines)
        child_line = lines[position]
        child_ref_match = CHILD_HEADING.match(child_line)
        child_ref = child_ref_match.group(1) if child_ref_match else ""
        child_text = "\n".join(lines[position:end]).strip()
        # A proviso/explanation after a subclause remains attached to that subclause.
        combined = "\n".join(part for part in (parent_context, child_text) if part)
        result.append((f"{passage['paragraph_reference']}{child_ref}", combined))
    return result


def candidate_type(text: str, passage: dict) -> tuple[str, str] | None:
    if DEFINITION.search(text) and not PROHIBITION.search(text):
        return None
    if not DIRECTIVE.search(text):
        return None
    if passage["paragraph_reference"] == "29" or re.search(r"\b(?:provided that|except|exception)\b", text, re.I):
        return "exception_or_qualification", "Explicit directive wording appears within an exception/qualification context; human review required."
    if PROHIBITION.search(text):
        return "prohibition_candidate", "Explicit prohibition wording detected; this is a discovery signal, not a legal conclusion."
    if CONDITION.search(text):
        return "conditional_requirement_candidate", "Directive wording and a condition marker detected; human review required."
    return "mandatory_requirement_candidate", "Explicit directive wording detected; legal meaning and applicability remain unverified."


def build_candidates(passages: list[dict]) -> list[dict]:
    candidates = []
    for passage in passages:
        for clause_reference, text in candidate_units(passage):
            classification = candidate_type(text, passage)
            if classification is None:
                continue
            kind, reason = classification
            candidate_number = len(candidates) + 1
            matched = sorted({match.group(0).lower() for match in DIRECTIVE.finditer(text)})
            candidates.append({
                "candidate_id": f"CAND-CB-2025-{candidate_number:04d}",
                "evidence_id": passage["evidence_id"],
                "document_id": passage["document_id"],
                "source_filename": passage["source_filename"],
                "source_page": passage["page_number"],
                "source_section_hint": passage["section_heading"],
                "original_text": passage["passage_text"],
                "matched_language": "; ".join(matched),
                "candidate_reason": reason,
                "normalized_requirement": "",
                "responsible_party": "",
                "applicability": "",
                "conditions": "",
                "exceptions": "",
                "verification_status": "pending_review",
                "review_notes": "Provisional candidate; review exact source wording, actor, applicability, conditions and qualifications.",
                "paragraph_reference": clause_reference,
                "candidate_text": text,
                "candidate_type": kind,
                "review_status": "needs_review",
            })
    return candidates


def extracted_hash(pages: list[dict]) -> str:
    full_text = "\n".join(page.get("text", "") for page in pages)
    return hashlib.sha256(full_text.encode("utf-8")).hexdigest()


def discover_registry() -> tuple[list[dict], dict]:
    registry = []
    loaded = {}
    for info in DOCUMENTS:
        json_path = PROCESSED / info["json"]
        if not json_path.exists():
            raise FileNotFoundError(f"Required extracted source JSON is missing: {json_path}")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        pages = data.get("pages", [])
        if not pages:
            raise ValueError(f"No page records found in {json_path.name}")
        filename = data.get("filename", json_path.name.removesuffix(".json") + ".pdf")
        first_text = pages[0].get("text", "")
        title_match = re.search(
            r"(Reserve Bank of India\s*\(Commercial Banks\s*[–-]\s*Credit Facilities\)\s*Directions,\s*2025)",
            normalized(first_text), re.I,
        )
        update_match = re.search(r"Updated\s+as\s+on\s+([^\)\n]+)", first_text, re.I)
        date_match = re.search(r"\b([A-Z][a-z]+\s+\d{1,2},\s+\d{4})\b", first_text)
        title = title_match.group(1) if title_match else ""
        registry.append({
            "document_id": info["document_id"],
            "filename": filename,
            "document_title": title,
            "issuer": "Reserve Bank of India" if "Reserve Bank of India" in title or "Reserve Bank of India" in first_text else "",
            "issue_date": date_match.group(1) if date_match and info["document_id"] == "DOC-CB-2025" else "",
            "updated_date": update_match.group(1).strip() if update_match and info["document_id"] == "DOC-CB-2025" else "",
            "page_count": data.get("page_count", len(pages)),
            "role": info["role"],
            "document_status": info["status"],
            "source_url": "",
            "pdf_sha256": data.get("sha256", ""),
            "extracted_text_sha256": extracted_hash(pages),
            "verification_notes": (
                "Title and dates populated only when present in extracted text. "
                "Source URL not verified. Legal/current status is not verified against an authoritative source."
                if info["document_id"] == "DOC-CB-2025"
                else "Historical comparison candidate only; no legal-status inference made."
            ),
        })
        loaded[info["document_id"]] = (info, data)
    return registry, loaded


def write_csv(directory: Path, filename: str, rows: list[dict], fields: list[str]) -> None:
    path = directory / filename
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    registry, loaded = discover_registry()
    primary_info, primary_data = loaded["DOC-CB-2025"]
    primary_registry = next(row for row in registry if row["document_id"] == "DOC-CB-2025")
    scoped_pages = scope_chapter(primary_data["pages"])
    passages = parse_passages(
        scoped_pages,
        "DOC-CB-2025",
        primary_registry["filename"],
        primary_registry["pdf_sha256"],
        primary_registry["document_status"],
    )
    candidates = build_candidates(passages)

    output_files = {
        "document_registry.csv": (registry, REGISTRY_FIELDS),
        "evidence_passages.csv": (passages, EVIDENCE_FIELDS),
        "obligation_candidates.csv": (candidates, CANDIDATE_FIELDS),
        "obligations.csv": ([], OBLIGATION_FIELDS),
    }
    with tempfile.TemporaryDirectory(prefix="phase2-", dir=PROCESSED) as temporary:
        temp_path = Path(temporary)
        for filename, (rows, fields) in output_files.items():
            write_csv(temp_path, filename, rows, fields)
        subprocess.run(
            [sys.executable, str(VALIDATOR), "--data-dir", str(temp_path)],
            check=True,
            cwd=PROJECT,
        )
        # Preserve the first pre-change version of each CSV, then atomically replace.
        for filename in output_files:
            target = PROCESSED / filename
            backup = target.with_suffix(target.suffix + ".bak")
            if target.exists() and not backup.exists():
                shutil.copy2(target, backup)
            os.replace(temp_path / filename, target)

    print(f"Chapter III source pages: {scoped_pages[0]['page']}–{scoped_pages[-1]['page']} (JSON page values, one-based)")
    print(f"Scoped evidence passages: {len(passages)}")
    print(f"Obligation candidates: {len(candidates)}")
    print("Verified obligations: 0 (header-only review dataset)")


if __name__ == "__main__":
    main()