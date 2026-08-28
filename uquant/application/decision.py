"""Production daily-decision orchestration."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Protocol

import pandas as pd

from ..config import (
    DEFAULT_CONFIG,
    SystemConfig,
    canonical_control_float,
    config_fingerprint,
)
from ..contracts.universe import AIUniverse, default_ai_universe
from ..data import DataManifest, DataStore, normalize_symbol
from ..models.strategic_universe import build_strategic_universe_roles
from ..execution import merge_pending_orders, plan_orders
from ..leader import (
    INDUSTRY,
    apply_leader_tenure,
    apply_opportunity_alpha,
    compute_leaders,
    compute_structural_leaders,
)
from ..opportunity import classify_opportunity
from ..portfolio import PortfolioAllocator, current_weights
from ..reference import ReferenceContext, build_reference_context
from ..reference_registry import resolve_reference_symbols
from ..risk_sentinel.integration import sentinel_freeze_authorized
from ..risk_sentinel.models import RiskEvidenceTimeline, SentinelAssessment
from ..types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountState,
    Decision,
    LeaderScore,
    Opportunity,
    PendingOrder,
    RiskAssessment,
    Target,
)
from .target_attribution import attach_target_attribution


class _ReplayUniverseRuntime(Protocol):
    @property
    def all_symbols(self) -> tuple[str, ...]: ...


class _DecisionWorkspaceRuntime(Protocol):
    def bind_tradable(self, symbols: Iterable[str]) -> _ReplayUniverseRuntime: ...

    def filter_reference_symbols(self, symbols: Iterable[str]) -> tuple[str, ...]: ...

    def manifest(
        self,
        symbols: Iterable[str],
        *,
        as_of: str | pd.Timestamp,
    ) -> DataManifest: ...


class DecisionEngineRuntime(Protocol):
    """Static structural view of the exact state consumed by decision orchestration."""

    @property
    def cfg(self) -> SystemConfig: ...

    @property
    def workspace(self) -> _DecisionWorkspaceRuntime: ...

    @property
    def data(self) -> DataStore: ...

    @property
    def allocator(self) -> PortfolioAllocator: ...

    _raw: dict[str, pd.DataFrame]
    _features: dict[str, pd.DataFrame]
    _code_hash: str | None
    _leader_score_cache: dict[tuple[object, ...], dict[str, LeaderScore]]

    def _load(self, symbols: Iterable[str]) -> None: ...

    def _price(self, symbol: str, date: pd.Timestamp, field: str = "close") -> float: ...

    @property
    def _reference_returns(self) -> pd.DataFrame | None: ...

    def _causal_risk_timeline(
        self,
        *,
        as_of: str,
        cfg: SystemConfig,
        universe: AIUniverse,
    ) -> RiskEvidenceTimeline: ...

    def _mark_account_positions(self, account: AccountState, date: pd.Timestamp) -> None: ...

    def decide(self, *, symbols: Iterable[str], as_of: str, account: AccountState) -> Decision: ...

    def equity(
        self,
        account: AccountState,
        date: pd.Timestamp,
        field: str = "close",
    ) -> float: ...


@dataclass(frozen=True, slots=True)
class _DecisionInputs:
    date: pd.Timestamp
    user_symbols: tuple[str, ...]
    durable_symbols: set[str]
    current_symbols: tuple[str, ...]
    data_digest: str
    current_code_hash: str


@dataclass(frozen=True, slots=True)
class _DecisionMarket:
    reference_panel: dict[str, pd.DataFrame]
    user_panel: dict[str, pd.DataFrame]
    broad: pd.DataFrame
    tech: pd.DataFrame
    cfg: SystemConfig
    reference_context: ReferenceContext
    reference_returns: pd.DataFrame | None
    structural_leaders: dict[str, LeaderScore]
    prices: dict[str, float]
    equity: float
    universe: AIUniverse
    canonical_symbols: tuple[str, ...]
    causal_timeline: RiskEvidenceTimeline


@dataclass(frozen=True, slots=True)
class _DecisionAllocation:
    opportunity: Opportunity
    risk: RiskAssessment
    leader_factor_profile: str
    targets: tuple[Target, ...]
    orders: tuple[PendingOrder, ...]
    user_leaders: dict[str, LeaderScore]


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


_attach_target_attribution = attach_target_attribution


def _mark_account_positions(
    self: DecisionEngineRuntime,
    account: AccountState,
    date: pd.Timestamp,
) -> None:
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
            tranche.lowest_close = close if tranche.lowest_close <= 0 else min(tranche.lowest_close, close)
            excursion = close / max(tranche.avg_cost, 1e-12) - 1.0
            tranche.mfe = max(tranche.mfe, excursion)
            tranche.mae = min(tranche.mae, excursion)


def _validated_decision_symbols(
    *,
    symbols: Iterable[str],
    as_of: str,
    account: AccountState,
) -> tuple[pd.Timestamp, tuple[str, ...], set[str]]:
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
    if account.strategic_grant is not None and not account.strategic_grant.terminal:
        durable_symbols.add(account.strategic_grant.candidate_symbol)
    if account.tactical_anchor_symbol:
        durable_symbols.add(account.tactical_anchor_symbol)
    return date, user_symbols, durable_symbols


def _verify_decision_provenance(
    self: DecisionEngineRuntime,
    *,
    date: pd.Timestamp,
    user_symbols: tuple[str, ...],
    durable_symbols: set[str],
    account: AccountState,
    code_fingerprint_fn: Callable[[], str],
) -> _DecisionInputs:
    replay_universe = self.workspace.bind_tradable(set(user_symbols) | durable_symbols)
    all_symbols = set(replay_universe.all_symbols)
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
            verified_digest = self.workspace.manifest(verification_symbols, as_of=verification_date).digest
        else:
            verified_digest = self.data.manifest(verification_symbols).digest
        if account.data_hash != verified_digest and self.cfg.fail_closed:
            raise RuntimeError("historical data prefix differs from account state")
    data_digest = self.workspace.manifest(current_symbols, as_of=date).digest
    if self._code_hash is None:
        self._code_hash = code_fingerprint_fn()
    current_code_hash = self._code_hash
    if account.code_hash and account.code_hash != current_code_hash and self.cfg.fail_closed:
        raise RuntimeError("production code hash differs from account state")
    self._mark_account_positions(account, date)
    return _DecisionInputs(
        date=date,
        user_symbols=user_symbols,
        durable_symbols=durable_symbols,
        current_symbols=current_symbols,
        data_digest=data_digest,
        current_code_hash=current_code_hash,
    )


def _decision_market_context(
    self: DecisionEngineRuntime,
    *,
    inputs: _DecisionInputs,
    account: AccountState,
) -> _DecisionMarket:
    date = inputs.date
    active_reference_symbols = self.workspace.filter_reference_symbols(resolve_reference_symbols(date))
    reference_panel = {symbol: self._features[symbol] for symbol in active_reference_symbols}
    strategy_symbols = tuple(sorted(set(inputs.user_symbols) | inputs.durable_symbols))
    user_panel = {
        symbol: self._features[symbol]
        for symbol in strategy_symbols
        if not self._raw[symbol].loc[:date].empty
    }
    combined = dict(reference_panel)
    combined.update(user_panel)
    broad = self._features["sh000300"]
    tech = self._features["sh000682"]
    decision_cfg = _decision_config_for_universe(len(inputs.user_symbols), self.cfg)
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
    visible_users = set(user_panel)
    prices = {symbol: self._price(symbol, date) for symbol in visible_users | set(account.positions)}
    _, equity = current_weights(account, prices)
    universe = default_ai_universe()
    canonical_symbols = universe.symbols_as_of(str(date.date()))
    expected_reference_symbols = self.workspace.filter_reference_symbols(canonical_symbols)
    if active_reference_symbols != expected_reference_symbols:
        raise RuntimeError("point-in-time reference registry differs from canonical universe")
    causal_timeline = self._causal_risk_timeline(
        as_of=str(date.date()),
        cfg=decision_cfg,
        universe=universe,
    )
    return _DecisionMarket(
        reference_panel=reference_panel,
        user_panel=user_panel,
        broad=broad,
        tech=tech,
        cfg=decision_cfg,
        reference_context=reference_context,
        reference_returns=self._reference_returns,
        structural_leaders=structural_leaders,
        prices=prices,
        equity=equity,
        universe=universe,
        canonical_symbols=canonical_symbols,
        causal_timeline=causal_timeline,
    )


def _assess_decision_risk(
    *,
    inputs: _DecisionInputs,
    market: _DecisionMarket,
    account: AccountState,
    assess_risk_fn: Callable[..., RiskAssessment],
    evaluate_sentinel_fn: Callable[..., SentinelAssessment],
) -> RiskAssessment:
    sentinel = None
    if market.cfg.risk_sentinel_mode != "SHADOW":
        sentinel = evaluate_sentinel_fn(
            as_of=str(inputs.date.date()),
            broad_frame=market.broad,
            tech_frame=market.tech,
            reference_panel=market.reference_panel,
            point_in_time_industries={
                symbol: market.universe.industry_of(symbol, str(inputs.date.date()))
                for symbol in market.canonical_symbols
            },
            held_symbols=tuple(
                sorted(symbol for symbol, position in account.positions.items() if position.shares > 0)
            ),
            leader_symbols=tuple(sorted(account.active_leaders)),
            capital_drawdown=max(0.0, 1.0 - market.equity / max(account.capital_peak, 1e-12)),
        )
    risk = assess_risk_fn(
        date=inputs.date,
        broad=market.broad,
        tech=market.tech,
        reference_panel=market.reference_panel,
        reference_returns=market.reference_returns,
        user_panel=market.user_panel,
        leaders=market.structural_leaders,
        account=account,
        equity=market.equity,
        cfg=market.cfg,
        reference_context=(market.reference_context if market.cfg.group_balanced_reference_enabled else None),
        configured_universe_size=len(inputs.user_symbols),
        sentinel_assessment=sentinel,
        sentinel_opportunity=account.opportunity,
    )
    risk.evidence["configured_user_universe_size"] = len(inputs.user_symbols)
    risk.evidence["universe_size_is_diagnostic_only"] = True
    latest_causal = market.causal_timeline.sentinel_rows[-1] if market.causal_timeline.sentinel_rows else None
    risk.evidence.update(
        {
            "sentinel_mode": market.cfg.risk_sentinel_mode,
            "sentinel_causal_confirmation_authority_enabled": (
                market.cfg.risk_sentinel_causal_confirmation_enabled
            ),
            "sentinel_causal_confirmation_history_trusted": (
                market.causal_timeline.confirmation_history_trusted
            ),
            "sentinel_causal_confirmation_days": market.causal_timeline.confirmation_days,
            "sentinel_causal_repair_days": market.causal_timeline.repair_days,
            "sentinel_causal_effective_level": market.causal_timeline.effective_level.value,
            "sentinel_causal_confirmed_since": market.causal_timeline.confirmed_since,
            "sentinel_causal_trust_reasons": list(market.causal_timeline.trust_reasons),
            "sentinel_causal_incremental_families": list(market.causal_timeline.incremental_families),
            "sentinel_causal_earlier_families": list(market.causal_timeline.earlier_families),
            "sentinel_first_family_dates": dict(market.causal_timeline.sentinel_first_family_dates),
            "base_first_family_dates": dict(market.causal_timeline.base_first_family_dates),
            "sentinel_causal_coverage_status": (
                latest_causal.coverage_status.value if latest_causal is not None else "NOT_READY"
            ),
            "sentinel_causal_confidence": (latest_causal.confidence if latest_causal is not None else 0.0),
            "sentinel_causal_observed_level": (
                latest_causal.level.value if latest_causal is not None else "NOT_READY"
            ),
            "sentinel_causal_active_families": (
                list(latest_causal.active_families) if latest_causal is not None else []
            ),
            "sentinel_causal_reasons": (
                list(latest_causal.reasons)
                if latest_causal is not None
                else ["causal market history is not ready"]
            ),
            "sentinel_causal_weakest_subindustries": (
                list(latest_causal.weakest_subindustries) if latest_causal is not None else []
            ),
        }
    )
    return risk


def _allocate_decision_orders(
    self: DecisionEngineRuntime,
    *,
    inputs: _DecisionInputs,
    market: _DecisionMarket,
    account: AccountState,
    risk: RiskAssessment,
    reconcile_account_orders_fn: Callable[..., tuple[PendingOrder, ...]],
    attach_target_attribution_fn: Callable[..., tuple[Target, ...]],
) -> _DecisionAllocation:
    # Provenance was already fail-closed above. Publishing it before allocation
    # lets any newly created grant bind the exact production source identity.
    account.code_hash = inputs.current_code_hash
    if not account.account_identity:
        account_identity_payload = "|".join(
            (
                float(account.initial_cash).hex(),
                inputs.current_code_hash,
                str(inputs.date.date()),
                ",".join(inputs.current_symbols),
            )
        )
        account.account_identity = "account_" + hashlib.sha256(
            account_identity_payload.encode("utf-8")
        ).hexdigest()
    structural_users = {
        symbol: market.structural_leaders[symbol]
        for symbol in inputs.user_symbols
        if symbol in market.structural_leaders
    }
    opportunity = classify_opportunity(
        date=inputs.date,
        broad=market.broad,
        tech=market.tech,
        reference_panel=market.reference_panel,
        leaders=structural_users,
        risk=risk.state,
        account=account,
        cfg=market.cfg,
        reference_context=(market.reference_context if market.cfg.group_balanced_reference_enabled else None),
    )
    if market.cfg.same_day_leader_pipeline_enabled:
        alpha_leaders = apply_opportunity_alpha(
            market.structural_leaders,
            opportunity=opportunity,
            cfg=market.cfg,
        )
        all_leaders = apply_leader_tenure(alpha_leaders, account=account, cfg=market.cfg)
    else:
        all_leaders = market.structural_leaders
    user_leaders = {symbol: all_leaders[symbol] for symbol in inputs.user_symbols if symbol in all_leaders}
    leader_factor_profile = (
        "TREND"
        if opportunity in {Opportunity.STRONG_TREND, Opportunity.TREND}
        else "RECOVERY"
        if opportunity is Opportunity.RECOVERY
        else "CHOPPY"
    )
    previous_orders = list(account.pending_orders)
    targets = self.allocator.allocate(
        date=inputs.date,
        opportunity=opportunity,
        risk=risk,
        user_panel=market.user_panel,
        leaders=user_leaders,
        account=account,
        prices=market.prices,
        qualification_panel={**market.reference_panel, **market.user_panel},
        qualification_leaders=all_leaders,
        strategic_universe=build_strategic_universe_roles(
            as_of=str(inputs.date.date()),
            tradable_symbols=inputs.user_symbols,
            qualification_reference_symbols=market.canonical_symbols,
            risk_reference_symbols=(
                *tuple(sorted(market.reference_panel)),
                "sh000300",
                "sh000682",
            ),
            industries={
                symbol: market.universe.industry_of(symbol, str(inputs.date.date()))
                for symbol in market.canonical_symbols
            },
            available_symbols=(
                *tuple(sorted({*market.reference_panel, *market.user_panel})),
                "sh000300",
                "sh000682",
            ),
        ),
    )
    targets = attach_target_attribution_fn(
        signal_date=str(inputs.date.date()),
        targets=targets,
        retained_orders=previous_orders,
        cfg=self.cfg,
    )
    if not market.cfg.group_balanced_reference_enabled:
        risk.evidence.update(market.reference_context.evidence())
    planned_orders = plan_orders(
        signal_date=str(inputs.date.date()),
        targets=targets,
        account=account,
        prices=market.prices,
        cfg=self.cfg,
    )
    orders = merge_pending_orders(
        retained=previous_orders,
        planned=planned_orders,
        targets=targets,
        cfg=self.cfg,
    )
    orders = reconcile_account_orders_fn(
        account=account,
        previous=previous_orders,
        current=orders,
        submitted_date=str(inputs.date.date()),
        removed_buy_reason="sentinel_freeze_new_risk" if sentinel_freeze_authorized(risk) else None,
    )
    account.last_successful_run = str(inputs.date.date())
    account.data_hash = inputs.data_digest
    account.data_hash_as_of = str(inputs.date.date())
    account.data_hash_symbols = list(inputs.current_symbols)
    account.code_hash = inputs.current_code_hash
    return _DecisionAllocation(
        opportunity=opportunity,
        risk=risk,
        leader_factor_profile=leader_factor_profile,
        targets=targets,
        orders=orders,
        user_leaders=user_leaders,
    )


def _finalize_decision(
    *,
    inputs: _DecisionInputs,
    market: _DecisionMarket,
    allocation: _DecisionAllocation,
    account: AccountState,
) -> Decision:
    decision = Decision(
        date=str(inputs.date.date()),
        opportunity=allocation.opportunity,
        risk=allocation.risk.state,
        target_gross=sum(item.weight for item in allocation.targets),
        target_k=sum(item.weight > 0 for item in allocation.targets),
        targets=allocation.targets,
        pending_orders=allocation.orders,
        risk_summary={
            **allocation.risk.evidence,
            "votes": allocation.risk.votes,
            "reasons": list(allocation.risk.reasons),
            "shock_state": allocation.risk.shock_state,
            "reduction_level": allocation.risk.reduction_level,
            "severity": allocation.risk.severity,
            "target_gross_cap": canonical_control_float(allocation.risk.target_gross_cap),
            "system_gross_cap": canonical_control_float(market.cfg.max_gross),
            "freeze_new_risk": allocation.risk.freeze_new_risk,
            "strategic_epoch": account.strategic_epoch,
            "strategic_candidate_signature": account.strategic_candidate_signature,
            "factor_profile": allocation.leader_factor_profile,
            "effective_config_sha256": config_fingerprint(market.cfg),
            "leader_ranking": [
                {
                    "symbol": item.symbol,
                    "score": item.score,
                    "industry": item.industry,
                    "mature": item.mature,
                    "emerging": item.emerging,
                }
                for item in sorted(
                    allocation.user_leaders.values(),
                    key=lambda candidate: (-candidate.score, candidate.symbol),
                )
            ],
        },
        decision_digest="",
    )
    canonical = decision.canonical_payload(effective_config_sha256=config_fingerprint(market.cfg))
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return replace(decision, decision_digest=digest)


def decide(
    self: DecisionEngineRuntime,
    assess_risk_fn: Callable[..., RiskAssessment],
    evaluate_sentinel_fn: Callable[..., SentinelAssessment],
    reconcile_account_orders_fn: Callable[..., tuple[PendingOrder, ...]],
    code_fingerprint_fn: Callable[[], str],
    attach_target_attribution_fn: Callable[..., tuple[Target, ...]],
    *,
    symbols: Iterable[str],
    as_of: str,
    account: AccountState,
) -> Decision:
    """Produce and persist one causal close-date portfolio decision.

    The account is advanced in place after all data, code, state, and
    chronology checks succeed. Returned orders are next-open intentions;
    this method never fills them on the signal date.
    """
    date, user_symbols, durable_symbols = _validated_decision_symbols(
        symbols=symbols,
        as_of=as_of,
        account=account,
    )
    inputs = _verify_decision_provenance(
        self,
        date=date,
        user_symbols=user_symbols,
        durable_symbols=durable_symbols,
        account=account,
        code_fingerprint_fn=code_fingerprint_fn,
    )
    market = _decision_market_context(self, inputs=inputs, account=account)
    risk = _assess_decision_risk(
        inputs=inputs,
        market=market,
        account=account,
        assess_risk_fn=assess_risk_fn,
        evaluate_sentinel_fn=evaluate_sentinel_fn,
    )
    allocation = _allocate_decision_orders(
        self,
        inputs=inputs,
        market=market,
        account=account,
        risk=risk,
        reconcile_account_orders_fn=reconcile_account_orders_fn,
        attach_target_attribution_fn=attach_target_attribution_fn,
    )
    return _finalize_decision(
        inputs=inputs,
        market=market,
        allocation=allocation,
        account=account,
    )


def deterministic_decision(
    self: DecisionEngineRuntime,
    *,
    symbols: Iterable[str],
    as_of: str,
    account: AccountState,
) -> tuple[Decision, AccountState]:
    """Evaluate a decision on a deep copy and return both result and copy."""
    cloned = copy.deepcopy(account)
    return (self.decide(symbols=symbols, as_of=as_of, account=cloned), cloned)


decision_config_for_universe = _decision_config_for_universe
mark_account_positions = _mark_account_positions
