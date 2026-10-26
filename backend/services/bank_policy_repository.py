"""Local JSON repository for imported bank-policy records.

Imports are retained as unverified source claims. Importing a file never grants
approval or source verification. The repository intentionally starts empty.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
REPOSITORY_FILE = PROJECT / "data" / "bank_policies" / "policy_repository.json"
MAX_IMPORT_BYTES = 10 * 1024 * 1024
_PREVIEWS: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()


class BankPolicyRepositoryError(ValueError):
    """Safe message for invalid repository or import requests."""


def empty_repository() -> dict[str, Any]:
    return {"schema_version": 1, "policies": []}


def load_repository(path: Path = REPOSITORY_FILE) -> dict[str, Any]:
    if not path.exists():
        return empty_repository()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BankPolicyRepositoryError("The policy repository could not be read; preserve the file and repair its JSON before continuing.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("policies"), list):
        raise BankPolicyRepositoryError("The policy repository has an unsupported structure; no data was changed.")
    return payload


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _date(value: Any, field: str, errors: list[str], *, required: bool = False) -> str | None:
    text = _text(value)
    if not text:
        if required:
            errors.append(f"{field} is required and must use YYYY-MM-DD.")
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        errors.append(f"{field} must use a real YYYY-MM-DD date.")
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        errors.append(f"{field} must use a real YYYY-MM-DD date.")
        return None


def _approval_events(raw: Any, row: dict[str, Any], errors: list[str], row_ref: str) -> list[dict[str, Any]]:
    if raw not in (None, "") and not isinstance(raw, list):
        errors.append(f"{row_ref}: approval_details must be an array.")
        return []
    events = raw if isinstance(raw, list) else []
    flat = {
        "approval_reference": row.get("approval_reference"),
        "approver": row.get("approver"),
        "approval_date": row.get("approval_date"),
        "decision": row.get("approval_decision"),
    }
    if not events and any(_text(value) for value in flat.values()):
        events = [flat]
    result = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"{row_ref}: approval_details[{index}] must be an object.")
            continue
        decision = _text(event.get("decision")).casefold()
        if decision and decision not in {"approved", "rejected", "returned", "withdrawn"}:
            errors.append(f"{row_ref}: approval decision '{decision}' is unsupported.")
            continue
        event_date = _date(event.get("approval_date"), f"{row_ref} approval_date", errors)
        result.append({
            "approval_reference": _text(event.get("approval_reference")) or None,
            "approver": _text(event.get("approver")) or None,
            "approval_date": event_date,
            "decision": decision or None,
            "provenance_status": "unverified_import",
        })
    return result


def _flatten_json(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise BankPolicyRepositoryError("JSON import must be an object containing a 'policies' array.")
    policies = payload.get("policies")
    if not isinstance(policies, list):
        raise BankPolicyRepositoryError("JSON import must contain a 'policies' array.")
    rows = []
    for p_index, policy in enumerate(policies, start=1):
        if not isinstance(policy, dict):
            rows.append({"__row_error": f"policy {p_index} must be an object"})
            continue
        versions = policy.get("versions")
        if not isinstance(versions, list) or not versions:
            rows.append({"__row_error": f"policy {p_index} must contain at least one version"})
            continue
        for v_index, version in enumerate(versions, start=1):
            if not isinstance(version, dict):
                rows.append({"__row_error": f"policy {p_index} version {v_index} must be an object"})
                continue
            clauses = version.get("clauses")
            base = {
                "policy_id": policy.get("policy_id"),
                "policy_title": policy.get("policy_title"),
                "policy_category": policy.get("policy_category"),
                "owning_organization": policy.get("owning_organization"),
                "source_document_reference": policy.get("source_document_reference"),
                "policy_source_document_reference": policy.get("source_document_reference"),
                "version": version.get("version"),
                "effective_from": version.get("effective_from"),
                "effective_to": version.get("effective_to"),
                "status": version.get("status"),
                "approval_details": version.get("approval_details", []),
                "verification_status": version.get("verification_status") or policy.get("verification_status"),
            }
            if not isinstance(clauses, list) or not clauses:
                rows.append({**base, "__row_error": f"policy {p_index} version {v_index} must contain at least one clause"})
                continue
            for clause in clauses:
                if not isinstance(clause, dict):
                    rows.append({**base, "__row_error": f"policy {p_index} version {v_index} clause must be an object"})
                    continue
                row = dict(base)
                for field in ("clause_id", "clause_title", "clause_text", "section", "paragraph", "page", "executable_rule_definition"):
                    if field in clause:
                        row[field] = clause[field]
                row["clause_verification_status"] = clause.get("verification_status") or clause.get("clause_verification_status")
                row["source_document_reference"] = clause.get("source_document_reference") or base["source_document_reference"]
                rows.append(row)
    return rows


def _parse_import(file_name: str, file_bytes: bytes) -> tuple[list[dict[str, Any]], str, str]:
    if not file_bytes:
        raise BankPolicyRepositoryError("The import file is empty.")
    if len(file_bytes) > MAX_IMPORT_BYTES:
        raise BankPolicyRepositoryError("The import file exceeds the 10 MB limit.")
    suffix = Path(file_name.replace("\\", "/")).suffix.casefold()
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    safe_name = Path(file_name.replace("\\", "/")).name[:200] or "policy-import"
    try:
        decoded = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BankPolicyRepositoryError("Use a UTF-8 encoded CSV or JSON export.") from exc
    if suffix == ".json":
        try:
            return _flatten_json(json.loads(decoded)), safe_name, sha256
        except json.JSONDecodeError as exc:
            raise BankPolicyRepositoryError(f"JSON is malformed near line {exc.lineno}, column {exc.colno}.") from exc
    if suffix == ".csv":
        reader = csv.DictReader(io.StringIO(decoded, newline=""))
        if not reader.fieldnames:
            raise BankPolicyRepositoryError("CSV must have a header row.")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise BankPolicyRepositoryError("CSV header contains duplicate column names.")
        required = {"policy_id", "version", "status", "clause_id", "clause_text"}
        missing = sorted(required - set(reader.fieldnames))
        if missing:
            raise BankPolicyRepositoryError(f"CSV is missing required columns: {', '.join(missing)}.")
        return [dict(row) for row in reader], safe_name, sha256
    raise BankPolicyRepositoryError("Choose a .csv or .json policy export.")


def _normalise_rows(rows: list[dict[str, Any]], file_name: str, sha256: str, repository: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    normalized: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates = 0
    now = datetime.now(timezone.utc).isoformat()
    existing_policy = {p["policy_id"]: p for p in repository["policies"] if isinstance(p, dict) and p.get("policy_id")}
    existing_versions = {
        (p.get("policy_id"), v.get("version"))
        for p in repository["policies"] if isinstance(p, dict)
        for v in p.get("versions", []) if isinstance(v, dict)
    }
    seen_clause_ids: set[tuple[str, str, str]] = set()
    row_errors: dict[tuple[str, str], list[str]] = {}

    for index, raw in enumerate(rows, start=2):
        if raw.get("__row_error"):
            errors.append({"row": index, "type": "rejected", "message": raw["__row_error"]})
            continue
        policy_id = _text(raw.get("policy_id"))
        version = _text(raw.get("version"))
        clause_id = _text(raw.get("clause_id"))
        raw_clause_text = raw.get("clause_text")
        clause_text = raw_clause_text if isinstance(raw_clause_text, str) and raw_clause_text.strip() else ""
        key = (policy_id, version)
        row_ref = f"row {index}"
        structural = []
        if not policy_id:
            structural.append("policy_id is required")
        if not version:
            structural.append("version is required")
        if not clause_id:
            structural.append("clause_id is required")
        if not clause_text:
            structural.append("clause_text is required")
        status = _text(raw.get("status")).casefold()
        if status not in {"draft", "approved", "retired"}:
            structural.append("status must be draft, approved, or retired")
        if structural:
            errors.append({"row": index, "type": "rejected", "message": "; ".join(structural) + "."})
            if policy_id and version:
                row_errors.setdefault(key, []).extend(structural)
            continue

        existing = existing_policy.get(policy_id)
        title = _text(raw.get("policy_title")) or (existing or {}).get("policy_title") or ""
        category = _text(raw.get("policy_category")) or (existing or {}).get("policy_category") or ""
        unit = _text(raw.get("owning_organization")) or (existing or {}).get("owning_organization") or None
        source_ref = _text(raw.get("policy_source_document_reference")) or _text(raw.get("source_document_reference")) or None
        identity_errors = []
        if existing and (title and title != existing.get("policy_title") or category and category != existing.get("policy_category")):
            identity_errors.append("policy title/category differs from the existing stable policy identity")
        if identity_errors:
            errors.append({"row": index, "type": "rejected", "message": "; ".join(identity_errors) + "."})
            row_errors.setdefault(key, []).extend(identity_errors)
            continue

        duplicate_key = (policy_id, version, clause_id)
        if duplicate_key in seen_clause_ids:
            duplicates += 1
            errors.append({"row": index, "type": "duplicate", "message": "Duplicate clause_id within the same policy version."})
            row_errors.setdefault(key, []).append("duplicate clause identifier")
            continue
        seen_clause_ids.add(duplicate_key)
        if (policy_id, version) in existing_versions:
            duplicates += 1
            errors.append({"row": index, "type": "duplicate", "message": "Policy version already exists; historical versions are not overwritten."})
            row_errors.setdefault(key, []).append("policy version already exists")
            continue

        version_record = normalized.get(key)
        if version_record is None:
            incomplete_reasons = []
            if not title: incomplete_reasons.append("policy_title missing")
            if not category: incomplete_reasons.append("policy_category missing")
            if not _text(raw.get("effective_from")): incomplete_reasons.append("effective_from missing")
            if not source_ref: incomplete_reasons.append("source document reference missing")
            local_validation_errors: list[str] = []
            effective_from = _date(raw.get("effective_from"), f"{row_ref} effective_from", local_validation_errors)
            effective_to = _date(raw.get("effective_to"), f"{row_ref} effective_to", local_validation_errors)
            approval_details = _approval_events(raw.get("approval_details"), raw, local_validation_errors, row_ref)
            if local_validation_errors:
                row_errors.setdefault(key, []).append("invalid dates or malformed approval information")
                errors.extend({"row": index, "type": "rejected", "message": message} for message in local_validation_errors)
            version_record = {
                "version": version,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "status": status,
                "source_document_reference": source_ref,
                "approval_details": approval_details,
                "clauses": [],
                "provenance": {
                    "source_filename": file_name,
                    "source_sha256": sha256,
                    "imported_at": now,
                    "verification_status": "unverified_import",
            "source_claimed_verification_status": _text(raw.get("verification_status")) or None,
                },
                "record_created_at": now,
                "record_updated_at": now,
                "_incomplete_reasons": incomplete_reasons,
            }
            start = version_record["effective_from"]
            end = version_record["effective_to"]
            if start and end and end < start:
                errors.append({"row": index, "type": "rejected", "message": "effective_to must be on or after effective_from."})
                row_errors.setdefault(key, []).append("invalid effective date range")
            if status == "approved":
                approvals = version_record["approval_details"]
                if not any(a.get("decision") == "approved" and a.get("approver") and a.get("approval_date") and a.get("approval_reference") for a in approvals):
                    version_record["_incomplete_reasons"].append("approval information missing or incomplete")
            normalized[key] = {
                "policy_id": policy_id,
                "policy_title": title or None,
                "policy_category": category or None,
                "owning_organization": unit,
                "versions": [version_record],
            }
        else:
            inconsistent = []
            if version_record["status"] != status:
                inconsistent.append("status")
            if title and title != normalized[key]["policy_title"]:
                inconsistent.append("policy_title")
            if category and category != normalized[key]["policy_category"]:
                inconsistent.append("policy_category")
            if unit and unit != normalized[key]["owning_organization"]:
                inconsistent.append("owning_organization")
            for date_field in ("effective_from", "effective_to"):
                candidate_errors: list[str] = []
                candidate_date = _date(raw.get(date_field), f"{row_ref} {date_field}", candidate_errors)
                if candidate_errors or candidate_date != version_record.get(date_field):
                    inconsistent.append(date_field)
            approval_values = [raw.get(k) for k in ("approval_details", "approval_reference", "approver", "approval_date", "approval_decision")]
            if any(value not in (None, "", []) for value in approval_values):
                candidate_errors = []
                candidate_approvals = _approval_events(raw.get("approval_details"), raw, candidate_errors, row_ref)
                if candidate_errors or candidate_approvals != version_record.get("approval_details"):
                    inconsistent.append("approval_details")
            if inconsistent:
                errors.append({"row": index, "type": "rejected", "message": f"{', '.join(inconsistent)} must be consistent across rows for one policy version."})
                row_errors.setdefault(key, []).append("inconsistent policy version fields")
                continue

        clause_source = _text(raw.get("source_document_reference")) or source_ref
        if not clause_source:
            version_record["_incomplete_reasons"].append("clause source document reference missing")
        clause = {
            "clause_id": clause_id,
            "clause_title": _text(raw.get("clause_title")) or None,
            "clause_text": clause_text,
            "section": _text(raw.get("section")) or None,
            "paragraph": _text(raw.get("paragraph")) or None,
            "page": _text(raw.get("page")) or None,
            "source_document_reference": clause_source,
            "verification_status": "unverified_import",
            "source_claimed_verification_status": _text(raw.get("clause_verification_status")) or None,
            "executable_rule_definition_claim": raw.get("executable_rule_definition") if isinstance(raw.get("executable_rule_definition"), dict) else None,
            "executable_rule_verification_status": "unverified_import",
            "record_created_at": now,
            "record_updated_at": now,
        }
        version_record["clauses"].append(clause)

    accepted, incomplete, rejected = [], [], []
    for key, policy in normalized.items():
        if row_errors.get(key):
            rejected.append({"policy_id": key[0], "version": key[1], "errors": sorted(set(row_errors[key]))})
            continue
        version = policy["versions"][0]
        reasons = sorted(set(version.pop("_incomplete_reasons")))
        if reasons:
            version["import_completeness"] = {"status": "incomplete", "missing_information": reasons}
            incomplete.append(policy)
        else:
            version["import_completeness"] = {"status": "complete_fields_present", "missing_information": []}
            accepted.append(policy)

    return {
        "accepted": accepted,
        "incomplete": incomplete,
        "rejected": rejected,
        "errors": errors,
        "summary": {
            "accepted_policy_versions": len(accepted),
            "incomplete_policy_versions": len(incomplete),
            "rejected_policy_versions": len(rejected),
            "duplicate_records": duplicates,
            "accepted_clauses": sum(len(p["versions"][0]["clauses"]) for p in accepted),
            "incomplete_clauses": sum(len(p["versions"][0]["clauses"]) for p in incomplete),
            "accepted_records": sum(len(p["versions"][0]["clauses"]) for p in accepted),
            "incomplete_records": sum(len(p["versions"][0]["clauses"]) for p in incomplete),
            "rejected_records": sum(1 for item in errors if item.get("type") == "rejected"),
        },
    }


def prepare_import(file_name: str, file_bytes: bytes, path: Path = REPOSITORY_FILE) -> dict[str, Any]:
    rows, safe_name, sha256 = _parse_import(file_name, file_bytes)
    preview = _normalise_rows(rows, safe_name, sha256, load_repository(path))
    token = str(uuid.uuid4())
    with _LOCK:
        _PREVIEWS[token] = preview
        while len(_PREVIEWS) > 50:
            _PREVIEWS.pop(next(iter(_PREVIEWS)))
    return {
        "preview_id": token,
        "source_filename": safe_name,
        "source_sha256": sha256,
        "summary": preview["summary"],
        "errors": preview["errors"],
        "accepted": [{"policy_id": p["policy_id"], "policy_title": p["policy_title"], "version": p["versions"][0]["version"], "clause_count": len(p["versions"][0]["clauses"])} for p in preview["accepted"]],
        "incomplete": [{"policy_id": p["policy_id"], "policy_title": p["policy_title"], "version": p["versions"][0]["version"], "clause_count": len(p["versions"][0]["clauses"]), "missing_information": p["versions"][0]["import_completeness"]["missing_information"]} for p in preview["incomplete"]],
        "message": "Preview only: no policy records have been saved. Imported source and approval claims remain unverified.",
    }


def commit_import(preview_id: str, path: Path = REPOSITORY_FILE) -> dict[str, Any]:
    with _LOCK:
        preview = _PREVIEWS.get(preview_id)
    if preview is None:
        raise BankPolicyRepositoryError("This import preview has expired. Upload the source file again.")
    if not preview["accepted"] and not preview["incomplete"]:
        raise BankPolicyRepositoryError("There are no valid or incomplete records to save.")
    with _LOCK:
        repository = load_repository(path)
        by_id = {p["policy_id"]: p for p in repository["policies"]}
        existing_pairs = {(p.get("policy_id"), v.get("version")) for p in repository["policies"] for v in p.get("versions", [])}
        adding = preview["accepted"] + preview["incomplete"]
        for p in adding:
            version = p["versions"][0]
            pair = (p["policy_id"], version["version"])
            if pair in existing_pairs:
                raise BankPolicyRepositoryError("Repository changed after preview and this policy version now exists. Preview the source again; existing versions are never overwritten.")
            target = by_id.get(p["policy_id"])
            if target and any(target.get(field) and p.get(field) and target[field] != p[field] for field in ("policy_title", "policy_category")):
                raise BankPolicyRepositoryError("Repository changed after preview and this policy identity now conflicts. Preview the source again; no records were saved.")
        for p in adding:
            target = by_id.get(p["policy_id"])
            if target is None:
                target = {key: p[key] for key in ("policy_id", "policy_title", "policy_category", "owning_organization")}
                target["versions"] = []
                repository["policies"].append(target)
                by_id[p["policy_id"]] = target
            else:
                for field in ("policy_title", "policy_category", "owning_organization"):
                    if not target.get(field) and p.get(field):
                        target[field] = p[field]
            target["versions"].extend(p["versions"])
        _atomic_write(path, repository)
        _PREVIEWS.pop(preview_id, None)
    return {
        "saved": True,
        "summary": preview["summary"],
        "message": "Validated records were saved as imported, unverified policy data. No approval or source verification was granted.",
    }


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="policy-repository-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        raise BankPolicyRepositoryError("The repository could not be saved. No success was reported; check local file permissions and try again.") from exc
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def list_policy_records(path: Path = REPOSITORY_FILE) -> dict[str, Any]:
    repository = load_repository(path)
    policies = repository["policies"]
    versions = [v for p in policies for v in p.get("versions", [])]
    clauses = [c for v in versions for c in v.get("clauses", [])]
    return {
        "repository_status": "empty" if not policies else "populated_unverified",
        "policy_count": len(policies),
        "version_count": len(versions),
        "clause_count": len(clauses),
        "policies": policies,
        "message": "No bank policies have been imported yet. Internal policy comparison is unavailable." if not policies else "Imported policy records are visible below. Source provenance and approval claims require independent verification.",
    }


def policy_clause_matches(query: str, exact_text: str = "", path: Path = REPOSITORY_FILE) -> dict[str, Any]:
    words = set(re.findall(r"[a-z0-9]+", query.casefold()))
    stop = {"the", "and", "for", "from", "that", "this", "with", "shall", "must", "may", "will", "their", "under", "where", "such", "each", "into", "between", "have", "has", "are", "was", "were", "not", "any", "all", "bank", "banks"}
    words = {w for w in words if len(w) > 2 and w not in stop}
    records = []
    active = []
    today = date.today()
    for policy in load_repository(path)["policies"]:
        for version in policy.get("versions", []):
            start = date.fromisoformat(version["effective_from"]) if version.get("effective_from") else None
            end = date.fromisoformat(version["effective_to"]) if version.get("effective_to") else None
            approvals = version.get("approval_details", [])
            approval_complete = any(a.get("decision") == "approved" and a.get("approver") and a.get("approval_date") and a.get("approval_reference") and a.get("provenance_status") == "verified" for a in approvals)
            version_active = (
                version.get("status") == "approved" and start is not None and start <= today
                and (end is None or end >= today) and approval_complete
                and version.get("provenance", {}).get("verification_status") == "verified"
            )
            for clause in version.get("clauses", []):
                clause_words = set(re.findall(r"[a-z0-9]+", clause.get("clause_text", "").casefold()))
                clause_words = {w for w in clause_words if len(w) > 2 and w not in stop}
                shared = words & clause_words
                strength = len(shared) / max(1, min(len(words), len(clause_words)))
                exact = bool(exact_text.strip()) and re.sub(r"\s+", " ", exact_text).strip().casefold() == re.sub(r"\s+", " ", clause.get("clause_text", "")).strip().casefold()
                if not exact and (not words or strength < 0.35):
                    continue
                missing_information = list(version.get("import_completeness", {}).get("missing_information", []))
                if version.get("provenance", {}).get("verification_status") != "verified":
                    missing_information.append("policy source provenance is not independently verified")
                if not approvals:
                    missing_information.append("approval information not supplied")
                elif not approval_complete:
                    missing_information.append("approval details or their provenance are incomplete or unverified")
                if not start:
                    missing_information.append("effective-from date is missing")
                if not clause.get("source_document_reference"):
                    missing_information.append("clause source document reference is missing")
                record = {
                    "classification": "possible_duplicate_wording" if exact else "related_policy_clause",
                    "policy_id": policy["policy_id"],
                    "policy_version": version["version"],
                    "policy_title": policy.get("policy_title"),
                    "policy_category": policy.get("policy_category"),
                    "lifecycle_status": version.get("status"),
                    "effective_from": version.get("effective_from"),
                    "effective_to": version.get("effective_to"),
                    "approval_details": [{"approval_reference": a.get("approval_reference"), "approval_date": a.get("approval_date"), "decision": a.get("decision"), "approver_information_present": bool(a.get("approver")), "provenance_status": a.get("provenance_status")} for a in approvals],
                    "policy_verification_status": version.get("provenance", {}).get("verification_status", "unknown"),
                    "clause_id": clause["clause_id"],
                    "clause_text": clause["clause_text"],
                    "clause_title": clause.get("clause_title"),
                    "source_document_reference": clause.get("source_document_reference"),
                    "section": clause.get("section"),
                    "paragraph": clause.get("paragraph"),
                    "page": clause.get("page"),
                    "clause_verification_status": clause.get("verification_status", "unknown"),
                    "executable_rule_definition": clause.get("executable_rule_definition_claim") if clause.get("executable_rule_verification_status") == "verified" else None,
                    "executable_rule_verification_status": clause.get("executable_rule_verification_status", "unverified_import"),
                    "match_strength": round(strength, 3),
                    "match_basis": "exact normalized wording" if exact else "shared-token text screening",
                    "missing_information": sorted(set(missing_information)),
                    "comparison_eligibility": "eligible_approved_effective_verified" if version_active and clause.get("verification_status") == "verified" else "screening_only_unverified_or_inactive",
                    "explanation": "Text similarity is a review signal only; it does not establish equivalence or conflict.",
                }
                records.append(record)
                if version_active and clause.get("verification_status") == "verified" and clause.get("source_document_reference"):
                    active.append(record)
    records.sort(key=lambda x: (x["classification"] != "possible_duplicate_wording", -x["match_strength"]))
    return {"screening_matches": records[:20], "active_verified_matches": active[:20], "policy_version_count": sum(len(p.get("versions", [])) for p in load_repository(path)["policies"])}
