"""Private raw-ledger reconciliation for absolute-generalization metrics."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from uquant.market import ReplayUniverse
from uquant.models.strategic_universe import StrategicUniverseRoles, build_strategic_universe_roles

from ._execution_chain_reconciliation import validate_exact_execution_chain
from ._metric_primitives import (
    metric_iso_session,
    metric_mapping,
    metric_number,
    metric_payload_mapping,
    metric_positive_number,
    metric_rows,
    metric_text,
    metric_trace_row,
)
from ._physical_identity import physical_fill_identity_map
from .metrics import (
    CellMetrics,
    EpochFact,
    EventEvidence,
    actual_epoch_facts_from_rows,
    assert_unique_execution_rows,
    longest_healthy_zero_target_streak,
    metric_healthy_trace_row,
    repair_episode_facts_from_trace,
)
from .replay import AbsoluteGeneralizationReplay, AbsoluteGeneralizationReplayObservation

_EVENT_NAMES: Final = (
    "first_divergence", "qualification_to_grant", "grant_to_target", "target_to_order",
    "order_to_fill", "fill_to_active_epoch", "failed_grant_retry",
    "terminal_zero_strategic_target_state",)
_TRADING_SESSIONS_PER_YEAR: Final = 242.0
_CHAIN_IDENTITY_FIELDS: Final = ("event_id", "epoch_id", "grant_id", "symbol", "side")


def _role_coverage(symbols: Sequence[str], available: Sequence[str]) -> float:
    if not symbols:
        return 1.0
    available_set = set(available)
    return sum(symbol in available_set for symbol in symbols) / len(symbols)


def _validated_roles(observation: AbsoluteGeneralizationReplayObservation) -> StrategicUniverseRoles:
    roles = observation.roles
    if roles.as_of != observation.session:
        raise ValueError("absolute generalization role session differs")
    rebuilt = build_strategic_universe_roles(
        as_of=roles.as_of,
        tradable_symbols=roles.tradable_symbols,
        qualification_reference_symbols=roles.qualification_reference_symbols,
        risk_reference_symbols=roles.risk_reference_symbols,
        industries=dict(roles.point_in_time_industries),
        available_symbols=roles.available_symbols,
    )
    for field, label in (
        ("tradable_symbols", "tradable role membership"),
        ("qualification_reference_symbols", "qualification role membership"),
        ("risk_reference_symbols", "risk role membership"),
        ("available_symbols", "available role membership"),
        ("unavailable_reference_symbols", "unavailable role membership"),
        ("point_in_time_industries", "industry role membership"),
        ("tradable_identity", "tradable role identity"),
        ("qualification_reference_identity", "qualification role identity"),
        ("risk_reference_identity", "risk role identity"),
        ("point_in_time_industry_identity", "industry role identity"),
    ):
        if getattr(roles, field) != getattr(rebuilt, field):
            raise ValueError(f"absolute generalization {label} differs")
    if observation.expected_but_unavailable_symbols != rebuilt.unavailable_reference_symbols:
        raise ValueError("absolute generalization unavailable role evidence differs")
    index_symbols = tuple(
        sorted(set(rebuilt.risk_reference_symbols) - set(rebuilt.qualification_reference_symbols))
    )
    replay_universe = ReplayUniverse.from_symbols(
        tradable_symbols=rebuilt.tradable_symbols,
        reference_symbols=rebuilt.qualification_reference_symbols,
        index_symbols=index_symbols,
    )
    if observation.replay_universe_identity != replay_universe.identity_sha256:
        raise ValueError("absolute generalization replay universe identity differs")
    return rebuilt


def _session_role_values(replay: AbsoluteGeneralizationReplay) -> tuple[float, float, float, int, bool]:
    tradable_coverages: list[float] = []
    qualification_coverages: list[float] = []
    risk_coverages: list[float] = []
    witness_sessions = 0
    identities_consistent = True
    for observation in replay.observations:
        roles = _validated_roles(observation)
        for value, label in (
            (roles.tradable_identity, "tradable role identity"),
            (roles.qualification_reference_identity, "qualification role identity"),
            (roles.risk_reference_identity, "risk role identity"),
            (roles.point_in_time_industry_identity, "industry role identity"),
            (observation.replay_universe_identity, "replay universe identity"),
        ):
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"absolute generalization {label} is malformed")
        tradable_coverages.append(_role_coverage(roles.tradable_symbols, roles.available_symbols))
        qualification_coverages.append(
            _role_coverage(roles.qualification_reference_symbols, roles.available_symbols)
        )
        risk_coverages.append(_role_coverage(roles.risk_reference_symbols, roles.available_symbols))
        if roles.qualification_reference_symbols and roles.risk_reference_symbols:
            witness_sessions += 1
        identities_consistent = identities_consistent and all(
            (
                roles.tradable_identity,
                roles.qualification_reference_identity,
                roles.risk_reference_identity,
                roles.point_in_time_industry_identity,
            )
        )
    return (
        sum(tradable_coverages) / len(tradable_coverages),
        sum(qualification_coverages) / len(qualification_coverages),
        sum(risk_coverages) / len(risk_coverages),
        witness_sessions,
        identities_consistent,
    )


def _terminal_healthy_zero_strategic_streak(trace: Sequence[Mapping[str, object]]) -> int:
    current = 0
    for row in reversed(trace):
        has_strategic = any(
            target.get("origin_subsystem") == "STRATEGIC"
            and metric_positive_number(target.get("weight")) > 0.0
            for target in metric_rows(row.get("targets", ()), label="trace targets")
        )
        if metric_healthy_trace_row(row) and not has_strategic:
            current += 1
        else:
            break
    return current


def _failed_grant_retry_sessions(*, final_account: Mapping[str, object], trace: Sequence[Mapping[str, object]]) -> int:
    epochs = metric_rows(final_account.get("strategic_epochs", ()), label="epoch ledger")
    expired = {
        metric_text(epoch.get("grant_id"), label="epoch grant"): metric_iso_session(
            epoch.get("closed_session", ""), label="expired epoch closed", empty=True
        )
        for epoch in epochs
        if epoch.get("realized_status") == "EXPIRED"
    }
    maxima = 0
    for row in trace:
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw_grant = risk.get("strategic_grant")
        if not isinstance(raw_grant, Mapping):
            continue
        previous = metric_text(
            metric_mapping(raw_grant, label="strategic grant").get("previous_grant_id", ""),
            label="previous grant",
            empty=True,
        )
        closed = expired.get(previous)
        if not closed:
            continue
        created = metric_iso_session(row.get("session"), label="retry session")
        maxima = max(
            maxima,
            sum(
                closed < metric_iso_session(item.get("session"), label="trace session") <= created
                and metric_healthy_trace_row(item)
                for item in trace
            ),
        )
    return maxima


@dataclass(frozen=True, slots=True)
class _Lot:
    shares: int
    unit_cost: float


@dataclass(frozen=True, slots=True)
class _FillEconomics:
    session: str
    symbol: str
    side: str
    shares: int
    gross: float
    fees: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float


@dataclass(frozen=True, slots=True)
class _AccountingEvidence:
    initial_cash: float
    final_equity: float
    cash: float
    realized_pnl: float
    open_pnl: float
    market_values: tuple[tuple[str, float], ...]


def _whole_shares(value: object, *, label: str) -> int:
    shares = metric_number(value, label=label, minimum=0.0)
    if shares <= 0.0 or not shares.is_integer():
        raise ValueError(f"absolute generalization {label} is malformed")
    return int(shares)


def _fill_economics(fill: Mapping[str, object]) -> _FillEconomics:
    shares = _whole_shares(fill.get("shares"), label="fill shares")
    price = metric_number(fill.get("price"), label="fill price", minimum=0.0)
    gross = metric_number(fill.get("gross_value"), label="fill gross value", minimum=0.0)
    if price <= 0.0 or not math.isclose(gross, shares * price, rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError("absolute generalization fill value does not reconcile")
    commission = metric_number(fill.get("commission", 0.0), label="fill commission", minimum=0.0)
    stamp = metric_number(fill.get("stamp_duty", 0.0), label="fill stamp_duty", minimum=0.0)
    transfer = metric_number(fill.get("transfer_fee", 0.0), label="fill transfer_fee", minimum=0.0)
    slippage = metric_number(
        fill.get("slippage_cost", 0.0), label="fill slippage cost", minimum=0.0
    )
    side = metric_text(fill.get("side"), label="fill side")
    if side not in {"BUY", "SELL"}:
        raise ValueError("absolute generalization fill side is malformed")
    return _FillEconomics(
        session=metric_iso_session(fill.get("fill_date"), label="fill session"),
        symbol=metric_text(fill.get("symbol"), label="fill symbol"),
        side=side,
        shares=shares,
        gross=gross,
        fees=commission + stamp + transfer,
        commission=commission,
        stamp_duty=stamp,
        transfer_fee=transfer,
        slippage_cost=slippage,
    )


def _apply_buy(
    *, fill: _FillEconomics, lots: dict[str, dict[str, _Lot]]
) -> float:
    if fill.stamp_duty != 0.0:
        raise ValueError("absolute generalization buy stamp duty differs")
    owned = lots.setdefault(fill.symbol, {})
    tranche_id = f"{fill.session}:{fill.symbol}:{len(owned) + 1}"
    if tranche_id in owned:
        raise ValueError("absolute generalization reconstructed tranche identity duplicates")
    owned[tranche_id] = _Lot(
        shares=fill.shares,
        unit_cost=(fill.gross + fill.commission + fill.transfer_fee) / fill.shares,
    )
    return -(fill.gross + fill.fees)


def _apply_sell(
    *,
    raw_fill: Mapping[str, object],
    fill: _FillEconomics,
    lots: dict[str, dict[str, _Lot]],
) -> tuple[float, float]:
    allocations = metric_rows(raw_fill.get("sold_tranches", ()), label="sold tranches")
    if not allocations:
        raise ValueError("absolute generalization sell fill lacks per-lot sold tranches")
    owned = lots.get(fill.symbol, {})
    allocated_shares = 0
    allocated_basis = 0.0
    allocated_costs = {field: 0.0 for field in ("commission", "stamp_duty", "transfer_fee", "slippage_cost")}
    seen: set[str] = set()
    for raw in allocations:
        tranche_id = metric_text(raw.get("tranche_id"), label="sold tranche identity")
        if tranche_id in seen or tranche_id not in owned:
            raise ValueError("absolute generalization sold tranche identity differs")
        seen.add(tranche_id)
        sold = _whole_shares(raw.get("shares"), label="sold tranche shares")
        lot = owned[tranche_id]
        if sold > lot.shares:
            raise ValueError("absolute generalization sold tranche exceeds reconstructed lot")
        for field in ("cost", "unit_cost", "avg_cost"):
            if not math.isclose(
                metric_number(raw.get(field), label=f"sold tranche {field}", minimum=0.0),
                lot.unit_cost,
                rel_tol=1e-9,
                abs_tol=1e-8,
            ):
                raise ValueError("absolute generalization sold tranche cost differs")
        basis = metric_number(raw.get("cost_basis"), label="sold tranche basis", minimum=0.0)
        if not math.isclose(basis, sold * lot.unit_cost, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError("absolute generalization sold tranche basis differs")
        allocated_shares += sold
        allocated_basis += basis
        for field in allocated_costs:
            allocated_costs[field] += metric_number(
                raw.get(field), label=f"sold tranche {field}", minimum=0.0
            )
        remaining = lot.shares - sold
        if remaining:
            owned[tranche_id] = _Lot(shares=remaining, unit_cost=lot.unit_cost)
        else:
            del owned[tranche_id]
    if allocated_shares != fill.shares:
        raise ValueError("absolute generalization sold tranche shares differ")
    for field, expected in (
        ("commission", fill.commission),
        ("stamp_duty", fill.stamp_duty),
        ("transfer_fee", fill.transfer_fee),
        ("slippage_cost", fill.slippage_cost),
    ):
        if not math.isclose(allocated_costs[field], expected, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(f"absolute generalization sold tranche {field} differs")
    return fill.gross - fill.fees, fill.gross - fill.fees - allocated_basis


def _final_position_evidence(
    *, account: Mapping[str, object], lots: Mapping[str, Mapping[str, _Lot]]
) -> dict[str, tuple[int, float]]:
    positions = metric_mapping(account.get("positions", {}), label="positions")
    expected: dict[str, tuple[int, float]] = {}
    for symbol, owned in lots.items():
        shares = sum(lot.shares for lot in owned.values())
        if shares:
            expected[symbol] = (shares, sum(lot.shares * lot.unit_cost for lot in owned.values()))
    if set(positions) != set(expected):
        raise ValueError("absolute generalization final positions differ from fill lots")
    for symbol, raw_position in positions.items():
        if not isinstance(symbol, str):
            raise ValueError("absolute generalization position symbol is malformed")
        position = metric_mapping(raw_position, label="account position")
        shares, basis = expected[symbol]
        if _whole_shares(position.get("shares"), label="account position shares") != shares:
            raise ValueError("absolute generalization position shares do not reconcile")
        average_cost = metric_number(
            position.get("avg_cost"), label="account position average cost", minimum=0.0
        )
        if not math.isclose(average_cost, basis / shares, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError("absolute generalization position average cost does not reconcile")
    return expected


def _accounting(
    *, replay: AbsoluteGeneralizationReplay, account: Mapping[str, object], fills: Sequence[Mapping[str, object]]
) -> _AccountingEvidence:
    account_initial = metric_number(account.get("initial_cash"), label="account initial cash", minimum=0.0)
    initial_cash = metric_number(replay.initial_cash, label="initial cash", minimum=0.0)
    if initial_cash <= 0.0 or not math.isclose(account_initial, initial_cash, rel_tol=0.0, abs_tol=1e-8):
        raise ValueError("absolute generalization initial cash does not reconcile")
    cash = initial_cash
    realized_pnl = 0.0
    lots: dict[str, dict[str, _Lot]] = {}
    previous_session = ""
    for raw_fill in fills:
        fill = _fill_economics(raw_fill)
        if fill.session < previous_session:
            raise ValueError("absolute generalization fill ledger is not chronological")
        previous_session = fill.session
        if fill.side == "BUY":
            cash += _apply_buy(fill=fill, lots=lots)
        else:
            cash_delta, realized_delta = _apply_sell(raw_fill=raw_fill, fill=fill, lots=lots)
            cash += cash_delta
            realized_pnl += realized_delta
    reported_cash = metric_number(account.get("cash"), label="cash", minimum=0.0)
    if not math.isclose(reported_cash, cash, rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError("absolute generalization cash does not reconcile")
    positions = _final_position_evidence(account=account, lots=lots)
    marks = replay.observations[-1].closing_marks
    marks_by_symbol = dict(marks)
    if len(marks_by_symbol) != len(marks) or tuple(sorted(marks_by_symbol.items())) != marks:
        raise ValueError("absolute generalization closing marks are malformed")
    if set(marks_by_symbol) != set(positions):
        raise ValueError("absolute generalization closing mark positions differ")
    market_values: dict[str, float] = {}
    open_pnl = 0.0
    for symbol, (shares, basis) in positions.items():
        mark = metric_number(marks_by_symbol[symbol], label="closing mark", minimum=0.0)
        if mark <= 0.0:
            raise ValueError("absolute generalization closing mark is malformed")
        market_values[symbol] = shares * mark
        open_pnl += market_values[symbol] - basis
    derived_equity = cash + sum(market_values.values())
    observed_equity = metric_number(
        replay.observations[-1].equity, label="observed final equity", minimum=0.0
    )
    final_equity = metric_number(replay.final_equity, label="final equity", minimum=0.0)
    if final_equity <= 0.0 or not math.isclose(final_equity, observed_equity, rel_tol=0.0, abs_tol=1e-8):
        raise ValueError("absolute generalization final equity does not reconcile")
    if not math.isclose(derived_equity, final_equity, rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError("absolute generalization closing mark equity does not reconcile")
    if not math.isclose(realized_pnl + open_pnl, final_equity - initial_cash, rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError("absolute generalization reconstructed PnL does not reconcile")
    return _AccountingEvidence(
        initial_cash=initial_cash,
        final_equity=final_equity,
        cash=cash,
        realized_pnl=realized_pnl,
        open_pnl=open_pnl,
        market_values=tuple(sorted(market_values.items())),
    )


def _concentrations(
    *,
    fills: Sequence[Mapping[str, object]],
    market_values: Sequence[tuple[str, float]],
) -> tuple[float, float, float]:
    values_by_symbol = dict(market_values)
    values = sorted((value for value in values_by_symbol.values() if value > 0.0), reverse=True)
    total = sum(values)
    top1 = values[0] / total if total else 0.0
    top3 = sum(values[:3]) / total if total else 0.0
    cash_flow: dict[str, float] = {}
    for fill in fills:
        symbol = metric_text(fill.get("symbol"), label="fill symbol")
        gross = metric_number(fill.get("gross_value"), label="fill gross value", minimum=0.0)
        fees = sum(
            metric_number(fill.get(field, 0.0), label=f"fill {field}", minimum=0.0)
            for field in ("commission", "stamp_duty", "transfer_fee")
        )
        metric_number(fill.get("slippage_cost", 0.0), label="fill slippage cost", minimum=0.0)
        if fill.get("side") == "BUY":
            cash_flow[symbol] = cash_flow.get(symbol, 0.0) - gross - fees
        else:
            cash_flow[symbol] = cash_flow.get(symbol, 0.0) + gross - fees
    for symbol, value in values_by_symbol.items():
        cash_flow[symbol] = cash_flow.get(symbol, 0.0) + value
    contributions = [abs(value) for value in cash_flow.values() if value]
    denominator = sum(contributions)
    hhi = sum((value / denominator) ** 2 for value in contributions) if denominator else 0.0
    return top1, top3, hhi


def _event_evidence(
    *,
    trace: Sequence[Mapping[str, object]],
    metrics: CellMetrics,
    final_account: Mapping[str, object],
) -> tuple[EventEvidence, ...]:
    chain = _chain_event_evidence(
        trace=trace,
        final_account=final_account,
        epochs=metrics.epochs,
    )
    expired_grants = {
        metric_text(row.get("grant_id"), label="expired grant")
        for row in metric_rows(final_account.get("strategic_epochs", ()), label="epoch ledger")
        if row.get("realized_status") == "EXPIRED"
    }
    retry_observed = any(
        isinstance((raw_grant := metric_mapping(row.get("risk", {}), label="trace risk").get("strategic_grant")), Mapping)
        and metric_mapping(raw_grant, label="strategic grant").get("previous_grant_id") in expired_grants
        for row in trace
    )
    failed_grant_applicable = bool(expired_grants)
    terminal_sessions = metrics.terminal_zero_strategic_target_state_sessions
    rows = (
        EventEvidence("first_divergence", False, False, 0, "NO_COMPARATOR"),
        *chain,
        EventEvidence(
            "failed_grant_retry",
            failed_grant_applicable,
            retry_observed,
            metrics.failed_grant_retry_healthy_sessions if failed_grant_applicable else 0,
            "OBSERVED"
            if retry_observed
            else "NO_RETRY"
            if failed_grant_applicable
            else "NO_FAILED_GRANT",
        ),
        EventEvidence(
            "terminal_zero_strategic_target_state",
            terminal_sessions > 0,
            terminal_sessions > 0,
            terminal_sessions,
            "OBSERVED" if terminal_sessions > 0 else "NO_TERMINAL_ZERO_STATE",
        ),
    )
    if tuple(item.name for item in rows) != _EVENT_NAMES:
        raise RuntimeError("absolute generalization event evidence names differ")
    return rows


_ChainRows = tuple[list[tuple[str, str]], list[tuple[str, str, str]], list[tuple[str, Mapping[str, object]]], list[tuple[str, Mapping[str, object]]]]


def _chain_event_rows(trace: Sequence[Mapping[str, object]]) -> _ChainRows:
    qualifications: list[tuple[str, str]] = []
    grants: list[tuple[str, str, str]] = []
    targets: list[tuple[str, Mapping[str, object]]] = []
    orders: list[tuple[str, Mapping[str, object]]] = []
    for row in trace:
        session = metric_iso_session(row.get("session"), label="trace session")
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw_qualification = risk.get("strategic_qualification")
        if isinstance(raw_qualification, Mapping):
            qualification = metric_mapping(raw_qualification, label="strategic qualification")
            if qualification.get("qualification_ready") is True:
                qualifications.append((
                    session,
                    metric_text(qualification.get("candidate_symbol"), label="qualification candidate"),
                ))
        raw_grant = risk.get("strategic_grant")
        if isinstance(raw_grant, Mapping):
            grant = metric_mapping(raw_grant, label="strategic grant")
            if grant.get("created_session") == session:
                grants.append((
                    metric_text(grant.get("grant_id"), label="strategic grant"), session,
                    metric_text(grant.get("candidate_symbol"), label="grant candidate"),
                ))
        targets.extend(
            (session, target)
            for target in metric_rows(row.get("targets", ()), label="trace targets")
            if target.get("origin_subsystem") == "STRATEGIC"
            and metric_positive_number(target.get("weight")) > 0.0
        )
        orders.extend(
            (session, order)
            for order in metric_rows(row.get("orders", ()), label="trace orders")
            if order.get("origin_subsystem") == "STRATEGIC"
        )
    return qualifications, grants, targets, orders


def _upstream_chain_flags(rows: _ChainRows) -> tuple[bool, bool, bool]:
    qualifications, grants, targets, orders = rows
    grant_by_id = {grant_id: (session, candidate) for grant_id, session, candidate in grants}
    qual_to_grant = any(
        candidate == grant_candidate and session <= grant_session
        for session, candidate in qualifications
        for grant_session, grant_candidate in grant_by_id.values()
    )
    grant_to_target = any(
        (grant_id := metric_text(target.get("grant_id"), label="target grant", empty=True))
        and (matched_grant := grant_by_id.get(grant_id)) is not None
        and matched_grant[1] == target.get("symbol")
        and matched_grant[0] <= session
        for session, target in targets
    )
    target_to_order = any(
        target_session == order_session
        and all(target.get(field, "") == order.get(field, "") for field in _CHAIN_IDENTITY_FIELDS[:-1])
        for target_session, target in targets
        for order_session, order in orders
    )
    return qual_to_grant, grant_to_target, target_to_order


def _downstream_chain_flags(*, orders: Sequence[tuple[str, Mapping[str, object]]], fills: Sequence[Mapping[str, object]], epochs: Sequence[EpochFact]) -> tuple[bool, bool]:
    order_to_fill = any(
        order.get("order_id") == fill.get("order_id")
        and all(order.get(field, "") == fill.get(field, "") for field in _CHAIN_IDENTITY_FIELDS)
        and order_session < metric_iso_session(fill.get("fill_date"), label="fill session")
        for order_session, order in orders
        for fill in fills
    )
    fill_to_epoch = any(
        fill.get("epoch_id") == epoch.epoch_id
        and fill.get("grant_id") == epoch.grant_id
        and fill.get("symbol") == epoch.owner_symbol
        and metric_iso_session(fill.get("fill_date"), label="fill session") == epoch.fill_session
        and epoch.fill_session == epoch.active_session
        for fill in fills
        for epoch in epochs
    )
    return order_to_fill, fill_to_epoch


def _chain_event_evidence(*, trace: Sequence[Mapping[str, object]], final_account: Mapping[str, object], epochs: Sequence[EpochFact]) -> tuple[EventEvidence, ...]:
    rows = _chain_event_rows(trace)
    qualifications, grants, targets, orders = rows
    fills = tuple(
        fill
        for fill in metric_rows(final_account.get("fills", ()), label="fill ledger")
        if fill.get("origin_subsystem") == "STRATEGIC"
    )
    qual_to_grant, grant_to_target, target_to_order = _upstream_chain_flags(rows)
    order_to_fill, fill_to_epoch = _downstream_chain_flags(orders=orders, fills=fills, epochs=epochs)
    transitions = (
        ("qualification_to_grant", bool(qualifications), qual_to_grant),
        ("grant_to_target", bool(grants), grant_to_target),
        ("target_to_order", bool(targets), target_to_order),
        ("order_to_fill", bool(orders), order_to_fill),
        ("fill_to_active_epoch", bool(fills), fill_to_epoch),
    )
    return tuple(
        EventEvidence(
            name=name,
            applicable=applicable,
            observed=applicable and observed,
            healthy_sessions=0,
            reason="OBSERVED" if applicable and observed else "NOT_OBSERVED",
        )
        for name, applicable, observed in transitions
    )


@dataclass(frozen=True, slots=True)
class _ObservationEvidence:
    trace: tuple[Mapping[str, object], ...]
    equities: tuple[float, ...]
    cash_ratios: tuple[float, ...]
    fills: tuple[Mapping[str, object], ...]
    intentional_absent: tuple[str, ...]
    unavailable: tuple[str, ...]


def _validate_data_manifest(observation: AbsoluteGeneralizationReplayObservation) -> None:
    manifest = observation.data_manifest
    files = dict(manifest.files)
    if (
        len(files) != len(manifest.files)
        or tuple(sorted(files.items())) != manifest.files
        or any(not name or not isinstance(digest, str) or len(digest) != 64 for name, digest in files.items())
    ):
        raise ValueError("absolute generalization data manifest files are malformed")
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != manifest.digest:
        raise ValueError("absolute generalization data manifest digest differs")
    if tuple(sorted(set(manifest.symbols))) != manifest.symbols:
        raise ValueError("absolute generalization data manifest symbols are malformed")
    if not set(manifest.symbols) <= set(observation.loaded_symbols):
        raise ValueError("absolute generalization data manifest symbols were not loaded")
    role_symbols = set(observation.roles.tradable_symbols)
    role_symbols.update(observation.roles.qualification_reference_symbols)
    role_symbols.update(observation.roles.risk_reference_symbols)
    if set(manifest.symbols) != role_symbols:
        raise ValueError("absolute generalization data manifest role symbols differ")
    start = metric_iso_session(manifest.start, label="data manifest start")
    end = metric_iso_session(manifest.end, label="data manifest end")
    session = metric_iso_session(observation.session, label="data manifest session")
    unavailable = set(observation.expected_but_unavailable_symbols).intersection(manifest.symbols)
    if start > session or end > session or (start > end and not unavailable):
        raise ValueError("absolute generalization data manifest interval differs")


def _validate_closing_marks(
    observation: AbsoluteGeneralizationReplayObservation,
    *,
    post_open: Mapping[str, object],
    equity: float,
) -> None:
    if not isinstance(observation.closing_marks, tuple):
        raise ValueError("absolute generalization closing marks are malformed")
    normalized: list[tuple[str, float]] = []
    for raw_pair in observation.closing_marks:
        if not isinstance(raw_pair, tuple) or len(raw_pair) != 2:
            raise ValueError("absolute generalization closing marks are malformed")
        symbol = metric_text(raw_pair[0], label="closing mark symbol")
        mark = metric_number(raw_pair[1], label="closing mark", minimum=0.0)
        if mark <= 0.0:
            raise ValueError("absolute generalization closing mark is malformed")
        normalized.append((symbol, mark))
    if tuple(sorted(set(normalized))) != observation.closing_marks:
        raise ValueError("absolute generalization closing marks are malformed")
    positions = metric_mapping(post_open.get("positions", {}), label="post-open positions")
    position_shares = {
        symbol: _whole_shares(
            metric_mapping(raw, label="post-open position").get("shares"),
            label="post-open position shares",
        )
        for symbol, raw in positions.items()
        if isinstance(symbol, str)
    }
    if len(position_shares) != len(positions) or set(position_shares) != {item[0] for item in normalized}:
        raise ValueError("absolute generalization closing mark positions differ")
    cash = metric_number(post_open.get("cash"), label="post-open cash", minimum=0.0)
    marked_equity = cash + sum(position_shares[symbol] * mark for symbol, mark in normalized)
    if not math.isclose(marked_equity, equity, rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError("absolute generalization closing mark equity does not reconcile")


def _observation_evidence(replay: AbsoluteGeneralizationReplay) -> _ObservationEvidence:
    sessions: list[str] = []
    trace: list[Mapping[str, object]] = []
    equities: list[float] = []
    cash_ratios: list[float] = []
    fills: list[Mapping[str, object]] = []
    intentional_absent: set[str] = set()
    unavailable: set[str] = set()
    for observation in replay.observations:
        _validate_data_manifest(observation)
        session = metric_iso_session(observation.session, label="observation session")
        if sessions and session <= sessions[-1]:
            raise ValueError("absolute generalization observation sessions are not ordered")
        sessions.append(session)
        equity = metric_number(observation.equity, label="observation equity", minimum=0.0)
        if equity <= 0.0:
            raise ValueError("absolute generalization observation equity is malformed")
        equities.append(equity)
        decision = metric_payload_mapping(observation.decision_payload, label="decision")
        if metric_iso_session(decision.get("date"), label="decision date") != session:
            raise ValueError("absolute generalization decision session differs")
        trace.append(
            metric_trace_row(
                session=session,
                decision=decision,
                qualification_coverage=_role_coverage(
                    observation.roles.qualification_reference_symbols,
                    observation.roles.available_symbols,
                ),
            )
        )
        post_open = metric_payload_mapping(
            observation.post_open_account.account_payload,
            label="post-open account",
        )
        _validate_closing_marks(observation, post_open=post_open, equity=equity)
        cash_ratios.append(metric_number(post_open.get("cash"), label="post-open cash", minimum=0.0) / equity)
        for payload in observation.new_fills:
            fill = metric_payload_mapping(payload, label="new fill")
            if metric_iso_session(fill.get("fill_date"), label="new fill session") != session:
                raise ValueError("absolute generalization incremental fill session differs")
            fills.append(fill)
        intentional_absent.update(
            metric_text(value, label="intentional absent symbol")
            for value in observation.intentional_role_absent_symbols
        )
        unavailable.update(
            metric_text(value, label="unavailable symbol")
            for value in observation.expected_but_unavailable_symbols
        )
    return _ObservationEvidence(
        trace=tuple(trace),
        equities=tuple(equities),
        cash_ratios=tuple(cash_ratios),
        fills=tuple(fills),
        intentional_absent=tuple(sorted(intentional_absent)),
        unavailable=tuple(sorted(unavailable)),
    )


@dataclass(frozen=True, slots=True)
class _LedgerEvidence:
    account: Mapping[str, object]
    orders: tuple[Mapping[str, object], ...]
    fills: tuple[Mapping[str, object], ...]
    epochs: tuple[EpochFact, ...]


def _ledger_evidence(
    replay: AbsoluteGeneralizationReplay,
    trace: Sequence[Mapping[str, object]],
    observed_fills: Sequence[Mapping[str, object]],
) -> _LedgerEvidence:
    account = metric_payload_mapping(replay.final_account_payload, label="final account")
    orders = metric_rows(account.get("order_ledger", ()), label="order ledger")
    fills = metric_rows(account.get("fills", ()), label="fill ledger")
    final_fills_by_id = physical_fill_identity_map(fills)
    observed_fills_by_id = physical_fill_identity_map(observed_fills)
    if observed_fills_by_id != final_fills_by_id:
        raise ValueError("absolute generalization incremental fill ledger differs")
    if any(
        epoch.get("owner_symbol") == replay.scenario.removed_symbol
        for epoch in metric_rows(account.get("strategic_epochs", ()), label="epoch ledger")
    ):
        raise ValueError("absolute generalization removed symbol became a strategic owner")
    allowed_symbols = tuple(
        symbol
        for symbol in replay.observations[-1].roles.tradable_symbols
        if symbol != replay.scenario.removed_symbol
    )
    assert_unique_execution_rows(
        final_account=account,
        trace=trace,
        allowed_symbols=allowed_symbols,
    )
    validate_exact_execution_chain(final_account=account, trace=trace, epochs=())
    epochs = actual_epoch_facts_from_rows(final_account=account, trace=trace)
    validate_exact_execution_chain(final_account=account, trace=trace, epochs=epochs)
    if replay.scenario.removed_symbol in {fact.owner_symbol for fact in epochs}:
        raise ValueError("absolute generalization removed symbol became a strategic owner")
    return _LedgerEvidence(account=account, orders=orders, fills=fills, epochs=epochs)


@dataclass(frozen=True, slots=True)
class _TraceActivity:
    total_positive: tuple[str, ...]
    strategic_positive: tuple[str, ...]
    qualification_sessions: tuple[str, ...]
    grant_sessions: Mapping[str, str]


def _trace_activity(trace: Sequence[Mapping[str, object]]) -> _TraceActivity:
    total_positive: list[str] = []
    strategic_positive: list[str] = []
    qualification_sessions: list[str] = []
    grant_sessions: dict[str, str] = {}
    for row in trace:
        session = metric_iso_session(row.get("session"), label="trace session")
        targets = metric_rows(row.get("targets", ()), label="trace targets")
        if any(metric_positive_number(item.get("weight")) > 0.0 for item in targets):
            total_positive.append(session)
        if any(
            item.get("origin_subsystem") == "STRATEGIC"
            and metric_positive_number(item.get("weight")) > 0.0
            for item in targets
        ):
            strategic_positive.append(session)
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw_qualification = risk.get("strategic_qualification")
        if (
            isinstance(raw_qualification, Mapping)
            and metric_mapping(raw_qualification, label="strategic qualification").get("qualification_ready")
            is True
        ):
            qualification_sessions.append(session)
        raw_grant = risk.get("strategic_grant")
        if isinstance(raw_grant, Mapping):
            grant = metric_mapping(raw_grant, label="strategic grant")
            grant_id = metric_text(grant.get("grant_id", ""), label="grant identity", empty=True)
            if grant_id:
                previous = grant_sessions.get(grant_id)
                if previous is not None and previous > session:
                    raise ValueError("absolute generalization grant sessions are not ordered")
                grant_sessions.setdefault(grant_id, session)
    return _TraceActivity(
        total_positive=tuple(total_positive),
        strategic_positive=tuple(strategic_positive),
        qualification_sessions=tuple(qualification_sessions),
        grant_sessions=grant_sessions,
    )


def _max_drawdown(equities: Sequence[float]) -> float:
    peak = equities[0]
    drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    return drawdown


def _complete_metrics(
    *,
    trace: Sequence[Mapping[str, object]],
    ledger: _LedgerEvidence,
    observations: _ObservationEvidence,
    activity: _TraceActivity,
    accounting: _AccountingEvidence,
    role_values: tuple[float, float, float, int, bool],
) -> CellMetrics:
    initial_cash = accounting.initial_cash
    final_equity = accounting.final_equity
    (
        tradable_coverage,
        qualification_coverage,
        risk_coverage,
        role_witness_sessions,
        role_identity_consistent,
    ) = role_values
    strategic_orders = tuple(order for order in ledger.orders if order.get("origin_subsystem") == "STRATEGIC")
    strategic_fills = tuple(fill for fill in ledger.fills if fill.get("origin_subsystem") == "STRATEGIC")
    gross_turnover = (
        sum(
            metric_number(fill.get("gross_value"), label="fill gross value", minimum=0.0)
            for fill in ledger.fills
        )
        / initial_cash
    )
    top1, top3, pnl_hhi = _concentrations(
        fills=ledger.fills, market_values=accounting.market_values
    )
    repairs = repair_episode_facts_from_trace(trace)
    return CellMetrics(
        initial_cash=initial_cash,
        final_equity=final_equity,
        final_wealth=final_equity / initial_cash,
        total_return=final_equity / initial_cash - 1.0,
        max_drawdown=_max_drawdown(observations.equities),
        account_orders=len(ledger.orders),
        fill_count=len(ledger.fills),
        gross_turnover=gross_turnover,
        annual_turnover=gross_turnover * _TRADING_SESSIONS_PER_YEAR / len(trace),
        realized_pnl=accounting.realized_pnl,
        open_pnl=accounting.open_pnl,
        cash_drag=sum(observations.cash_ratios) / len(observations.cash_ratios),
        top1_concentration=top1,
        top3_concentration=top3,
        pnl_hhi=pnl_hhi,
        positive_total_target_sessions=len(activity.total_positive),
        positive_strategic_target_sessions=len(activity.strategic_positive),
        first_positive_total_target_session=activity.total_positive[0] if activity.total_positive else "",
        first_positive_strategic_target_session=(
            activity.strategic_positive[0] if activity.strategic_positive else ""
        ),
        longest_healthy_zero_total_target_streak=longest_healthy_zero_target_streak(
            trace, strategic_only=False
        ),
        longest_healthy_zero_strategic_target_streak=longest_healthy_zero_target_streak(
            trace, strategic_only=True
        ),
        qualification_ready_sessions=len(activity.qualification_sessions),
        first_qualification_session=activity.qualification_sessions[0]
        if activity.qualification_sessions
        else "",
        strategic_grant_count=len(activity.grant_sessions),
        first_strategic_grant_session=min(activity.grant_sessions.values())
        if activity.grant_sessions
        else "",
        strategic_order_count=len(strategic_orders),
        first_strategic_order_session=min(
            (
                metric_iso_session(order.get("signal_date"), label="strategic order session")
                for order in strategic_orders
            ),
            default="",
        ),
        strategic_fill_count=len(strategic_fills),
        first_strategic_fill_session=min(
            (
                metric_iso_session(fill.get("fill_date"), label="strategic fill session")
                for fill in strategic_fills
            ),
            default="",
        ),
        actual_strategic_epoch_count=len(ledger.epochs),
        first_actual_strategic_epoch_session=ledger.epochs[0].active_session if ledger.epochs else "",
        distinct_owner_count=len({fact.owner_symbol for fact in ledger.epochs}),
        owner_symbols=tuple(sorted({fact.owner_symbol for fact in ledger.epochs})),
        epochs=ledger.epochs,
        repair_episode_count=len(repairs),
        repairs=repairs,
        intentional_role_absent_symbols=observations.intentional_absent,
        expected_but_unavailable_symbols=observations.unavailable,
        tradable_coverage=tradable_coverage,
        qualification_coverage=qualification_coverage,
        risk_coverage=risk_coverage,
        role_witness_sessions=role_witness_sessions,
        role_identity_consistent=role_identity_consistent,
        failed_grant_retry_healthy_sessions=_failed_grant_retry_sessions(
            final_account=ledger.account, trace=trace
        ),
        terminal_zero_strategic_target_state_sessions=_terminal_healthy_zero_strategic_streak(
            trace
        ),
    )


def derive_complete_cell_metrics_impl(
    replay: AbsoluteGeneralizationReplay,
) -> tuple[CellMetrics, tuple[EventEvidence, ...]]:
    """Validate raw replay ledgers and derive all finite complete-cell facts."""
    if type(replay) is not AbsoluteGeneralizationReplay:
        raise ValueError("absolute generalization replay type differs")
    if replay.status != "COMPLETE" or replay.replay_error:
        raise ValueError("absolute generalization complete metrics require complete replay")
    if not replay.observations:
        raise ValueError("absolute generalization complete replay has no observations")
    observations = _observation_evidence(replay)
    trace = observations.trace
    (
        tradable_coverage,
        qualification_coverage,
        risk_coverage,
        role_witness_sessions,
        role_identity_consistent,
    ) = _session_role_values(replay)
    ledger = _ledger_evidence(replay, trace, observations.fills)
    account, fills = ledger.account, ledger.fills
    accounting = _accounting(replay=replay, account=account, fills=fills)
    if not math.isclose(
        metric_number(
            metric_payload_mapping(
                replay.observations[-1].post_decision_account.account_payload,
                label="post-decision account",
            ).get("cash"),
            label="post-decision cash",
            minimum=0.0,
        ),
        accounting.cash,
        rel_tol=0.0,
        abs_tol=1e-8,
    ):
        raise ValueError("absolute generalization final cash does not reconcile")
    metrics = _complete_metrics(
        trace=trace,
        ledger=ledger,
        observations=observations,
        activity=_trace_activity(trace),
        accounting=accounting,
        role_values=(
            tradable_coverage,
            qualification_coverage,
            risk_coverage,
            role_witness_sessions,
            role_identity_consistent,
        ),
    )
    return metrics, _event_evidence(trace=trace, metrics=metrics, final_account=account)
