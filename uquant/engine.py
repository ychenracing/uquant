"""The only production engine; daily and backtest call the same decision path."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from .atomic_io import atomic_write_text
from .attribution import (
    build_daily_ledger_row,
    build_daily_replay_evidence_row,
    build_economic_attribution,
)
from .config import (
    DEFAULT_CONFIG,
    SystemConfig,
    canonical_control_float,
    config_fingerprint,
)
from .config_governance import DEFAULT_GOVERNANCE_PATH
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
from .risk_sentinel.history import (
    build_risk_evidence_timeline,
    risk_evidence_timeline_from_dict,
    risk_evidence_timeline_prefix,
    risk_evidence_timeline_to_dict,
)
from .risk_sentinel.integration import sentinel_freeze_authorized
from .risk_sentinel.models import RiskEvidenceTimeline
from .risk_sentinel.service import evaluate_sentinel
from .types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountOrder,
    AccountState,
    Decision,
    Fill,
    LeaderScore,
    Opportunity,
    PendingOrder,
    Side,
    Target,
    derive_attribution_event_id,
)
from .validation.ai_era import require_ai_era_interval
from .validation.universe import (
    REQUIRED_AI_UNIVERSE_SHA256,
    AIUniverse,
    default_ai_universe,
)

INDEX_SYMBOLS = ("sh000300", "sh000682")
_LEGACY_INDUSTRY = "legacy_unmapped"
_LEGACY_MANIFEST_SHA256 = "0" * 64
_SHARED_RISK_TIMELINE_CACHE: dict[tuple[str, str, str, int], RiskEvidenceTimeline] = {}
_RISK_TIMELINE_BUILDER = build_risk_evidence_timeline
_RISK_TIMELINE_CACHE_SCHEMA = "uquant.risk-evidence-cache.v1"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _risk_timeline_disk_path(key: tuple[str, str, str, str]) -> Path:
    identity = hashlib.sha256(_canonical_json(list(key))).hexdigest()
    return Path(tempfile.gettempdir()) / "uquant-risk-evidence-v1" / f"{identity}.json"


def _load_risk_timeline_disk_cache(
    path: Path,
    *,
    key: tuple[str, str, str, str],
) -> RiskEvidenceTimeline | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
            return None
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            return None
        if hashlib.sha256(_canonical_json(payload)).hexdigest() != envelope["sha256"]:
            return None
        if payload.get("schema") != _RISK_TIMELINE_CACHE_SCHEMA:
            return None
        if payload.get("key") != list(key):
            return None
        timeline = payload.get("timeline")
        if not isinstance(timeline, dict):
            return None
        return risk_evidence_timeline_from_dict(timeline)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _write_risk_timeline_disk_cache(
    path: Path,
    *,
    key: tuple[str, str, str, str],
    timeline: RiskEvidenceTimeline,
) -> None:
    payload = {
        "schema": _RISK_TIMELINE_CACHE_SCHEMA,
        "key": list(key),
        "timeline": risk_evidence_timeline_to_dict(timeline),
    }
    envelope = {
        "payload": payload,
        "sha256": hashlib.sha256(_canonical_json(payload)).hexdigest(),
    }
    atomic_write_text(
        path,
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


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
        # Presence in the active pending collection is authoritative. A
        # blocked order can still have ``remaining_shares == 0`` before the
        # next open supplies the first executable quantity.
        if order.event_id
    }
    attributed: list[Target] = []
    for target in targets:
        if target.event_id:
            attributed.append(target)
            continue
        retained = retained_by_symbol.get(target.symbol)
        if (
            retained is not None
            and retained.side == Side.SELL.value
            and abs(retained.target_weight) <= 1e-12
            and abs(target.weight) <= 1e-12
            and retained.lifecycle == target.lifecycle
            and retained.reduction_policy == target.reduction_policy
        ):
            # A full liquidation is one causal event even when a later daily
            # classifier gives the still-unfilled residual a different label.
            # Preserve the originating machine identity at the production
            # boundary; direct merge callers still fail closed on fabricated
            # or genuinely changed attributed intents.
            attributed.append(
                replace(
                    target,
                    event_id=retained.event_id,
                    origin_subsystem=retained.origin_subsystem,
                    mechanism=retained.mechanism,
                    origin_lifecycle=retained.origin_lifecycle,
                    replaces_symbol=retained.replaces_symbol,
                    industry_at_entry=retained.industry_at_entry,
                    industry_manifest_sha256=retained.industry_manifest_sha256,
                )
            )
            continue
        if (
            retained is not None
            and retained.side == Side.BUY.value
            and target.weight > 1e-12
            and abs(retained.target_weight - target.weight) < cfg.min_trade_weight
            and retained.lifecycle == target.lifecycle
            and retained.reduction_policy == target.reduction_policy
            and retained.reason_code == target.reason_code
            and retained.exit_kind == target.exit_kind
            and retained.origin_subsystem == target.origin_subsystem
            and retained.origin_lifecycle == target.origin_lifecycle
            and retained.replaces_symbol == target.replaces_symbol
        ):
            # A partially filled GTC buy remains the causal event submitted on
            # its original signal date. Daily portfolio classification may
            # move from restoration to cohort/hold while the target stays
            # inside the reviewed no-trade band; that is not a new order cause.
            attributed.append(
                replace(
                    target,
                    event_id=retained.event_id,
                    origin_subsystem=retained.origin_subsystem,
                    mechanism=retained.mechanism,
                    origin_lifecycle=retained.origin_lifecycle,
                    replaces_symbol=retained.replaces_symbol,
                    industry_at_entry=retained.industry_at_entry,
                    industry_manifest_sha256=retained.industry_manifest_sha256,
                )
            )
            continue
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
    """Return a stable digest of every production Python module and contract."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    for path in (DEFAULT_REGISTRY_PATH, DEFAULT_GOVERNANCE_PATH):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
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
        self._risk_timeline_cache_key: tuple[str, str, str, int] | None = None
        self._risk_timeline_cache: RiskEvidenceTimeline | None = None

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

    def _causal_risk_timeline(
        self,
        *,
        as_of: str,
        cfg: SystemConfig,
        universe: AIUniverse,
    ) -> RiskEvidenceTimeline:
        """Return one immutable data/config cache prefix without account inputs."""

        broad = self._features["sh000300"]
        tech = self._features["sh000682"]
        common = broad.index.intersection(tech.index)
        if common.empty:
            raise RuntimeError("Sentinel timeline has no common index session")
        full_as_of = str(pd.Timestamp(common[-1]).date())
        timeline_symbols = tuple(sorted({*universe.symbols, *INDEX_SYMBOLS}))
        full_data_digest = self.data.manifest(
            timeline_symbols,
            as_of=pd.Timestamp(full_as_of),
        ).digest
        key = (
            full_data_digest,
            config_fingerprint(cfg),
            str(universe.sha256),
            id(build_risk_evidence_timeline),
        )
        disk_key = (
            full_data_digest,
            config_fingerprint(cfg),
            str(universe.sha256),
            self._code_hash or code_fingerprint(),
        )
        if self._risk_timeline_cache_key != key:
            timeline = _SHARED_RISK_TIMELINE_CACHE.get(key)
            disk_path = _risk_timeline_disk_path(disk_key)
            if timeline is None and build_risk_evidence_timeline is _RISK_TIMELINE_BUILDER:
                timeline = _load_risk_timeline_disk_cache(
                    disk_path,
                    key=disk_key,
                )
            if timeline is None:
                timeline = build_risk_evidence_timeline(
                    as_of=full_as_of,
                    broad_frame=broad,
                    tech_frame=tech,
                    reference_panel={
                        symbol: self._features[symbol]
                        for symbol in sorted(universe.symbols)
                        if symbol in self._features
                    },
                    reference_returns=self._reference_returns,
                    universe=universe,
                    cfg=cfg,
                )
                if build_risk_evidence_timeline is _RISK_TIMELINE_BUILDER:
                    _write_risk_timeline_disk_cache(
                        disk_path,
                        key=disk_key,
                        timeline=timeline,
                    )
                _SHARED_RISK_TIMELINE_CACHE[key] = timeline
            self._risk_timeline_cache_key = key
            self._risk_timeline_cache = timeline
        if self._risk_timeline_cache is None:
            raise RuntimeError("Sentinel timeline cache was not initialized")
        return risk_evidence_timeline_prefix(
            self._risk_timeline_cache,
            as_of=as_of,
            cfg=cfg,
        )

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
        universe = default_ai_universe()
        canonical_symbols = universe.symbols_as_of(str(date.date()))
        if active_reference_symbols != canonical_symbols:
            raise RuntimeError(
                "point-in-time reference registry differs from canonical universe"
            )
        causal_timeline = self._causal_risk_timeline(
            as_of=str(date.date()),
            cfg=decision_cfg,
            universe=universe,
        )
        sentinel = None
        if decision_cfg.risk_sentinel_mode != "SHADOW":
            sentinel = evaluate_sentinel(
                as_of=str(date.date()),
                broad_frame=broad,
                tech_frame=tech,
                reference_panel=reference_panel,
                point_in_time_industries={
                    symbol: universe.industry_of(symbol, str(date.date()))
                    for symbol in canonical_symbols
                },
                held_symbols=tuple(
                    sorted(
                        symbol
                        for symbol, position in account.positions.items()
                        if position.shares > 0
                    )
                ),
                leader_symbols=tuple(sorted(account.active_leaders)),
                capital_drawdown=max(
                    0.0,
                    1.0 - equity / max(account.capital_peak, 1e-12),
                ),
            )
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
            sentinel_assessment=sentinel,
            sentinel_opportunity=account.opportunity,
            sentinel_causal_timeline=causal_timeline,
        )
        risk.evidence["configured_user_universe_size"] = len(user_symbols)
        risk.evidence["universe_size_is_diagnostic_only"] = True
        latest_causal = (
            causal_timeline.sentinel_rows[-1]
            if causal_timeline.sentinel_rows
            else None
        )
        risk.evidence.update(
            {
                "sentinel_mode": decision_cfg.risk_sentinel_mode,
                "sentinel_causal_confirmation_authority_enabled": (
                    decision_cfg.risk_sentinel_causal_confirmation_enabled
                ),
                "sentinel_causal_confirmation_history_trusted": (
                    causal_timeline.confirmation_history_trusted
                ),
                "sentinel_causal_confirmation_days": (
                    causal_timeline.confirmation_days
                ),
                "sentinel_causal_repair_days": causal_timeline.repair_days,
                "sentinel_causal_effective_level": (
                    causal_timeline.effective_level.value
                ),
                "sentinel_causal_confirmed_since": (
                    causal_timeline.confirmed_since
                ),
                "sentinel_causal_trust_reasons": list(
                    causal_timeline.trust_reasons
                ),
                "sentinel_causal_incremental_families": list(
                    causal_timeline.incremental_families
                ),
                "sentinel_causal_earlier_families": list(
                    causal_timeline.earlier_families
                ),
                "sentinel_first_family_dates": dict(
                    causal_timeline.sentinel_first_family_dates
                ),
                "base_first_family_dates": dict(
                    causal_timeline.base_first_family_dates
                ),
                "sentinel_causal_coverage_status": (
                    latest_causal.coverage_status.value
                    if latest_causal is not None
                    else "NOT_READY"
                ),
                "sentinel_causal_confidence": (
                    latest_causal.confidence
                    if latest_causal is not None
                    else 0.0
                ),
                "sentinel_causal_observed_level": (
                    latest_causal.level.value
                    if latest_causal is not None
                    else "NOT_READY"
                ),
                "sentinel_causal_active_families": (
                    list(latest_causal.active_families)
                    if latest_causal is not None
                    else []
                ),
                "sentinel_causal_reasons": (
                    list(latest_causal.reasons)
                    if latest_causal is not None
                    else ["causal market history is not ready"]
                ),
                "sentinel_causal_weakest_subindustries": (
                    list(latest_causal.weakest_subindustries)
                    if latest_causal is not None
                    else []
                ),
            }
        )
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
            removed_buy_reason=(
                "sentinel_freeze_new_risk"
                if sentinel_freeze_authorized(risk)
                else None
            ),
        )
        account.last_successful_run = str(date.date())
        account.data_hash = data_digest
        account.data_hash_as_of = str(date.date())
        account.data_hash_symbols = list(current_symbols)
        account.code_hash = current_code_hash
        decision = Decision(
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
                "target_gross_cap": canonical_control_float(risk.target_gross_cap),
                "system_gross_cap": canonical_control_float(decision_cfg.max_gross),
                "freeze_new_risk": risk.freeze_new_risk,
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
            decision_digest="",
        )
        canonical = decision.canonical_payload(
            effective_config_sha256=config_fingerprint(decision_cfg)
        )
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return replace(decision, decision_digest=digest)

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
                build_daily_replay_evidence_row(
                    date=str(date.date()),
                    account=account,
                    close_prices=close_prices,
                )
            )
            previous_equity = equity
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
            decision_trace=[
                item.canonical_payload(
                    effective_config_sha256=config_fingerprint(self.cfg)
                )
                for item in decisions
            ],
            legacy_decision_digests=[
                hashlib.sha256(
                    json.dumps(
                        item.legacy_canonical_payload(),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                for item in decisions
            ],
            daily_replay_evidence=daily_replay_evidence,
            sentinel_events=[
                {
                    "date": item.date,
                    "level": item.risk_summary["sentinel_assessment"]["level"],
                    "confidence": item.risk_summary["sentinel_assessment"][
                        "confidence"
                    ],
                    "families": item.risk_summary["sentinel_assessment"][
                        "evidence_families"
                    ],
                    "base_family_active": item.risk_summary["base_family_active"],
                    "sentinel_family_active": item.risk_summary[
                        "sentinel_family_active"
                    ],
                    "combined_family_active": item.risk_summary[
                        "combined_family_active"
                    ],
                    "incremental": item.risk_summary["sentinel_incremental"],
                    "incremental_families": item.risk_summary[
                        "sentinel_incremental_families"
                    ],
                    "earlier_families": item.risk_summary[
                        "sentinel_earlier_families"
                    ],
                    "first_base_date": item.risk_summary["first_base_date"],
                    "first_sentinel_date": item.risk_summary["first_sentinel_date"],
                    "confirmation_days": item.risk_summary[
                        "sentinel_confirmation_days"
                    ],
                    "freeze_new_risk": item.risk_summary[
                        "sentinel_freeze_new_risk"
                    ],
                    "base_freeze_new_risk": item.risk_summary[
                        "base_freeze_new_risk"
                    ],
                    "target_gross_cap": item.risk_summary["target_gross_cap"],
                    "base_target_gross_cap": item.risk_summary[
                        "base_target_gross_cap"
                    ],
                }
                for item in decisions
                if isinstance(item.risk_summary.get("sentinel_assessment"), dict)
                and (
                    bool(
                        item.risk_summary["sentinel_assessment"].get(
                            "evidence_families"
                        )
                    )
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
                benchmark_close={
                    str(date.date()): float(self._raw["sh000682"].loc[date, "close"])
                    for date in sessions
                },
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
