from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from uquant.engine import attribution
from uquant.types import Fill, Lifecycle


def _fill(
    *,
    fill_date: str,
    symbol: str,
    side: str,
    shares: int,
    price: float,
    reason: str,
    reason_code: str,
) -> Fill:
    return Fill(
        signal_date=fill_date,
        fill_date=fill_date,
        symbol=symbol,
        side=side,
        shares=shares,
        price=price,
        gross_value=shares * price,
        commission=0.0,
        stamp_duty=0.0,
        transfer_fee=0.0,
        slippage_cost=0.0,
        reason=reason,
        lifecycle=Lifecycle.CORE.value,
        reason_code=reason_code,
    )


def test_actual_rotation_fills_produce_forward_replacement_spread() -> None:
    dates = pd.bdate_range("2025-01-02", periods=70)
    rotation_date = dates[10]
    old_close = np.linspace(100.0, 80.0, len(dates))
    new_close = np.linspace(10.0, 16.0, len(dates))
    panel = {
        "old": pd.DataFrame({"close": old_close}, index=dates),
        "new": pd.DataFrame({"close": new_close}, index=dates),
    }
    fills = [
        _fill(
            fill_date=str(dates[0].date()),
            symbol="old",
            side="BUY",
            shares=10,
            price=float(old_close[0]),
            reason="confirmed mature leader core",
            reason_code="strategy_target",
        ),
        _fill(
            fill_date=str(rotation_date.date()),
            symbol="old",
            side="SELL",
            shares=10,
            price=float(old_close[10]),
            reason="rotation exit: new confirmed edge",
            reason_code="rotation",
        ),
        _fill(
            fill_date=str(rotation_date.date()),
            symbol="new",
            side="BUY",
            shares=20,
            price=float(new_close[10]),
            reason="rotation entry: replaces old",
            reason_code="rotation",
        ),
    ]

    result = attribution(fills, panel=panel)

    for horizon in (20, 40):
        item = result["replacement_spread"][str(horizon)][0]
        expected_old = old_close[10 + horizon] / old_close[10] - 1.0
        expected_new = new_close[10 + horizon] / new_close[10] - 1.0
        assert item["old_symbol"] == "old"
        assert item["new_symbol"] == "new"
        assert item["old_return"] == pytest.approx(expected_old)
        assert item["new_return"] == pytest.approx(expected_new)
        assert item["spread"] == pytest.approx(expected_new - expected_old)
    assert set(result["by_reason"]) == {"rotation"}
    assert result["by_reason"]["rotation"]["fills"] == 1


def test_unlinked_buy_reason_cannot_fabricate_replacement_spread() -> None:
    dates = pd.bdate_range("2025-01-02", periods=50)
    panel = {
        symbol: pd.DataFrame({"close": np.linspace(1.0, 2.0, len(dates))}, index=dates)
        for symbol in ("old", "new")
    }
    entry = _fill(
        fill_date=str(dates[5].date()),
        symbol="new",
        side="BUY",
        shares=10,
        price=float(panel["new"].iloc[5]["close"]),
        reason="rotation entry: replaces old",
        reason_code="rotation",
    )

    result = attribution([entry], panel=panel)

    assert result["replacement_spread"] == {"20": [], "40": []}
