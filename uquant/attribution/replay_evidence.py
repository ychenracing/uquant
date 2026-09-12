"""Daily same-close replay evidence construction and validation."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any

from ..models.corporate_action import CorporateAction, DividendTaxDebit
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
    rights = {state.action.symbol for state in account.corporate_actions
              if state.ex_processed_date and state.action.share_ratio and not state.distributed_date}
    if set(close_prices) != set(position_shares) | rights:
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
    from ..account.corporate_actions import corporate_action_receivable

    return {
        **({"corporate_action_receivable": corporate_action_receivable(account, close_prices)} if account.corporate_actions else {}),
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
        row = _require_exact_fields(raw_row, _DAILY_REPLAY_FIELDS | ({"corporate_action_receivable"} if isinstance(raw_row, Mapping) and "corporate_action_receivable" in raw_row else set()), label=f"daily replay evidence row {index}")
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
    extra_mark_symbols: set[str] | None = None,
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
    if not isinstance(raw_marks, Mapping) or set(raw_marks) != set(evidence_shares) | (extra_mark_symbols or set()):
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
    ledger = _require_exact_fields(raw_ledger, _LEDGER_FIELDS | ({"corporate_action_receivable", "corporate_action_share_rights_value"} if isinstance(raw_ledger, Mapping) and "corporate_action_receivable" in raw_ledger else set()), label=f"daily replay ledger/{row_date}")
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
    trusted_corporate_actions: Sequence[CorporateAction] | None = None,
    trusted_tax_debits: Sequence[DividendTaxDebit] | None = None,
) -> None:
    """Rebuild every derived daily value from fills plus verified closing marks."""

    if account.get("corporate_actions") or account.get("dividend_tax_debits") or trusted_corporate_actions is not None or trusted_tax_debits is not None:
        validate_corporate_sources(
            account, economic_start=economic_start, economic_end=economic_end,
            trusted_corporate_actions=trusted_corporate_actions, trusted_tax_debits=trusted_tax_debits,
        )
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
    if account.get("corporate_actions"):
        _validate_corporate_replay(account=account, evidence_by_date=evidence_by_date,
            evidence_dates=evidence_dates, ledger_value=ledger_value, curve_by_date=curve_by_date,
            fills_by_date=fills_by_date, trusted_close=trusted_close,
            trusted_corporate_actions=trusted_corporate_actions, trusted_tax_debits=trusted_tax_debits)
        return
    if any("corporate_action_receivable" in row for row in evidence_by_date.values()):
        raise ValueError("corporate action replay evidence lacks source state")
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


def corporate_account_from_payload(payload: Mapping[str, Any]) -> AccountState:
    """Decode only source accounting facts; never accept derived report totals."""
    from ..account.corporate_actions import (
        corporate_action_state_from_payload,
        validate_corporate_action_state,
    )
    from ..models.corporate_action import DividendTaxDebit
    from ..types import Fill, Position, Tranche

    account = AccountState.empty(float(payload["initial_cash"]))
    account.cash = float(payload["cash"])
    account.fills = [Fill(**raw) for raw in payload["fills"]]
    account.positions = {
        symbol: Position(**{**raw, "tranches": [Tranche(**lot) for lot in raw["tranches"]]})
        for symbol, raw in payload["positions"].items()
    }
    account.corporate_actions = [corporate_action_state_from_payload(raw) for raw in payload.get("corporate_actions", [])]
    account.dividend_tax_debits = [DividendTaxDebit(**raw) for raw in payload.get("dividend_tax_debits", [])]
    validate_corporate_action_state(account)
    return account


def economic_positions(account: AccountState) -> AccountState:
    """Include recognized, undelivered stock rights with their true cost basis."""
    from copy import deepcopy
    from dataclasses import replace

    from ..account.corporate_actions import corporate_action_share_award, validate_corporate_action_state
    from ..types import Position

    validate_corporate_action_state(account)
    if not account.corporate_actions:
        return account
    result = deepcopy(account)
    for state in account.corporate_actions:
        if not state.ex_processed_date or state.distributed_date:
            continue
        action = state.action
        position = result.positions.setdefault(action.symbol, Position(symbol=action.symbol))
        for index, lot in enumerate(state.entitled_lots):
            quantity = corporate_action_share_award(lot, action)
            if quantity:
                position.tranches.append(replace(
                    lot, tranche_id=f"ca:{action.action_id}:{index}", shares=quantity,
                    avg_cost=lot.avg_cost / (1 + action.share_ratio),
                ))
                position.shares += quantity
    return result


def corporate_origin_cash(account: AccountState) -> dict[tuple[str, str, str], tuple[float, float]]:
    """Assign dividends and statutory FIFO tax to the original acquisition."""
    from ..account.corporate_actions import (
        _fifo_inventory,
        dividend_tax_rate,
        validate_corporate_action_state,
    )

    validate_corporate_action_state(account)
    totals: dict[tuple[str, str, str], tuple[float, float]] = {}
    origins = {f"fill:{index}": (fill.symbol, fill.event_id, fill.fill_date)
               for index, fill in enumerate(account.fills) if fill.side == "BUY"}
    for state in account.corporate_actions:
        for index, tax_lot in enumerate(state.tax_lots):
            if tax_lot.lot_id not in origins:
                raise ValueError("corporate action tax lot lacks originating acquisition")
            origins[f"action:{state.action.action_id}:{index}"] = origins[tax_lot.lot_id]
        if not state.ex_processed_date:
            continue
        for lot in state.entitled_lots:
            key = (state.action.symbol, lot.event_id, lot.entry_date)
            income, tax = totals.get(key, (0.0, 0.0))
            totals[key] = (income + lot.shares * state.action.cash_per_share, tax)
        sales: list[tuple[str, str, int]] = []
        _fifo_inventory(account, state.action.symbol, sales=sales)
        for tax_lot in state.tax_lots:
            key = origins[tax_lot.lot_id]
            income, tax = totals.get(key, (0.0, 0.0))
            assessed = sum(quantity * state.action.cash_per_share * dividend_tax_rate(tax_lot.acquired_date, day)
                           for lot_id, day, quantity in sales
                           if lot_id == tax_lot.lot_id and day > state.action.record_date)
            totals[key] = (income, tax + assessed)
    return totals


def _replay_corporate_fill(account: AccountState, raw: Mapping[str, Any]) -> None:
    from ..account.corporate_actions import settle_dividend_tax
    from ..execution.tranches import rebuild_position_from_tranches
    from ..types import Fill, Position, Tranche

    fill = Fill(**raw)
    fees = fill.commission + fill.stamp_duty + fill.transfer_fee
    position: Position | None
    if fill.side == "BUY":
        position = account.positions.setdefault(fill.symbol, Position(symbol=fill.symbol))
        identity = {name: getattr(fill, name) for name in (
            "event_id", "origin_subsystem", "mechanism", "origin_lifecycle", "replaces_symbol",
            "industry_at_entry", "industry_manifest_sha256", "grant_id", "epoch_id",
        )}
        position.tranches.append(Tranche(
            tranche_id=f"{fill.fill_date}:{fill.symbol}:{len(position.tranches) + 1}",
            lifecycle=fill.lifecycle, shares=fill.shares,
            avg_cost=(fill.gross_value + fees) / fill.shares,
            entry_date=fill.fill_date, sellable_date=fill.fill_date,
            highest_close=fill.price, **identity,
        ))
        account.cash -= fill.gross_value + fees
    else:
        position = account.positions.get(fill.symbol)
        if position is None:
            raise ValueError("corporate replay SELL has no reconstructed position")
        settle_dividend_tax(account, symbol=fill.symbol, shares=fill.shares, date=fill.fill_date)
        by_id = {lot.tranche_id: lot for lot in position.tranches}
        for allocation in fill.sold_tranches:
            lot = by_id.get(str(allocation["tranche_id"]))
            shares = int(allocation["shares"])
            if lot is None or shares <= 0 or shares > lot.shares or allocation["event_id"] != lot.event_id:
                raise ValueError("corporate replay sold lot differs from reconstructed acquisition")
            _close(float(allocation["cost_basis"]), shares * lot.avg_cost, label="corporate replay sold basis")
            lot.shares -= shares
        position.tranches = [lot for lot in position.tranches if lot.shares]
        account.cash += fill.gross_value - fees
    rebuild_position_from_tranches(position)
    if not position.shares:
        account.positions.pop(fill.symbol, None)
    account.fills.append(fill)


def _validate_corporate_replay(
    *, account: Mapping[str, Any], evidence_by_date: Mapping[str, Mapping[str, Any]],
    evidence_dates: list[str], ledger_value: list[Any], curve_by_date: dict[str, float],
    fills_by_date: Mapping[str, list[Mapping[str, Any]]], trusted_close: Callable[[str, str], float] | None,
    trusted_corporate_actions: Sequence[CorporateAction] | None,
    trusted_tax_debits: Sequence[DividendTaxDebit] | None,
) -> None:
    from dataclasses import asdict

    from ..account.corporate_actions import (
        apply_corporate_actions,
        corporate_action_receivable,
        corporate_action_share_rights,
    )

    final = corporate_account_from_payload(account)
    replay = AccountState.empty(final.initial_cash)
    actions, debits = validate_corporate_sources(
        account, economic_start=evidence_dates[0], economic_end=evidence_dates[-1],
        trusted_corporate_actions=trusted_corporate_actions, trusted_tax_debits=trusted_tax_debits,
    )
    previous_equity = replay.initial_cash
    for day, ledger in zip(evidence_dates, ledger_value, strict=True):
        apply_corporate_actions(replay, actions, date=day, phase="open", tax_debits=debits)
        for fill in fills_by_date.get(day, []):
            _replay_corporate_fill(replay, fill)
        apply_corporate_actions(replay, actions, date=day, phase="close", tax_debits=debits)
        evidence = evidence_by_date[day]
        receivable = corporate_action_receivable(replay, evidence["close_marks"])
        _close(float(evidence.get("corporate_action_receivable", 0.0)), receivable, label="source-rebuilt corporate receivable")
        native = _ReplayState(replay.cash, {symbol: position.shares for symbol, position in replay.positions.items()}, previous_equity)
        cash, values, base_equity = _daily_replay_position_values(
            evidence=evidence, state=native, row_date=day, trusted_close=trusted_close,
            extra_mark_symbols={state.action.symbol for state in replay.corporate_actions
                                if state.ex_processed_date and state.action.share_ratio and not state.distributed_date},
        )
        equity = base_equity + receivable
        rights_value = 0.0
        for symbol, shares in corporate_action_share_rights(replay).items():
            value = shares * float(evidence["close_marks"][symbol])
            rights_value += value
            values[symbol] = values.get(symbol, 0.0) + value
        _close(float(ledger.get("corporate_action_share_rights_value", 0.0)), rights_value, label="corporate replay ledger share rights")
        _close(curve_by_date[day], equity, label="corporate replay equity curve")
        _validate_daily_ledger_row(raw_ledger=ledger, row_date=day, evidence_cash=cash,
                                   position_values=values, equity=equity, previous_equity=previous_equity)
        _close(float(ledger.get("corporate_action_receivable", 0.0)), receivable, label="corporate replay ledger receivable")
        previous_equity = equity
    _validate_final_replay_state(
        _ReplayState(replay.cash, {symbol: position.shares for symbol, position in replay.positions.items()}, previous_equity),
        account=account, positions=account["positions"],
    )
    for symbol, position in final.positions.items():
        reconstructed = {lot.tranche_id: lot for lot in replay.positions[symbol].tranches} if position.shares else {}
        for lot in position.tranches:
            origin = reconstructed.get(lot.tranche_id)
            if origin is None or (lot.shares, lot.event_id, lot.entry_date) != (origin.shares, origin.event_id, origin.entry_date):
                raise ValueError("corporate replay final lot origin differs")
            _close(lot.avg_cost, origin.avg_cost, label="corporate replay final lot basis")
    if len(replay.corporate_actions) != len(final.corporate_actions):
        raise ValueError("corporate replay source state coverage differs")
    for observed, expected in zip(final.corporate_actions, replay.corporate_actions, strict=True):
        left, right = asdict(observed), asdict(expected)
        left.pop("entitled_lots")
        right.pop("entitled_lots")
        if left != right:
            raise ValueError("corporate replay final entitlements or settlement differs")
        def economic_lots(state: Any) -> list[tuple[Any, ...]]:
            return [(lot.tranche_id, lot.event_id, lot.entry_date, lot.shares, lot.avg_cost)
                    for lot in state.entitled_lots]
        if economic_lots(observed) != economic_lots(expected):
            raise ValueError("corporate replay record-date origin or cost differs")


def validate_corporate_sources(
    account: Mapping[str, Any], *, economic_start: str, economic_end: str,
    trusted_corporate_actions: Sequence[CorporateAction] | None,
    trusted_tax_debits: Sequence[DividendTaxDebit] | None,
) -> tuple[list[CorporateAction], list[DividendTaxDebit]]:
    """Bind action/debit facts to caller-verified input, outside the report."""
    from dataclasses import asdict

    if trusted_corporate_actions is None or trusted_tax_debits is None:
        raise ValueError("corporate action replay requires independently trusted action and tax-debit sources")
    actions = [action for action in trusted_corporate_actions
               if economic_start <= action.record_date <= economic_end]
    expected_actions = {action.action_id: asdict(action) for action in actions}
    if len(expected_actions) != len(actions):
        raise ValueError("trusted corporate action sources contain duplicate identities")
    raw_states = account.get("corporate_actions", [])
    if not isinstance(raw_states, list):
        raise ValueError("corporate action source state is malformed")
    observed_actions = {state["action"]["action_id"]: state["action"] for state in raw_states}
    if len(observed_actions) != len(raw_states) or observed_actions != expected_actions:
        raise ValueError("corporate action source state differs from independently trusted actions")
    debits = [debit for debit in trusted_tax_debits
              if economic_start <= debit.date <= economic_end and debit.action_id in expected_actions]
    expected_debits = {debit.debit_id: asdict(debit) for debit in debits}
    if len(expected_debits) != len(debits):
        raise ValueError("trusted tax-debit sources contain duplicate identities")
    raw_debits = account.get("dividend_tax_debits", [])
    if not isinstance(raw_debits, list):
        raise ValueError("corporate action tax-debit state is malformed")
    observed_debits = {debit["debit_id"]: debit for debit in raw_debits}
    if len(observed_debits) != len(raw_debits) or observed_debits != expected_debits:
        raise ValueError("corporate action tax-debit source state differs from independently trusted debits")
    return actions, debits
