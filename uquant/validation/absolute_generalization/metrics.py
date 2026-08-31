"""Literal replay-derived metrics that neither decide nor call research runners."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import cast

from uquant.portfolio.strategic.rearm import flat_book_capital_repair_requirement
from uquant.types import (
    FlatBookCapitalRepairResetReason,
    FlatBookCapitalRepairState,
    FlatBookCapitalRepairStatus,
)

from ._metric_primitives import (
    metric_integer,
    metric_iso_session,
    metric_mapping,
    metric_number,
    metric_positive_number,
    metric_rows,
    metric_sequence,
    metric_stable_ids,
    metric_text,
)
from ._physical_identity import physical_fill_identity_map


@dataclass(frozen=True, slots=True)
class EpochFact:
    """One actual strategic epoch, gated by its positive physical fill."""

    epoch_id: str
    grant_id: str
    owner_symbol: str
    qualification_signature: str
    qualification_route: str
    qualification_quorum: str
    qualification_session: str
    grant_session: str
    target_session: str
    order_session: str
    fill_session: str
    active_session: str
    closed_session: str
    close_reason: str
    realized_status: str
    previous_epoch_id: str
    previous_grant_id: str
    authorization_id: str
    authorization_session: str

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class RepairEpisodeFact:
    """One capital-repair episode observed in the production decision trace."""

    repair_episode_id: str
    capital_budget_level: int
    repair_target_level: int
    required_healthy_sessions: int
    reported_healthy_sessions: int
    actual_healthy_sessions_to_ready: int
    first_observed_session: str
    last_ready_session: str
    status: str
    reset_reason: str

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True, slots=True)
class EventEvidence:
    """Derived, unsealed observation for an artifact event fact."""

    name: str
    applicable: bool
    observed: bool
    healthy_sessions: int
    reason: str


@dataclass(frozen=True, slots=True)
class CellMetrics:
    """Complete finite metrics and causal facts for one successful replay."""

    initial_cash: float
    final_equity: float
    final_wealth: float
    total_return: float
    max_drawdown: float
    account_orders: int
    fill_count: int
    gross_turnover: float
    annual_turnover: float
    realized_pnl: float
    open_pnl: float
    cash_drag: float
    top1_concentration: float
    top3_concentration: float
    pnl_hhi: float
    positive_total_target_sessions: int
    positive_strategic_target_sessions: int
    first_positive_total_target_session: str
    first_positive_strategic_target_session: str
    longest_healthy_zero_total_target_streak: int
    longest_healthy_zero_strategic_target_streak: int
    qualification_ready_sessions: int
    first_qualification_session: str
    strategic_grant_count: int
    first_strategic_grant_session: str
    strategic_order_count: int
    first_strategic_order_session: str
    strategic_fill_count: int
    first_strategic_fill_session: str
    actual_strategic_epoch_count: int
    first_actual_strategic_epoch_session: str
    distinct_owner_count: int
    owner_symbols: tuple[str, ...]
    epochs: tuple[EpochFact, ...]
    repair_episode_count: int
    repairs: tuple[RepairEpisodeFact, ...]
    intentional_role_absent_symbols: tuple[str, ...]
    expected_but_unavailable_symbols: tuple[str, ...]
    tradable_coverage: float
    qualification_coverage: float
    risk_coverage: float
    role_witness_sessions: int
    role_identity_consistent: bool
    failed_grant_retry_healthy_sessions: int
    terminal_zero_strategic_target_state_sessions: int

    def to_dict(self) -> dict[str, object]:
        raw = cast(dict[str, object], asdict(self))
        raw["owner_symbols"] = list(self.owner_symbols)
        raw["epochs"] = [fact.to_dict() for fact in self.epochs]
        raw["repairs"] = [fact.to_dict() for fact in self.repairs]
        raw["intentional_role_absent_symbols"] = list(self.intentional_role_absent_symbols)
        raw["expected_but_unavailable_symbols"] = list(self.expected_but_unavailable_symbols)
        return raw


def actual_epoch_facts_from_rows(
    *,
    final_account: Mapping[str, object],
    trace: Sequence[Mapping[str, object]],
) -> tuple[EpochFact, ...]:
    """Derive fill-gated actual epochs from final ledgers and decision observations."""

    epochs = metric_rows(final_account.get("strategic_epochs", ()), label="epoch ledger")
    epoch_by_id = metric_stable_ids(epochs, field="epoch_id", label="strategic epoch")
    grants = [metric_text(epoch.get("grant_id"), label="epoch grant") for epoch in epochs]
    if len(grants) != len(set(grants)):
        raise ValueError("absolute generalization duplicate strategic grant identity")
    fills = metric_rows(final_account.get("fills", ()), label="fill ledger")
    facts = [
        fact
        for epoch_id, epoch in epoch_by_id.items()
        if (
            fact := _actual_epoch_fact(
                epoch_id=epoch_id,
                epoch=epoch,
                fills=fills,
                trace=trace,
            )
        )
        is not None
    ]
    facts.sort(key=lambda item: (item.active_session, item.epoch_id))
    for left, right in pairwise(facts):
        if not left.closed_session or left.closed_session >= right.active_session:
            raise ValueError("absolute generalization strategic epochs overlap active ownership")
    return tuple(facts)


def _matching_trace_session(
    trace: Sequence[Mapping[str, object]],
    *,
    collection: str,
    epoch_id: str,
    grant_id: str,
    owner_symbol: str,
) -> str:
    for row in trace:
        for item in metric_rows(row.get(collection, ()), label=f"trace {collection}"):
            if (
                metric_text(item.get("epoch_id", ""), label=f"{collection} epoch", empty=True)
                != epoch_id
                or metric_text(item.get("symbol", ""), label=f"{collection} symbol", empty=True)
                != owner_symbol
                or (
                    grant_id
                    and metric_text(
                        item.get("grant_id", ""), label=f"{collection} grant", empty=True
                    )
                    != grant_id
                )
            ):
                continue
            if collection == "targets" and metric_positive_number(item.get("weight")) <= 0.0:
                continue
            if collection == "orders" and (
                item.get("side") != "BUY"
                or metric_positive_number(item.get("target_weight")) <= 0.0
            ):
                continue
            return metric_iso_session(row.get("session"), label="trace session")
    return ""


def _qualification_session(trace: Sequence[Mapping[str, object]], epoch: Mapping[str, object]) -> str:
    owner = metric_text(epoch.get("owner_symbol"), label="epoch owner")
    signature = metric_text(
        epoch.get("qualification_signature", ""), label="epoch qualification signature", empty=True
    )
    for row in trace:
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw = risk.get("strategic_qualification")
        if not isinstance(raw, Mapping):
            continue
        qualification = metric_mapping(raw, label="strategic qualification")
        if (
            qualification.get("qualification_ready") is True
            and qualification.get("candidate_symbol") == owner
            and qualification.get("qualification_signature", "") == signature
        ):
            return metric_iso_session(row.get("session"), label="qualification session")
    return ""


def _grant_provenance(
    trace: Sequence[Mapping[str, object]],
    grant_id: str,
) -> tuple[str, str, str]:
    for row in trace:
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw_grant = risk.get("strategic_grant")
        if not isinstance(raw_grant, Mapping):
            continue
        grant = metric_mapping(raw_grant, label="strategic grant")
        if grant.get("grant_id") != grant_id:
            continue
        authorization_id = metric_text(
            grant.get("authorization_id", ""), label="grant authorization", empty=True
        )
        raw_rearm = risk.get("strategic_cash_rearm")
        authorization_session = ""
        if authorization_id and isinstance(raw_rearm, Mapping):
            authorization_session = metric_iso_session(
                metric_mapping(raw_rearm, label="strategic cash rearm").get(
                    "authorized_session", ""
                ),
                label="authorization session",
                empty=True,
            )
        return (
            authorization_id,
            authorization_session,
            metric_text(grant.get("previous_grant_id", ""), label="previous grant", empty=True),
        )
    raise ValueError("absolute generalization strategic epoch grant is absent from trace")


_ACTUAL_EPOCH_STATUSES = frozenset({"ACTIVE", "CLOSED"})


def _actual_epoch_fact(
    *,
    epoch_id: str,
    epoch: Mapping[str, object],
    fills: Sequence[Mapping[str, object]],
    trace: Sequence[Mapping[str, object]],
) -> EpochFact | None:
    status = metric_text(epoch.get("realized_status"), label="epoch realized status")
    first_fill_session = metric_iso_session(
        epoch.get("first_fill_session", ""), label="epoch first fill", empty=True
    )
    if not first_fill_session:
        if status in _ACTUAL_EPOCH_STATUSES:
            raise ValueError("absolute generalization active epoch has no first fill")
        return None
    if status not in _ACTUAL_EPOCH_STATUSES:
        raise ValueError("absolute generalization filled epoch has non-realized status")
    grant_id = metric_text(epoch.get("grant_id"), label="epoch grant")
    owner = metric_text(epoch.get("owner_symbol"), label="epoch owner")
    matching = [
        fill
        for fill in fills
        if fill.get("epoch_id") == epoch_id
        and fill.get("grant_id") == grant_id
        and fill.get("symbol") == owner
        and fill.get("side") == "BUY"
        and metric_positive_number(fill.get("shares")) > 0.0
    ]
    if not matching:
        raise ValueError("absolute generalization strategic epoch has no matching real fill")
    fill_session = min(metric_iso_session(item.get("fill_date"), label="fill session") for item in matching)
    if fill_session != first_fill_session:
        raise ValueError("absolute generalization epoch first fill differs from fill ledger")
    target_session = _matching_trace_session(
        trace, collection="targets", epoch_id=epoch_id, grant_id=grant_id, owner_symbol=owner
    )
    order_session = _matching_trace_session(
        trace, collection="orders", epoch_id=epoch_id, grant_id=grant_id, owner_symbol=owner
    )
    if not target_session or not order_session:
        raise ValueError("absolute generalization epoch lacks a target or order")
    active_session = metric_iso_session(
        epoch.get("active_session", ""), label="epoch active session", empty=True
    )
    if not (target_session <= order_session < fill_session == active_session):
        raise ValueError("absolute generalization epoch target/order/fill causality differs")
    qualification_session = _qualification_session(trace, epoch)
    if not qualification_session:
        raise ValueError("absolute generalization epoch lacks a production qualification")
    authorization_id, authorization_session, previous_grant_id = _grant_provenance(trace, grant_id)
    return EpochFact(
        epoch_id=epoch_id,
        grant_id=grant_id,
        owner_symbol=owner,
        qualification_signature=metric_text(
            epoch.get("qualification_signature", ""), label="epoch qualification signature", empty=True
        ),
        qualification_route=metric_text(
            epoch.get("qualification_route", ""), label="epoch qualification route", empty=True
        ),
        qualification_quorum=metric_text(
            epoch.get("qualification_quorum", ""), label="epoch qualification quorum", empty=True
        ),
        qualification_session=qualification_session,
        grant_session=metric_iso_session(epoch.get("opened_session"), label="epoch opened"),
        target_session=target_session,
        order_session=order_session,
        fill_session=fill_session,
        active_session=active_session,
        closed_session=metric_iso_session(
            epoch.get("closed_session", ""), label="epoch closed", empty=True
        ),
        close_reason=metric_text(epoch.get("close_reason", ""), label="epoch close reason", empty=True),
        realized_status=status,
        previous_epoch_id=metric_text(
            epoch.get("previous_epoch_id", ""), label="previous epoch", empty=True
        ),
        previous_grant_id=previous_grant_id,
        authorization_id=authorization_id,
        authorization_session=authorization_session,
    )


def metric_healthy_trace_row(trace_row: Mapping[str, object]) -> bool:
    risk = metric_mapping(trace_row.get("risk", {}), label="trace risk")
    raw_qualification = risk.get("strategic_qualification")
    if not isinstance(raw_qualification, Mapping):
        return False
    qualification = metric_mapping(raw_qualification, label="strategic qualification")
    unavailable = metric_sequence(
        qualification.get("unavailable_reference_symbols", ()), label="unavailable references"
    )
    return bool(
        trace_row.get("opportunity") in {"TREND", "STRONG_TREND"}
        and risk.get("state", trace_row.get("decision_risk", "NORMAL")) == "NORMAL"
        and qualification.get("qualification_ready") is True
        and metric_number(trace_row.get("qualification_coverage"), label="qualification coverage") >= 1.0
        and not unavailable
        and metric_positive_number(risk.get("target_gross_cap")) > 0.0
        and not bool(risk.get("market_wide_execution_block", False))
    )


def longest_healthy_zero_target_streak(trace: Sequence[Mapping[str, object]], *, strategic_only: bool) -> int:
    """Return the longest healthy zero-target session streak for one scope."""
    longest = 0
    current = 0
    for row in trace:
        positive = any(
            metric_positive_number(target.get("weight")) > 0.0
            and (not strategic_only or target.get("origin_subsystem") == "STRATEGIC")
            for target in metric_rows(row.get("targets", ()), label="trace targets")
        )
        if metric_healthy_trace_row(row) and not positive:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def repair_episode_facts_from_trace(
    trace: Sequence[Mapping[str, object]],
) -> tuple[RepairEpisodeFact, ...]:
    """Reconcile every explicit repair episode in the decision trace."""
    grouped: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
    for row in trace:
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw = risk.get("flat_book_capital_repair")
        if not isinstance(raw, Mapping):
            continue
        repair = metric_mapping(raw, label="capital repair")
        if not repair.get("repair_episode_id"):
            if dict(repair) == asdict(FlatBookCapitalRepairState()):
                continue
            raise ValueError("absolute generalization empty repair state differs")
        episode_id = metric_text(repair.get("repair_episode_id"), label="repair episode")
        grouped.setdefault(episode_id, []).append(
            (metric_iso_session(row.get("session"), label="repair session"), repair)
        )
    facts = []
    for episode_id, rows in sorted(grouped.items()):
        rows.sort(key=lambda item: item[0])
        facts.append(_repair_episode_fact(episode_id, rows))
    return tuple(facts)


def _repair_bootstrap_reset(
    first_session: str,
    first: Mapping[str, object],
) -> tuple[str, str]:
    reset_reason = metric_text(first.get("reset_reason", ""), label="repair reset reason", empty=True)
    reset_session = metric_iso_session(first.get("last_reset_session", ""), label="repair reset session", empty=True)
    if bool(reset_reason) != bool(reset_session) or (
        reset_session and reset_session != first_session
    ):
        raise ValueError("absolute generalization repair reset bootstrap differs")
    return reset_reason, reset_session


@dataclass(slots=True)
class _RepairLiteralState:
    previous_count: int = 0
    counted_since_reset: int = 0
    last_counted_session: str = ""
    current_ready_session: str = ""
    last_reset_session: str = ""
    historical_ready_session: str = ""
    historical_sessions_to_ready: int = 0
    current_reset_reason: str = ""


def _repair_status_and_reason(row: Mapping[str, object]) -> tuple[str, str]:
    status = metric_text(row.get("status"), label="repair status")
    try:
        FlatBookCapitalRepairStatus(status)
    except ValueError as exc:
        raise ValueError("absolute generalization repair status differs") from exc
    reset_reason = metric_text(
        row.get("reset_reason", ""), label="repair reset reason", empty=True
    )
    if reset_reason:
        try:
            FlatBookCapitalRepairResetReason(reset_reason)
        except ValueError as exc:
            raise ValueError(
                "absolute generalization repair reset reason differs"
            ) from exc
    return status, reset_reason


def _repair_ready_session(row: Mapping[str, object], expected: str) -> str:
    return metric_iso_session(row.get("last_ready_session", expected), label="repair ready session", empty=True)


def _apply_repair_reset(
    state: _RepairLiteralState,
    *,
    row: Mapping[str, object],
    session: str,
    count: int,
    reset_reason: str,
) -> None:
    if count != 0 or not reset_reason:
        raise ValueError("absolute generalization repair reset progression differs")
    if reset_reason == FlatBookCapitalRepairResetReason.LIVE_CAPITAL_AUTHORITY.value:
        expected_counted = ""
        expected_ready = ""
    elif reset_reason == FlatBookCapitalRepairResetReason.CAPITAL_BUDGET_CLEARED.value:
        expected_counted = state.last_counted_session
        expected_ready = state.current_ready_session
    else:
        raise ValueError("absolute generalization repair reset reason differs")
    if "last_reset_session" in row and metric_iso_session(
        row.get("last_reset_session"), label="repair reset session"
    ) != session:
        raise ValueError("absolute generalization repair reset session differs")
    state.previous_count = 0
    state.counted_since_reset = 0
    state.last_counted_session = expected_counted
    state.current_ready_session = expected_ready
    state.last_reset_session = session
    state.current_reset_reason = reset_reason
    _validate_repair_progress_sessions(
        row=row,
        last_counted_session=expected_counted,
        last_reset_session=session,
    )
    if _repair_ready_session(row, expected_ready) != expected_ready:
        raise ValueError("absolute generalization repair ready session differs")


def _validate_repair_nonreset_status(
    state: _RepairLiteralState, status: str, reset_reason: str, count: int, required: int
) -> bool:
    ready = status in (FlatBookCapitalRepairStatus.READY.value, FlatBookCapitalRepairStatus.CONSUMED.value)
    expected_reason = "" if ready or state.current_ready_session else state.current_reset_reason
    if reset_reason in (FlatBookCapitalRepairResetReason.LIVE_CAPITAL_AUTHORITY.value, FlatBookCapitalRepairResetReason.CAPITAL_BUDGET_CLEARED.value) or reset_reason != expected_reason:
        raise ValueError("absolute generalization repair reset reason differs")
    if ready and count != required:
        raise ValueError("absolute generalization repair became ready too early")
    return ready


def _apply_repair_nonreset(
    state: _RepairLiteralState,
    *,
    row: Mapping[str, object],
    session: str,
    status: str,
    reset_reason: str,
    count: int,
    required: int,
) -> None:
    next_count = min(state.previous_count + 1, required)
    if count > required or count not in {state.previous_count, next_count}:
        raise ValueError("absolute generalization repair healthy session progression differs")
    if status == FlatBookCapitalRepairStatus.BLOCKED.value and count != state.previous_count:
        raise ValueError("absolute generalization repair healthy session progression differs")
    ready_status = _validate_repair_nonreset_status(
        state, status, reset_reason, count, required
    )
    if count > state.previous_count:
        state.counted_since_reset += 1
        state.last_counted_session = session
    elif status == FlatBookCapitalRepairStatus.READY.value:
        state.last_counted_session = session
    elif status == FlatBookCapitalRepairStatus.CONSUMED.value:
        reported_counted = metric_iso_session(
            row.get("last_counted_session", state.last_counted_session),
            label="repair counted session",
            empty=True,
        )
        if reported_counted == session:
            state.last_counted_session = session
    _validate_repair_progress_sessions(
        row=row,
        last_counted_session=state.last_counted_session,
        last_reset_session=state.last_reset_session,
    )
    if status == FlatBookCapitalRepairStatus.ACCUMULATING.value and (
        count != next_count or count >= required
    ):
        raise ValueError("absolute generalization repair healthy session progression differs")
    reported_ready = _repair_ready_session(row, state.current_ready_session)
    if ready_status and not state.current_ready_session:
        if reported_ready != session:
            raise ValueError("absolute generalization repair ready session differs")
        state.current_ready_session = session
        if not state.historical_ready_session:
            state.historical_ready_session = session
            state.historical_sessions_to_ready = state.counted_since_reset
    if ready_status:
        state.current_reset_reason = ""
    if reported_ready != state.current_ready_session:
        raise ValueError("absolute generalization repair ready session differs")
    state.previous_count = count


def _repair_episode_fact(
    episode_id: str,
    rows: Sequence[tuple[str, Mapping[str, object]]],
) -> RepairEpisodeFact:
    sessions = [session for session, _ in rows]
    if len(sessions) != len(set(sessions)):
        raise ValueError("absolute generalization repair session duplicates")
    first_session, first = rows[0]
    capital_level = metric_integer(first.get("capital_budget_level"), label="repair capital level")
    target_level = metric_integer(first.get("repair_target_level"), label="repair target level")
    required = metric_integer(
        first.get("required_healthy_sessions"), label="repair required sessions", minimum=1
    )
    try:
        expected_target, expected_required = flat_book_capital_repair_requirement(capital_level)
    except ValueError as exc:
        raise ValueError("absolute generalization repair tier/bound differs") from exc
    if (target_level, required) != (expected_target, expected_required):
        raise ValueError("absolute generalization repair tier/bound differs")
    first_observed = metric_iso_session(
        first.get("first_observed_session", first_session), label="repair first session"
    )
    if first_observed != first_session:
        raise ValueError("absolute generalization repair first session differs")
    stable_optional = ("account_identity", "risk_reference_universe_identity", "config_identity")
    reset_reason, reset_session = _repair_bootstrap_reset(first_session, first)
    state = _RepairLiteralState(
        last_reset_session=reset_session,
        current_reset_reason=reset_reason,
    )
    for session, row in rows:
        _validate_repair_row_identity(
            row=row, session=session, first=first, first_session=first_session,
            expected=(capital_level, target_level, required), stable_optional=stable_optional,
        )
        status, reset_reason = _repair_status_and_reason(row)
        count = metric_integer(row.get("healthy_session_count"), label="repair healthy sessions")
        if status == FlatBookCapitalRepairStatus.RESET.value:
            _apply_repair_reset(
                state,
                row=row,
                session=session,
                count=count,
                reset_reason=reset_reason,
            )
        else:
            _apply_repair_nonreset(
                state,
                row=row,
                session=session,
                status=status,
                reset_reason=reset_reason,
                count=count,
                required=required,
            )
    _, final = rows[-1]
    return RepairEpisodeFact(
        repair_episode_id=episode_id,
        capital_budget_level=capital_level,
        repair_target_level=target_level,
        required_healthy_sessions=required,
        reported_healthy_sessions=(
            required if state.historical_ready_session else state.previous_count
        ),
        actual_healthy_sessions_to_ready=state.historical_sessions_to_ready,
        first_observed_session=first_observed,
        last_ready_session=state.historical_ready_session,
        status=metric_text(final.get("status"), label="repair status"),
        reset_reason=metric_text(
            final.get("reset_reason", ""), label="repair reset reason", empty=True
        ),
    )


def _validate_repair_progress_sessions(
    *,
    row: Mapping[str, object],
    last_counted_session: str,
    last_reset_session: str,
) -> None:
    expected = (
        ("last_counted_session", last_counted_session, "repair counted session"),
        ("last_reset_session", last_reset_session, "repair reset session"),
    )
    for field, session, label in expected:
        if field in row and metric_iso_session(
            row.get(field), label=label, empty=True
        ) != session:
            raise ValueError(f"absolute generalization {label} differs")


def _validate_repair_row_identity(*, row: Mapping[str, object], session: str, first: Mapping[str, object], first_session: str, expected: tuple[int, int, int], stable_optional: Sequence[str]) -> None:
    observed = (
        metric_integer(row.get("capital_budget_level"), label="repair capital level"),
        metric_integer(row.get("repair_target_level"), label="repair target level"),
        metric_integer(row.get("required_healthy_sessions"), label="repair required sessions"),
    )
    first_observed = metric_iso_session(
        row.get("first_observed_session", first_session), label="repair first session"
    )
    if (
        observed != expected
        or first_observed != first_session
        or any(row.get(field, "") != first.get(field, "") for field in stable_optional)
    ):
        raise ValueError("absolute generalization repair episode identity differs")
    if "last_observed_session" in row and metric_iso_session(
        row.get("last_observed_session"), label="repair observed session"
    ) != session:
        raise ValueError("absolute generalization repair observed session differs")


def first_repair_ready_fact(trace: Sequence[Mapping[str, object]]) -> RepairEpisodeFact | None:
    """Expose the first ready repair fact for compatibility runner consumers."""

    return next(
        (fact for fact in repair_episode_facts_from_trace(trace) if fact.last_ready_session),
        None,
    )


def assert_unique_execution_rows(
    *,
    final_account: Mapping[str, object],
    trace: Sequence[Mapping[str, object]],
    allowed_symbols: Sequence[str],
) -> None:
    """Reject duplicate execution identities and reference-only capital rows."""

    orders = metric_rows(final_account.get("order_ledger", ()), label="order ledger")
    epochs = metric_rows(final_account.get("strategic_epochs", ()), label="epoch ledger")
    fills = metric_rows(final_account.get("fills", ()), label="fill ledger")
    metric_stable_ids(orders, field="order_id", label="order")
    metric_stable_ids(epochs, field="epoch_id", label="strategic epoch")
    grants = [metric_text(epoch.get("grant_id"), label="epoch grant") for epoch in epochs]
    if len(grants) != len(set(grants)):
        raise ValueError("absolute generalization duplicate strategic grant identity")
    physical_fill_identity_map(fills)
    allowed = set(allowed_symbols)
    for values, field, label in (
        (orders, "symbol", "order"),
        (fills, "symbol", "fill"),
        (epochs, "owner_symbol", "strategic epoch"),
    ):
        for value in values:
            symbol = metric_text(value.get(field, ""), label=f"{label} symbol", empty=True)
            if symbol and symbol not in allowed:
                raise ValueError(
                    "absolute generalization reference-only symbol received capital authority"
                )
    for row in trace:
        for collection in ("targets", "orders", "fills"):
            for value in metric_rows(row.get(collection, ()), label=f"trace {collection}"):
                symbol = metric_text(value.get("symbol", ""), label="economic symbol", empty=True)
                if symbol and symbol not in allowed:
                    raise ValueError(
                        "absolute generalization reference-only symbol received capital authority"
                    )


def metric_exact_fields(raw: Mapping[str, object], expected: set[str], *, label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"absolute generalization {label} fields differ")


def _epoch_from_raw(value: object) -> EpochFact:
    raw = metric_mapping(value, label="epoch fact")
    expected = {field for field in EpochFact.__dataclass_fields__}
    metric_exact_fields(raw, expected, label="epoch fact")
    empty = {
        "closed_session",
        "close_reason",
        "previous_epoch_id",
        "previous_grant_id",
        "authorization_id",
        "authorization_session",
        "qualification_signature",
        "qualification_route",
        "qualification_quorum",
    }
    values = {
        field: metric_text(raw[field], label=f"epoch fact {field}", empty=field in empty)
        for field in expected
    }
    for field in (
        "qualification_session",
        "grant_session",
        "target_session",
        "order_session",
        "fill_session",
        "active_session",
        "closed_session",
        "authorization_session",
    ):
        values[field] = metric_iso_session(
            values[field],
            label=f"epoch fact {field}",
            empty=field in {"closed_session", "authorization_session"},
        )
    return EpochFact(**values)


def _repair_from_raw(value: object) -> RepairEpisodeFact:
    raw = metric_mapping(value, label="repair fact")
    expected = {field for field in RepairEpisodeFact.__dataclass_fields__}
    metric_exact_fields(raw, expected, label="repair fact")
    return RepairEpisodeFact(
        repair_episode_id=metric_text(raw["repair_episode_id"], label="repair episode"),
        capital_budget_level=metric_integer(raw["capital_budget_level"], label="repair capital level"),
        repair_target_level=metric_integer(raw["repair_target_level"], label="repair target level"),
        required_healthy_sessions=metric_integer(
            raw["required_healthy_sessions"], label="repair required sessions"
        ),
        reported_healthy_sessions=metric_integer(
            raw["reported_healthy_sessions"], label="repair reported sessions"
        ),
        actual_healthy_sessions_to_ready=metric_integer(
            raw["actual_healthy_sessions_to_ready"], label="repair actual sessions"
        ),
        first_observed_session=metric_iso_session(
            raw["first_observed_session"], label="repair first session"
        ),
        last_ready_session=metric_iso_session(
            raw["last_ready_session"], label="repair ready session", empty=True
        ),
        status=metric_text(raw["status"], label="repair status"),
        reset_reason=metric_text(raw["reset_reason"], label="repair reset reason", empty=True),
    )


def _metric_scalar_values(
    raw: Mapping[str, object],
) -> tuple[dict[str, float], dict[str, int], dict[str, str]]:
    floats = {
        name: metric_number(raw[name], label=f"metric {name}")
        for name in (
            "initial_cash",
            "final_equity",
            "final_wealth",
            "total_return",
            "max_drawdown",
            "gross_turnover",
            "annual_turnover",
            "realized_pnl",
            "open_pnl",
            "cash_drag",
            "top1_concentration",
            "top3_concentration",
            "pnl_hhi",
            "tradable_coverage",
            "qualification_coverage",
            "risk_coverage",
        )
    }
    integers = {
        name: metric_integer(raw[name], label=f"metric {name}")
        for name in (
            "account_orders",
            "fill_count",
            "positive_total_target_sessions",
            "positive_strategic_target_sessions",
            "longest_healthy_zero_total_target_streak",
            "longest_healthy_zero_strategic_target_streak",
            "qualification_ready_sessions",
            "strategic_grant_count",
            "strategic_order_count",
            "strategic_fill_count",
            "actual_strategic_epoch_count",
            "distinct_owner_count",
            "repair_episode_count",
            "role_witness_sessions",
            "failed_grant_retry_healthy_sessions",
            "terminal_zero_strategic_target_state_sessions",
        )
    }
    texts = {
        name: metric_iso_session(raw[name], label=f"metric {name}", empty=True)
        for name in (
            "first_positive_total_target_session",
            "first_positive_strategic_target_session",
            "first_qualification_session",
            "first_strategic_grant_session",
            "first_strategic_order_session",
            "first_strategic_fill_session",
            "first_actual_strategic_epoch_session",
        )
    }
    return floats, integers, texts


_MetricCollections = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[EpochFact, ...], tuple[RepairEpisodeFact, ...], bool]


def _metric_collection_values(raw: Mapping[str, object]) -> _MetricCollections:
    owner_symbols = tuple(
        metric_text(item, label="metric owner symbol")
        for item in metric_sequence(raw["owner_symbols"], label="metric owner symbols")
    )
    absent = tuple(
        metric_text(item, label="metric intentional role absence")
        for item in metric_sequence(
            raw["intentional_role_absent_symbols"], label="metric intentional role absences"
        )
    )
    unavailable = tuple(
        metric_text(item, label="metric unavailable symbol")
        for item in metric_sequence(
            raw["expected_but_unavailable_symbols"], label="metric unavailable symbols"
        )
    )
    epochs = tuple(_epoch_from_raw(item) for item in metric_sequence(raw["epochs"], label="metric epochs"))
    repairs = tuple(
        _repair_from_raw(item) for item in metric_sequence(raw["repairs"], label="metric repairs")
    )
    consistent = raw["role_identity_consistent"]
    if type(consistent) is not bool:
        raise ValueError("absolute generalization metric role identity consistency is malformed")
    return owner_symbols, absent, unavailable, epochs, repairs, consistent


def _cell_metrics_from_values(
    floats: Mapping[str, float],
    integers: Mapping[str, int],
    texts: Mapping[str, str],
    collections: _MetricCollections,
) -> CellMetrics:
    owner_symbols, absent, unavailable, epochs, repairs, consistent = collections
    return CellMetrics(
        initial_cash=floats["initial_cash"],
        final_equity=floats["final_equity"],
        final_wealth=floats["final_wealth"],
        total_return=floats["total_return"],
        max_drawdown=floats["max_drawdown"],
        account_orders=integers["account_orders"],
        fill_count=integers["fill_count"],
        gross_turnover=floats["gross_turnover"],
        annual_turnover=floats["annual_turnover"],
        realized_pnl=floats["realized_pnl"],
        open_pnl=floats["open_pnl"],
        cash_drag=floats["cash_drag"],
        top1_concentration=floats["top1_concentration"],
        top3_concentration=floats["top3_concentration"],
        pnl_hhi=floats["pnl_hhi"],
        positive_total_target_sessions=integers["positive_total_target_sessions"],
        positive_strategic_target_sessions=integers["positive_strategic_target_sessions"],
        first_positive_total_target_session=texts["first_positive_total_target_session"],
        first_positive_strategic_target_session=texts["first_positive_strategic_target_session"],
        longest_healthy_zero_total_target_streak=integers["longest_healthy_zero_total_target_streak"],
        longest_healthy_zero_strategic_target_streak=integers["longest_healthy_zero_strategic_target_streak"],
        qualification_ready_sessions=integers["qualification_ready_sessions"],
        first_qualification_session=texts["first_qualification_session"],
        strategic_grant_count=integers["strategic_grant_count"],
        first_strategic_grant_session=texts["first_strategic_grant_session"],
        strategic_order_count=integers["strategic_order_count"],
        first_strategic_order_session=texts["first_strategic_order_session"],
        strategic_fill_count=integers["strategic_fill_count"],
        first_strategic_fill_session=texts["first_strategic_fill_session"],
        actual_strategic_epoch_count=integers["actual_strategic_epoch_count"],
        first_actual_strategic_epoch_session=texts["first_actual_strategic_epoch_session"],
        distinct_owner_count=integers["distinct_owner_count"],
        owner_symbols=owner_symbols,
        epochs=epochs,
        repair_episode_count=integers["repair_episode_count"],
        repairs=repairs,
        intentional_role_absent_symbols=absent,
        expected_but_unavailable_symbols=unavailable,
        tradable_coverage=floats["tradable_coverage"],
        qualification_coverage=floats["qualification_coverage"],
        risk_coverage=floats["risk_coverage"],
        role_witness_sessions=integers["role_witness_sessions"],
        role_identity_consistent=consistent,
        failed_grant_retry_healthy_sessions=integers["failed_grant_retry_healthy_sessions"],
        terminal_zero_strategic_target_state_sessions=integers[
            "terminal_zero_strategic_target_state_sessions"
        ],
    )


def _validate_cell_metric_values(metrics: CellMetrics) -> None:
    invalid = (
        metrics.initial_cash <= 0.0
        or metrics.final_equity <= 0.0
        or metrics.max_drawdown < 0.0
        or metrics.gross_turnover < 0.0
        or metrics.annual_turnover < 0.0
        or not 0.0 <= metrics.top1_concentration <= metrics.top3_concentration <= 1.0
        or not 0.0 <= metrics.pnl_hhi <= 1.0
        or any(
            not 0.0 <= value <= 1.0
            for value in (metrics.tradable_coverage, metrics.qualification_coverage, metrics.risk_coverage)
        )
        or metrics.actual_strategic_epoch_count != len(metrics.epochs)
        or metrics.repair_episode_count != len(metrics.repairs)
        or metrics.distinct_owner_count != len(metrics.owner_symbols)
        or tuple(sorted(set(metrics.owner_symbols))) != metrics.owner_symbols
    )
    if invalid:
        raise ValueError("absolute generalization metric values differ")


def _cell_metrics_from_raw_impl(raw: Mapping[str, object]) -> CellMetrics:
    metric_exact_fields(raw, {field for field in CellMetrics.__dataclass_fields__}, label="cell metrics")
    floats, integers, texts = _metric_scalar_values(raw)
    metrics = _cell_metrics_from_values(floats, integers, texts, _metric_collection_values(raw))
    _validate_cell_metric_values(metrics)
    return metrics


def cell_metrics_from_raw(raw: Mapping[str, object]) -> CellMetrics:
    """Strictly decode a serialized metric payload without mutable collections."""

    return _cell_metrics_from_raw_impl(raw)


__all__ = (
    "CellMetrics",
    "EpochFact",
    "EventEvidence",
    "RepairEpisodeFact",
    "actual_epoch_facts_from_rows",
    "assert_unique_execution_rows",
    "cell_metrics_from_raw",
    "first_repair_ready_fact",
    "longest_healthy_zero_target_streak",
    "repair_episode_facts_from_trace",
)
