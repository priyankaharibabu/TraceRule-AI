"""Offline-safe Phase 4 candidate extraction over caller-supplied text only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


PROJECT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT / "backend" / "schemas" / "phase4_candidate.schema.json"
MAX_PASSAGE_CHARS = 12_000

REQUIRED_METADATA = {"document_id", "evidence_id", "page", "paragraph_reference"}
OUTPUT_FIELDS = {
    "document_id",
    "evidence_id",
    "page",
    "paragraph_reference",
    "potential_actor",
    "potential_action",
    "applicability",
    "conditions",
    "exceptions",
    "candidate_obligation",
    "supporting_quote",
    "uncertainty",
    "review_status",
    "provider_kind",
}
INTERPRETATION_FIELDS = {
    "potential_actor",
    "potential_action",
    "applicability",
    "conditions",
    "exceptions",
    "candidate_obligation",
}
MAX_FIELD_LENGTHS = {
    "potential_actor": 1_000,
    "potential_action": 2_000,
    "applicability": 2_000,
    "conditions": 2_000,
    "exceptions": 2_000,
    "candidate_obligation": 2_000,
    "supporting_quote": MAX_PASSAGE_CHARS,
    "uncertainty": 2_000,
}


class ExtractionError(ValueError):
    """Sanitized failure validating a provisional extraction."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ExtractionProviderError(Exception):
    """Provider failure whose safe details may be reported by the caller."""


class ExtractionProvider(Protocol):
    provider_kind: str

    def extract(self, passage: str, metadata: dict[str, Any]) -> str:
        """Return structured JSON for the supplied passage, without source lookup."""


def _validate_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ExtractionError("invalid_metadata", "Source metadata must be an object")
    missing = REQUIRED_METADATA.difference(metadata)
    unknown = set(metadata).difference(REQUIRED_METADATA)
    if missing:
        raise ExtractionError("missing_source_ids", "Source metadata is missing required identifiers")
    if unknown:
        raise ExtractionError("invalid_metadata", "Source metadata contains unsupported fields")
    for key in ("document_id", "evidence_id", "paragraph_reference"):
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise ExtractionError("missing_source_ids", f"{key} must be a non-empty source identifier")
    if isinstance(metadata["page"], bool) or not isinstance(metadata["page"], int) or metadata["page"] < 1:
        raise ExtractionError("invalid_metadata", "page must be a positive integer")
    return dict(metadata)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExtractionError("invalid_structure", "Provider response contains duplicate JSON fields")
        result[key] = value
    return result


def validate_candidate_response(
    raw_response: str,
    passage: str,
    metadata: dict[str, Any],
    *,
    provider_kind: str,
) -> dict[str, Any]:
    """Validate exact shape, supplied references, quote grounding, and review state."""
    metadata = _validate_metadata(metadata)
    if not isinstance(passage, str) or not passage.strip() or len(passage) > MAX_PASSAGE_CHARS:
        raise ExtractionError("invalid_passage", "Passage must be non-empty and within the local size limit")
    if provider_kind not in {"mock", "llm"}:
        raise ExtractionError("invalid_provider", "Provider kind is unsupported")
    try:
        payload = json.loads(raw_response, object_pairs_hook=_reject_duplicate_keys)
    except ExtractionError:
        raise
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ExtractionError("invalid_json", "Provider response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ExtractionError("invalid_structure", "Provider response must be a JSON object")
    if set(payload) != OUTPUT_FIELDS:
        raise ExtractionError("invalid_structure", "Provider response must contain exactly the supported fields")

    for key in ("document_id", "evidence_id", "page", "paragraph_reference"):
        if payload[key] != metadata[key]:
            raise ExtractionError("source_mismatch", f"Provider response {key} does not match supplied metadata")
    if isinstance(payload["page"], bool) or not isinstance(payload["page"], int):
        raise ExtractionError("invalid_structure", "page must be an integer")

    for field in INTERPRETATION_FIELDS:
        value = payload[field]
        if value is not None and (not isinstance(value, str) or len(value) > MAX_FIELD_LENGTHS[field]):
            raise ExtractionError("invalid_structure", f"{field} must be null or a bounded string")
    for field in ("supporting_quote", "uncertainty"):
        value = payload[field]
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD_LENGTHS[field]:
            raise ExtractionError("invalid_structure", f"{field} must be a non-empty bounded string")
    if payload["supporting_quote"] not in passage:
        raise ExtractionError("unsupported_quote", "Supporting quote is not an exact substring of the supplied passage")

    candidate = payload["candidate_obligation"]
    quote = payload["supporting_quote"]
    if candidate is not None and candidate not in quote:
        raise ExtractionError("unsupported_candidate", "Candidate wording is not an exact substring of its supporting quote")
    if payload["review_status"] != "requires_human_review":
        raise ExtractionError("human_review_required", "Extraction output cannot claim human verification or approval")
    if payload["provider_kind"] != provider_kind:
        raise ExtractionError("provider_mismatch", "Response provider kind does not match the configured provider")
    return payload


class OfflineCandidateExtractor:
    """Call an explicitly supplied provider and validate its provisional output.

    This service accepts no evidence ID lookup API and performs no writes. A
    provider must be supplied by the caller; there is no implicit network client.
    """

    def __init__(self, provider: ExtractionProvider):
        if not callable(getattr(provider, "extract", None)):
            raise TypeError("provider must implement extract(passage, metadata)")
        self.provider = provider

    def extract(self, passage: str, metadata: dict[str, Any]) -> dict[str, Any]:
        source_metadata = _validate_metadata(metadata)
        if not isinstance(passage, str) or not passage.strip() or len(passage) > MAX_PASSAGE_CHARS:
            raise ExtractionError("invalid_passage", "Passage must be non-empty and within the local size limit")
        provider_kind = getattr(self.provider, "provider_kind", None)
        if provider_kind not in {"mock", "llm"}:
            raise ExtractionError("invalid_provider", "Provider kind is unsupported")
        try:
            raw_response = self.provider.extract(passage, dict(source_metadata))
        except ExtractionError:
            raise
        except ExtractionProviderError as exc:
            raise ExtractionError("provider_error", "Extraction provider failed") from exc
        if not isinstance(raw_response, str):
            raise ExtractionError("invalid_structure", "Provider must return a JSON string")
        return validate_candidate_response(
            raw_response,
            passage,
            source_metadata,
            provider_kind=provider_kind,
        )


class DeterministicMockProvider:
    """Offline mock that echoes caller-provided fields; it does no legal analysis."""

    provider_kind = "mock"

    def __init__(
        self,
        *,
        potential_actor: str | None = None,
        potential_action: str | None = None,
        applicability: str | None = None,
        conditions: str | None = None,
        exceptions: str | None = None,
        candidate_obligation: str | None = None,
        supporting_quote: str | None = None,
        uncertainty: str = "MOCK ONLY: no model inference or legal assessment was performed.",
    ):
        self.fields = {
            "potential_actor": potential_actor,
            "potential_action": potential_action,
            "applicability": applicability,
            "conditions": conditions,
            "exceptions": exceptions,
            "candidate_obligation": candidate_obligation,
            "supporting_quote": supporting_quote,
            "uncertainty": uncertainty,
        }

    def extract(self, passage: str, metadata: dict[str, Any]) -> str:
        quote = self.fields["supporting_quote"] if self.fields["supporting_quote"] is not None else passage
        candidate = self.fields["candidate_obligation"]
        if candidate is None:
            candidate = quote
        response = {
            **metadata,
            **self.fields,
            "candidate_obligation": candidate,
            "supporting_quote": quote,
            "review_status": "requires_human_review",
            "provider_kind": self.provider_kind,
        }
        return json.dumps(response, ensure_ascii=False)


def load_output_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    """Load the published local JSON Schema for inspection/tooling."""
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)
