"""Daily same-close replay evidence construction and validation."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any

from ..types import AccountState, Side
from .concentration import RECONCILIATION_TOLERANCE
from .concentration import finite_attribution_number as _finite

_LEDGER_FIELDS = frozenset(
    {
        "date",
        "cash",
        "equity",
        "gross_exposure",
        "net_exposure",
        "cash_weight",
        "position_weights",
        "daily_pnl",
        "target_weights",
        "target_gross",
        "caps",
        "binding_owner",
        "risk_state",
        "opportunity",
    }
)


_DAILY_REPLAY_FIELDS = frozenset(
    {
        "date",
        "cash",
        "position_shares",
        "close_marks",
    }
)


def _positive_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


def _require_exact_fields(value: Any, expected: Set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label} fields differ from the exact attribution schema")
    return value


def _close(observed: float, expected: float, *, label: str) -> None:
    if not math.isclose(
        observed,
        expected,
        rel_tol=1e-12,
        abs_tol=RECONCILIATION_TOLERANCE,
    ):
        raise ValueError(f"{label} does not reconcile")


def build_daily_replay_evidence_row(
    *,
    date: str,
    account: AccountState,
    close_prices: Mapping[str, float],
) -> dict[str, Any]:
    """Capture only raw same-close facts used to independently rebuild a ledger row."""

    try:
        date_type.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValueError("daily replay evidence date must be ISO") from exc
    cash = _finite(account.cash, label="daily replay evidence cash", minimum=0.0)
    position_shares = {
        symbol: _positive_integer(position.shares, label=f"daily replay shares/{symbol}")
        for symbol, position in sorted(account.positions.items())
        if position.shares > 0
    }
    if set(close_prices) != set(position_shares):
        raise ValueError("daily replay close marks differ from open positions")
    close_marks = {
        symbol: _finite(
            close_prices[symbol],
            label=f"daily replay close/{symbol}",
            minimum=0.0,
        )
        for symbol in sorted(close_prices)
    }
    if any(mark <= 0.0 for mark in close_marks.values()):
        raise ValueError("daily replay close marks must be positive")
    return {
        "date": date,
        "cash": cash,
        "position_shares": position_shares,
        "close_marks": close_marks,
    }


def _validated_evidence_rows(
    value: Any,
    *,
    economic_start: str,
    economic_end: str,
    trusted_sessions: Sequence[str] | None,
    trusted_close: Callable[[str, str], float] | None,
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("engine result daily replay evidence is required")
    by_date: dict[str, Mapping[str, Any]] = {}
    dates: list[str] = []
    for index, raw_row in enumerate(value):
        row = _require_exact_fields(raw_row, _DAILY_REPLAY_FIELDS, label=f"daily replay evidence row {index}")
        row_date = row["date"]
        if not isinstance(row_date, str):
            raise ValueError("daily replay evidence date is invalid")
        try:
            parsed = date_type.fromisoformat(row_date)
        except ValueError as exc:
            raise ValueError("daily replay evidence date is invalid") from exc
        if not date_type.fromisoformat(economic_start) <= parsed <= date_type.fromisoformat(economic_end):
            raise ValueError("daily replay evidence lies outside the economic interval")
        if row_date in by_date:
            raise ValueError("daily replay evidence dates must be unique")
        dates.append(row_date)
        by_date[row_date] = row
    if tuple(dates) != tuple(sorted(dates)):
        raise ValueError("daily replay evidence dates must be ordered")
    if dates[0] != economic_start or dates[-1] != economic_end:
        raise ValueError("daily replay evidence does not span the exact economic interval")
    if trusted_sessions is not None and tuple(dates) != tuple(trusted_sessions):
        raise ValueError("daily replay evidence differs from verified market sessions")
    if (trusted_sessions is None) != (trusted_close is None):
        raise ValueError("daily replay evidence trusted market source is incomplete")
    return by_date, dates


def _validated_equity_curve(
    value: Any,
    *,
    evidence_dates: list[str],
    ledger_value: list[Any],
) -> dict[str, float]:
    if not isinstance(value, list) or not value:
        raise ValueError("engine result equity curve is required for daily replay evidence")
    by_date: dict[str, float] = {}
    dates: list[str] = []
    for index, raw_point in enumerate(value):
        point = _require_exact_fields(raw_point, {"date", "equity"}, label=f"engine equity curve row {index}")
        point_date = point["date"]
        if not isinstance(point_date, str) or point_date in by_date:
            raise ValueError("engine equity curve dates are malformed")
        dates.append(point_date)
        by_date[point_date] = _finite(
            point["equity"],
            label="engine equity curve value",
            minimum=0.0,
        )
    ledger_dates = [str(row.get("date", "")) for row in ledger_value]
    if dates != evidence_dates or ledger_dates != evidence_dates:
        raise ValueError("daily replay evidence, equity curve, and attribution ledger dates differ")
    return by_date


@dataclass(slots=True)
class _ReplayState:
    cash: float
    positions: dict[str, int]
    previous_equity: float


def _apply_daily_fills(state: _ReplayState, fills: Sequence[Mapping[str, Any]]) -> None:
    for fill in fills:
        side = fill.get("side")
        symbol = str(fill.get("symbol", ""))
        shares = _positive_integer(fill.get("shares"), label="daily replay fill shares")
        gross = _finite(fill.get("gross_value"), label="daily replay fill gross value", minimum=0.0)
        cash_fees = sum(
            _finite(fill.get(name), label=f"daily replay fill {name}", minimum=0.0)
            for name in ("commission", "stamp_duty", "transfer_fee")
        )
        if side == Side.BUY.value:
            state.cash -= gross + cash_fees
            state.positions[symbol] = state.positions.get(symbol, 0) + shares
        elif side == Side.SELL.value:
            available = state.positions.get(symbol, 0)
            if shares > available:
                raise ValueError("daily replay SELL exceeds reconstructed position shares")
            state.cash += gross - cash_fees
            remaining = available - shares
            if remaining:
                state.positions[symbol] = remaining
            else:
                state.positions.pop(symbol, None)
        else:  # pragma: no cover - raw fill validation rejects this first
            raise ValueError("daily replay fill side is invalid")


def _daily_replay_position_values(
    *,
    evidence: Mapping[str, Any],
    state: _ReplayState,
    row_date: str,
    trusted_close: Callable[[str, str], float] | None,
) -> tuple[float, dict[str, float], float]:
    evidence_cash = _finite(evidence["cash"], label="daily replay evidence cash", minimum=0.0)
    _close(evidence_cash, state.cash, label="daily replay evidence cash versus fills")
    raw_shares = evidence["position_shares"]
    if not isinstance(raw_shares, Mapping):
        raise ValueError("daily replay evidence position shares are malformed")
    evidence_shares = {
        str(symbol): _positive_integer(shares, label=f"daily replay evidence shares/{symbol}")
        for symbol, shares in raw_shares.items()
    }
    if evidence_shares != dict(sorted(state.positions.items())):
        raise ValueError("daily replay evidence position shares differ from fills")
    raw_marks = evidence["close_marks"]
    if not isinstance(raw_marks, Mapping) or set(raw_marks) != set(evidence_shares):
        raise ValueError("daily replay evidence close marks differ from positions")
    marks = {
        str(symbol): _finite(mark, label=f"daily replay evidence close/{symbol}", minimum=0.0)
        for symbol, mark in raw_marks.items()
    }
    if any(mark <= 0.0 for mark in marks.values()):
        raise ValueError("daily replay evidence close marks must be positive")
    if trusted_close is not None:
        for symbol, mark in marks.items():
            _close(
                mark,
                trusted_close(symbol, row_date),
                label=f"daily replay evidence close versus frozen data/{symbol}/{row_date}",
            )
    values = {symbol: shares * marks[symbol] for symbol, shares in evidence_shares.items()}
    return evidence_cash, values, evidence_cash + sum(values.values())


def _validate_daily_ledger_row(
    *,
    raw_ledger: Any,
    row_date: str,
    evidence_cash: float,
    position_values: dict[str, float],
    equity: float,
    previous_equity: float,
) -> None:
    ledger = _require_exact_fields(raw_ledger, _LEDGER_FIELDS, label=f"daily replay ledger/{row_date}")
    _close(float(ledger["cash"]), evidence_cash, label="daily replay ledger cash")
    _close(float(ledger["equity"]), equity, label="daily replay ledger equity")
    _close(float(ledger["cash_weight"]), evidence_cash / equity, label="daily replay ledger cash weight")
    expected_weights = {symbol: value / equity for symbol, value in position_values.items()}
    observed_weights = ledger["position_weights"]
    if not isinstance(observed_weights, Mapping) or set(observed_weights) != set(expected_weights):
        raise ValueError("daily replay ledger position weights differ from positions")
    for symbol, expected_weight in expected_weights.items():
        _close(
            float(observed_weights[symbol]),
            expected_weight,
            label=f"daily replay ledger position weight/{symbol}",
        )
    gross = sum(abs(value) for value in position_values.values()) / equity
    net = sum(position_values.values()) / equity
    _close(float(ledger["gross_exposure"]), gross, label="daily replay gross exposure")
    _close(float(ledger["net_exposure"]), net, label="daily replay net exposure")
    _close(float(ledger["daily_pnl"]), equity - previous_equity, label="daily replay ledger PnL")


def _validate_final_replay_state(
    state: _ReplayState,
    *,
    account: Mapping[str, Any],
    positions: Mapping[str, Any],
) -> None:
    final_cash = _finite(account.get("cash"), label="engine final account cash", minimum=0.0)
    _close(state.cash, final_cash, label="daily replay cash versus final account")
    final_position_shares: dict[str, int] = {}
    for symbol, raw_position in positions.items():
        if not isinstance(raw_position, Mapping):
            raise ValueError("engine final position is malformed")
        raw_shares_value = raw_position.get("shares")
        if (
            isinstance(raw_shares_value, bool)
            or not isinstance(raw_shares_value, int)
            or raw_shares_value < 0
        ):
            raise ValueError("engine final position shares are malformed")
        position_shares = int(raw_shares_value)
        if position_shares:
            final_position_shares[str(symbol)] = position_shares
    if state.positions != final_position_shares:
        raise ValueError("daily replay positions differ from final account")


def _validate_daily_replay_evidence(
    *,
    result: Mapping[str, Any],
    attribution: Mapping[str, Any],
    account: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
    positions: Mapping[str, Any],
    economic_start: str,
    economic_end: str,
    trusted_sessions: Sequence[str] | None,
    trusted_close: Callable[[str, str], float] | None,
) -> None:
    """Rebuild every derived daily value from fills plus verified closing marks."""

    evidence_value = result.get("daily_replay_evidence")
    equity_curve_value = result.get("equity_curve")
    if not isinstance(evidence_value, list) or not evidence_value:
        raise ValueError("engine result daily replay evidence is required")
    if not isinstance(equity_curve_value, list) or not equity_curve_value:
        raise ValueError("engine result equity curve is required for daily replay evidence")
    ledger_value = attribution.get("daily_ledger")
    if not isinstance(ledger_value, list) or not ledger_value:
        raise ValueError("economic attribution daily ledger is required for replay")
    evidence_by_date, evidence_dates = _validated_evidence_rows(
        evidence_value,
        economic_start=economic_start,
        economic_end=economic_end,
        trusted_sessions=trusted_sessions,
        trusted_close=trusted_close,
    )
    curve_by_date = _validated_equity_curve(
        equity_curve_value,
        evidence_dates=evidence_dates,
        ledger_value=ledger_value,
    )
    fills_by_date: dict[str, list[Mapping[str, Any]]] = {}
    for raw_fill in fills:
        fill_date = str(raw_fill.get("fill_date", ""))
        fills_by_date.setdefault(fill_date, []).append(raw_fill)
    initial_cash = _finite(account.get("initial_cash"), label="daily replay initial cash", minimum=0.0)
    state = _ReplayState(cash=initial_cash, positions={}, previous_equity=initial_cash)
    for row_date, raw_ledger in zip(evidence_dates, ledger_value, strict=True):
        _apply_daily_fills(state, fills_by_date.get(row_date, []))
        evidence_cash, position_values, equity = _daily_replay_position_values(
            evidence=evidence_by_date[row_date],
            state=state,
            row_date=row_date,
            trusted_close=trusted_close,
        )
        _close(
            curve_by_date[row_date],
            equity,
            label="daily replay evidence versus engine equity curve",
        )
        _validate_daily_ledger_row(
            raw_ledger=raw_ledger,
            row_date=row_date,
            evidence_cash=evidence_cash,
            position_values=position_values,
            equity=equity,
            previous_equity=state.previous_equity,
        )
        state.previous_equity = equity
    _validate_final_replay_state(state, account=account, positions=positions)


# Stable domain stages used by attribution validation owners.
DAILY_REPLAY_FIELDS = _DAILY_REPLAY_FIELDS
LEDGER_FIELDS = _LEDGER_FIELDS
close_attribution_values = _close
positive_attribution_integer = _positive_integer
require_exact_attribution_fields = _require_exact_fields
validate_daily_replay_evidence = _validate_daily_replay_evidence
