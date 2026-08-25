"""Production backtest orchestration and result assembly."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import pandas as pd

from ..attribution import (
    build_daily_ledger_row,
    build_daily_replay_evidence_row,
    build_economic_attribution,
)
from ..config import SystemConfig, config_fingerprint
from ..contracts.runtime_identity import require_ai_era_interval
from ..data import normalize_symbol
from ..execution import ExecutionPlanner
from ..market import MarketWorkspace
from ..types import AccountState, Decision


class EquityEngineRuntime(Protocol):
    _raw: dict[str, pd.DataFrame]

    def _price(self, symbol: str, date: pd.Timestamp, field: str = "close") -> float: ...


class BacktestEngineRuntime(EquityEngineRuntime, Protocol):
    @property
    def cfg(self) -> SystemConfig: ...

    @property
    def workspace(self) -> MarketWorkspace: ...

    @property
    def execution(self) -> ExecutionPlanner: ...

    def equity(
        self,
        account: AccountState,
        date: pd.Timestamp,
        field: str = "close",
    ) -> float: ...

    def decide(self, *, symbols: Iterable[str], as_of: str, account: AccountState) -> Decision: ...


def equity(
    self: EquityEngineRuntime,
    account: AccountState,
    date: pd.Timestamp,
    field: str = "close",
) -> float:
    """Mark current positions at the latest visible field and add cash."""
    return account.cash + sum(
        (
            position.shares * self._price(symbol, date, field)
            for symbol, position in account.positions.items()
            if symbol in self._raw
        )
    )


def _backtest_stage_1(
    *,
    account: Any,
    daily_ledger: Any,
    daily_replay_evidence: Any,
    decisions: Any,
    final_date: Any,
    final_equity: Any,
    metrics: Any,
    self: Any,
    sessions: Any,
) -> None:
    metrics.update(
        start=str(sessions[0].date()),
        end=str(sessions[-1].date()),
        effective_config_sha256=config_fingerprint(self.cfg),
        final_wealth=final_equity / account.initial_cash,
        final_equity=final_equity,
        decision_digests=[item.decision_digest for item in decisions],
        decision_trace=[
            item.canonical_payload(effective_config_sha256=config_fingerprint(self.cfg)) for item in decisions
        ],
        legacy_decision_digests=[
            hashlib.sha256(
                json.dumps(item.legacy_canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for item in decisions
        ],
        daily_replay_evidence=daily_replay_evidence,
        sentinel_events=[
            {
                "date": item.date,
                "level": item.risk_summary["sentinel_assessment"]["level"],
                "confidence": item.risk_summary["sentinel_assessment"]["confidence"],
                "families": item.risk_summary["sentinel_assessment"]["evidence_families"],
                "base_family_active": item.risk_summary["base_family_active"],
                "sentinel_family_active": item.risk_summary["sentinel_family_active"],
                "combined_family_active": item.risk_summary["combined_family_active"],
                "incremental": item.risk_summary["sentinel_incremental"],
                "incremental_families": item.risk_summary["sentinel_incremental_families"],
                "earlier_families": item.risk_summary["sentinel_earlier_families"],
                "first_base_date": item.risk_summary["first_base_date"],
                "first_sentinel_date": item.risk_summary["first_sentinel_date"],
                "confirmation_days": item.risk_summary["sentinel_confirmation_days"],
                "freeze_new_risk": item.risk_summary["sentinel_freeze_new_risk"],
                "base_freeze_new_risk": item.risk_summary["base_freeze_new_risk"],
                "target_gross_cap": item.risk_summary["target_gross_cap"],
                "base_target_gross_cap": item.risk_summary["base_target_gross_cap"],
            }
            for item in decisions
            if isinstance(item.risk_summary.get("sentinel_assessment"), dict)
            and (
                bool(item.risk_summary["sentinel_assessment"].get("evidence_families"))
                or bool(item.risk_summary.get("sentinel_incremental", False))
                or bool(item.risk_summary.get("sentinel_freeze_new_risk", False))
            )
        ],
        pending_orders=len(account.pending_orders),
        final_account=account.to_dict(),
        attribution=build_economic_attribution(
            account=account,
            final_prices={
                symbol: self._price(symbol, final_date)
                for symbol, position in account.positions.items()
                if position.shares > 0
            },
            sessions=tuple(str(date.date()) for date in sessions),
            economic_start=str(sessions[0].date()),
            economic_end=str(sessions[-1].date()),
            final_equity=final_equity,
            daily_ledger=daily_ledger,
            benchmark_close={str(date.date()): self.workspace.price("sh000682", date) for date in sessions},
        ),
        internal_events={
            "risk": len(account.risk_events),
            "lifecycle": len(account.lifecycle_events),
            "replacement": len(account.replacement_events),
            "target_decisions": sum(len(item.targets) for item in decisions),
            "pending_order_intents": sum(len(item.pending_orders) for item in decisions),
            "broker_submissions": len(account.order_ledger),
            "unfilled_broker_submissions": sum(item.filled_shares == 0 for item in account.order_ledger),
        },
        daily_risk_states=[{"date": item.date, "state": item.risk.value} for item in decisions],
    )


def backtest(
    self: BacktestEngineRuntime,
    performance_metrics_fn: Callable[..., dict[str, Any]],
    *,
    symbols: Iterable[str],
    start: str,
    end: str,
    initial_cash: float | None = None,
) -> dict[str, Any]:
    """Replay the production decision and next-open execution path."""
    start, end = require_ai_era_interval(start, end)
    user_symbols = tuple(sorted({normalize_symbol(item) for item in symbols}))
    self.workspace.prepare(self.workspace.bind_tradable(user_symbols))
    sessions = self.workspace.common_sessions(*self.workspace.universe.index_symbols)
    sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
    if len(sessions) < 2:
        raise RuntimeError("backtest window has fewer than two sessions")
    account = AccountState.empty(initial_cash or self.cfg.initial_cash)
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    decisions: list[Decision] = []
    daily_ledger: list[dict[str, Any]] = []
    daily_replay_evidence: list[dict[str, Any]] = []
    previous_equity = account.initial_cash
    raw_user_panel = {symbol: self._raw[symbol] for symbol in user_symbols}
    for date in sessions:
        self.execution.execute_open(date=date, account=account, panel=raw_user_panel)
        equity = self.equity(account, date)
        equity_rows.append((date, equity))
        decision = self.decide(symbols=user_symbols, as_of=str(date.date()), account=account)
        decisions.append(decision)
        close_prices = {
            symbol: self._price(symbol, date)
            for symbol, position in account.positions.items()
            if position.shares > 0
        }
        daily_ledger.append(
            build_daily_ledger_row(
                date=str(date.date()),
                account=account,
                close_prices=close_prices,
                previous_equity=previous_equity,
                target_weights={item.symbol: item.weight for item in decision.targets},
                target_gross=decision.target_gross,
                risk_gross_cap=float(decision.risk_summary["target_gross_cap"]),
                system_gross_cap=float(decision.risk_summary["system_gross_cap"]),
                risk_state=decision.risk.value,
                opportunity=decision.opportunity.value,
            )
        )
        daily_replay_evidence.append(
            build_daily_replay_evidence_row(date=str(date.date()), account=account, close_prices=close_prices)
        )
        previous_equity = equity
        account.pending_orders = list(decision.pending_orders)
    final_date = sessions[-1]
    final_equity = self.equity(account, final_date)
    metrics = performance_metrics_fn(
        equity_rows=equity_rows,
        fills=account.fills,
        orders=account.order_ledger,
        initial_cash=account.initial_cash,
        risk_events=account.risk_events,
        benchmark_total_return=self.workspace.price("sh000682", final_date)
        / self.workspace.price("sh000682", sessions[0])
        - 1.0,
    )
    _backtest_stage_1(
        account=account,
        daily_ledger=daily_ledger,
        daily_replay_evidence=daily_replay_evidence,
        decisions=decisions,
        final_date=final_date,
        final_equity=final_equity,
        metrics=metrics,
        self=self,
        sessions=sessions,
    )
    return metrics
