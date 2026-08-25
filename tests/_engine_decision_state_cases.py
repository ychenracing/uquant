from __future__ import annotations

import copy

import pandas as pd
import pytest
from test_engine_contracts import (
    RISK_REGRESSION_POOLS,
    SYMBOLS,
)

from uquant import engine as engine_module
from uquant.engine import (
    ProductionEngine,
)
from uquant.types import (
    AccountState,
    Position,
    Tranche,
)


def test_decision_does_not_route_sentinel_diagnostic_without_formal_flag(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uquant.types import Risk, RiskAssessment

    observed: dict[str, object] = {}

    def diagnostic_only(**_: object) -> RiskAssessment:
        return RiskAssessment(
            Risk.NORMAL,
            1.0,
            0,
            {
                "base_freeze_new_risk": False,
                "sentinel_freeze_new_risk": True,
                "freeze_new_risk": False,
            },
            (),
            "NONE",
            freeze_new_risk=False,
        )

    original = engine_module.reconcile_account_orders

    def reconcile(**kwargs: object):
        observed.update(kwargs)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_module, "assess_risk", diagnostic_only)
    monkeypatch.setattr(engine_module, "reconcile_account_orders", reconcile)
    ProductionEngine(data_dir).decide(
        symbols=SYMBOLS,
        as_of="2026-06-30",
        account=AccountState.empty(2e6),
    )

    assert observed["removed_buy_reason"] is None

def test_future_dated_state_fails_closed(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.last_successful_run = "2027-01-01"
    with pytest.raises(RuntimeError):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)

def test_decision_state_advances_at_most_once_per_session(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    persisted = copy.deepcopy(state.to_dict())

    with pytest.raises(RuntimeError, match="strictly after"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    with pytest.raises(RuntimeError, match="strictly after"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-29", account=state)

    assert state.to_dict() == persisted

def test_decision_cannot_predate_authoritative_broker_snapshot(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.broker_as_of = "2026-06-30"

    with pytest.raises(RuntimeError, match="authoritative broker snapshot"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-29", account=state)

    decision = engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    assert decision.date == state.broker_as_of

def test_daily_decision_marks_position_and_tranche_excursions(data_dir):
    engine = ProductionEngine(data_dir)
    date = pd.Timestamp("2026-06-30")
    symbol = SYMBOLS[0]
    close = float(engine.data.load(symbol).loc[date, "close"])
    cheap = Tranche(
        tranche_id="cheap-core",
        lifecycle="CORE",
        shares=100,
        avg_cost=close / 2.0,
        entry_date="2026-01-02",
        sellable_date="2026-01-05",
        highest_close=close / 2.0,
        lowest_close=close * 3.0,
    )
    expensive = Tranche(
        tranche_id="expensive-core",
        lifecycle="CORE",
        shares=100,
        avg_cost=close * 2.0,
        entry_date="2026-01-02",
        sellable_date="2026-01-05",
        highest_close=close / 2.0,
        lowest_close=close * 3.0,
    )
    state = AccountState.empty(2e6)
    state.positions[symbol] = Position(
        symbol=symbol,
        shares=200,
        avg_cost=(cheap.avg_cost + expensive.avg_cost) / 2.0,
        entry_date="2026-01-02",
        highest_close=close / 2.0,
        tranches=[cheap, expensive],
    )

    engine.decide(symbols=SYMBOLS, as_of=str(date.date()), account=state)

    position = state.positions[symbol]
    assert position.highest_close == pytest.approx(close)
    by_id = {item.tranche_id: item for item in position.tranches}
    assert by_id["cheap-core"].highest_close == pytest.approx(close)
    assert by_id["cheap-core"].lowest_close == pytest.approx(close)
    assert by_id["cheap-core"].mfe == pytest.approx(1.0)
    assert by_id["cheap-core"].mae == pytest.approx(0.0)
    assert by_id["expensive-core"].highest_close == pytest.approx(close)
    assert by_id["expensive-core"].lowest_close == pytest.approx(close)
    assert by_id["expensive-core"].mfe == pytest.approx(0.0)
    assert by_id["expensive-core"].mae == pytest.approx(-0.5)

def test_stale_code_hash_fails_closed(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.code_hash = "stale-code-hash"
    with pytest.raises(RuntimeError, match="code hash"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)

def test_pre_listing_symbols_are_point_in_time_invisible(data_dir):
    result = ProductionEngine(data_dir).backtest(
        symbols=(*SYMBOLS, "sh688146"),
        start="2023-01-03",
        end="2023-02-28",
    )
    assert result["start"] == "2023-01-03"
    assert all(fill["symbol"] != "sh688146" for fill in result["final_account"]["fills"])
    assert all(order["symbol"] != "sh688146" for order in result["order_ledger"])

def test_recent_shock_window_preserves_capital_across_pool_sizes(data_dir):
    engine = ProductionEngine(data_dir)
    for symbols in RISK_REGRESSION_POOLS:
        result = engine.backtest(
            symbols=symbols,
            start="2026-07-21",
            end="2026-08-05",
        )
        assert result["final_wealth"] > 0.85
        assert result["max_drawdown"] < 0.15
        assert result["account_orders"] <= 3
