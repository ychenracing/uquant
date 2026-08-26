from __future__ import annotations

import json
from pathlib import Path

from research.strategic_evidence.contract import load_contract
from research.strategic_evidence.models import canonical_sha256

ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "benchmarks" / "strategic_evidence_closure_contract.json"


def test_contract_is_canonically_sealed_and_source_bound() -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert payload["payload_sha256"] == canonical_sha256(payload)
    assert payload["base_commit"] == "70d66b37edea3cd42ffb19c896b3f318e8bd536e"
    assert payload["window"] == {"start": "2023-01-03", "end": "2026-08-05"}
    assert payload["future_holdout_boundary"] == "2026-08-06"


def test_contract_freezes_every_required_matrix_axis() -> None:
    contract = load_contract(CONTRACT_PATH)

    assert len(contract.canonical_universe) == 34
    assert contract.positive_controls == (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688008",
        "sh603986",
    )
    assert contract.initial_state_ids == tuple(f"S{index:02d}" for index in range(1, 15))
    assert contract.path_ids == tuple(f"P{index:02d}" for index in range(1, 7))
    assert contract.random_seed == 20260826


def test_contract_keeps_failure_states_literal() -> None:
    contract = load_contract(CONTRACT_PATH)

    assert contract.replay_error_policy == "PRESERVE_AND_FAIL_REQUIRED_CELL"
    assert contract.insufficient_sample_policy == "PRESERVE_NULL_METRICS_AND_FAIL_ABSOLUTE_GATE"
    assert contract.absolute_thresholds["critical_removal"]["max_drawdown"] == 0.30
    assert contract.absolute_thresholds["canonical_leave_one_out"]["positive_return_fraction"] == 0.90
    assert contract.absolute_thresholds["reachability"]["terminal_scc_healthy_zero_target_limit"] == 60
