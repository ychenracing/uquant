from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from uquant.config import DEFAULT_CONFIG


def _summary_api() -> Callable[..., dict[str, Any]]:
    try:
        from research.sentinel_exclusive_freeze import (
            summarize_exclusive_freeze_comparison,
        )
    except ImportError:
        pytest.fail("exclusive-freeze comparison API is missing", pytrace=False)
    return summarize_exclusive_freeze_comparison


def _locked_config_api() -> Callable[..., dict[str, str]]:
    try:
        from research.sentinel_exclusive_freeze import validate_locked_configs
    except ImportError:
        pytest.fail("exclusive-freeze config lock API is missing", pytrace=False)
    return validate_locked_configs


def _runner_api() -> Callable[..., dict[str, Any]]:
    try:
        from research.sentinel_exclusive_freeze import (
            run_exclusive_freeze_comparison,
        )
    except ImportError:
        pytest.fail("exclusive-freeze production runner is missing", pytrace=False)
    return run_exclusive_freeze_comparison


def _row(
    *,
    date: str,
    sentinel_freeze: bool,
    orders: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "date": date,
        "risk": "NORMAL",
        "risk_evidence": {
            "base_freeze_new_risk": False,
            "sentinel_freeze_new_risk": sentinel_freeze,
            "sentinel_severe_direct": False,
            "sentinel_causal_coverage_status": "READY",
            "sentinel_causal_confidence": 0.9,
            "sentinel_causal_confirmation_history_trusted": True,
            "sentinel_causal_confirmation_days": 2,
            "sentinel_causal_incremental_families": ["breadth_structure"],
            "sentinel_causal_earlier_families": [],
            "sentinel_causal_active_families": [
                "breadth_structure",
                "market_velocity",
            ],
            "target_gross_cap": 1.0,
        },
        "targets": (
            {
                "symbol": "old",
                "weight": 0.5,
                "lifecycle": "CORE",
                "reason_code": "strategy_target",
            },
        ),
        "pending_orders": tuple(orders),
        "fills": (),
        "equity": 1_000_000.0,
    }


def _buy() -> dict[str, object]:
    return {
        "event_id": "buy-new-2026-08-19",
        "symbol": "new",
        "side": "BUY",
        "target_weight": 0.3,
        "reason_code": "strategy_target",
        "exit_kind": "",
    }


def test_summary_retains_first_divergence_and_nonsevere_value_event() -> None:
    summarize = _summary_api()
    baseline = (
        _row(date="2026-08-18", sentinel_freeze=False, orders=()),
        _row(date="2026-08-19", sentinel_freeze=False, orders=(_buy(),)),
    )
    candidate = (
        _row(date="2026-08-18", sentinel_freeze=False, orders=()),
        _row(date="2026-08-19", sentinel_freeze=True, orders=()),
    )

    result = summarize(
        baseline_trace=baseline,
        candidate_trace=candidate,
        baseline_metrics={"final_wealth": 1.0, "max_drawdown": 0.1},
        candidate_metrics={"final_wealth": 1.0, "max_drawdown": 0.1},
        forward_returns={
            "2026-08-19": {
                "new": {"5d": -0.05, "10d": 0.02, "20d": 0.10},
            }
        },
    )

    assert result["first_divergence"]["date"] == "2026-08-19"
    assert result["first_divergence"]["changed_fields"] == ["risk", "orders"]
    assert result["hard_gate"] == {
        "target_gross_cap_equal_to_base": True,
        "sentinel_direct_sell_count": 0,
        "sentinel_risk_gross_cap_event_count": 0,
        "healthy_holding_reduction_count": 0,
    }
    assert result["value_gate"] == {
        "passed": True,
        "qualifying_non_severe_events": 1,
    }
    assert result["exclusive_freeze_events"] == [
        {
            "date": "2026-08-19",
            "non_severe_direct": True,
            "coverage": "READY",
            "confidence": 0.9,
            "confirmation_history_trusted": True,
            "confirmation_days": 2,
            "active_families": ["breadth_structure", "market_velocity"],
            "incremental_families": ["breadth_structure"],
            "earlier_families": [],
            "comparison_class": "incremental_same_day",
            "blocked_new_risk_count": 1,
            "blocked_orders": [_buy()],
            "sentinel_direct_sell_count": 0,
            "healthy_holding_reduction_count": 0,
            "opportunity_cost": [
                {
                    "symbol": "new",
                    "blocked_order_value": 300_000.0,
                    "counterfactual_return_5d": -0.05,
                    "counterfactual_return_10d": 0.02,
                    "counterfactual_return_20d": 0.10,
                    "missed_upside": 30_000.0,
                    "avoided_loss": 15_000.0,
                    "net_opportunity_cost": 15_000.0,
                }
            ],
        }
    ]


def test_locked_configs_differ_only_by_causal_authority() -> None:
    validate = _locked_config_api()
    candidate = DEFAULT_CONFIG.override(
        risk_sentinel_causal_confirmation_enabled=True,
    )

    assert validate(baseline=DEFAULT_CONFIG, candidate=candidate) == {
        "baseline_config_sha256": (
            "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5"
        ),
        "candidate_config_sha256": (
            "b75d02d238e7ea18793c6f15727b34bc15b7a002b5ec4c4e620f86f1c39c93fa"
        ),
    }

    with pytest.raises(ValueError, match="differ only by causal confirmation authority"):
        validate(
            baseline=DEFAULT_CONFIG,
            candidate=candidate.override(risk_sentinel_confirm_days=1),
        )


def test_summary_detects_every_forbidden_authority_carrier() -> None:
    summarize = _summary_api()
    baseline_row = _row(date="2026-08-19", sentinel_freeze=False, orders=())
    candidate_row = _row(
        date="2026-08-19",
        sentinel_freeze=True,
        orders=(
            {
                "event_id": "sell-old-2026-08-19",
                "symbol": "old",
                "side": "SELL",
                "target_weight": 0.4,
                "reason_code": "risk_off",
                "exit_kind": "risk",
            },
        ),
    )
    candidate_row["targets"] = (
        {
            "symbol": "old",
            "weight": 0.4,
            "lifecycle": "CORE",
            "reason_code": "risk_off",
        },
    )
    candidate_evidence = candidate_row["risk_evidence"]
    assert isinstance(candidate_evidence, dict)
    candidate_evidence["target_gross_cap"] = 0.7
    candidate_evidence["risk_events"] = [{"event": "RISK_GROSS_CAP"}]

    result = summarize(
        baseline_trace=(baseline_row,),
        candidate_trace=(candidate_row,),
        baseline_metrics={},
        candidate_metrics={},
    )

    assert result["hard_gate"] == {
        "target_gross_cap_equal_to_base": False,
        "sentinel_direct_sell_count": 1,
        "sentinel_risk_gross_cap_event_count": 1,
        "healthy_holding_reduction_count": 1,
    }
    assert result["value_gate"] == {
        "passed": False,
        "qualifying_non_severe_events": 0,
    }


def test_summary_rejects_misaligned_trace_calendars() -> None:
    summarize = _summary_api()

    with pytest.raises(ValueError, match="identical non-empty calendars"):
        summarize(
            baseline_trace=(
                _row(date="2026-08-18", sentinel_freeze=False, orders=()),
            ),
            candidate_trace=(
                _row(date="2026-08-19", sentinel_freeze=False, orders=()),
            ),
            baseline_metrics={},
            candidate_metrics={},
        )


def test_production_runner_rejects_retuning_before_loading_data() -> None:
    run = _runner_api()

    with pytest.raises(ValueError, match="differ only by causal confirmation authority"):
        run(
            data_dir=Path("does-not-exist"),
            symbols=("sz300308",),
            start="2024-01-02",
            end="2024-01-03",
            scenario="locked-config-contract",
            baseline_cfg=DEFAULT_CONFIG,
            candidate_cfg=DEFAULT_CONFIG.override(
                risk_sentinel_causal_confirmation_enabled=True,
                risk_sentinel_confirm_days=1,
            ),
        )
