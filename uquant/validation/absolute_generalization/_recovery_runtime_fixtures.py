"""Deterministic market-input fixtures driven only by the production engine loop."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd

from uquant.engine import INDEX_SYMBOLS

from .contract import AbsoluteGeneralizationContract
from .replay import (
    AbsoluteGeneralizationReplay,
    run_absolute_generalization_replay_sessions,
)
from .scenarios import build_leave_one_out_scenarios

_OPTICAL = ("sz300308", "sz300502", "sz300394")
_MATERIALS = ("sh688019", "sh688300", "sz300666")
_STRATEGIC = (*_OPTICAL, *_MATERIALS)


def _fixture_symbols(contract: AbsoluteGeneralizationContract) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*contract.canonical_universe, *INDEX_SYMBOLS)))


def _write_prices(
    root: Path,
    *,
    symbol: str,
    dates: pd.DatetimeIndex,
    daily_returns: Sequence[float],
    locked_session: str = "",
) -> None:
    closes: list[float] = []
    value = 10.0
    for change in daily_returns:
        value *= 1.0 + change
        closes.append(value)
    opens = [10.0, *closes[:-1]]
    highs = [max(open_price, close) * 1.004 for open_price, close in zip(opens, closes, strict=True)]
    lows = [min(open_price, close) * 0.996 for open_price, close in zip(opens, closes, strict=True)]
    if locked_session:
        location = dates.get_loc(pd.Timestamp(locked_session))
        if type(location) is not int:
            raise ValueError("absolute recovery locked session is ambiguous")
        index = location
        locked = closes[index - 1] * 1.20
        opens[index] = highs[index] = lows[index] = closes[index] = locked
    volume = 8_000_000.0
    pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
            "amount": [close * volume for close in closes],
        }
    ).to_csv(root / f"{symbol}.csv", index=False)


def _run_fixture(
    *,
    data: Path,
    contract: AbsoluteGeneralizationContract,
    start: str,
    end: str,
    symbols_for_session: Callable[[pd.Timestamp], tuple[str, ...]],
    initial_budget_level: int = 0,
    initial_peak_drawdown: float = 0.0,
) -> AbsoluteGeneralizationReplay:
    scenario = build_leave_one_out_scenarios(contract)[0]
    return run_absolute_generalization_replay_sessions(
        scenario=scenario,
        data_dir=data,
        start=start,
        end=end,
        strategic_symbols=_STRATEGIC,
        symbols_for_session=symbols_for_session,
        initial_budget_level=initial_budget_level,
        initial_peak_drawdown=initial_peak_drawdown,
    )


def run_failed_grant_fixture(
    contract: AbsoluteGeneralizationContract,
) -> AbsoluteGeneralizationReplay:
    """Create a blocked first grant and real distinct successor via production."""

    with tempfile.TemporaryDirectory(prefix="uquant-absolute-failed-") as temporary:
        data = Path(temporary)
        dates = pd.bdate_range("2022-01-03", "2023-02-28")
        material_rates = {"sh688019": 0.007, "sh688300": 0.0055, "sz300666": 0.005}
        for symbol in _fixture_symbols(contract):
            rate = (
                0.001
                if symbol in INDEX_SYMBOLS
                else 0.0045
                if symbol in _OPTICAL
                else material_rates.get(symbol, 0.001)
            )
            changes = [rate] * len(dates)
            if symbol == "sh688019":
                locked_index = dates.get_loc(pd.Timestamp("2023-02-02"))
                if type(locked_index) is not int:
                    raise ValueError("absolute recovery locked session is ambiguous")
                changes[locked_index + 1 :] = [-0.009] * (
                    len(changes) - locked_index - 1
                )
            _write_prices(
                data,
                symbol=symbol,
                dates=dates,
                daily_returns=changes,
                locked_session="2023-02-02" if symbol == "sh688019" else "",
            )
        return _run_fixture(
            data=data,
            contract=contract,
            start="2023-01-03",
            end="2023-02-28",
            symbols_for_session=lambda _session: _STRATEGIC,
            initial_budget_level=1,
            initial_peak_drawdown=0.09,
        )


def run_cross_industry_fixture(
    contract: AbsoluteGeneralizationContract,
) -> AbsoluteGeneralizationReplay:
    """Create two industry regimes without injecting any economic object."""

    with tempfile.TemporaryDirectory(prefix="uquant-absolute-cross-") as temporary:
        data = Path(temporary)
        dates = pd.bdate_range("2022-01-03", "2026-04-30")
        location = dates.get_loc(pd.Timestamp("2023-01-03"))
        if type(location) is not int:
            raise ValueError("absolute recovery replay session is ambiguous")
        replay_index = location
        material_rates = {"sh688019": 0.009, "sh688300": 0.006, "sz300666": 0.005}
        for symbol in _fixture_symbols(contract):
            changes: list[float] = []
            for index in range(len(dates)):
                offset = index - replay_index
                if symbol in INDEX_SYMBOLS:
                    change = 0.0012
                elif symbol in _OPTICAL:
                    change = 0.0045 if offset < 210 else -0.009 if offset < 285 else 0.0005
                elif symbol in material_rates:
                    change = (
                        -0.0001
                        if offset < 360
                        else material_rates[symbol]
                        if offset < 800
                        else 0.001
                        if offset < 840
                        else -0.009
                    )
                else:
                    change = 0.001
                changes.append(change)
            _write_prices(data, symbol=symbol, dates=dates, daily_returns=changes)
        return _run_fixture(
            data=data,
            contract=contract,
            start="2023-01-03",
            end="2026-04-30",
            symbols_for_session=lambda _session: _STRATEGIC,
        )


def run_repair_fixture(
    contract: AbsoluteGeneralizationContract, *, level: int, sessions: int
) -> AbsoluteGeneralizationReplay:
    """Advance one real account-owned repair episode from its initial damage level."""

    if level not in {1, 2, 3, 4} or sessions not in {20, 40, 60}:
        raise ValueError("absolute recovery repair fixture selection differs")
    with tempfile.TemporaryDirectory(prefix=f"uquant-absolute-repair-{level}-") as temporary:
        data = Path(temporary)
        dates = pd.bdate_range("2025-01-02", periods=sessions + 260)
        for index, symbol in enumerate(_fixture_symbols(contract)):
            rate = 0.001 + (index % 5) * 0.00005
            _write_prices(data, symbol=symbol, dates=dates, daily_returns=[rate] * len(dates))
        replay_dates = dates[-(sessions + 2) :]
        return _run_fixture(
            data=data,
            contract=contract,
            start=str(replay_dates[0].date()),
            end=str(replay_dates[-1].date()),
            symbols_for_session=lambda _session: _STRATEGIC,
            initial_budget_level=level,
            initial_peak_drawdown={1: 0.09, 2: 0.13, 3: 0.17, 4: 0.21}[level],
        )


def run_terminal_fixture(
    contract: AbsoluteGeneralizationContract,
) -> AbsoluteGeneralizationReplay:
    """Produce one isolated finite no-outlet trace for the SCC analyzer."""

    with tempfile.TemporaryDirectory(prefix="uquant-absolute-terminal-") as temporary:
        data = Path(temporary)
        dates = pd.bdate_range("2024-01-02", periods=320)
        for symbol in _fixture_symbols(contract):
            _write_prices(data, symbol=symbol, dates=dates, daily_returns=[0.0] * len(dates))
        replay_dates = dates[-62:]
        return _run_fixture(
            data=data,
            contract=contract,
            start=str(replay_dates[0].date()),
            end=str(replay_dates[-1].date()),
            symbols_for_session=lambda _session: _STRATEGIC,
        )


__all__ = (
    "run_cross_industry_fixture",
    "run_failed_grant_fixture",
    "run_repair_fixture",
    "run_terminal_fixture",
)
