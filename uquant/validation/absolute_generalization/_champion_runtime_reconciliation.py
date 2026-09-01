"""Pure champion replay projections shared by its producer and consumer."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, Sequence
from typing import cast

from uquant.account import account_from_dict, economic_state_sha256
from uquant.attribution import (
    build_daily_ledger_row,
    build_economic_attribution,
    validate_attribution_against_engine_result,
)
from uquant.contracts.strict_json import canonical_json_sha256
from uquant.types import AccountOrder, AccountState, Position

from .metrics import assert_unique_execution_rows


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"champion runtime {label} is malformed")
    return cast(Mapping[str, object], value)


def _rows(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"champion runtime {label} is malformed")
    return cast(Sequence[object], value)


def _finite_runtime_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"champion runtime {label} is malformed")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"champion runtime {label} is malformed")
    return result


def _runtime_close_marks(value: object, *, label: str) -> dict[str, float]:
    return {
        symbol: _finite_runtime_number(mark, label=f"{label}/{symbol}")
        for symbol, mark in _mapping(value, label=label).items()
    }


def _strip(value: object, ignored: frozenset[str]) -> object:
    if isinstance(value, Mapping):
        return {str(key): _strip(item, ignored) for key, item in value.items() if str(key) not in ignored}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_strip(item, ignored) for item in value]
    return value


def project_champion_account(raw: Mapping[str, object]) -> dict[str, object]:
    """Losslessly rename domain predicate booleans away from pass-claim syntax."""

    projected = copy.deepcopy(dict(raw))
    repair = _mapping(projected.get("flat_book_capital_repair"), label="capital repair")
    for item in _rows(repair.get("predicate_results"), label="repair predicates"):
        predicate = cast(dict[str, object], _mapping(item, label="repair predicate"))
        if (
            set(predicate)
            != {
                "authoritative_state",
                "code",
                "economic_authority",
                "orphan_residue",
                "passed",
            }
            or type(predicate["passed"]) is not bool
        ):
            raise ValueError("champion runtime repair predicate fields differ")
        predicate["satisfied"] = predicate.pop("passed")
    return projected


def decode_champion_account(raw: Mapping[str, object]) -> dict[str, object]:
    """Restore the exact AccountState field name after manifest transport."""

    decoded = copy.deepcopy(dict(raw))
    repair = _mapping(decoded.get("flat_book_capital_repair"), label="capital repair")
    for item in _rows(repair.get("predicate_results"), label="repair predicates"):
        predicate = cast(dict[str, object], _mapping(item, label="repair predicate"))
        raw_fields = {
            "authoritative_state",
            "code",
            "economic_authority",
            "orphan_residue",
            "passed",
        }
        if set(predicate) == raw_fields and type(predicate["passed"]) is bool:
            continue
        if (
            set(predicate)
            != {
                "authoritative_state",
                "code",
                "economic_authority",
                "orphan_residue",
                "satisfied",
            }
            or type(predicate["satisfied"]) is not bool
        ):
            raise ValueError("champion runtime repair predicate fields differ")
        predicate["passed"] = predicate.pop("satisfied")
    return decoded


def project_champion_baseline_views(
    result: Mapping[str, object], ignored: frozenset[str]
) -> dict[str, object]:
    """Apply the frozen legacy physical-ID normalization to raw replay facts."""

    trace = _rows(result.get("decision_trace"), label="decision trace")
    targets = [
        {"date": row["date"], "targets": row["targets"], "target_gross": row["target_gross"]}
        for item in trace
        for row in (_mapping(item, label="decision row"),)
    ]
    orders = copy.deepcopy(list(_rows(result.get("order_ledger"), label="order ledger")))
    physical_order_ids: dict[str, str] = {}
    for index, item in enumerate(orders, start=1):
        order = cast(dict[str, object], _mapping(item, label="order"))
        canonical = f"ECONOMIC_ORDER_{index:06d}"
        physical_order_ids[str(order["order_id"])] = canonical
        order["order_id"] = canonical
    account = _mapping(result.get("final_account"), label="final account")
    fills = copy.deepcopy(list(_rows(account.get("fills"), label="fill ledger")))
    event_order_ids: dict[str, str] = {}
    for item in fills:
        fill = cast(dict[str, object], _mapping(item, label="fill"))
        event_id = str(fill.get("event_id", ""))
        if event_id:
            event_order_ids.setdefault(event_id, f"ECONOMIC_ORDER_{len(event_order_ids) + 1:06d}")
        matched = event_order_ids.get(event_id) or physical_order_ids.get(str(fill["order_id"]))
        if matched is None:
            raise ValueError("champion runtime fill has no matching economic order")
        fill["order_id"] = matched
    return {
        "targets": _strip(targets, ignored),
        "orders": _strip(orders, ignored),
        "fills": _strip(fills, ignored),
        "positions": _strip(result.get("daily_replay_evidence"), ignored),
        "equity": _strip(result.get("equity_curve"), ignored),
    }


def _equity_values(result: Mapping[str, object]) -> tuple[float, ...]:
    values = tuple(
        _finite_runtime_number(_mapping(item, label="equity row").get("equity"), label="equity")
        for item in _rows(result.get("equity_curve"), label="equity curve")
    )
    if not values or any(value <= 0.0 for value in values):
        raise ValueError("champion runtime equity curve is malformed")
    return values


def _account_order_count(account: AccountState, economic_orders: Sequence[object]) -> int:
    groups: dict[tuple[str, ...], list[AccountOrder]] = {}
    for order in account.order_ledger:
        key = (
            ("STRATEGIC_GRANT_EVENT", order.grant_id, order.event_id)
            if order.grant_id and order.event_id
            else ("PHYSICAL_ORDER", order.order_id)
        )
        groups.setdefault(key, []).append(order)
    filled = [group for group in groups.values() if sum(item.filled_shares for item in group) > 0]
    economic_ids = {str(_mapping(item, label="economic order").get("order_id")) for item in economic_orders}
    if len(economic_ids) != len(economic_orders) or economic_ids != {group[0].order_id for group in filled}:
        raise ValueError("champion runtime economic order ledger differs from account")
    return len(filled)


def _terminal_equity(result: Mapping[str, object], account: AccountState) -> float:
    rows = _rows(result.get("daily_replay_evidence"), label="daily replay evidence")
    final = _mapping(rows[-1], label="terminal replay evidence")
    shares = _mapping(final.get("position_shares"), label="terminal shares")
    marks = _mapping(final.get("close_marks"), label="terminal marks")
    expected_shares = {
        symbol: position.shares for symbol, position in account.positions.items() if position.shares > 0
    }
    if (
        dict(shares) != expected_shares
        or set(marks) != set(shares)
        or _finite_runtime_number(final.get("cash"), label="terminal cash") != float(account.cash)
    ):
        raise ValueError("champion runtime terminal account evidence differs")
    return float(account.cash) + sum(
        int(cast(int, shares[symbol])) * _finite_runtime_number(marks[symbol], label="terminal mark")
        for symbol in shares
    )


def derive_champion_runtime_claims(
    result: Mapping[str, object], ignored: frozenset[str]
) -> dict[str, object]:
    """Recompute all champion summary claims from raw ProductionEngine facts."""

    account = account_from_dict(
        decode_champion_account(_mapping(result.get("final_account"), label="account")),
        require_hashes=False,
    )
    economic_orders = _rows(result.get("order_ledger"), label="order ledger")
    equity = _equity_values(result)
    final_equity = _terminal_equity(result, account)
    if equity[-1] != final_equity:
        raise ValueError("champion runtime terminal equity differs")
    peak = equity[0]
    maximum_drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        maximum_drawdown = max(maximum_drawdown, 1.0 - value / peak)
    wealth = final_equity / float(account.initial_cash)
    epoch_ids = [epoch.epoch_id for epoch in account.strategic_epochs]
    grant_ids = [epoch.grant_id for epoch in account.strategic_epochs]
    account_order_ids = [order.order_id for order in account.order_ledger]
    epochs = {epoch.epoch_id: epoch for epoch in account.strategic_epochs}
    preemptions = sum(
        predecessor is not None
        and bool(successor.first_fill_session)
        and (not predecessor.closed_session or successor.first_fill_session <= predecessor.closed_session)
        for successor in account.strategic_epochs
        for predecessor in (epochs.get(successor.previous_epoch_id),)
    )
    views = project_champion_baseline_views(result, ignored)
    return {
        "metrics": {
            "account_orders": _account_order_count(account, economic_orders),
            "final_equity": final_equity,
            "final_wealth": wealth,
            "max_drawdown": maximum_drawdown,
            "total_return": wealth - 1.0,
        },
        "path_sha256": {name: canonical_json_sha256(value) for name, value in views.items()},
        "duplicate_grant_count": len(grant_ids) - len(set(grant_ids)),
        "duplicate_order_count": len(account_order_ids) - len(set(account_order_ids)),
        "duplicate_epoch_count": len(epoch_ids) - len(set(epoch_ids)),
        "incumbent_epoch_count": sum(bool(epoch.first_fill_session) for epoch in account.strategic_epochs),
        "successor_capital_before_incumbent_exit_count": preemptions,
    }


def _rebuild_report_attribution(
    result: Mapping[str, object], account: AccountState, final_equity: float
) -> dict[str, object]:
    evidence = _rows(result.get("daily_replay_evidence"), label="report replay")
    decisions = _rows(result.get("decision_trace"), label="report decisions")
    if len(evidence) != len(decisions):
        raise ValueError("champion runtime report observation count differs")
    ledger: list[dict[str, object]] = []
    previous_equity = float(account.initial_cash)
    sessions: list[str] = []
    for raw_evidence, raw_decision in zip(evidence, decisions, strict=True):
        observed = _mapping(raw_evidence, label="report replay row")
        decision = _mapping(raw_decision, label="report decision")
        session = str(observed.get("date"))
        if decision.get("date") != observed.get("date"):
            raise ValueError("champion runtime report decision session differs")
        shares = _mapping(observed.get("position_shares"), label="report shares")
        pseudo_account = AccountState.empty(float(account.initial_cash))
        pseudo_account.cash = _finite_runtime_number(observed.get("cash"), label="report cash")
        pseudo_account.positions = {
            symbol: Position(symbol=symbol, shares=int(cast(int, quantity)))
            for symbol, quantity in shares.items()
        }
        risk = _mapping(decision.get("risk"), label="report risk")
        targets = _rows(decision.get("targets"), label="report targets")
        target_gross = _finite_runtime_number(
            decision.get("target_gross"), label="report target gross"
        )
        risk_gross_cap = _finite_runtime_number(
            risk.get("target_gross_cap"), label="report risk cap"
        )
        system_gross_cap = _finite_runtime_number(
            risk.get("system_gross_cap"), label="report system cap"
        )
        row = build_daily_ledger_row(
            date=session,
            account=pseudo_account,
            close_prices=_runtime_close_marks(observed.get("close_marks"), label="report marks"),
            previous_equity=previous_equity,
            target_weights={
                str(target["symbol"]): _finite_runtime_number(
                    target.get("weight"), label="report target weight"
                )
                for item in targets
                for target in (_mapping(item, label="report target"),)
            },
            target_gross=target_gross,
            risk_gross_cap=risk_gross_cap,
            system_gross_cap=system_gross_cap,
            risk_state=str(risk.get("state")),
            opportunity=str(decision.get("opportunity")),
        )
        if row["binding_owner"] == "STRATEGY_RETENTION_OVERRIDE":
            raise ValueError("champion runtime report capital authority differs")
        ledger.append(row)
        previous_equity = float(row["equity"])
        sessions.append(session)
    final_observation = _mapping(evidence[-1], label="report final replay")
    attribution = build_economic_attribution(
        account=account,
        final_prices=_runtime_close_marks(final_observation.get("close_marks"), label="final marks"),
        sessions=sessions,
        economic_start=sessions[0],
        economic_end=sessions[-1],
        final_equity=final_equity,
        daily_ledger=ledger,
        benchmark_close={session: 1.0 for session in sessions},
    )
    by_symbol = _mapping(attribution.get("by_symbol"), label="report symbols")
    validation_result = dict(result)
    validation_result.update(
        attribution=attribution,
        start=sessions[0],
        end=sessions[-1],
        final_equity=final_equity,
        final_wealth=final_equity / float(account.initial_cash),
        gross_turnover=sum(fill.gross_value for fill in account.fills) / float(account.initial_cash),
        symbol_pnl={
            symbol: _finite_runtime_number(
                _mapping(bucket, label="report symbol bucket").get("total_pnl"),
                label="report symbol pnl",
            )
            for symbol, bucket in by_symbol.items()
        },
    )
    return cast(
        dict[str, object],
        validate_attribution_against_engine_result(
            validation_result,
            economic_start=sessions[0],
            economic_end=sessions[-1],
            require_daily_replay_evidence=True,
        ),
    )


def derive_report_runtime_claims(
    result: Mapping[str, object], allowed_symbols: Sequence[str]
) -> tuple[dict[str, object], dict[str, object]]:
    """Recompute report-13 accounting and completion claims from raw replay facts."""

    raw_account = _mapping(result.get("final_account"), label="report account")
    account = account_from_dict(decode_champion_account(raw_account), require_hashes=False)
    trace = [
        _mapping(item, label="report decision")
        for item in _rows(result.get("decision_trace"), label="report trace")
    ]
    assert_unique_execution_rows(
        final_account=raw_account,
        trace=trace,
        allowed_symbols=allowed_symbols,
    )
    equity = _equity_values(result)
    final_equity = _terminal_equity(result, account)
    if equity[-1] != final_equity or not trace:
        raise ValueError("champion runtime report terminal equity differs")
    canonical_attribution = _rebuild_report_attribution(result, account, final_equity)
    accounting = _mapping(canonical_attribution.get("accounting"), label="report accounting")
    owners = sorted({epoch.owner_symbol for epoch in account.strategic_epochs if epoch.first_fill_session})
    report = {
        "initial_cash": float(account.initial_cash),
        "cash": float(account.cash),
        "position_market_value": final_equity - float(account.cash),
        "realized_pnl": _finite_runtime_number(accounting.get("realized_pnl"), label="realized pnl"),
        "open_pnl": _finite_runtime_number(accounting.get("open_pnl"), label="open pnl"),
        "final_equity": final_equity,
        "maximum_target_gross": max(
            _finite_runtime_number(row.get("target_gross"), label="target gross") for row in trace
        ),
        "minimum_risk_target_gross_cap": min(
            _finite_runtime_number(
                _mapping(row.get("risk"), label="risk").get("target_gross_cap"),
                label="risk cap",
            )
            for row in trace
        ),
        "owner_symbols": owners,
        "unexpected_owner_symbols": sorted(set(owners).difference(allowed_symbols)),
    }
    completion = {
        "observed_sessions": len(trace),
        "account_orders": _account_order_count(
            account, _rows(result.get("order_ledger"), label="report orders")
        ),
        "final_equity": final_equity,
        "final_account_sha256": economic_state_sha256(account),
        "trace_sha256": canonical_json_sha256(trace),
    }
    return report, completion


__all__: tuple[str, ...] = ()
