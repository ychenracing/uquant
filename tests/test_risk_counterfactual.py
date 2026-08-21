from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from research.risk_counterfactual import (
    NEGATIVE_CONTROL_IDS,
    clamp_pyramid_targets,
    classify_promotion,
    effective_shadow_cap,
    execution_day,
    layered_protection_line,
    rebuild_shadow_orders,
    wilder_atr,
)
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
