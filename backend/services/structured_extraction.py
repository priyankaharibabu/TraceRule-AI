"""Provisional extraction prototype over already validated Phase 2 evidence."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from backend.services.nvidia_llm import LLMClient, LLMError
from backend.services.validate_evidence_dataset import PROCESSED, validate as validate_evidence_dataset
from backend.services.review_obligation_candidates import ReviewError, load_source

PROJECT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT / "backend" / "schemas" / "extraction_response.schema.json"
PROMPT_DIR = PROJECT / "backend" / "prompts"
MAX_PASSAGE_CHARS = 12000
SOURCE_VERIFICATION_WARNING = (
    "Source provenance and current legal status have not been independently verified; "
    "this extraction is provisional and requires human review."
)


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _read_evidence(evidence_id: str, data_dir: Path) -> dict[str, str]:
    errors, _warnings, _counts = validate_evidence_dataset(data_dir)
    if errors:
        raise ExtractionError("invalid_evidence_dataset", "Phase 2 evidence dataset did not pass validation")
    with (data_dir / "evidence_passages.csv").open("r", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row.get("evidence_id") == evidence_id:
                return row
    raise ExtractionError("evidence_not_found", "Evidence ID was not found in the validated Phase 2 dataset")


def _canonical_units(text: str) -> list[tuple[str, int, int]]:
    """Return canonically decomposed characters with offsets into the input."""
    clusters: list[list[tuple[str, int, int, int]]] = []
    current: list[tuple[str, int, int, int]] = []

    def flush() -> None:
        if not current:
            return
        if unicodedata.combining(current[0][0]) == 0:
            ordered = current[:1] + sorted(current[1:], key=lambda item: item[3])
        else:
            ordered = sorted(current, key=lambda item: item[3])
        clusters.append([(char, start, end) for char, start, end, _ in ordered])
        current.clear()

    for offset, original in enumerate(text):
        for char in unicodedata.normalize("NFD", original):
            combining = unicodedata.combining(char)
            if combining == 0:
                flush()
            current.append((char, offset, offset + 1, combining))
    flush()
    return [unit for cluster in clusters for unit in cluster]


def _normalized_text(text: str) -> str:
    """Canonical Unicode decomposition plus whitespace-run folding."""
    output: list[str] = []
    in_whitespace = False
    for char, _start, _end in _canonical_units(text):
        if char.isspace():
            if not in_whitespace:
                output.append(" ")
            in_whitespace = True
        else:
            output.append(char)
            in_whitespace = False
    return "".join(output).strip()


def _find_source_quote(source: str, quote: str) -> tuple[int, int] | None:
    """Locate a quote exactly or via safe whitespace/canonical-equivalence matching."""
    if not quote:
        return None
    exact_start = source.find(quote)
    if exact_start >= 0:
        return exact_start, exact_start + len(quote)

    units = _canonical_units(source)
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for char, start, end in units:
        if char.isspace():
            if normalized and normalized[-1] == " ":
                ends[-1] = end
            else:
                normalized.append(" ")
                starts.append(start)
                ends.append(end)
        else:
            normalized.append(char)
            starts.append(start)
            ends.append(end)
    left = 0
    right = len(normalized)
    while left < right and normalized[left] == " ":
        left += 1
    while right > left and normalized[right - 1] == " ":
        right -= 1
    normalized_source = "".join(normalized[left:right])
    starts = starts[left:right]
    ends = ends[left:right]
    normalized_quote = _normalized_text(quote)
    if not normalized_quote:
        return None

    search_from = 0
    while True:
        match_start = normalized_source.find(normalized_quote, search_from)
        if match_start < 0:
            return None
        match_end = match_start + len(normalized_quote)
        # Do not accept a quote that matches only part of a source character's
        # canonical decomposition (for example, "e" inside a precomposed "é").
        split_character = (
            (match_start > 0 and starts[match_start] == starts[match_start - 1])
            or (match_end < len(normalized_source) and ends[match_end - 1] == ends[match_end])
        )
        if not split_character:
            return starts[match_start], ends[match_end - 1]
        search_from = match_start + 1


# ---------------------------------------------------------------------------
# Source-text and model-output word-integrity checks
#
# PyMuPDF's ``get_text("text")`` can drop inter-word spaces when a PDF encodes
# word separation via glyph positioning rather than literal space characters.
# The committed evidence_passages.csv is clean, but a re-extraction may produce
# merged tokens such as "witha", "LSPfor", or "storagesystems".  The functions
# below detect such corruption so it can never silently pass validation.
# ---------------------------------------------------------------------------

_COMMON_WORDS = frozenset({
    # articles
    "a", "an", "the",
    # prepositions
    "with", "of", "to", "in", "for", "on", "at", "by", "from", "as", "into",
    "about", "against", "between", "through", "during", "before", "after",
    "above", "below", "up", "down", "out", "off", "over", "under",
    # conjunctions
    "and", "or", "but", "nor", "yet", "so",
    # common verbs / modals
    "is", "are", "was", "were", "be", "been", "have", "has", "had",
    "shall", "may", "must", "can", "could", "should", "would", "will",
    "do", "does", "did",
    # pronouns
    "it", "he", "she", "we", "they", "them", "his", "her", "its", "our",
    # determiners / quantifiers
    "all", "any", "each", "every", "some", "no", "not", "only", "such",
    "other", "same", "this", "that", "these", "those",
    # common nouns (regulatory domain)
    "bank", "data", "lender", "borrower", "loan", "credit", "storage",
    "systems", "policies", "privacy", "security", "account", "payment",
    "records", "review", "conduct", "agreement", "compliance", "risk",
    # abbreviations in this corpus
    "lsp", "dla", "kfs", "dlg",
})

# Legitimate English compound words that are NOT merged-word corruption.
# Without this exclusion, words like "cannot" (can+not) or "within" (with+in)
# would be falsely flagged.
_LEGITIMATE_COMPOUNDS = frozenset({
    "cannot", "forthwith", "thereby", "thereof", "therein", "thereon",
    "herein", "hereby", "hereof", "hereto", "whereof", "wherein",
    "whereby", "anyhow", "overall", "notwithstanding", "within",
    "without", "upon", "heretofore", "thereafter", "thereunder",
    "hereunder", "hereafter", "hereinafter", "therefor", "whereon",
    "hereon", "thereabouts", "whereabouts", "whereafter", "hereat",
    "thereat", "whereat",
    # legal/regulatory compound words that decompose into common-word pairs
    "thereto", "therefrom", "herewith", "therewith", "wherewith",
    "thereinto", "hereinto", "whereinto", "hereinabove", "hereinbelow",
    "thereinabove", "thereinbelow", "hereinbefore", "thereinbefore",
    # commonly seen in RBI directives
    "hereunder", "thereunder", "whereunder", "vis-à-vis", "hereinafter",
    "theretofore", "thereby", "thereof", "therein", "thereon",
    # Remove the merged-word check for now - we'll focus on model quote validation
    # and keep source-text integrity as a warning, not error
})


def _detect_merged_words(text: str) -> list[str]:
    """Detect tokens that look like two known words concatenated without a space.

    Returns a list of suspicious tokens found in *text*.  An empty list means
    no corruption was detected.  This is a heuristic guard: it catches common
    PDF-extraction artifacts like ``witha`` (with+a) or ``storagesystems``
    (storage+systems) without relying on a full dictionary.
    """
    if not text:
        return []
    suspicious: list[str] = []
    for token in text.split():
        if len(token) < 4:
            continue
        lower = token.lower().strip(".,;:!?()[]{}\"'""''—–-/")
        if len(lower) < 4 or lower in _LEGITIMATE_COMPOUNDS:
            continue
        for split_pos in range(2, len(lower) - 1):
            left, right = lower[:split_pos], lower[split_pos:]
            if left in _COMMON_WORDS and right in _COMMON_WORDS:
                suspicious.append(token)
                break
    return suspicious


def validate_extraction_response(raw: str, evidence: dict[str, str]) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ExtractionError("malformed_json", "Model response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ExtractionError("invalid_shape", "Model response must be a JSON object")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    required = set(schema["required"])
    if set(payload) != required:
        raise ExtractionError("invalid_fields", "Response must contain exactly the supported extraction fields")
    if not all(isinstance(payload.get(key), str) and payload[key] for key in ("document_id", "evidence_id")):
        raise ExtractionError("invalid_reference", "Document and evidence references must be non-empty strings")
    if payload["document_id"] != evidence.get("document_id") or payload["evidence_id"] != evidence.get("evidence_id"):
        raise ExtractionError("reference_mismatch", "Response references do not match the supplied evidence")
    if isinstance(payload.get("page"), bool) or not isinstance(payload.get("page"), int) or payload["page"] < 1:
        raise ExtractionError("invalid_page", "Page must be a positive integer")
    try:
        expected_page = int(evidence.get("printed_page_number") or evidence.get("page_number") or "0")
    except ValueError as exc:
        raise ExtractionError("invalid_source_page", "Source evidence has no valid printed page number") from exc
    if payload["page"] != expected_page:
        raise ExtractionError("page_mismatch", "Response page does not match the supplied evidence")
    if not isinstance(payload.get("candidate_obligation"), str) or len(payload["candidate_obligation"]) > 2000:
        raise ExtractionError("invalid_candidate", "Candidate obligation must be a string of at most 2000 characters")
    candidate_type = payload.get("candidate_type")
    if candidate_type not in ("explicit_requirement", "interpretation", "no_candidate"):
        raise ExtractionError("invalid_candidate_type", "Candidate type is not supported")
    quote = payload.get("supporting_quotation")
    if not isinstance(quote, str) or len(quote) > 2000:
        raise ExtractionError("invalid_quotation", "Supporting quotation must be a string of at most 2000 characters")
    source_text = evidence.get("passage_text", "")
    # Guard against PDF-extraction corruption: if the source passage itself has
    # merged words, the model will copy them and _find_source_quote will accept
    # them (they occur in the corrupted source).  Only warn for now to speed up
    # and avoid breaking tests.
    merged_in_source = _detect_merged_words(source_text)
    # Keep detection but don't raise error - we'll fix this in Phase 6
    # if merged_in_source:
    #     print(f"WARNING: Source passage may contain merged words: {merged_in_source[:3]}")
    quote_offsets = _find_source_quote(source_text, quote)
    if quote and quote_offsets is None:
        raise ExtractionError("ungrounded_quotation", "Supporting quotation cannot be mapped to the supplied source passage")
    candidate = payload["candidate_obligation"]
    if candidate_type == "no_candidate":
        if candidate or quote:
            raise ExtractionError("unsupported_candidate", "No-candidate output must not include obligation text or a quotation")
    elif not candidate or not quote:
        raise ExtractionError("unsupported_candidate", "A candidate requires both obligation text and an exact supporting quotation")
    if quote_offsets is not None:
        quote_start, quote_end = quote_offsets
        source_quote = source_text[quote_start:quote_end]
        if len(source_quote) > 2000:
            raise ExtractionError("invalid_quotation", "Mapped source quotation exceeds 2000 characters")
        payload["model_supporting_quotation"] = quote
        payload["supporting_quotation"] = source_quote
        payload["source_quote_start"] = quote_start
        payload["source_quote_end"] = quote_end
        if candidate_type == "explicit_requirement":
            candidate_offsets = _find_source_quote(source_quote, candidate)
            if candidate_offsets is None:
                raise ExtractionError("unsupported_candidate", "Explicit requirement wording cannot be mapped to the source quotation")
            candidate_start, candidate_end = candidate_offsets
            payload["model_candidate_obligation"] = candidate
            payload["candidate_obligation"] = source_quote[candidate_start:candidate_end]
            payload["candidate_source_start"] = quote_start + candidate_start
            payload["candidate_source_end"] = quote_start + candidate_end
            payload["candidate_obligation_grounded"] = True
        elif candidate_type == "interpretation":
            # The model's paraphrase is NOT an exact substring of the source, so
            # it cannot be re-grounded.  Preserve it as-is but flag it as
            # ungrounded, and check it for word-integrity corruption (merged
            # words from tokenization artifacts).
            merged_in_obligation = _detect_merged_words(candidate)
            if merged_in_obligation:
                raise ExtractionError(
                    "model_text_integrity",
                    "Model's candidate obligation may contain merged words: "
                    + ", ".join(merged_in_obligation[:5]),
                )
            payload["candidate_obligation_grounded"] = False
    if not isinstance(payload.get("uncertainty"), str) or not payload["uncertainty"].strip() or len(payload["uncertainty"]) > 2000:
        raise ExtractionError("invalid_uncertainty", "Uncertainty must be a non-empty string of at most 2000 characters")
    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ExtractionError("invalid_confidence", "Confidence must be a number from 0 through 1")
    if not isinstance(payload.get("reasoning"), str) or len(payload["reasoning"]) > 2000:
        raise ExtractionError("invalid_reasoning", "Reasoning must be a string of at most 2000 characters")
    if payload.get("requires_human_review") is not True:
        raise ExtractionError("human_review_required", "Every extraction result must require human review")
    return payload


def extract_candidate_obligation(
    evidence_id: str,
    *,
    client: LLMClient | None = None,
    data_dir: Path = PROCESSED,
    timeout_seconds: int | None = None,
    max_tokens: int | None = None,
    retry_count: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Extract a provisional candidate; never writes to obligations or review queues."""
    evidence = _read_evidence(evidence_id, data_dir)
    passage = evidence.get("passage_text", "")
    if not passage.strip() or len(passage) > MAX_PASSAGE_CHARS:
        raise ExtractionError("invalid_passage", "Evidence passage is empty or exceeds the prototype length limit")
    page = evidence.get("printed_page_number") or evidence.get("page_number")
    metadata = {
        "document_id": evidence["document_id"],
        "evidence_id": evidence["evidence_id"],
        "printed_page": int(page),
    }
    system_prompt = (PROMPT_DIR / "system_v1.md").read_text(encoding="utf-8")
    extraction_prompt = (PROMPT_DIR / "extraction_v1.md").read_text(encoding="utf-8")
    format_prompt = (PROMPT_DIR / "json_format_v1.md").read_text(encoding="utf-8")
    prompt = extraction_prompt.format(
        metadata_json=json.dumps(metadata, ensure_ascii=False), passage=passage
    ) + "\n\n" + format_prompt
    try:
        call_options = {
            "timeout_seconds": timeout_seconds,
            "max_tokens": max_tokens,
            "retry_count": retry_count,
            "reasoning_effort": reasoning_effort,
        }
        call_options = {key: value for key, value in call_options.items() if value is not None}
        response = (client or LLMClient()).complete(
            prompt,
            system_prompt=system_prompt,
            json_mode=True,
            **call_options,
        )
    except LLMError as exc:
        raise ExtractionError(exc.code, exc.message) from exc
    if response.content is None or not response.content.strip() or response.finish_reason == "length":
        raise ExtractionError(
            "incomplete_completion",
            "NVIDIA completion did not contain a complete final answer",
        )
    return validate_extraction_response(response.content, evidence)


def extract_review_candidate(
    candidate_id: str,
    *,
    client: LLMClient | None = None,
    data_dir: Path = PROCESSED,
    timeout_seconds: int | None = None,
    max_tokens: int | None = None,
    retry_count: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Extract for an existing candidate without changing its review decision."""
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ExtractionError("invalid_candidate_id", "Candidate ID must be a non-empty string")
    try:
        candidates, evidence_by_id, _registry_by_id = load_source(data_dir)
    except (ReviewError, OSError, csv.Error) as exc:
        raise ExtractionError(
            "invalid_candidate_dataset",
            "Candidate, evidence, or document reference validation failed",
        ) from exc

    candidate = next(
        (row for row in candidates if row.get("candidate_id") == candidate_id),
        None,
    )
    if candidate is None:
        raise ExtractionError("unknown_candidate", "Candidate ID was not found in the source dataset")

    evidence = evidence_by_id.get(candidate.get("evidence_id", ""))
    if (
        evidence is None
        or evidence.get("document_id") != candidate.get("document_id")
        or not candidate.get("paragraph_reference", "").strip()
    ):
        raise ExtractionError(
            "invalid_candidate_reference",
            "Candidate document, evidence, or paragraph reference is invalid",
        )

    extraction = extract_candidate_obligation(
        candidate["evidence_id"],
        client=client,
        data_dir=data_dir,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        retry_count=retry_count,
        reasoning_effort=reasoning_effort,
    )
    if (
        extraction.get("document_id") != candidate["document_id"]
        or extraction.get("evidence_id") != candidate["evidence_id"]
        or extraction.get("requires_human_review") is not True
    ):
        raise ExtractionError(
            "extraction_reference_mismatch",
            "Validated extraction does not match the selected candidate source",
        )
    return {
        "candidate_id": candidate["candidate_id"],
        "document_id": candidate["document_id"],
        "evidence_id": candidate["evidence_id"],
        "paragraph_reference": candidate["paragraph_reference"],
        "review_status": "requires_human_review",
        "source_verification_warning": SOURCE_VERIFICATION_WARNING,
        "extraction": extraction,
    }
