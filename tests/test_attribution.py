from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from uquant.attribution import ExitRecord, post_exit_diagnostics


def test_structured_exit_mechanism_produces_bounded_diagnostic() -> None:
    """Catches post-exit diagnostics detached from machine causal identity."""
    dates = pd.bdate_range("2025-01-02", periods=70)
    exit_date = str(dates[10].date())
    prices = pd.Series(np.linspace(100.0, 80.0, len(dates)), index=dates)

    result = post_exit_diagnostics(
        exits=(
            ExitRecord(
                symbol="old",
                exit_date=exit_date,
                exit_price=float(prices.iloc[10]),
                origin_subsystem="LEADER",
                mechanism="LEADER_ROTATION",
            ),
        ),
        prices={"old": prices},
        economic_end=str(dates[50].date()),
        horizons=(20, 41),
    )

    assert result[0]["origin_subsystem"] == "LEADER"
    assert result[0]["mechanism"] == "LEADER_ROTATION"
    assert result[0]["horizons"]["20"]["absolute_return"] == pytest.approx(
        prices.iloc[30] / prices.iloc[10] - 1.0
    )
    assert result[0]["horizons"]["41"] is None


def test_human_reason_cannot_enter_the_canonical_exit_identity() -> None:
    """Catches reintroduction of human prose as an economic classifier."""
    with pytest.raises(TypeError, match="reason"):
        ExitRecord(
            symbol="new",
            exit_date="2025-01-09",
            exit_price=10.0,
            origin_subsystem="LEADER",
            mechanism="LEADER_ROTATION",
            reason="rotation entry: replaces old",  # type: ignore[call-arg]
        )
