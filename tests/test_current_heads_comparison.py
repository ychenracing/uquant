from __future__ import annotations

import json
from pathlib import Path

import pytest

from uquant.validation.ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS
from uquant.validation.competitor import CANONICAL_EXECUTION_CONTRACT
from uquant.validation.current_heads import (
    MATRIX_STATUSES,
    REQUIRED_METRICS,
    REQUIRED_SYSTEMS,
    canonical_sha256,
    load_comparison_contract,
    load_source_registry,
    python_source_sha256,
)

ROOT = Path(__file__).resolve().parents[1]


def test_current_heads_contract_freezes_every_shared_comparison_axis() -> None:
    contract = load_comparison_contract(ROOT / "benchmarks/current_heads_comparison_contract.json")

    assert tuple(contract["systems"]) == REQUIRED_SYSTEMS
    assert contract["market"] == "A-share AI supply chain"
    assert contract["execution_contract"] == {
        **CANONICAL_EXECUTION_CONTRACT.to_payload(),
        "stock_adjustment": "qfq",
        "index_adjustment": "raw",
        "position_direction": "cash_long_only",
        "star_board_rules": True,
        "price_limits": True,
        "capacity": True,
    }
    assert contract["windows"] == {
        name: {
            "start": bounds[0],
            "end": bounds[1],
            "acute_start": AI_ERA_ACUTE_WINDOWS[name][0],
            "acute_end": AI_ERA_ACUTE_WINDOWS[name][1],
        }
        for name, bounds in AI_ERA_WINDOWS.items()
    }
    assert tuple(contract["official_pools"]) == ("a", "b", "c", "d", "e")
    assert contract["generalization"]["records_per_window"] == 39
    assert contract["generalization"]["random_pool_sizes"] == [5, 9, 15, 20]
    assert contract["generalization"]["random_seed_indexes"] == [0, 1, 2, 3, 4]
    assert tuple(contract["metrics"]) == REQUIRED_METRICS
    assert tuple(contract["statuses"]) == MATRIX_STATUSES
    assert contract["expected_cells"] == {
        "official_pool": 120,
        "generalization": 936,
        "total": 1056,
    }


def test_contract_payload_hash_fails_closed_after_any_edit(tmp_path: Path) -> None:
    source = ROOT / "benchmarks/current_heads_comparison_contract.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["execution_contract"]["one_way_slippage"] = 0.0
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="payload SHA-256 mismatch"):
        load_comparison_contract(changed)


def test_python_source_hash_is_stable_by_relative_path_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "b.py").write_text("B = 2\n", encoding="utf-8")
    (first / "a.py").write_text("A = 1\n", encoding="utf-8")
    (second / "a.py").write_text("A = 1\n", encoding="utf-8")
    (second / "b.py").write_text("B = 2\n", encoding="utf-8")

    assert python_source_sha256(first) == python_source_sha256(second)
    (second / "b.py").write_text("B = 3\n", encoding="utf-8")
    assert python_source_sha256(first) != python_source_sha256(second)


def test_source_registry_binds_all_four_remote_heads_and_adapter() -> None:
    registry = load_source_registry(
        ROOT / "benchmarks/current_heads_source_registry.json",
        adapter_path=ROOT / "scripts/run_current_heads_competitor_matrix.py",
        expected_heads={
            "uquant": "ea24f1837f8b7f2d91e73a5d3c70875f2ea98015",
            "trade": "2066fbf0f99be94142c5d0cb0b6c99d276c2472d",
            "qwenquant": "63e05fe7adc2eae67d78e2cfca6222f88e041d89",
            "aquant": "55009a628515a0d612034c132bc90d21cf720c25",
        },
    )

    assert tuple(registry["systems"]) == REQUIRED_SYSTEMS
    assert all(registry["repositories"][name]["read_only"] for name in REQUIRED_SYSTEMS)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("commit", "short", "commit must be a 40-character SHA"),
        ("tree_sha", "short", "tree_sha must be a 40-character SHA"),
        ("python_source_sha256", "short", "python_source_sha256 must be SHA-256"),
        ("lock_sha256", "short", "lock_sha256 must be SHA-256"),
    ),
)
def test_source_registry_rejects_missing_or_malformed_identity(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    source = ROOT / "benchmarks/current_heads_source_registry.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["repositories"]["trade"][field] = value
    body = {key: item for key, item in payload.items() if key != "payload_sha256"}
    payload["payload_sha256"] = canonical_sha256(body)
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_source_registry(changed)


def test_source_registry_rejects_a_different_claimed_remote_head(tmp_path: Path) -> None:
    source = ROOT / "benchmarks/current_heads_source_registry.json"

    with pytest.raises(ValueError, match="trade remote HEAD mismatch"):
        load_source_registry(
            source,
            expected_heads={
                "uquant": "ea24f1837f8b7f2d91e73a5d3c70875f2ea98015",
                "trade": "0" * 40,
                "qwenquant": "63e05fe7adc2eae67d78e2cfca6222f88e041d89",
                "aquant": "55009a628515a0d612034c132bc90d21cf720c25",
            },
        )
