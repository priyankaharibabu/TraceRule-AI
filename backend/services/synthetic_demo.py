"""Isolated fixture and runtime state for the offline synthetic demonstration."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.services.baseline_simulation import run_simulation
from backend.services.conflict_detector import detect_conflicts
from backend.services.governance import GovernanceError, RuleGovernance
from backend.services.offline_extraction import (
    DeterministicMockProvider,
    OfflineCandidateExtractor,
)
from backend.services.rule_engine import SYNTHETIC_BANNER, load_test_cases
from backend.services.rule_validator import validate_rule

PROJECT = Path(__file__).resolve().parents[2]
DEMO_DATA_DIR = PROJECT / "data" / "demo"
DEMO_FIXTURE_FILE = DEMO_DATA_DIR / "synthetic_demo_fixture.json"
DEMO_STATE_FILE = "synthetic_demo_state.json"
PROCESSED_DATA_DIR = PROJECT / "data" / "processed"
SYNTHETIC_CASES_FILE = PROJECT / "data" / "synthetic" / "synthetic_test_cases.json"
DEMO_STATE_VERSION = "1.0.0"


class SyntheticDemoError(ValueError):
    """Raised when a demo fixture or state boundary is invalid."""


def _canonical_demo_directory() -> Path:
    """Resolve the repository demo directory and reject symlink redirection."""
    try:
        project_root = PROJECT.resolve(strict=True)
        data_directory = project_root / "data"
        if data_directory.resolve(strict=True) != data_directory:
            raise SyntheticDemoError("Project data directory must not be a symlink")
        demo_directory = data_directory / "demo"
        if demo_directory.resolve(strict=False) != demo_directory:
            raise SyntheticDemoError("Project demo directory must not be a symlink")
    except (OSError, RuntimeError, ValueError) as exc:
        raise SyntheticDemoError("Canonical project demo directory could not be resolved safely") from exc
    return demo_directory


def load_demo_fixture(path: Path = DEMO_FIXTURE_FILE) -> dict[str, Any]:
    """Load and validate the isolated synthetic fixture."""
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyntheticDemoError("Synthetic demo fixture could not be loaded") from exc
    if not isinstance(fixture, dict) or fixture.get("label") != SYNTHETIC_BANNER:
        raise SyntheticDemoError("Synthetic demo fixture must carry the synthetic-data label")
    if not isinstance(fixture.get("demo_id"), str) or not fixture["demo_id"].startswith("OFFLINE-SYNTHETIC-"):
        raise SyntheticDemoError("Synthetic demo fixture has an invalid demo ID")

    candidates = fixture.get("candidate_inputs")
    if not isinstance(candidates, list) or not candidates:
        raise SyntheticDemoError("Synthetic demo fixture must contain candidate inputs")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise SyntheticDemoError("Synthetic demo candidate input must be an object")
        if not str(candidate.get("candidate_id", "")).startswith("DEMO-"):
            raise SyntheticDemoError("Synthetic demo candidate IDs must use the DEMO- namespace")
        metadata = candidate.get("metadata")
        if not isinstance(metadata, dict) or any(
            not str(metadata.get(field, "")).startswith("DEMO-")
            for field in ("document_id", "evidence_id", "paragraph_reference")
        ):
            raise SyntheticDemoError("Synthetic demo source references must use the DEMO- namespace")

    scenarios = fixture.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise SyntheticDemoError("Synthetic demo fixture must contain rule scenarios")
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not str(scenario.get("scenario_id", "")).startswith("DEMO-"):
            raise SyntheticDemoError("Synthetic demo scenarios must use the DEMO- namespace")
        proposed_rule = scenario.get("proposed_rule")
        rules = [*scenario.get("baseline_rules", []), proposed_rule]
        if not isinstance(proposed_rule, dict):
            raise SyntheticDemoError("Synthetic demo scenario requires a proposed rule")
        for rule in rules:
            if not isinstance(rule, dict) or not str(rule.get("rule_id", "")).startswith("DEMO-"):
                raise SyntheticDemoError("Synthetic demo rule IDs must use the DEMO- namespace")
            if (rule.get("source_obligation") or {}).get("dataset_kind") != "synthetic":
                raise SyntheticDemoError("Synthetic demo rules must have dataset_kind=synthetic")
            if rule.get("status") == "approved" or rule.get("approved_by"):
                raise SyntheticDemoError("Synthetic demo fixture cannot contain approved rules")
            if rule_errors := validate_rule(rule):
                raise SyntheticDemoError(f"Synthetic demo rule failed schema validation: {rule_errors}")
    return fixture


def _build_demo_candidates(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for item in fixture["candidate_inputs"]:
        provider = DeterministicMockProvider(**item["mock_fields"])
        extracted = OfflineCandidateExtractor(provider).extract(item["passage"], item["metadata"])
        candidates.append({
            "candidate_id": item["candidate_id"],
            **extracted,
            "label": SYNTHETIC_BANNER,
            "dataset_kind": "synthetic",
            "review_status": "pending_human_review",
            "verification_status": "unverified_candidate",
            "activation_status": "inactive",
        })
    return candidates


class SyntheticDemoStore:
    """Read/initialize demo state under data/demo, never under data/processed."""

    def __init__(
        self,
        demo_dir: Path = DEMO_DATA_DIR,
        fixture_path: Path = DEMO_FIXTURE_FILE,
    ) -> None:
        canonical_demo_dir = _canonical_demo_directory()
        try:
            supplied_path = Path(demo_dir).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SyntheticDemoError("Supplied demo directory could not be resolved safely") from exc
        if supplied_path != canonical_demo_dir:
            raise SyntheticDemoError("Demo state must be stored in the project's canonical data/demo directory")
        self.demo_dir = canonical_demo_dir
        self.fixture_path = Path(fixture_path)
        self.state_path = self.demo_dir / DEMO_STATE_FILE

    def initialize(self) -> dict[str, Any]:
        """Create or reset isolated demo state from the checked-in fixture."""
        fixture = load_demo_fixture(self.fixture_path)
        state = {
            "label": SYNTHETIC_BANNER,
            "demo_id": fixture["demo_id"],
            "state_version": DEMO_STATE_VERSION,
            "provider_kind": "mock",
            "candidates": _build_demo_candidates(fixture),
            "scenarios": [
                {
                    "scenario_id": scenario["scenario_id"],
                    "rule_status": scenario["proposed_rule"]["status"],
                    "approval_status": "not_requested",
                    "activation_status": "inactive",
                }
                for scenario in fixture["scenarios"]
            ],
        }
        self.demo_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="synthetic-demo-",
            suffix=".tmp",
            dir=self.demo_dir,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            os.replace(temporary_path, self.state_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return state

    def read(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SyntheticDemoError("Synthetic demo state could not be loaded") from exc
        if not isinstance(state, dict) or state.get("label") != SYNTHETIC_BANNER:
            raise SyntheticDemoError("Synthetic demo state is missing its synthetic-data label")
        return state


def assess_demo_scenario(
    scenario_id: str,
    *,
    demo_dir: Path = DEMO_DATA_DIR,
    fixture_path: Path = DEMO_FIXTURE_FILE,
    cases_path: Path = SYNTHETIC_CASES_FILE,
) -> dict[str, Any]:
    """Run existing validation, conflict, simulation, and approval gates without approval."""
    fixture = load_demo_fixture(fixture_path)
    scenario = next(
        (item for item in fixture["scenarios"] if item["scenario_id"] == scenario_id),
        None,
    )
    if scenario is None:
        raise SyntheticDemoError(f"Unknown synthetic demo scenario: {scenario_id}")

    baseline_rules = deepcopy(scenario["baseline_rules"])
    proposed_rule = deepcopy(scenario["proposed_rule"])
    proposed_rules = [*baseline_rules, proposed_rule]
    validation_errors = [
        {"rule_id": rule["rule_id"], "errors": validate_rule(rule)}
        for rule in proposed_rules
        if validate_rule(rule)
    ]
    findings = detect_conflicts(proposed_rules)
    cases_payload = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    test_cases = load_test_cases(Path(cases_path))
    simulation = run_simulation(
        baseline_rules,
        proposed_rules,
        test_cases,
        baseline_version="demo-baseline-1.0.0",
        proposed_version=str(proposed_rule["rule_version"]),
        cases_version=str(cases_payload.get("dataset_version", "unknown")),
        seed=cases_payload.get("seed"),
    )
    try:
        RuleGovernance(data_dir=Path(demo_dir))._check_approval_gates(
            proposed_rule,
            proposed_rules,
        )
        approval_gate = {"status": "passed", "error_code": None}
    except GovernanceError as exc:
        approval_gate = {"status": "blocked", "error_code": exc.code}

    return {
        "label": SYNTHETIC_BANNER,
        "scenario_id": scenario_id,
        "validation_errors": validation_errors,
        "conflicts": [
            {
                "rule_ids": finding.rule_ids,
                "finding_type": finding.finding_type,
                "severity": finding.severity,
                "overlap_established": finding.overlap_established,
                "requires_human_review": finding.requires_human_review,
            }
            for finding in findings
        ],
        "simulation": simulation,
        "approval_gate": approval_gate,
        "approval_status": "not_performed",
        "rule_status": proposed_rule["status"],
        "activation_status": "inactive",
    }
