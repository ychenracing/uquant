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
    order_ledger: Sequence[Mapping[str, object]] = (),
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
        "order_ledger": tuple(order_ledger),
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


def _ledger_buy(*, cancel_requested: bool) -> dict[str, object]:
    return {
        **_buy(),
        "order_id": "O000000001",
        "status": "PARTIALLY_FILLED",
        "requested_shares": 500,
        "filled_shares": 200,
        "remaining_shares": 300,
        "cancel_reason": "sentinel_freeze_new_risk" if cancel_requested else "",
        "last_event": "CANCEL_REQUESTED" if cancel_requested else "PARTIALLY_FILLED",
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
        "risk_state_drift_count": 0,
        "reduction_level_drift_count": 0,
        "shock_state_drift_count": 0,
        "capital_budget_level_drift_count": 0,
        "passed": True,
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


def test_first_behavior_divergence_ignores_the_locked_switch_diagnostic() -> None:
    summarize = _summary_api()
    baseline = _row(date="2026-08-18", sentinel_freeze=False, orders=())
    candidate = _row(date="2026-08-18", sentinel_freeze=False, orders=())
    baseline_evidence = baseline["risk_evidence"]
    candidate_evidence = candidate["risk_evidence"]
    assert isinstance(baseline_evidence, dict)
    assert isinstance(candidate_evidence, dict)
    baseline_evidence["sentinel_causal_confirmation_authority_enabled"] = False
    candidate_evidence["sentinel_causal_confirmation_authority_enabled"] = True
    baseline_evidence["effective_config_sha256"] = "phase6"
    candidate_evidence["effective_config_sha256"] = "candidate"

    result = summarize(
        baseline_trace=(baseline,),
        candidate_trace=(candidate,),
        baseline_metrics={},
        candidate_metrics={},
    )

    assert result["first_divergence"] is None


def test_value_gate_counts_broker_visible_partial_buy_cancel_request() -> None:
    summarize = _summary_api()
    baseline = _row(
        date="2026-08-19",
        sentinel_freeze=False,
        orders=(_buy(),),
        order_ledger=(_ledger_buy(cancel_requested=False),),
    )
    candidate = _row(
        date="2026-08-19",
        sentinel_freeze=True,
        orders=(_buy(),),
        order_ledger=(_ledger_buy(cancel_requested=True),),
    )

    result = summarize(
        baseline_trace=(baseline,),
        candidate_trace=(candidate,),
        baseline_metrics={},
        candidate_metrics={},
    )

    assert result["value_gate"] == {
        "passed": True,
        "qualifying_non_severe_events": 1,
    }
    event = result["exclusive_freeze_events"][0]
    assert event["blocked_new_risk_count"] == 1
    assert event["blocked_orders"][0]["order_id"] == "O000000001"
    assert event["blocked_orders"][0]["remaining_shares"] == 300


def test_value_gate_does_not_miscount_economically_identical_buy_with_new_event_id() -> None:
    summarize = _summary_api()
    replacement = {**_buy(), "event_id": "metadata-rebound-event-id"}

    result = summarize(
        baseline_trace=(
            _row(date="2026-08-19", sentinel_freeze=False, orders=(_buy(),)),
        ),
        candidate_trace=(
            _row(date="2026-08-19", sentinel_freeze=True, orders=(replacement,)),
        ),
        baseline_metrics={},
        candidate_metrics={},
    )

    assert result["exclusive_freeze_events"][0]["blocked_new_risk_count"] == 0
    assert result["value_gate"] == {
        "passed": False,
        "qualifying_non_severe_events": 0,
    }


def test_locked_configs_differ_only_by_causal_authority() -> None:
    validate = _locked_config_api()
    baseline = DEFAULT_CONFIG.override(
        risk_sentinel_causal_confirmation_enabled=False,
    )
    candidate = DEFAULT_CONFIG

    assert validate(baseline=baseline, candidate=candidate) == {
        "baseline_config_sha256": (
            "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5"
        ),
        "candidate_config_sha256": (
            "b75d02d238e7ea18793c6f15727b34bc15b7a002b5ec4c4e620f86f1c39c93fa"
        ),
    }

    with pytest.raises(ValueError, match="differ only by causal confirmation authority"):
        validate(
            baseline=baseline,
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
    candidate_row["risk"] = "RISK_OFF"
    candidate_evidence["reduction_level"] = 1
    candidate_evidence["shock_state"] = "ACTIVE"
    candidate_evidence["capital_budget_level"] = 2

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
        "risk_state_drift_count": 1,
        "reduction_level_drift_count": 1,
        "shock_state_drift_count": 1,
        "capital_budget_level_drift_count": 1,
        "passed": False,
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
            baseline_cfg=DEFAULT_CONFIG.override(
                risk_sentinel_causal_confirmation_enabled=False,
            ),
            candidate_cfg=DEFAULT_CONFIG.override(
                risk_sentinel_confirm_days=1,
            ),
        )
