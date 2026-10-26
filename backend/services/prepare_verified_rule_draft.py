"""Validation-only helper: check whether a verified rule draft could be mapped under Option A.

Does not write rules, approve rules, or promote obligation candidates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.services.scope_governance import check_real_rule_mapping_eligibility, load_scope, summarize_scope_for_report
from backend.services.rule_validator import validate_rule

PROJECT = Path(__file__).resolve().parents[2]
DRAFTS_DIR = PROJECT / "data" / "processed" / "verified_rule_drafts"


def prepare_draft(rule: dict) -> tuple[dict, list[str]]:
    structural = validate_rule(rule)
    scope_errors = check_real_rule_mapping_eligibility(rule)
    all_errors = structural + scope_errors
    payload = {
        "scope_id": load_scope().scope_id,
        "rule_id": rule.get("rule_id"),
        "status": "draft",
        "approved_by": None,
        "mapping_eligible": not all_errors,
        "errors": all_errors,
        "note": "Draft validation only — not approved or activated.",
    }
    return payload, all_errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule-json", type=Path, help="Optional JSON file containing one rule object")
    parser.add_argument("--report", action="store_true", help="Print scope summary only")
    args = parser.parse_args()

    if args.report:
        print(json.dumps(summarize_scope_for_report(), indent=2))
        return 0

    if not args.rule_json:
        print(json.dumps(summarize_scope_for_report(), indent=2))
        print("\nNo --rule-json supplied; verified obligation count is 0 — real mapping blocked.")
        return 0

    rule = json.loads(args.rule_json.read_text(encoding="utf-8"))
    payload, errors = prepare_draft(rule)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFTS_DIR / f"{rule.get('rule_id', 'draft')}_eligibility.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
