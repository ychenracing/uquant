"""Production daily-decision orchestration."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Protocol, cast

import pandas as pd

from ..account.codec import UnsupportedAccountSchemaError
from ..config import (
    DEFAULT_CONFIG,
    SystemConfig,
    canonical_control_float,
    config_fingerprint,
)
from ..contracts.universe import AIUniverse, default_ai_universe
from ..data import DataManifest, DataStore, normalize_symbol
from ..execution import merge_pending_orders, plan_orders
from ..leader import (
    INDUSTRY,
    compute_leaders,
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
    StrategicUniverseDeclaration,
    StrategicUniverseRoles,
    Target,
    bind_account_strategic_ownership,
    build_strategic_universe_roles,
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
        role_absent_symbols: tuple[str, ...] = (),
    ) -> RiskEvidenceTimeline: ...

    def _mark_account_positions(self, account: AccountState, date: pd.Timestamp) -> None: ...

    def decide(
        self,
        *,
        symbols: Iterable[str],
        as_of: str,
        account: AccountState,
        strategic_universe_declaration: StrategicUniverseDeclaration | None = None,
    ) -> Decision: ...

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
    qualification_reference_panel: dict[str, pd.DataFrame]
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
    qualification_reference_symbols: tuple[str, ...]
    risk_reference_symbols: tuple[str, ...]
    causal_timeline: RiskEvidenceTimeline


@dataclass(frozen=True, slots=True)
class _DecisionAllocation:
    opportunity: Opportunity
    risk: RiskAssessment
    leader_factor_profile: str
    targets: tuple[Target, ...]
    orders: tuple[PendingOrder, ...]
    user_leaders: dict[str, LeaderScore]
    all_leaders: dict[str, LeaderScore]
    strategic_universe: StrategicUniverseRoles
    qualification_snapshots: dict[str, dict[str, float]]


@dataclass(frozen=True, slots=True)
class _ObservedDecisionFacts:
    effective_config_sha256: str
    risk_assessment: Mapping[str, object]
    strategic_universe_roles: StrategicUniverseRoles
    strategic_qualification: Mapping[str, object]
    strategic_successor_qualification: Mapping[str, object]
    leader_scores: tuple[Mapping[str, object], ...]
    qualification_snapshots: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _DecisionResult:
    decision: Decision
    observation: _ObservedDecisionFacts


def _freeze_observed_fact(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_observed_fact(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_observed_fact(item) for item in value)
    return value


def _decision_config_for_universe(
    configured_universe_size: int,
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> SystemConfig:
    """Return one production policy regardless of unrelated universe members.

    Universe size is retained only as diagnostic provenance. It must never select
    a different strategy configuration: an otherwise irrelevant symbol cannot
    change the decision path merely by crossing a pool-size threshold.
    """
    del configured_universe_size
    return cfg


def _declared_reference_roles(
    *,
    workspace: _DecisionWorkspaceRuntime,
    active_reference_symbols: tuple[str, ...],
    declaration: StrategicUniverseDeclaration | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve current role membership while keeping deliberate absence explicit."""

    if declaration is None:
        return active_reference_symbols, active_reference_symbols
    declared = set(declaration.qualification_reference_symbols) | set(
        declaration.risk_reference_symbols
    )
    allowed = set(workspace.filter_reference_symbols(declared))
    unknown = tuple(sorted(declared - allowed))
    if unknown:
        raise ValueError(f"strategic reference declaration is outside the production registry: {unknown}")
    active = set(active_reference_symbols)
    return (
        tuple(
            symbol
            for symbol in declaration.qualification_reference_symbols
            if symbol in active
        ),
        tuple(symbol for symbol in declaration.risk_reference_symbols if symbol in active),
    )


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


def validated_decision_symbols(
    *,
    symbols: Iterable[str],
    as_of: str,
    account: AccountState,
) -> tuple[pd.Timestamp, tuple[str, ...], set[str]]:
    if account.schema_version != ACCOUNT_SCHEMA_VERSION:
        raise UnsupportedAccountSchemaError(
            f"unsupported account schema {account.schema_version}; expected {ACCOUNT_SCHEMA_VERSION}"
        )
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


def verify_decision_provenance(
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


def decision_market_context(
    self: DecisionEngineRuntime,
    *,
    inputs: _DecisionInputs,
    account: AccountState,
    strategic_universe_declaration: StrategicUniverseDeclaration | None,
) -> _DecisionMarket:
    date = inputs.date
    universe = default_ai_universe()
    canonical_symbols = universe.symbols_as_of(str(date.date()))
    registry_symbols = resolve_reference_symbols(date)
    if registry_symbols != canonical_symbols:
        raise RuntimeError("point-in-time reference registry differs from canonical universe")
    active_reference_symbols = self.workspace.filter_reference_symbols(registry_symbols)
    qualification_reference_symbols, risk_reference_symbols = _declared_reference_roles(
        workspace=self.workspace,
        active_reference_symbols=active_reference_symbols,
        declaration=strategic_universe_declaration,
    )
    qualification_reference_panel = {
        symbol: self._features[symbol] for symbol in qualification_reference_symbols
    }
    reference_panel = {symbol: self._features[symbol] for symbol in risk_reference_symbols}
    strategy_symbols = tuple(sorted(set(inputs.user_symbols) | inputs.durable_symbols))
    user_panel = {
        symbol: self._features[symbol]
        for symbol in strategy_symbols
        if not self._raw[symbol].loc[:date].empty
    }
    combined = dict(reference_panel)
    combined.update(qualification_reference_panel)
    combined.update(user_panel)
    broad = self._features["sh000300"]
    tech = self._features["sh000682"]
    decision_cfg = _decision_config_for_universe(len(inputs.user_symbols), self.cfg)
    reference_returns = self._reference_returns
    if reference_returns is not None:
        reference_returns = reference_returns.loc[
            :,
            [symbol for symbol in risk_reference_symbols if symbol in reference_returns],
        ]
    reference_context = build_reference_context(
        date=date,
        panel=reference_panel,
        industries=INDUSTRY,
        cfg=decision_cfg,
        reference_returns=reference_returns,
    )
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
    expected_reference_symbols = self.workspace.filter_reference_symbols(canonical_symbols)
    if active_reference_symbols != expected_reference_symbols:
        raise RuntimeError("point-in-time reference registry differs from canonical universe")
    causal_timeline = self._causal_risk_timeline(
        as_of=str(date.date()),
        cfg=decision_cfg,
        universe=universe,
        role_absent_symbols=tuple(
            symbol
            for symbol in canonical_symbols
            if symbol not in risk_reference_symbols
        ),
    )
    return _DecisionMarket(
        reference_panel=reference_panel,
        qualification_reference_panel=qualification_reference_panel,
        user_panel=user_panel,
        broad=broad,
        tech=tech,
        cfg=decision_cfg,
        reference_context=reference_context,
        reference_returns=reference_returns,
        structural_leaders=structural_leaders,
        prices=prices,
        equity=equity,
        universe=universe,
        canonical_symbols=canonical_symbols,
        qualification_reference_symbols=qualification_reference_symbols,
        risk_reference_symbols=risk_reference_symbols,
        causal_timeline=causal_timeline,
    )


def assess_decision_risk(
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
                for symbol in market.risk_reference_symbols
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
        reference_context=None,
        configured_universe_size=len(inputs.user_symbols),
        sentinel_assessment=sentinel,
        sentinel_opportunity=account.opportunity,
    )
    # Bounded rearm needs causal coverage before allocation, while the
    # established qualification routes must keep reading the frozen risk
    # evidence surface.  Publishing every reference diagnostic here would
    # silently turn group-balanced diagnostics into new economic inputs.
    risk.evidence["reference_coverage"] = market.reference_context.coverage
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
    bind_account_strategic_ownership(account)
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
    bind_decision_account_identity(inputs=inputs, account=account)
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
        reference_context=None,
    )
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
    strategic_universe = _decision_strategic_universe(inputs=inputs, market=market)
    qualification_panel = {
        **market.qualification_reference_panel,
        **market.user_panel,
    }
    qualification_snapshots = self.allocator._strategic_qualification_snapshots(
        date=inputs.date,
        user_panel=qualification_panel,
        leaders=all_leaders,
    )
    targets = self.allocator.allocate(
        date=inputs.date,
        opportunity=opportunity,
        risk=risk,
        user_panel=market.user_panel,
        leaders=user_leaders,
        account=account,
        prices=market.prices,
        qualification_panel=qualification_panel,
        qualification_leaders=all_leaders,
        strategic_universe=strategic_universe,
    )
    risk.evidence.update(market.reference_context.evidence())
    bind_account_strategic_ownership(account)
    targets = attach_target_attribution_fn(
        signal_date=str(inputs.date.date()),
        targets=targets,
        retained_orders=previous_orders,
        cfg=self.cfg,
    )
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
        all_leaders=all_leaders,
        strategic_universe=strategic_universe,
        qualification_snapshots=qualification_snapshots,
    )


def bind_decision_account_identity(
    *,
    inputs: _DecisionInputs,
    account: AccountState,
) -> None:
    account.code_hash = inputs.current_code_hash
    if account.account_identity:
        return
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


def _decision_strategic_universe(
    *,
    inputs: _DecisionInputs,
    market: _DecisionMarket,
) -> StrategicUniverseRoles:
    panels = {
        **market.reference_panel,
        **market.qualification_reference_panel,
        **market.user_panel,
    }
    available = tuple(
        sorted(
            symbol
            for symbol, frame in panels.items()
            if inputs.date in frame.index
        )
    )
    return build_strategic_universe_roles(
        as_of=str(inputs.date.date()),
        tradable_symbols=inputs.user_symbols,
        qualification_reference_symbols=market.qualification_reference_symbols,
        risk_reference_symbols=(
            *market.risk_reference_symbols,
            "sh000300",
            "sh000682",
        ),
        industries={
            symbol: market.universe.industry_of(symbol, str(inputs.date.date()))
            for symbol in market.qualification_reference_symbols
        },
        available_symbols=(
            *available,
            *(("sh000300",) if inputs.date in market.broad.index else ()),
            *(("sh000682",) if inputs.date in market.tech.index else ()),
        ),
    )


def _finalize_decision_result(
    *,
    inputs: _DecisionInputs,
    market: _DecisionMarket,
    allocation: _DecisionAllocation,
    account: AccountState,
) -> _DecisionResult:
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
            "strategic_qualification": asdict(account.strategic_qualification),
            "strategic_successor_qualification": asdict(
                account.strategic_successor_qualification
            ),
            "strategic_grant": (
                None
                if account.strategic_grant is None
                else asdict(account.strategic_grant)
            ),
            "strategic_epochs": [asdict(item) for item in account.strategic_epochs],
            "active_strategic_epoch_id": account.active_strategic_epoch_id,
            "strategic_universe_identities": {
                "tradable": account.strategic_tradable_universe_identity,
                "qualification_reference": (
                    account.strategic_qualification_universe_identity
                ),
                "risk_reference": account.strategic_risk_universe_identity,
            },
            "flat_book_capital_repair": asdict(account.flat_book_capital_repair),
            "strategic_cash_rearm": asdict(account.strategic_cash_rearm),
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
    finalized = replace(decision, decision_digest=digest)
    leaders = tuple(
        cast(Mapping[str, object], _freeze_observed_fact(asdict(item)))
        for item in sorted(
            allocation.all_leaders.values(),
            key=lambda candidate: (-candidate.score, candidate.symbol),
        )
    )
    observation = _ObservedDecisionFacts(
        effective_config_sha256=config_fingerprint(market.cfg),
        risk_assessment=cast(
            Mapping[str, object], _freeze_observed_fact(asdict(allocation.risk))
        ),
        strategic_universe_roles=allocation.strategic_universe,
        strategic_qualification=cast(
            Mapping[str, object],
            _freeze_observed_fact(asdict(account.strategic_qualification)),
        ),
        strategic_successor_qualification=cast(
            Mapping[str, object],
            _freeze_observed_fact(asdict(account.strategic_successor_qualification)),
        ),
        leader_scores=leaders,
        qualification_snapshots=cast(
            Mapping[str, object],
            _freeze_observed_fact(allocation.qualification_snapshots),
        ),
    )
    return _DecisionResult(decision=finalized, observation=observation)


def _decide_result(
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
    strategic_universe_declaration: StrategicUniverseDeclaration | None = None,
) -> _DecisionResult:
    """Produce one decision and its private lossless production observation.

    The account is advanced in place after all data, code, state, and
    chronology checks succeed. Returned orders are next-open intentions;
    this method never fills them on the signal date.
    """
    date, user_symbols, durable_symbols = validated_decision_symbols(
        symbols=symbols,
        as_of=as_of,
        account=account,
    )
    inputs = verify_decision_provenance(
        self,
        date=date,
        user_symbols=user_symbols,
        durable_symbols=durable_symbols,
        account=account,
        code_fingerprint_fn=code_fingerprint_fn,
    )
    market = decision_market_context(
        self,
        inputs=inputs,
        account=account,
        strategic_universe_declaration=strategic_universe_declaration,
    )
    risk = assess_decision_risk(
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
    return _finalize_decision_result(
        inputs=inputs,
        market=market,
        allocation=allocation,
        account=account,
    )


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
    strategic_universe_declaration: StrategicUniverseDeclaration | None = None,
) -> Decision:
    """Produce and persist one causal close-date portfolio decision.

    The account is advanced in place after all data, code, state, and
    chronology checks succeed. Returned orders are next-open intentions;
    this method never fills them on the signal date.
    """

    return _decide_result(
        self,
        assess_risk_fn,
        evaluate_sentinel_fn,
        reconcile_account_orders_fn,
        code_fingerprint_fn,
        attach_target_attribution_fn,
        symbols=symbols,
        as_of=as_of,
        account=account,
        strategic_universe_declaration=strategic_universe_declaration,
    ).decision


observed_decision = _decide_result


def deterministic_decision(
    self: DecisionEngineRuntime,
    *,
    symbols: Iterable[str],
    as_of: str,
    account: AccountState,
    strategic_universe_declaration: StrategicUniverseDeclaration | None = None,
) -> tuple[Decision, AccountState]:
    """Evaluate a decision on a deep copy and return both result and copy."""
    cloned = copy.deepcopy(account)
    return (
        self.decide(
            symbols=symbols,
            as_of=as_of,
            account=cloned,
            strategic_universe_declaration=strategic_universe_declaration,
        ),
        cloned,
    )


decision_config_for_universe = _decision_config_for_universe
mark_account_positions = _mark_account_positions
