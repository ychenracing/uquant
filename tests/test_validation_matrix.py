from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import unified_ai_quant.validation.robustness as robustness_module
import unified_ai_quant.validation.stress as stress_module
from unified_ai_quant.engine import ProductionEngine
from unified_ai_quant.validation.provenance import (
    assert_replay_signature_unchanged,
    bounded_data_fingerprint,
    validation_fingerprint,
)
from unified_ai_quant.validation.robustness import promotion_holdback_status
from unified_ai_quant.validation.runner import (
    _assert_evidence_inputs_unchanged,
    _evidence_input_hashes,
    _legacy_lookup,
    _order_ledger_audit,
    _primary_cell_comparison,
)
from unified_ai_quant.validation.stress import build_scenarios
from unified_ai_quant.validation.universes import (
    FIXED_POOL_SIZES,
    POOLS,
    PRIMARY_POOLS,
)


def test_fixed_primary_pool_sizes_match_acceptance_contract():
    assert tuple(sorted(len(symbols) for symbols in POOLS.values())) == (
        FIXED_POOL_SIZES
    )
    assert FIXED_POOL_SIZES == (1, 3, 5, 9, 15, 22, 32)
    assert tuple(sorted(len(symbols) for symbols in PRIMARY_POOLS.values())) == (
        3,
        5,
        9,
        15,
        22,
        32,
    )
    assert "single" not in PRIMARY_POOLS


@pytest.mark.parametrize(
    ("start", "end"),
    (("2018-01-02", "2018-12-28"), ("2022-01-04", "2022-12-30")),
)
def test_single_pool_historical_replay_handles_visibility_and_cohort_breadth(
    data_dir, start, end
):
    result = ProductionEngine(data_dir).backtest(
        symbols=POOLS["single"],
        start=start,
        end=end,
    )
    assert result["final_wealth"] > 0


def test_primary_cell_comparison_reports_each_noninferiority_metric():
    olds = {
        "qwenquant": {
            "final_wealth": 10.0,
            "max_drawdown": 0.20,
            "account_orders": 100,
        },
        "aquant": {
            "final_wealth": 9.0,
            "max_drawdown": 0.10,
            "account_orders": 90,
        },
        "trade": {
            "final_wealth": 8.0,
            "max_drawdown": 0.15,
            "account_orders": 80,
        },
    }
    boundary = _primary_cell_comparison(
        {
            "final_wealth": 9.9,
            "max_drawdown": 0.105,
            "account_orders": 84,
        },
        olds,
    )
    assert boundary["near_best_return"] is True
    assert boundary["near_best_dd"] is True
    assert boundary["near_best_orders"] is True
    assert boundary["allowed_orders"] == 84

    for field, value, failed_gate in (
        ("final_wealth", 9.899, "near_best_return"),
        ("max_drawdown", 0.10501, "near_best_dd"),
        ("account_orders", 85, "near_best_orders"),
    ):
        candidate = {
            "final_wealth": 9.9,
            "max_drawdown": 0.105,
            "account_orders": 84,
            field: value,
        }
        result = _primary_cell_comparison(candidate, olds)
        assert result[failed_gate] is False


def test_order_ledger_audit_requires_fill_to_order_linkage():
    row = {
        "account_orders": 1,
        "order_ledger": [
            {
                "order_id": "O1",
                "symbol": "sz300308",
                "side": "BUY",
                "status": "FILLED",
                "requested_shares": 200,
                "filled_shares": 200,
            }
        ],
        "fills": [
            {
                "order_id": "O1",
                "symbol": "sz300308",
                "side": "BUY",
                "shares": 200,
            }
        ],
    }
    assert _order_ledger_audit(row)["passed"] is True
    row["fills"][0]["order_id"] = "missing"
    assert _order_ledger_audit(row)["passed"] is False


def test_legacy_common_adapter_binds_frozen_sources_and_all_rows(data_dir):
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "benchmarks" / "legacy_common_adapter.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(_legacy_lookup(payload, data_dir)) == 189

    payload["source_hashes"]["trade"] = "tampered"
    with pytest.raises(RuntimeError, match="frozen-source hash mismatch"):
        _legacy_lookup(payload, data_dir)


def test_stress_matrix_contains_every_required_universe_structure(data_dir):
    scenarios = build_scenarios(data_dir)
    random_rows = [item for item in scenarios if item.scenario_type == "random_subset"]
    assert len(random_rows) == 900
    assert {len(item.symbols) for item in random_rows} == {3, 5, 9, 15, 22, 32}
    identifiers = {item.scenario_id for item in scenarios}
    for required in (
        "prefix-01",
        "structure-optical",
        "structure-equipment",
        "structure-materials",
        "structure-memory-compute",
        "structure-diversified",
        "structure-high-correlation",
        "structure-low-correlation",
        "structure-mature-heavy",
        "structure-emerging-heavy",
        "structure-loser-heavy",
        "permutation-primary-reversed",
    ):
        assert required in identifiers
    assert sum(item.scenario_type == "replace_one" for item in scenarios) == 5


def test_trade_common_stress_rejects_any_scenario_mismatch(
    data_dir, tmp_path, monkeypatch
):
    scenarios = stress_module.build_scenarios(data_dir)
    artifact = tmp_path / "trade_common_stress.json"
    adapter = tmp_path / "trade_adapter.py"
    common_adapter = tmp_path / "common_adapter.py"
    trade_source = tmp_path / "trade_source"
    trade_source.mkdir()
    adapter.write_text("# adapter\n", encoding="utf-8")
    common_adapter.write_text("# common adapter\n", encoding="utf-8")
    (trade_source / "engine.py").write_text("# frozen Trade\n", encoding="utf-8")
    monkeypatch.setattr(stress_module, "TRADE_COMMON_STRESS_PATH", artifact)
    monkeypatch.setattr(stress_module, "TRADE_COMMON_ADAPTER_PATH", adapter)
    monkeypatch.setattr(stress_module, "LEGACY_COMMON_ADAPTER_PATH", common_adapter)
    monkeypatch.setattr(stress_module, "TRADE_SOURCE_ROOT", trade_source)

    rows = [
        {
            "scenario_id": scenario.scenario_id,
            "scenario_type": scenario.scenario_type,
            "symbols": list(scenario.symbols),
            "symbol_count": len(scenario.symbols),
            "final_wealth": 1.0,
            "total_return": 0.0,
            "max_drawdown": 0.1,
            "account_orders": 0,
            "sleeve_fill_count": 0,
        }
        for scenario in scenarios
    ]
    payload = {
        "schema_version": 1,
        "formal_complete": True,
        "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
        "common_adapter_sha256": hashlib.sha256(
            common_adapter.read_bytes()
        ).hexdigest(),
        "trade_source_sha256": stress_module._python_source_fingerprint(
            trade_source
        ),
        "contract": {
            "initial_cash": 2_000_000.0,
            "start": stress_module.STRESS_START,
            "end": stress_module.STRESS_END,
            "signal": "close_t",
            "execution": "next_tradable_open",
            "account_orders": "unique executed fill_date/symbol/side",
        },
        "data_provenance": {
            "through": stress_module.STRESS_END,
            "sha256": bounded_data_fingerprint(
                data_dir, end=stress_module.STRESS_END
            ),
        },
        "scenario_sha256": stress_module.scenario_fingerprint(scenarios),
        "scenario_count": len(scenarios),
        "results": rows,
    }
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    _, validated = stress_module._load_trade_common_stress(data_dir, scenarios)
    assert len(validated) == len(scenarios)

    payload["results"][0]["symbols"] = ["tampered"]
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="scenario symbols mismatch"):
        stress_module._load_trade_common_stress(data_dir, scenarios)


def test_robustness_contract_is_unique_and_rejects_tampered_rows():
    experiments = robustness_module.build_experiments()
    assert len(experiments) == 184
    assert len({item.experiment_id for item in experiments}) == len(experiments)
    production = next(
        item for item in experiments if item.experiment_id == "production"
    )
    metrics = {
        "final_wealth": 1.1,
        "total_return": 0.1,
        "max_drawdown": 0.05,
        "account_orders": 2,
        "sharpe": 1.0,
        "calmar": 2.0,
        "worst_20d": -0.02,
        "worst_60d": -0.03,
        "pending_orders": 0,
    }
    rows = [
        {
            "experiment_id": production.experiment_id,
            "experiment_type": production.experiment_type,
            "changes": production.changes,
            "bull": dict(metrics),
            "through_july": dict(metrics),
            "choppy": dict(metrics),
        }
    ]
    validated = robustness_module._validate_experiment_results(
        rows, [production]
    )
    assert validated[0]["experiment_id"] == "production"

    rows[0]["changes"] = {"risk_off_gross": 0.01}
    with pytest.raises(ValueError, match="experiment changes mismatch"):
        robustness_module._validate_experiment_results(rows, [production])


def test_promotion_holdback_lock_preserves_sealed_or_consumed_contract(data_dir):
    root = Path(__file__).resolve().parents[1]
    lock = json.loads(
        (root / "benchmarks" / "PROMOTION_HOLDBACK.json").read_text(
            encoding="utf-8"
        )
    )
    status = promotion_holdback_status(data_dir)
    assert status["files"] == 36
    assert status["rows"] == 432
    assert status["expected_sessions"] == 12
    assert status["complete_coverage"] is True
    assert status["incomplete_files"] == []
    if lock["status"] == "SEALED_UNEVALUATED":
        assert status["candidate_hash_match"] is True
        assert status["untouched"] is True
        return

    assert lock["status"] in {"CONSUMED_PASS", "CONSUMED_FAIL"}
    assert status["untouched"] is False
    assert status["historical_evidence_intact"] is True
    assert status["consumed_result_hash_match"] is True
    assert status["consumed_contract_match"] is True
    result_path = root / "benchmarks" / "promotion_holdback_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert lock["status"] == f"CONSUMED_{result['status']}"
    assert hashlib.sha256(result_path.read_bytes()).hexdigest() == lock[
        "consumed_result_sha256"
    ]
    assert result["canonical_sha256"] == lock["canonical_sha256"]
    assert result["production_code_sha256"] == lock[
        "consumed_production_code_sha256"
    ]
    assert result["validation_code_sha256"] == lock[
        "consumed_validation_code_sha256"
    ]


def test_artifact_signatures_bind_validation_code(data_dir, monkeypatch):
    scenarios = stress_module.build_scenarios(data_dir)
    original = stress_module._signature(data_dir, scenarios)
    assert original["validation_code_sha256"] == validation_fingerprint()
    assert original["data_sha256"] == bounded_data_fingerprint(
        data_dir,
        end=stress_module.STRESS_END,
    )
    robust = robustness_module._signature(
        data_dir,
        robustness_module.build_experiments(),
    )
    assert robust["data_sha256"] == bounded_data_fingerprint(
        data_dir,
        end=robustness_module.THROUGH_JULY[1],
    )

    monkeypatch.setattr(stress_module, "validation_fingerprint", lambda: "changed")
    changed = stress_module._signature(data_dir, scenarios)
    assert changed != original


def test_long_replay_rejects_mixed_input_snapshots():
    initial = {
        "production_code_sha256": "before",
        "validation_code_sha256": "validation",
        "data_sha256": "data",
    }
    current = {**initial, "production_code_sha256": "after"}

    with pytest.raises(RuntimeError, match="refusing mixed-version evidence"):
        assert_replay_signature_unchanged(
            initial,
            current,
            replay="stress",
        )

    assert_replay_signature_unchanged(
        initial,
        dict(initial),
        replay="stress",
    )


def test_promotion_holdback_rejects_candidate_hash_mismatch(data_dir, monkeypatch):
    monkeypatch.setattr(robustness_module, "code_fingerprint", lambda: "changed")
    status = robustness_module.promotion_holdback_status(data_dir)
    assert status["candidate_hash_match"] is False
    assert status["untouched"] is False


def test_acceptance_rejects_inputs_changed_during_long_replay(data_dir):
    root = Path(__file__).resolve().parents[1]
    legacy_path = root / "benchmarks" / "legacy_common_adapter.json"
    expected = _evidence_input_hashes(root, data_dir, legacy_path)
    expected["production_code_sha256"] = "changed-after-workers-started"

    with pytest.raises(RuntimeError, match="mixed-version evidence"):
        _assert_evidence_inputs_unchanged(
            expected,
            root=root,
            data_dir=data_dir,
            legacy_path=legacy_path,
        )
