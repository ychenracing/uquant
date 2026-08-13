from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from research.window_matrix import (
    WINDOW_SPECS,
    WindowSpec,
    canonicalize_requested_interval,
    select_acute_window,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "frozen"


def _close(symbol: str) -> pd.Series:
    frame = pd.read_csv(DATA / f"{symbol}.csv", parse_dates=["date"])
    return frame.set_index("date")["close"]


def test_requested_boundaries_map_inward_and_preserve_the_shared_session() -> None:
    broad = _close("sh000300").index
    tech = _close("sh000682").index

    observed = {
        spec.name: canonicalize_requested_interval(
            requested_start=spec.requested_start,
            requested_end=spec.requested_end,
            broad_sessions=broad,
            tech_sessions=tech,
        )
        for spec in WINDOW_SPECS
    }

    assert observed == {spec.name: (spec.start, spec.end) for spec in WINDOW_SPECS}
    assert observed["h1_2024"][1] == observed["h2_2024"][0] == "2024-07-01"


@pytest.mark.parametrize("spec", WINDOW_SPECS, ids=lambda item: item.name)
def test_a1_selector_reproduces_the_frozen_22_session_stress_window(spec: WindowSpec) -> None:
    acute_start, acute_end, acute_return = select_acute_window(
        close=_close("sh000682"),
        start=spec.start,
        end=spec.end,
        horizon_sessions=22,
    )

    assert (acute_start, acute_end) == (spec.acute_start, spec.acute_end)
    assert acute_return == pytest.approx(spec.acute_reference_return, abs=5e-7)


def test_a1_selector_uses_the_earliest_end_session_for_an_exact_tie() -> None:
    sessions = pd.bdate_range("2024-01-02", periods=26)
    close = pd.Series([100.0] * 23 + [90.0] * 3, index=sessions)

    start, end, value = select_acute_window(
        close=close,
        start=str(sessions[0].date()),
        end=str(sessions[-1].date()),
        horizon_sessions=22,
    )

    assert start == str(sessions[1].date())
    assert end == str(sessions[23].date())
    assert value == pytest.approx(-0.10)
