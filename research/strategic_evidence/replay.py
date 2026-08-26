"""Official close/open replay loop with one research-only intervention hook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from math import isclose
from pathlib import Path
from typing import Any

import pandas as pd

from research.candidate_runner import CandidateRunner, _CausalReplayDataStore
from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.engine import ProductionEngine, performance_metrics
from uquant.market import ReplayHarness
from uquant.types import AccountState, Decision
from uquant.validation.ai_era import AI_ERA_START

from .intervention import StrategicOwnerIntervention
from .trace import RouteTraceRow

_ACCOUNTING_TOLERANCE = 1e-8
_FUTURE_HOLDOUT_BOUNDARY = "2026-08-06"


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """A bounded production replay request that cannot select Future Holdout data."""

    symbols: tuple[str, ...]
    start: str
    end: str
    future_holdout_boundary: str = _FUTURE_HOLDOUT_BOUNDARY
    scenario: str = "baseline"
    initial_cash: float | None = None
    intervention_date: str | None = None

    def __post_init__(self) -> None:
        if self.future_holdout_boundary != _FUTURE_HOLDOUT_BOUNDARY:
            raise ValueError("future holdout boundary is immutable")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("replay symbols must be a non-empty unique sequence")
        try:
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
            boundary = date.fromisoformat(self.future_holdout_boundary)
            intervention = (
                date.fromisoformat(self.intervention_date) if self.intervention_date is not None else None
            )
        except ValueError as exc:
            raise ValueError("replay dates must be ISO-8601") from exc
        if start < date.fromisoformat(AI_ERA_START):
            raise ValueError(f"replay begins before AI era {AI_ERA_START}")
        if start > end:
            raise ValueError("replay starts after it ends")
        if end >= boundary or (intervention is not None and intervention >= boundary):
            raise ValueError("replay request includes future holdout data")
        if intervention is not None and not start <= intervention <= end:
            raise ValueError("intervention date lies outside replay window")
        if self.initial_cash is not None and self.initial_cash <= 0:
            raise ValueError("initial cash must be positive")


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Replay output retaining trace, account facts, and intervention audit evidence."""

    request: ReplayRequest
    metrics: Mapping[str, Any]
    trace: tuple[RouteTraceRow, ...]
    final_account: Mapping[str, Any]
    intervention_provenance: Mapping[str, Any] | None
    status: str = "SUCCESS"
    error: str | None = None


def reconcile_accounting(
    *,
    cash: float,
    position_shares: Mapping[str, int],
    close_marks: Mapping[str, float],
    equity: float,
) -> float:
    """Rebuild same-close equity from durable cash, shares, and frozen marks."""

    if set(position_shares) != set(close_marks):
        raise ValueError("accounting position shares and close marks differ")
    if cash < 0 or equity < 0:
        raise ValueError("accounting cash and equity must be non-negative")
    marked = float(cash)
    for symbol, shares in position_shares.items():
        if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
            raise ValueError(f"accounting shares are invalid for {symbol}")
        mark = close_marks[symbol]
        if mark <= 0:
            raise ValueError(f"accounting close mark is invalid for {symbol}")
        marked += shares * float(mark)
    if not isclose(marked, float(equity), rel_tol=1e-12, abs_tol=_ACCOUNTING_TOLERANCE):
        raise ValueError("accounting equity does not reconcile to cash and marked positions")
    return marked


def _route_trace_row(
    *,
    account: AccountState,
    decision: Decision,
    equity: float,
    new_fills: tuple[Any, ...],
    intervention_provenance: Mapping[str, Any] | None,
    close_marks: Mapping[str, float],
) -> RouteTraceRow:
    risk_summary = decision.risk_summary
    raw_leaders = risk_summary.get("leader_ranking", ())
    leaders = tuple(dict(item) for item in raw_leaders if isinstance(item, Mapping))
    reference_context = {
        str(name): value for name, value in risk_summary.items() if str(name).startswith("reference_")
    }
    risk = {
        str(name): value
        for name, value in risk_summary.items()
        if name not in {"leader_ranking", "effective_config_sha256"}
        and not str(name).startswith("reference_")
    }
    from uquant.account import economic_state_sha256

    return RouteTraceRow(
        date=decision.date,
        reference_context=reference_context,
        leaders=leaders,
        risk={"state": decision.risk.value, **risk},
        opportunity=decision.opportunity.value,
        targets=tuple(asdict(item) for item in decision.targets),
        orders=tuple(asdict(item) for item in decision.pending_orders),
        fills=tuple(asdict(item) for item in new_fills),
        account_sha256=economic_state_sha256(account),
        equity=equity,
        target_gross=decision.target_gross,
        intervention_provenance=intervention_provenance,
        cash=account.cash,
        position_shares={
            symbol: position.shares for symbol, position in account.positions.items() if position.shares
        },
        close_marks=dict(close_marks),
    )


def run_replay(
    data_dir: str | Path,
    request: ReplayRequest,
    *,
    cfg: SystemConfig = DEFAULT_CONFIG,
    intervention: StrategicOwnerIntervention | None = None,
) -> ReplayResult:
    """Preserve terminal replay outcomes as result rows rather than dropping cells."""

    try:
        return _run_replay_success(data_dir, request, cfg=cfg, intervention=intervention)
    except (RuntimeError, ValueError) as exc:
        status = "INSUFFICIENT_SAMPLE" if "fewer than two sessions" in str(exc) else "REPLAY_ERROR"
        return ReplayResult(
            request=request,
            metrics={},
            trace=(),
            final_account={},
            intervention_provenance=intervention.provenance if intervention is not None else None,
            status=status,
            error=str(exc),
        )


def _run_replay_success(
    data_dir: str | Path,
    request: ReplayRequest,
    *,
    cfg: SystemConfig = DEFAULT_CONFIG,
    intervention: StrategicOwnerIntervention | None = None,
) -> ReplayResult:
    """Run only production execution/decision code with a one-shot pre-decision hook."""

    if (intervention is None) != (request.intervention_date is None):
        raise ValueError("intervention and intervention date must be supplied together")
    engine = ProductionEngine(data_dir, cfg)
    engine.data = _CausalReplayDataStore(data_dir)
    runner = CandidateRunner(data_dir, cfg)
    harness = ReplayHarness(
        workspace=engine.workspace,
        universe=runner.replay_universe(request.symbols),
    )
    sessions = harness.sessions(start=request.start, end=request.end)
    if len(sessions) < 2:
        raise RuntimeError("replay window has fewer than two sessions")
    account = AccountState.empty(request.initial_cash or cfg.initial_cash)
    raw_user_panel = harness.raw_panel(request.symbols)
    trace: list[RouteTraceRow] = []
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    intervention_provenance: Mapping[str, Any] | None = None
    fill_cursor = 0
    for session in sessions:
        engine.execution.execute_open(date=session, account=account, panel=raw_user_panel)
        equity = engine.equity(account, session)
        equity_rows.append((session, equity))
        session_date = str(session.date())
        if request.intervention_date == session_date:
            assert intervention is not None
            intervention_provenance = intervention.apply(account)
        new_fills = tuple(account.fills[fill_cursor:])
        fill_cursor = len(account.fills)
        decision = engine.decide(symbols=request.symbols, as_of=session_date, account=account)
        if request.intervention_date == session_date:
            assert intervention is not None
            decision = intervention.preserve_activation(account, decision)
        account.pending_orders = list(decision.pending_orders)
        close_marks = {
            symbol: engine._price(symbol, session)
            for symbol, position in account.positions.items()
            if position.shares > 0
        }
        reconcile_accounting(
            cash=account.cash,
            position_shares={
                symbol: position.shares for symbol, position in account.positions.items() if position.shares
            },
            close_marks=close_marks,
            equity=equity,
        )
        trace.append(
            _route_trace_row(
                account=account,
                decision=decision,
                equity=equity,
                new_fills=new_fills,
                intervention_provenance=intervention_provenance
                if request.intervention_date == session_date
                else None,
                close_marks=close_marks,
            )
        )
    if intervention is not None and not intervention.applied:
        raise RuntimeError("intervention date is absent from the official replay calendar")
    final_equity = engine.equity(account, pd.Timestamp(sessions[-1]))
    metrics = performance_metrics(
        equity_rows=equity_rows,
        fills=account.fills,
        orders=account.order_ledger,
        initial_cash=account.initial_cash,
        risk_events=account.risk_events,
        benchmark_total_return=(
            engine._price("sh000682", sessions[-1]) / engine._price("sh000682", sessions[0]) - 1.0
        ),
    )
    metrics["final_equity"] = final_equity
    return ReplayResult(
        request=request,
        metrics=metrics,
        trace=tuple(trace),
        final_account=account.to_dict(),
        intervention_provenance=intervention_provenance,
    )


def validate_replay_accounting(result: ReplayResult) -> None:
    """Verify final replay equity independently from its durable account state."""

    account = result.final_account
    positions = account.get("positions")
    if not isinstance(positions, Mapping):
        raise ValueError("accounting final positions are malformed")
    # The final route has the exact same-close marks needed to reconcile the durable state.
    if not result.trace:
        raise ValueError("accounting replay trace is empty")
    last = result.trace[-1]
    close_marks = {str(symbol): float(value) for symbol, value in last.close_marks.items()}
    reconcile_accounting(
        cash=float(account.get("cash", -1.0)),
        position_shares={
            str(symbol): _exact_account_shares(item, symbol=str(symbol)) for symbol, item in positions.items()
        },
        close_marks=close_marks,
        equity=float(result.metrics["final_equity"]),
    )


def _exact_account_shares(value: object, *, symbol: str) -> int:
    if not isinstance(value, Mapping):
        raise ValueError(f"accounting position is malformed for {symbol}")
    shares = value.get("shares")
    if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
        raise ValueError(f"accounting shares are invalid for {symbol}")
    return shares


def common_activation_date(result: ReplayResult) -> str:
    """Find the first causal strategic activation without a hand-selected date."""

    previous_epoch = 0
    for row in result.trace:
        raw_epoch = row.risk.get("strategic_epoch", 0)
        if isinstance(raw_epoch, bool) or not isinstance(raw_epoch, int) or raw_epoch < 0:
            raise ValueError("strategic epoch trace is malformed")
        opens_cohort = any(
            target.get("origin_subsystem") == "STRATEGIC" and target.get("mechanism") == "STRATEGIC_COHORT"
            for target in row.targets
        )
        if raw_epoch > previous_epoch or opens_cohort:
            return row.date
        previous_epoch = raw_epoch
    raise ValueError("baseline replay has no strategic activation")


def common_activation_target_gross(result: ReplayResult) -> float:
    """Return the production target gross on the causally detected activation date."""

    activation = common_activation_date(result)
    row = next(item for item in result.trace if item.date == activation)
    if row.target_gross <= 0.0:
        raise ValueError("baseline strategic activation has no positive target gross")
    return row.target_gross


__all__ = (
    "ReplayRequest",
    "ReplayResult",
    "common_activation_date",
    "common_activation_target_gross",
    "reconcile_accounting",
    "run_replay",
    "validate_replay_accounting",
)
