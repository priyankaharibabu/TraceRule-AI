"""Baseline versus proposed rule-set simulation with reproducible outputs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.services.rule_engine import SYNTHETIC_BANNER, evaluate_case, load_rules, load_test_cases

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = PROJECT / "data" / "synthetic" / "synthetic_rules_baseline.json"
DEFAULT_PROPOSED = PROJECT / "data" / "synthetic" / "synthetic_rules_proposed.json"
DEFAULT_CASES = PROJECT / "data" / "synthetic" / "synthetic_test_cases.json"
DEFAULT_OUTPUT = PROJECT / "data" / "processed" / "simulation_baseline_vs_proposed.json"


def _outcome_signature(result: dict[str, Any]) -> tuple[Any, ...]:
    fired = tuple(
        sorted(
            (
                item.get("rule_id"),
                item.get("action_type"),
                json.dumps(item.get("parameters", {}), sort_keys=True),
            )
            for item in result.get("fired_actions", [])
        )
    )
    errors = tuple(sorted(result.get("validation_errors", [])))
    return fired, errors


def run_simulation(
    baseline_rules: list[dict[str, Any]],
    proposed_rules: list[dict[str, Any]],
    test_cases: list[dict[str, Any]],
    *,
    baseline_version: str,
    proposed_version: str,
    cases_version: str,
    seed: int | None,
) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    changed = 0
    for case in test_cases:
        case_id = case["case_id"]
        inputs = case.get("inputs", {})
        purpose = case.get("purpose", "")
        expected = case.get("expected", {})

        baseline = evaluate_case(case_id, inputs, baseline_rules)
        proposed = evaluate_case(case_id, inputs, proposed_rules)
        baseline_payload = {
            "fired_actions": baseline.fired_actions,
            "validation_errors": baseline.validation_errors,
        }
        proposed_payload = {
            "fired_actions": proposed.fired_actions,
            "validation_errors": proposed.validation_errors,
        }
        old_sig = _outcome_signature(baseline_payload)
        new_sig = _outcome_signature(proposed_payload)
        outcome_changed = old_sig != new_sig
        if outcome_changed:
            changed += 1
        comparisons.append(
            {
                "case_id": case_id,
                "purpose": purpose,
                "expected": expected,
                "inputs": inputs,
                "baseline": baseline_payload,
                "proposed": proposed_payload,
                "outcome_changed": outcome_changed,
                "change_reason": _explain_change(baseline, proposed) if outcome_changed else "",
            }
        )

    return {
        "label": SYNTHETIC_BANNER,
        "simulation_type": "baseline_vs_proposed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_rule_set_version": baseline_version,
        "proposed_rule_set_version": proposed_version,
        "test_case_dataset_version": cases_version,
        "random_seed": seed,
        "case_count": len(test_cases),
        "changed_outcomes": changed,
        "comparisons": comparisons,
        "notes": "Synthetic baseline — not a production banking system.",
    }


def _explain_change(baseline, proposed) -> str:
    baseline_ids = {item["rule_id"] for item in baseline.fired_actions}
    proposed_ids = {item["rule_id"] for item in proposed.fired_actions}
    added = sorted(proposed_ids - baseline_ids)
    removed = sorted(baseline_ids - proposed_ids)
    parts: list[str] = []
    if added:
        parts.append(f"rules newly firing: {', '.join(added)}")
    if removed:
        parts.append(f"rules no longer firing: {', '.join(removed)}")
    if baseline.validation_errors != proposed.validation_errors:
        parts.append("validation error set changed")
    return "; ".join(parts) if parts else "action parameters or ordering changed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--proposed", type=Path, default=DEFAULT_PROPOSED)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    baseline_payload = json.loads(args.baseline.read_text(encoding="utf-8"))
    proposed_payload = json.loads(args.proposed.read_text(encoding="utf-8"))
    cases_payload = json.loads(args.cases.read_text(encoding="utf-8"))

    report = run_simulation(
        load_rules(args.baseline),
        load_rules(args.proposed),
        load_test_cases(args.cases),
        baseline_version=str(baseline_payload.get("ruleset_version", "unknown")),
        proposed_version=str(proposed_payload.get("ruleset_version", "unknown")),
        cases_version=str(cases_payload.get("dataset_version", "unknown")),
        seed=cases_payload.get("seed"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote simulation report to {args.output}")
    print(f"Changed outcomes: {report['changed_outcomes']} / {report['case_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
