"""The only production engine; daily and backtest call the same decision path."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from .data import DataStore, normalize_symbol
from .execution import (
    ExecutionPlanner,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from .features import compute_features
from .leader import (
    INDUSTRY,
    REFERENCE_UNIVERSE,
    apply_leader_tenure,
    apply_opportunity_alpha,
    compute_leaders,
    compute_structural_leaders,
)
from .opportunity import classify_opportunity
from .portfolio import PortfolioAllocator, current_weights
from .reference import build_reference_context
from .reference_registry import DEFAULT_REGISTRY_PATH, resolve_reference_symbols
from .risk import assess_risk
from .types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountOrder,
    AccountState,
    Decision,
    Fill,
    LeaderScore,
    Opportunity,
    PendingOrder,
    Target,
    derive_attribution_event_id,
)
from .validation.ai_era import require_ai_era_interval
from .validation.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe

INDEX_SYMBOLS = ("sh000300", "sh000682")
_LEGACY_INDUSTRY = "legacy_unmapped"
_LEGACY_MANIFEST_SHA256 = "0" * 64


def _decision_config_for_universe(
    configured_universe_size: int,
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> SystemConfig:
    """Return one production policy regardless of unrelated universe members.

    The positional argument remains for state/API compatibility and diagnostic
    provenance.  It must never select a different strategy configuration: an
    otherwise irrelevant symbol cannot change the decision path merely by
    crossing a pool-size threshold.
    """
    del configured_universe_size
    return cfg


def _attach_target_attribution(
    *,
    signal_date: str,
    targets: tuple[Target, ...],
    retained_orders: Iterable[PendingOrder] = (),
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> tuple[Target, ...]:
    """Finalize deterministic IDs and PIT industry for newly causal targets."""

    universe = default_ai_universe()
    retained_by_symbol = {
        order.symbol: order
        for order in retained_orders
        if order.event_id and order.remaining_shares > 0
    }
    attributed: list[Target] = []
    for target in targets:
        if target.event_id:
            attributed.append(target)
            continue
        retained = retained_by_symbol.get(target.symbol)
        if retained is not None and (
            abs(retained.target_weight - target.weight) < cfg.min_trade_weight
            and retained.lifecycle == target.lifecycle
            and retained.reduction_policy == target.reduction_policy
            and retained.origin_subsystem == target.origin_subsystem
            and retained.mechanism == target.mechanism
            and retained.origin_lifecycle == target.origin_lifecycle
            and retained.replaces_symbol == target.replaces_symbol
        ):
            attributed.append(
                replace(
                    target,
                    event_id=retained.event_id,
                    industry_at_entry=retained.industry_at_entry,
                    industry_manifest_sha256=retained.industry_manifest_sha256,
                )
            )
            continue
        industry = universe.industry_of(target.symbol, signal_date)
        manifest = REQUIRED_AI_UNIVERSE_SHA256
        if industry == "unknown":
            industry = _LEGACY_INDUSTRY
            manifest = _LEGACY_MANIFEST_SHA256
        event_id = derive_attribution_event_id(
            signal_date=signal_date,
            symbol=target.symbol,
            target_weight=target.weight,
            lifecycle=target.lifecycle,
            origin_lifecycle=target.origin_lifecycle,
            origin_subsystem=target.origin_subsystem,
            mechanism=target.mechanism,
            replaces_symbol=target.replaces_symbol,
            industry_at_entry=industry,
            industry_manifest_sha256=manifest,
            reduction_policy=target.reduction_policy,
            reason_code=target.reason_code,
            exit_kind=target.exit_kind,
        )
        attributed.append(
            replace(
                target,
                event_id=event_id,
                industry_at_entry=industry,
                industry_manifest_sha256=manifest,
            )
        )
    return tuple(attributed)


def code_fingerprint() -> str:
    """Return a stable digest of all production modules in the package root."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(DEFAULT_REGISTRY_PATH.name.encode())
    digest.update(DEFAULT_REGISTRY_PATH.read_bytes())
    return digest.hexdigest()


class ProductionEngine:
    """Own the single decision path used by both daily operation and replay."""

    def __init__(self, data_dir: str | Path, cfg: SystemConfig = DEFAULT_CONFIG) -> None:
        self.cfg = cfg
        self.data = DataStore(data_dir)
        self.execution = ExecutionPlanner(cfg)
        self.allocator = PortfolioAllocator(cfg)
        self._raw: dict[str, pd.DataFrame] = {}
        self._features: dict[str, pd.DataFrame] = {}
        self._manifest_cache: dict[tuple[tuple[str, ...], str], str] = {}
        self._reference_returns: pd.DataFrame | None = None
        self._code_hash: str | None = None
        self._leader_score_cache: dict[tuple[object, ...], dict[str, LeaderScore]] = {}

    def _load(self, symbols: Iterable[str]) -> None:
        for symbol in sorted({normalize_symbol(item) for item in symbols}):
            if symbol not in self._raw:
                raw = self.data.load(symbol)
                self._raw[symbol] = raw
                self._features[symbol] = compute_features(raw, self.cfg)
        if self._reference_returns is None and set(REFERENCE_UNIVERSE).issubset(self._raw):
            self._reference_returns = pd.DataFrame(
                {
                    symbol: self._raw[symbol]["close"].pct_change(fill_method=None)
                    for symbol in REFERENCE_UNIVERSE
                }
            )

    def _price(self, symbol: str, date: pd.Timestamp, field: str = "close") -> float:
        frame = self._raw[symbol].loc[:date]
        if frame.empty:
            raise RuntimeError(f"{symbol} has no mark price at {date.date()}")
        return float(frame.iloc[-1][field])

    def equity(self, account: AccountState, date: pd.Timestamp, field: str = "close") -> float:
        """Mark current positions at the latest visible field and add cash."""

        return account.cash + sum(
            position.shares * self._price(symbol, date, field)
            for symbol, position in account.positions.items()
            if symbol in self._raw
        )

    def _mark_account_positions(self, account: AccountState, date: pd.Timestamp) -> None:
        """Advance every owned economic lot once using the causal closing mark.

        Daily operation and replay both enter through :meth:`decide`, so keeping
        mark-to-market state here prevents live trailing exits, winner retention,
        and lot-priority decisions from diverging from a backtest.  Suspended
        holdings retain their prior mark until the next observed session.
        """
        for symbol, position in account.positions.items():
            frame = self._raw.get(symbol)
            if frame is None or date not in frame.index:
                continue
            close = self._price(symbol, date)
            position.highest_close = max(position.highest_close, close)
            for tranche in position.tranches:
                tranche.highest_close = max(tranche.highest_close, close)
                tranche.lowest_close = (
                    close if tranche.lowest_close <= 0 else min(tranche.lowest_close, close)
                )
                excursion = close / max(tranche.avg_cost, 1e-12) - 1.0
                tranche.mfe = max(tranche.mfe, excursion)
                tranche.mae = min(tranche.mae, excursion)

    def decide(self, *, symbols: Iterable[str], as_of: str, account: AccountState) -> Decision:
        """Produce and persist one causal close-date portfolio decision.

        The account is advanced in place after all data, code, state, and
        chronology checks succeed. Returned orders are next-open intentions;
        this method never fills them on the signal date.
        """

        if account.schema_version != ACCOUNT_SCHEMA_VERSION:
            raise RuntimeError(f"account schema {account.schema_version} requires explicit migration")
        date = pd.Timestamp(as_of).normalize()
        if account.last_successful_run and pd.Timestamp(account.last_successful_run) >= date:
            raise RuntimeError("decision date must be strictly after the last successful run")
        broker_as_of = getattr(account, "broker_as_of", "")
        if broker_as_of and date < pd.Timestamp(str(broker_as_of)):
            raise RuntimeError("decision date predates the authoritative broker snapshot")
        user_symbols = tuple(sorted({normalize_symbol(item) for item in symbols}))
        if not user_symbols:
            raise ValueError("at least one technology-sector symbol is required")
        durable_symbols = (
            set(account.positions)
            | set(account.protected_weights)
            | set(account.sector_guard_symbols)
            | set(account.anchor_weights)
            | set(account.strategic_cohort_symbols)
            | set(account.strategic_cohort_targets)
            | set(account.strategic_restore_weights)
            | set(account.active_leaders)
            | {order.symbol for order in account.pending_orders}
        )
        if account.tactical_anchor_symbol:
            durable_symbols.add(account.tactical_anchor_symbol)
        all_symbols = set(user_symbols) | durable_symbols | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)
        self._load(all_symbols)
        if date not in self._features["sh000300"].index or date not in self._features["sh000682"].index:
            raise RuntimeError("decision date is not a common index session")
        current_symbols = tuple(
            sorted(symbol for symbol in all_symbols if not self._raw[symbol].loc[:date].empty)
        )
        if account.data_hash:
            verification_symbols = tuple(sorted(account.data_hash_symbols or current_symbols))
            verification_as_of = account.data_hash_as_of or account.last_successful_run
            if verification_as_of:
                verification_date = pd.Timestamp(verification_as_of).normalize()
                if verification_date > date:
                    raise RuntimeError("account data provenance comes from a future date")
                verification_key = (
                    verification_symbols,
                    str(verification_date.date()),
                )
                if verification_key not in self._manifest_cache:
                    self._manifest_cache[verification_key] = self.data.manifest(
                        verification_symbols,
                        as_of=verification_date,
                    ).digest
                verified_digest = self._manifest_cache[verification_key]
            else:
                # Accounts without an as-of boundary require their exact data
                # snapshot. A successful run then records bounded provenance.
                verified_digest = self.data.manifest(verification_symbols).digest
            if account.data_hash != verified_digest and self.cfg.fail_closed:
                raise RuntimeError("historical data prefix differs from account state")
        manifest_key = (current_symbols, str(date.date()))
        if manifest_key not in self._manifest_cache:
            self._manifest_cache[manifest_key] = self.data.manifest(
                current_symbols,
                as_of=date,
            ).digest
        data_digest = self._manifest_cache[manifest_key]
        if self._code_hash is None:
            self._code_hash = code_fingerprint()
        current_code_hash = self._code_hash
        if account.code_hash and account.code_hash != current_code_hash and self.cfg.fail_closed:
            raise RuntimeError("production code hash differs from account state")
        self._mark_account_positions(account, date)
        active_reference_symbols = resolve_reference_symbols(date)
        reference_panel = {
            symbol: self._features[symbol] for symbol in active_reference_symbols
        }
        strategy_symbols = tuple(sorted(set(user_symbols) | durable_symbols))
        user_panel = {
            symbol: self._features[symbol]
            for symbol in strategy_symbols
            if not self._raw[symbol].loc[:date].empty
        }
        combined = dict(reference_panel)
        combined.update(user_panel)
        broad = self._features["sh000300"]
        tech = self._features["sh000682"]
        decision_cfg = _decision_config_for_universe(len(user_symbols), self.cfg)
        reference_context = build_reference_context(
            date=date,
            panel=reference_panel,
            industries=INDUSTRY,
            cfg=decision_cfg,
            reference_returns=self._reference_returns,
        )
        if decision_cfg.same_day_leader_pipeline_enabled:
            structural_leaders = compute_structural_leaders(
                combined,
                as_of=date,
                tech=tech,
                cfg=decision_cfg,
                score_cache=self._leader_score_cache,
            )
        else:
            structural_leaders = compute_leaders(
                combined,
                as_of=date,
                tech=tech,
                account=account,
                cfg=decision_cfg,
                score_cache=self._leader_score_cache,
            )
        # A historical universe can legitimately contain securities that had not
        # listed yet. They are invisible until their first row; an existing
        # position, however, must always remain markable and therefore still
        # fails closed through ``_price`` if its data disappears.
        visible_users = set(user_panel)
        prices = {symbol: self._price(symbol, date) for symbol in visible_users | set(account.positions)}
        _, equity = current_weights(account, prices)
        risk = assess_risk(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            reference_returns=self._reference_returns,
            user_panel=user_panel,
            leaders=structural_leaders,
            account=account,
            equity=equity,
            cfg=decision_cfg,
            reference_context=(
                reference_context if decision_cfg.group_balanced_reference_enabled else None
            ),
            configured_universe_size=len(user_symbols),
        )
        risk.evidence["configured_user_universe_size"] = len(user_symbols)
        risk.evidence["universe_size_is_diagnostic_only"] = True
        structural_users = {
            symbol: structural_leaders[symbol]
            for symbol in user_symbols
            if symbol in structural_leaders
        }
        opportunity = classify_opportunity(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            leaders=structural_users,
            risk=risk.state,
            account=account,
            cfg=decision_cfg,
            reference_context=(
                reference_context if decision_cfg.group_balanced_reference_enabled else None
            ),
        )
        if decision_cfg.same_day_leader_pipeline_enabled:
            alpha_leaders = apply_opportunity_alpha(
                structural_leaders,
                opportunity=opportunity,
                cfg=decision_cfg,
            )
            all_leaders = apply_leader_tenure(
                alpha_leaders,
                account=account,
                cfg=decision_cfg,
            )
        else:
            all_leaders = structural_leaders
        user_leaders = {
            symbol: all_leaders[symbol] for symbol in user_symbols if symbol in all_leaders
        }
        leader_factor_profile = (
            "TREND"
            if opportunity in {Opportunity.STRONG_TREND, Opportunity.TREND}
            else "RECOVERY"
            if opportunity is Opportunity.RECOVERY
            else "CHOPPY"
        )
        targets = self.allocator.allocate(
            date=date,
            opportunity=opportunity,
            risk=risk,
            user_panel=user_panel,
            leaders=user_leaders,
            account=account,
            prices=prices,
        )
        targets = _attach_target_attribution(
            signal_date=str(date.date()),
            targets=targets,
            retained_orders=account.pending_orders,
            cfg=self.cfg,
        )
        if not decision_cfg.group_balanced_reference_enabled:
            # The selected policy uses the security-weighted view for decisions.
            # Preserve the independently computed point-in-time snapshot only
            # as diagnostics so traces stay complete without changing weights.
            risk.evidence.update(reference_context.evidence())
        planned_orders = plan_orders(
            signal_date=str(date.date()),
            targets=targets,
            account=account,
            prices=prices,
            cfg=self.cfg,
        )
        previous_orders = list(account.pending_orders)
        orders = merge_pending_orders(
            retained=previous_orders,
            planned=planned_orders,
            targets=targets,
            cfg=self.cfg,
        )
        orders = reconcile_account_orders(
            account=account,
            previous=previous_orders,
            current=orders,
            submitted_date=str(date.date()),
        )
        canonical = {
            "date": str(date.date()),
            "opportunity": opportunity.value,
            "risk": risk.state.value,
            "targets": [
                {
                    "symbol": item.symbol,
                    "weight": round(item.weight, 12),
                    "lifecycle": item.lifecycle,
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                    "event_id": item.event_id,
                    "origin_subsystem": item.origin_subsystem,
                    "mechanism": item.mechanism,
                    "origin_lifecycle": item.origin_lifecycle,
                    "replaces_symbol": item.replaces_symbol,
                    "industry_at_entry": item.industry_at_entry,
                    "industry_manifest_sha256": item.industry_manifest_sha256,
                }
                for item in targets
            ],
            "orders": [
                {
                    "order_id": item.order_id,
                    "symbol": item.symbol,
                    "side": item.side,
                    "target_weight": round(item.target_weight, 12),
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                    "event_id": item.event_id,
                    "origin_subsystem": item.origin_subsystem,
                    "mechanism": item.mechanism,
                    "origin_lifecycle": item.origin_lifecycle,
                    "replaces_symbol": item.replaces_symbol,
                    "industry_at_entry": item.industry_at_entry,
                    "industry_manifest_sha256": item.industry_manifest_sha256,
                }
                for item in orders
            ],
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        account.last_successful_run = str(date.date())
        account.data_hash = data_digest
        account.data_hash_as_of = str(date.date())
        account.data_hash_symbols = list(current_symbols)
        account.code_hash = current_code_hash
        return Decision(
            date=str(date.date()),
            opportunity=opportunity,
            risk=risk.state,
            target_gross=sum(item.weight for item in targets),
            target_k=sum(item.weight > 0 for item in targets),
            targets=targets,
            pending_orders=orders,
            risk_summary={
                **risk.evidence,
                "votes": risk.votes,
                "reasons": list(risk.reasons),
                "shock_state": risk.shock_state,
                "reduction_level": risk.reduction_level,
                "severity": risk.severity,
                "strategic_epoch": account.strategic_epoch,
                "strategic_candidate_signature": (account.strategic_candidate_signature),
                "factor_profile": leader_factor_profile,
                "effective_config_sha256": config_fingerprint(decision_cfg),
                "leader_ranking": [
                    {
                        "symbol": item.symbol,
                        "score": item.score,
                        "industry": item.industry,
                        "mature": item.mature,
                        "emerging": item.emerging,
                    }
                    for item in sorted(
                        user_leaders.values(),
                        key=lambda candidate: (-candidate.score, candidate.symbol),
                    )
                ],
            },
            decision_digest=digest,
        )

    def deterministic_decision(
        self, *, symbols: Iterable[str], as_of: str, account: AccountState
    ) -> tuple[Decision, AccountState]:
        """Evaluate a decision on a deep copy and return both result and copy."""

        cloned = copy.deepcopy(account)
        return self.decide(symbols=symbols, as_of=as_of, account=cloned), cloned

    def backtest(
        self,
        *,
        symbols: Iterable[str],
        start: str,
        end: str,
        initial_cash: float | None = None,
    ) -> dict[str, Any]:
        """Replay the production decision and next-open execution path."""

        start, end = require_ai_era_interval(start, end)
        user_symbols = tuple(sorted({normalize_symbol(item) for item in symbols}))
        self._load(set(user_symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS))
        sessions = self._raw["sh000300"].index.intersection(self._raw["sh000682"].index)
        sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
        if len(sessions) < 2:
            raise RuntimeError("backtest window has fewer than two sessions")
        account = AccountState.empty(initial_cash or self.cfg.initial_cash)
        # First state has no prior persisted hash. Daily production requires hashes after initialization.
        equity_rows: list[tuple[pd.Timestamp, float]] = []
        decisions: list[Decision] = []
        raw_user_panel = {symbol: self._raw[symbol] for symbol in user_symbols}
        for date in sessions:
            self.execution.execute_open(date=date, account=account, panel=raw_user_panel)
            equity = self.equity(account, date)
            equity_rows.append((date, equity))
            decision = self.decide(symbols=user_symbols, as_of=str(date.date()), account=account)
            decisions.append(decision)
            account.pending_orders = list(decision.pending_orders)
        final_date = sessions[-1]
        final_equity = self.equity(account, final_date)
        metrics = performance_metrics(
            equity_rows=equity_rows,
            fills=account.fills,
            orders=account.order_ledger,
            initial_cash=account.initial_cash,
            risk_events=account.risk_events,
            benchmark_total_return=(
                float(
                    self._raw["sh000682"].loc[final_date, "close"]
                    / self._raw["sh000682"].loc[sessions[0], "close"]
                    - 1.0
                )
            ),
        )
        metrics.update(
            start=str(sessions[0].date()),
            end=str(sessions[-1].date()),
            effective_config_sha256=config_fingerprint(self.cfg),
            final_wealth=final_equity / account.initial_cash,
            final_equity=final_equity,
            decision_digests=[item.decision_digest for item in decisions],
            pending_orders=len(account.pending_orders),
            final_account=account.to_dict(),
            attribution=attribution(
                account.fills,
                panel=raw_user_panel,
                benchmark=self._raw["sh000682"],
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
        return metrics


def _drawdown_stats(equity: pd.Series) -> dict[str, float | int]:
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    underwater = drawdown < 0
    duration = current = 0
    for flag in underwater:
        current = current + 1 if flag else 0
        duration = max(duration, current)
    trough = int(drawdown.to_numpy(dtype=float).argmin())
    recovery = 0
    peak_value = float(peak.iloc[trough])
    for value in equity.iloc[trough + 1 :]:
        recovery += 1
        if value >= peak_value:
            break
    return {
        "max_drawdown": float(-drawdown.min()),
        "rolling_drawdown_p95": float((-drawdown).quantile(0.95)),
        "max_drawdown_duration": duration,
        "peak_to_recovery_days": recovery,
    }


def performance_metrics(
    *,
    equity_rows: list[tuple[pd.Timestamp, float]],
    fills: list[Fill],
    orders: list[AccountOrder],
    initial_cash: float,
    risk_events: list[dict[str, Any]],
    benchmark_total_return: float,
) -> dict[str, Any]:
    """Calculate portfolio, drawdown, turnover, order, and attribution metrics."""
    broker_orders = [item for item in orders if item.filled_shares > 0]
    equity = pd.Series({date: value for date, value in equity_rows}, dtype=float).sort_index()
    returns = equity.pct_change(fill_method=None).dropna()
    years = max(len(equity) / 242.0, 1 / 242.0)
    total_return = float(equity.iloc[-1] / initial_cash - 1.0)
    cagr = float((equity.iloc[-1] / initial_cash) ** (1.0 / years) - 1.0)
    sharpe = (
        float(np.sqrt(242) * returns.mean() / returns.std(ddof=0)) if returns.std(ddof=0) > 1e-12 else 0.0
    )
    dd = _drawdown_stats(equity)
    max_dd = float(dd["max_drawdown"])
    gross_turnover = sum(item.gross_value for item in fills) / initial_cash
    fees = sum(item.commission + item.stamp_duty + item.transfer_fee for item in fills)
    holding_days: list[int] = []
    buy_lots: dict[str, list[list[Any]]] = {}
    inventory: dict[str, int] = {}
    round_trips = 0
    for fill in fills:
        if fill.side == "BUY":
            buy_lots.setdefault(fill.symbol, []).append([fill.shares, pd.Timestamp(fill.fill_date)])
            inventory[fill.symbol] = inventory.get(fill.symbol, 0) + fill.shares
            continue
        before = inventory.get(fill.symbol, 0)
        remaining = fill.shares
        if fill.sold_tranches:
            for allocation in fill.sold_tranches:
                entry_date = str(allocation.get("entry_date", ""))
                if entry_date:
                    holding_days.append((pd.Timestamp(fill.fill_date) - pd.Timestamp(entry_date)).days)
            # Execution supplied authoritative lot identity. The synthetic FIFO
            # queue is needed only when a fill lacks tranche attribution.
            remaining = 0
        else:
            for lot in buy_lots.get(fill.symbol, []):
                available = int(lot[0])
                if available <= 0 or remaining <= 0:
                    continue
                sold = min(available, remaining)
                holding_days.append((pd.Timestamp(fill.fill_date) - pd.Timestamp(lot[1])).days)
                lot[0] = available - sold
                remaining -= sold
        buy_lots[fill.symbol] = [lot for lot in buy_lots.get(fill.symbol, []) if int(lot[0]) > 0]
        inventory[fill.symbol] = max(0, before - fill.shares)
        if before > 0 and inventory[fill.symbol] == 0:
            round_trips += 1
    rolling20 = equity.pct_change(20, fill_method=None)
    rolling60 = equity.pct_change(60, fill_method=None)
    first_caution = next(
        (str(item.get("date")) for item in risk_events if item.get("to") == "CAUTION"),
        None,
    )
    first_risk_off = next(
        (str(item.get("date")) for item in risk_events if item.get("to") in {"RISK_OFF", "CRISIS"}),
        None,
    )
    risk_tokens = ("risk", "drawdown", "shock", "crisis", "capital protection")
    structured_risk_exits = {
        "risk",
        "portfolio_risk",
        "sector_guard",
        "risk_off",
        "crisis",
        "capital_budget",
    }
    first_reduce = next(
        (
            fill.fill_date
            for fill in fills
            if fill.side == "SELL"
            and (
                fill.exit_kind in structured_risk_exits
                or any(token in fill.reason.lower() for token in risk_tokens)
            )
        ),
        None,
    )
    first_action = min(
        (pd.Timestamp(value) for value in (first_caution, first_risk_off, first_reduce) if value),
        default=None,
    )
    drawdown = 1.0 - equity / equity.cummax()

    def lead_to_drawdown(threshold: float) -> int | None:
        """Count sessions from the first risk action to a drawdown crossing."""

        crossings = drawdown[drawdown >= threshold]
        if crossings.empty or first_action is None:
            return None
        target = crossings.index[0]
        target_location = equity.index.get_indexer(pd.Index([target]))[0]
        action_location = equity.index.get_indexer(
            pd.Index([first_action]),
            method="ffill",
        )[0]
        return int(target_location - action_location)

    return {
        "total_return": total_return,
        "cagr": cagr,
        "benchmark_total_return": benchmark_total_return,
        "excess_return": total_return - benchmark_total_return,
        "sharpe": sharpe,
        "calmar": cagr / max_dd if max_dd > 1e-12 else 0.0,
        **dd,
        "worst_20d": float(rolling20.min()) if rolling20.notna().any() else 0.0,
        "worst_60d": float(rolling60.min()) if rolling60.notna().any() else 0.0,
        "account_orders": len(broker_orders),
        "submitted_account_orders": len(orders),
        "unfilled_account_submissions": sum(item.filled_shares == 0 for item in orders),
        "round_trips": round_trips,
        "gross_turnover": gross_turnover,
        "annual_turnover": gross_turnover / years,
        "median_holding_days": float(median(holding_days)) if holding_days else 0.0,
        "fees": fees,
        "slippage_cost": sum(item.slippage_cost for item in fills),
        "first_caution": first_caution,
        "first_risk_off": first_risk_off,
        "first_reduce": first_reduce,
        "lead_to_10pct_dd": lead_to_drawdown(0.10),
        "lead_to_15pct_dd": lead_to_drawdown(0.15),
        "risk_events": risk_events,
        "order_ledger": [
            {
                "order_id": item.order_id,
                "signal_date": item.signal_date,
                "submitted_date": item.submitted_date,
                "symbol": item.symbol,
                "side": item.side,
                "target_weight": item.target_weight,
                "reason": item.reason,
                "lifecycle": item.lifecycle,
                "reduction_policy": item.reduction_policy,
                "reason_code": item.reason_code,
                "exit_kind": item.exit_kind,
                "status": item.status,
                "requested_shares": item.requested_shares,
                "filled_shares": item.filled_shares,
                "remaining_shares": item.remaining_shares,
                "attempts": item.attempts,
                "last_update_date": item.last_update_date,
                "last_event": item.last_event,
                "replaced_by": item.replaced_by,
                "cancel_reason": item.cancel_reason,
            }
            for item in broker_orders
        ],
        "submission_ledger": [
            {
                "order_id": item.order_id,
                "signal_date": item.signal_date,
                "submitted_date": item.submitted_date,
                "symbol": item.symbol,
                "side": item.side,
                "target_weight": item.target_weight,
                "reason": item.reason,
                "lifecycle": item.lifecycle,
                "reduction_policy": item.reduction_policy,
                "reason_code": item.reason_code,
                "exit_kind": item.exit_kind,
                "status": item.status,
                "requested_shares": item.requested_shares,
                "filled_shares": item.filled_shares,
                "remaining_shares": item.remaining_shares,
                "attempts": item.attempts,
                "last_update_date": item.last_update_date,
                "last_event": item.last_event,
                "replaced_by": item.replaced_by,
                "cancel_reason": item.cancel_reason,
            }
            for item in orders
        ],
        "equity_curve": [{"date": str(date)[:10], "equity": value} for date, value in equity.items()],
    }


def attribution(
    fills: list[Fill],
    *,
    panel: dict[str, pd.DataFrame] | None = None,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Attribute actual sold lots and calculate causal, offline exit outcomes."""
    lifecycle_names = ("core", "add1", "add2", "satellite", "recovery")
    empty_bucket = {
        "fills": 0,
        "gross_value": 0.0,
        "fees": 0.0,
        "realized_pnl": 0.0,
        "mfe": [],
        "mae": [],
    }
    lifecycle: dict[str, dict[str, Any]] = {name: copy.deepcopy(empty_bucket) for name in lifecycle_names}
    lots: dict[str, list[dict[str, Any]]] = {}
    reason_buckets: dict[str, dict[str, Any]] = {}
    post_exit: list[dict[str, Any]] = []

    def reason_family(fill: Fill) -> str:
        """Map one sell fill to a stable economic attribution family."""

        normalized = fill.reason.lower().replace("-", "_")
        code = fill.reason_code.lower()
        exit_kind = fill.exit_kind.lower()
        if exit_kind == "sector_guard" or "sector" in code or "sector" in normalized:
            return "sector_guard"
        # Structured execution intent has precedence over a strategy's
        # retained human-readable target reason.  A sparse portfolio-risk cut
        # may deliberately preserve that text for auditability, but it is
        # still economically a risk exit.
        if exit_kind in {"risk", "risk_off", "crisis", "capital_budget"}:
            return "risk_off"
        if "strategic" in code or "strategic" in normalized:
            return "strategic_tail"
        if "rotation" in code or "rotation" in normalized or "replacement" in normalized:
            return "rotation"
        if "satellite" in code or "satellite" in normalized or "scout" in normalized:
            return "satellite_expiry"
        if "recovery" in code or "recovery" in normalized:
            return "recovery_exit"
        if "lifecycle" in code or "lifecycle" in normalized:
            return "lifecycle_exit"
        if "risk" in normalized or "crisis" in normalized:
            return "risk_off"
        return "strategy"

    for fill in fills:
        fees = fill.commission + fill.stamp_duty + fill.transfer_fee
        if fill.side == "BUY":
            name = fill.lifecycle.lower()
            bucket = lifecycle.setdefault(name, copy.deepcopy(empty_bucket))
            bucket["fills"] += 1
            bucket["gross_value"] += fill.gross_value
            bucket["fees"] += fees
            active_lots = lots.setdefault(fill.symbol, [])
            tranche_id = (
                f"broker-fill:{fill.fill_id}"
                if fill.fill_id
                else f"{fill.fill_date}:{fill.symbol}:{len(active_lots) + 1}"
            )
            active_lots.append(
                {
                    "tranche_id": tranche_id,
                    "shares": fill.shares,
                    "unit_cost": (fill.gross_value + fees) / fill.shares,
                    "lifecycle": name,
                    "entry_date": fill.fill_date,
                }
            )
            continue

        family = reason_family(fill)
        reason_bucket = reason_buckets.setdefault(
            family,
            {"fills": 0, "gross_value": 0.0, "fees": 0.0, "realized_pnl": 0.0},
        )
        reason_bucket["fills"] += 1
        reason_bucket["gross_value"] += fill.gross_value
        reason_bucket["fees"] += fees

        allocations = list(fill.sold_tranches)
        if not allocations:
            remaining = fill.shares
            for lot in lots.get(fill.symbol, []):
                available = int(lot["shares"])
                if available <= 0 or remaining <= 0:
                    continue
                sold = min(available, remaining)
                allocations.append(
                    {
                        "shares": sold,
                        "unit_cost": float(lot["unit_cost"]),
                        "lifecycle": str(lot["lifecycle"]).upper(),
                        "entry_date": str(lot["entry_date"]),
                        "mfe": 0.0,
                        "mae": 0.0,
                    }
                )
                remaining -= sold
        unit_proceeds = (fill.gross_value - fees) / max(fill.shares, 1)
        fill_pnl = 0.0
        for allocation in allocations:
            sold = int(allocation.get("shares", 0))
            if sold <= 0:
                continue
            origin = str(allocation.get("lifecycle", fill.lifecycle)).lower()
            unit_cost = float(allocation.get("unit_cost", allocation.get("avg_cost", 0.0)))
            pnl = sold * (unit_proceeds - unit_cost)
            fill_pnl += pnl
            bucket = lifecycle.setdefault(origin, copy.deepcopy(empty_bucket))
            bucket["fills"] += 1
            bucket["gross_value"] += sold * fill.price
            bucket["fees"] += fees * sold / max(fill.shares, 1)
            bucket["realized_pnl"] += pnl
            bucket["mfe"].append(float(allocation.get("mfe", 0.0)))
            bucket["mae"].append(float(allocation.get("mae", 0.0)))
            remaining = sold
            symbol_lots = lots.get(fill.symbol, [])
            tranche_id = str(allocation.get("tranche_id", ""))
            exact = [lot for lot in symbol_lots if tranche_id and lot.get("tranche_id") == tranche_id]
            candidates = exact or symbol_lots
            for lot in candidates:
                if remaining <= 0:
                    break
                if not exact and (
                    str(lot["lifecycle"]) != origin
                    or str(lot["entry_date"]) != str(allocation.get("entry_date", lot["entry_date"]))
                ):
                    continue
                consumed = min(int(lot["shares"]), remaining)
                lot["shares"] -= consumed
                remaining -= consumed
                if exact and int(lot["shares"]) > 0:
                    # Lifecycle promotion changes the economic lot in place;
                    # retain that identity for the unsold remainder.
                    lot["lifecycle"] = origin
        reason_bucket["realized_pnl"] += fill_pnl
        lots[fill.symbol] = [lot for lot in lots.get(fill.symbol, []) if int(lot["shares"]) > 0]

        frame = panel.get(fill.symbol) if panel is not None else None
        fill_date = pd.Timestamp(fill.fill_date)
        if frame is None or fill_date not in frame.index:
            continue
        location = int(frame.index.get_indexer(pd.DatetimeIndex([fill_date]))[0])
        benchmark_location = (
            int(benchmark.index.get_indexer(pd.DatetimeIndex([fill_date]))[0])
            if benchmark is not None and fill_date in benchmark.index
            else -1
        )
        horizons: dict[str, Any] = {}
        for horizon in (5, 10, 20, 40):
            future_location = location + horizon
            if future_location >= len(frame):
                horizons[str(horizon)] = None
                continue
            absolute = float(frame.iloc[future_location]["close"] / fill.price - 1.0)
            benchmark_return = 0.0
            if (
                benchmark is not None
                and benchmark_location >= 0
                and benchmark_location + horizon < len(benchmark)
            ):
                benchmark_return = float(
                    benchmark.iloc[benchmark_location + horizon]["close"]
                    / benchmark.iloc[benchmark_location]["close"]
                    - 1.0
                )
            relative = absolute - benchmark_return
            horizons[str(horizon)] = {
                "absolute_return": absolute,
                "relative_return": relative,
                "avoided_loss": max(0.0, -absolute),
                "false_exit_regret": max(0.0, relative),
            }
        post_exit.append(
            {
                "symbol": fill.symbol,
                "exit_date": fill.fill_date,
                "reason_code": fill.reason_code,
                "reason_family": family,
                "exit_kind": fill.exit_kind,
                "horizons": horizons,
            }
        )

    open_lots = {
        name: int(
            sum(
                int(lot["shares"])
                for symbol_lots in lots.values()
                for lot in symbol_lots
                if str(lot["lifecycle"]) == name
            )
        )
        for name in lifecycle
    }
    for bucket in lifecycle.values():
        for key in ("mfe", "mae"):
            values = list(bucket[key])
            bucket[f"median_{key}"] = float(np.median(values)) if values else 0.0
            del bucket[key]

    replacement_spread: dict[str, list[dict[str, Any]]] = {"20": [], "40": []}
    replacement_exits = [
        fill for fill in fills if fill.side == "SELL" and reason_family(fill) in {"rotation", "recovery_exit"}
    ]
    for entry in fills:
        marker = "replaces "
        normalized_reason = entry.reason.lower().replace("-", "_")
        if entry.side != "BUY" or marker not in normalized_reason:
            continue
        old_symbol = normalized_reason.split(marker, 1)[1].split(maxsplit=1)[0].strip(" ,.:;()[]")
        linked_exits = [
            fill
            for fill in replacement_exits
            if fill.symbol.lower() == old_symbol
            and pd.Timestamp(fill.fill_date) <= pd.Timestamp(entry.fill_date)
            and entry.symbol.lower() in fill.reason.lower()
        ]
        if not linked_exits or panel is None:
            continue
        linked_exit = max(
            linked_exits,
            key=lambda fill: (pd.Timestamp(fill.fill_date), fill.fill_id, fill.order_id),
        )
        old_frame = panel.get(linked_exit.symbol)
        new_frame = panel.get(entry.symbol)
        entry_date = pd.Timestamp(entry.fill_date)
        if (
            old_frame is None
            or new_frame is None
            or entry_date not in old_frame.index
            or entry_date not in new_frame.index
        ):
            continue
        old_location = int(old_frame.index.get_indexer(pd.DatetimeIndex([entry_date]))[0])
        new_location = int(new_frame.index.get_indexer(pd.DatetimeIndex([entry_date]))[0])
        old_start = float(old_frame.iloc[old_location]["close"])
        if old_start <= 0 or entry.price <= 0:
            continue
        for horizon in (20, 40):
            if old_location + horizon >= len(old_frame) or new_location + horizon >= len(new_frame):
                continue
            old_return = float(old_frame.iloc[old_location + horizon]["close"] / old_start - 1.0)
            new_return = float(new_frame.iloc[new_location + horizon]["close"] / entry.price - 1.0)
            replacement_spread[str(horizon)].append(
                {
                    "old_symbol": linked_exit.symbol,
                    "new_symbol": entry.symbol,
                    "entry_date": entry.fill_date,
                    "gross_value": entry.gross_value,
                    "old_return": old_return,
                    "new_return": new_return,
                    "spread": new_return - old_return,
                }
            )
    return {
        "by_lifecycle": lifecycle,
        "by_reason": reason_buckets,
        "open_shares_by_lifecycle": open_lots,
        "post_exit": post_exit,
        "replacement_spread": replacement_spread,
    }
