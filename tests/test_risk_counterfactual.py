from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from research.risk_counterfactual import (
    NEGATIVE_CONTROL_IDS,
    POLICY_SET,
    clamp_pyramid_targets,
    classify_promotion,
    effective_shadow_cap,
    execution_day,
    layered_protection_line,
    rebuild_shadow_orders,
    wilder_atr,
)
from research.risk_differential_models import canonical_sha256
from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Position, Target

_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "risk_counterfactual_runner_under_test",
    Path(__file__).parents[1] / "scripts/run_risk_counterfactual.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(_SCRIPT)
_layered_targets = _SCRIPT._layered_targets

_ANALYZER_SPEC = importlib.util.spec_from_file_location(
    "risk_differential_analyzer_under_test",
    Path(__file__).parents[1] / "scripts/analyze_risk_differential.py",
)
assert _ANALYZER_SPEC is not None and _ANALYZER_SPEC.loader is not None
_ANALYZER = importlib.util.module_from_spec(_ANALYZER_SPEC)
_ANALYZER_SPEC.loader.exec_module(_ANALYZER)


def _target(symbol: str, weight: float) -> Target:
    return Target(symbol, weight, "ADD1", 1.0, 1.0, "fixture")


def test_trade_gross_cap_never_relaxes_base_cap() -> None:
    assert effective_shadow_cap(0.5, 0.7) == 0.5
    assert effective_shadow_cap(1.0, 0.7) == 0.7


def test_layered_stop_uses_next_open_execution() -> None:
    calendar = ("2026-08-03", "2026-08-04", "2026-08-05")
    assert execution_day("2026-08-04", calendar) == "2026-08-05"


def test_pyramid_freeze_clamps_add_without_blocking_independent_exit() -> None:
    targets = (_target("sz000001", 0.5), _target("sz000002", 0.1))
    result = clamp_pyramid_targets(targets, {"sz000001": 0.3, "sz000002": 0.2})
    assert [item.weight for item in result] == [0.3, 0.1]


def test_shadow_runner_does_not_mutate_baseline_account() -> None:
    baseline = AccountState.empty(2_000_000.0)
    before = baseline.to_dict()
    shadow = deepcopy(baseline)
    rebuild_shadow_orders(
        account=shadow,
        previous_account=deepcopy(shadow),
        signal_date="2026-08-24",
        targets=(),
        prices={},
        cfg=DEFAULT_CONFIG,
    )
    assert baseline.to_dict() == before


def test_entry_freeze_does_not_sell_incumbent_for_blocked_replacement() -> None:
    account = AccountState.empty(1_000_000.0)
    account.positions["sz000001"] = Position(
        symbol="sz000001", shares=10_000, avg_cost=10.0, highest_close=10.0
    )
    frozen = PortfolioAllocator._frozen_existing_targets(
        strategy_targets=(_target("sz000002", 0.2),),
        leaders={},
        account=account,
        weights_now={"sz000001": 0.1},
    )
    assert [(item.symbol, item.weight) for item in frozen] == [("sz000001", 0.1)]


def test_layered_lines_are_independently_armed() -> None:
    clean, clean_kind = layered_protection_line(
        entry=100, peak_close=150, atr=2, risk_level=0, account_drawdown=0.0
    )
    warned, warned_kind = layered_protection_line(
        entry=100, peak_close=150, atr=2, risk_level=2, account_drawdown=0.08
    )
    assert (clean, clean_kind) == (108.0, "catastrophe_stop")
    assert warned > clean
    assert warned_kind in {"atr_stop", "profit_tier_stop"}


def test_wilder_atr_matches_pinned_fixture() -> None:
    highs = (10.0, 12.0, 13.0, 15.0)
    lows = (8.0, 9.0, 10.0, 11.0)
    closes = (9.0, 11.0, 12.0, 14.0)
    assert wilder_atr(highs, lows, closes, period=3) == pytest.approx(3.111111111111111)


def test_negative_controls_and_hybrid_can_never_promote() -> None:
    metrics = {
        "sample_pass": True,
        "detection_pass": True,
        "economic_pass": True,
        "generalization_pass": True,
    }
    for candidate in NEGATIVE_CONTROL_IDS:
        assert classify_promotion(candidate, "NEGATIVE_CONTROL", metrics) != "PROMOTION_CANDIDATE"
    assert classify_promotion("cluster", "HYBRID_DIAGNOSTIC", metrics) == "HYBRID_DIAGNOSTIC_ONLY"


def test_unproven_action_translations_are_not_labeled_exact_or_promotable() -> None:
    policies = {item.policy_id: item for item in POLICY_SET}
    for policy_id in ("trade_gross_cap_shadow", "trade_layered_protection_shadow"):
        assert policies[policy_id].transfer_kind == "TRANSLATED_SHADOW"
        assert (
            classify_promotion(
                policy_id,
                policies[policy_id].transfer_kind,
                {
                    "sample_pass": True,
                    "detection_pass": True,
                    "economic_pass": True,
                    "generalization_pass": True,
                    "causal_validity_pass": False,
                },
            )
            != "PROMOTION_CANDIDATE"
        )


def test_axis_calibration_does_not_substitute_global_warning_signals() -> None:
    start = date(2026, 1, 1)
    days = []
    for index in range(45):
        warning = index in {0, 10}
        days.append(
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "portfolio_equity": 1.0 + index / 1000,
                "trade": {
                    "severity_rank": 1 if warning else 0,
                    "block_new_entries": index == 0,
                    "block_pyramiding": False,
                    "recommended_gross_cap": 0.85 if warning else 1.0,
                },
            }
        )
    result = _ANALYZER._calibration(
        [{"status": "SUCCESS", "days": days}], "trade", axis="block_new_entries"
    )
    assert result["axis"] == "block_new_entries"
    assert result["warning_episode_count"] == 1


def test_detection_gate_emits_each_axis_specific_metric_and_reason() -> None:
    exclusive = {"precision": 0.60, "false_positive_opportunity_cost": 0.004}
    candidate_axis = {
        "evaluable": True,
        "recall": 0.76,
        "median_lead_time": 3.0,
        "bull_silence_rate": 0.91,
        "missed_shock_count": 3,
        "missed_shock_depth": -0.09,
    }
    base_axis = {
        "evaluable": True,
        "precision": 0.54,
        "recall": 0.80,
        "median_lead_time": 2.0,
        "false_positive_opportunity_cost": 0.0,
        "bull_silence_rate": 0.92,
        "missed_shock_count": 2,
        "missed_shock_depth": -0.08,
    }
    result = _ANALYZER._detection_gate_details(exclusive, candidate_axis, base_axis)
    assert result["passed"] is True
    assert result["precision_pass"] is True
    assert result["lead_time_pass"] is True
    assert result["recall_pass"] is True
    assert result["opportunity_cost_pass"] is True
    assert result["bull_silence_pass"] is True
    assert result["missed_shock_count"] == 3
    assert result["missed_shock_depth"] == pytest.approx(-0.09)
    assert set(result["reasons"]) == {
        "precision",
        "lead_time",
        "recall",
        "opportunity_cost",
        "bull_silence",
        "missed_shocks",
    }


def test_counterfactual_deltas_are_positive_only_for_improvement() -> None:
    raw = {
        "payload_sha256": "f" * 64,
        "cells": [
            {
                "cell_id": "official_pool/h1/a",
                "matrix_axis": "official_pool",
                "policy_id": "baseline_uquant",
                "final_wealth": 1.0,
                "max_drawdown": 0.20,
                "acute_return": -0.10,
                "account_orders": 10,
                "gross_turnover": 1.0,
                "trigger_count": 0,
            },
            {
                "cell_id": "official_pool/h1/a",
                "matrix_axis": "official_pool",
                "policy_id": "candidate",
                "final_wealth": 1.0,
                "max_drawdown": 0.15,
                "acute_return": -0.05,
                "account_orders": 10,
                "gross_turnover": 1.0,
                "trigger_count": 1,
            },
        ],
    }
    _, aggregate = _ANALYZER._counterfactual_summary(raw)
    assert aggregate["candidate"]["median_mdd_delta"] == pytest.approx(0.05)
    assert aggregate["candidate"]["best_acute_return_delta"] == pytest.approx(0.05)


def test_economic_gate_requires_real_risk_cell_mdd_improvement() -> None:
    raw = {
        "payload_sha256": "f" * 64,
        "cells": [
            {
                "cell_id": "official_pool/quiet/a",
                "matrix_axis": "official_pool",
                "policy_id": "baseline_uquant",
                "final_wealth": 1.0,
                "max_drawdown": 0.04,
                "acute_return": -0.02,
                "account_orders": 10,
                "gross_turnover": 1.0,
                "trigger_count": 0,
            },
            {
                "cell_id": "official_pool/quiet/a",
                "matrix_axis": "official_pool",
                "policy_id": "candidate",
                "final_wealth": 1.0,
                "max_drawdown": 0.03,
                "acute_return": 0.02,
                "account_orders": 10,
                "gross_turnover": 1.0,
                "trigger_count": 1,
            },
        ],
    }
    _, aggregate = _ANALYZER._counterfactual_summary(raw)
    economic = aggregate["candidate"]
    assert economic.get("real_risk_cell_count") == 0
    assert _ANALYZER._economic_gate(economic)["passed"] is False


def test_economic_gate_accepts_preregistered_half_point_real_risk_mdd_gain() -> None:
    economic = {
        "median_wealth_retention": 0.995,
        "worst_wealth_retention": 0.985,
        "worst_mdd_delta": -0.004,
        "max_real_risk_mdd_delta": 0.005,
        "real_risk_cell_count": 1,
        "best_acute_return_delta": 0.0,
        "worst_acute_return_delta": -0.5,
        "max_order_delta_pct": 0.03,
        "max_turnover_delta_pct": 0.05,
    }
    result = _ANALYZER._economic_gate(economic)
    assert result["passed"] is True
    assert result["mdd_real_risk_pass"] is True


def test_economic_gate_accepts_preregistered_one_point_real_risk_acute_gain() -> None:
    economic = {
        "median_wealth_retention": 0.995,
        "worst_wealth_retention": 0.985,
        "worst_mdd_delta": -0.004,
        "max_real_risk_mdd_delta": 0.0,
        "max_real_risk_acute_return_delta": 0.01,
        "real_risk_cell_count": 1,
        "best_acute_return_delta": 0.01,
        "worst_acute_return_delta": 0.0,
        "max_order_delta_pct": 0.03,
        "max_turnover_delta_pct": 0.05,
    }
    result = _ANALYZER._economic_gate(economic)
    assert result["passed"] is True
    assert result["mdd_real_risk_pass"] is False
    assert result["acute_real_risk_pass"] is True


def test_closure_decision_is_derived_from_candidate_outcomes() -> None:
    passing = [{"candidate_id": "entry", "decision": "PROMOTION_CANDIDATE", "gates": {}}]
    insufficient = [{"candidate_id": "entry", "decision": "INSUFFICIENT_SAMPLE", "gates": {}}]
    rejected = [
        {"candidate_id": "entry", "decision": "REJECTED_ECONOMIC_REGRESSION", "gates": {}}
    ]
    assert _ANALYZER._closure_outcome(passing)["final_decision"] == (
        "PROMOTION_CANDIDATE_REQUIRES_FUTURE_HOLDOUT"
    )
    assert _ANALYZER._closure_outcome(insufficient)["final_decision"] == (
        "INCREMENTAL_EVIDENCE_INSUFFICIENT_SAMPLE"
    )
    assert _ANALYZER._closure_outcome(rejected)["final_decision"] == (
        "NO_PROMOTABLE_INCREMENTAL_RISK"
    )


def _sealed(payload: dict) -> dict:
    result = dict(payload)
    result["payload_sha256"] = canonical_sha256(result)
    return result


def _analysis_inputs() -> tuple[dict, dict, bytes, dict, dict, dict, dict]:
    provenance = {
        "contract_sha256": "contract",
        "capability_registry_sha256": "capability",
        "source_registry_sha256": "source",
        "adapter_sha256": "adapter",
        "market_data_prefix_sha256": "market",
        "sealed_trade_challenger_trace_sha256": "trade-trace",
        "trade_commit": "trade-commit",
        "uquant_starting_commit": "uquant-commit",
    }
    capability = _sealed(
        {"schema_version": 1, "trade_commit": "trade-commit", "capabilities": []}
    )
    provenance["capability_registry_sha256"] = capability["payload_sha256"]
    matrix = _sealed(
        {"schema_version": 1, "provenance": provenance, "cells": [], "summary": {}}
    )
    daily = _sealed({"schema_version": 1, "provenance": provenance, "cells": []})
    daily_gzip = gzip.compress((json.dumps(daily, sort_keys=True) + "\n").encode())
    exclusive = _sealed(
        {
            "schema_version": 1,
            "provenance": provenance,
            "events_frozen_before_outcome_analysis": True,
            "events": [],
        }
    )
    raw = _sealed(
        {
            "schema_version": 1,
            "provenance": {
                "risk_differential_matrix_sha256": matrix["payload_sha256"],
                "daily_trace_gzip_sha256": hashlib.sha256(daily_gzip).hexdigest(),
                "frozen_exclusive_events_sha256": exclusive["payload_sha256"],
                "trade_commit": "trade-commit",
                "uquant_starting_commit": "uquant-commit",
            },
            "cells": [],
        }
    )
    negative = _sealed({"schema_version": 1, "phase5": {}, "phase7": {}})
    return matrix, daily, daily_gzip, exclusive, raw, negative, capability


def test_analyzer_inputs_fail_closed_on_tampered_canonical_seal() -> None:
    matrix, daily, daily_gzip, exclusive, raw, negative, capability = _analysis_inputs()
    matrix["summary"]["tampered"] = True
    with pytest.raises(RuntimeError, match="matrix canonical seal"):
        _ANALYZER._validate_analysis_inputs(
            matrix, daily, daily_gzip, exclusive, raw, negative, capability
        )


def test_analyzer_inputs_accept_matching_canonical_bindings() -> None:
    matrix, daily, daily_gzip, exclusive, raw, negative, capability = _analysis_inputs()
    _ANALYZER._validate_analysis_inputs(
        matrix, daily, daily_gzip, exclusive, raw, negative, capability
    )


def test_analyzer_inputs_fail_closed_on_cross_artifact_mismatch() -> None:
    matrix, daily, daily_gzip, exclusive, raw, negative, capability = _analysis_inputs()
    raw["provenance"]["risk_differential_matrix_sha256"] = "other"
    raw["payload_sha256"] = canonical_sha256(raw)
    with pytest.raises(RuntimeError, match="raw-to-matrix binding"):
        _ANALYZER._validate_analysis_inputs(
            matrix, daily, daily_gzip, exclusive, raw, negative, capability
        )


def test_generalization_gate_is_calculated_from_distribution() -> None:
    rows = [
        {"matrix_axis": "generalization", "mdd_delta": 0.0, "wealth_retention": 1.0},
        {"matrix_axis": "generalization", "mdd_delta": -0.004, "wealth_retention": 0.99},
    ]
    gate = _ANALYZER._generalization_gate(rows)
    assert gate["evaluable"] is True
    assert gate["passed"] is True


def test_counterfactual_job_checkpoint_resumes_only_matching_identity(tmp_path: Path) -> None:
    checkpoint = tmp_path / "cell.json"
    result = {"cell_id": "official_pool/h1_2024/a", "policy_id": "baseline_uquant"}
    _SCRIPT._write_job_checkpoint(checkpoint, identity="identity-a", result=result)
    assert _SCRIPT._load_job_checkpoint(checkpoint, identity="identity-a") == result
    assert _SCRIPT._load_job_checkpoint(checkpoint, identity="identity-b") is None


def test_layered_shadow_emits_canonical_risk_attribution() -> None:
    date = pd.Timestamp("2026-08-21")
    frame = pd.DataFrame(
        {"open": [7.0], "high": [7.2], "low": [6.8], "close": [7.0]},
        index=[date],
    )
    account = AccountState.empty(1_000_000.0)
    account.positions["sz000001"] = Position(
        symbol="sz000001",
        shares=10_000,
        avg_cost=10.0,
        highest_close=10.0,
    )
    targets, triggered = _layered_targets(
        engine=SimpleNamespace(_raw={"sz000001": frame}),
        date=date,
        account=account,
        targets=(),
        trade={"severity_rank": 0},
        equity=1_000_000.0,
    )
    assert triggered == 1
    assert targets[0].origin_subsystem == "RISK"
    assert targets[0].mechanism == "RISK_OFF"
