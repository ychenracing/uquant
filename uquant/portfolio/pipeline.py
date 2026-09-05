"""One capital budget for retained holdings, restoration and core admissions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pandas as pd

from ..features import scalar
from ..models.strategic_universe import StrategicUniverseRoles
from ..portfolio_core import current_weights, symbol_weight_cap
from ..types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Risk,
    RiskAssessment,
    Target,
)
from .capital import admission_room, committed_capital
from .strategic.authority import assess_strategic_capital_authority
from .strategic.qualification_candidates import (
    reset_strategic_candidate_eligibility,
    strategic_candidate_confirmation,
)
from .strategic.rearm import strategic_cash_rearm_grant_open

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _core_candidates(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> list[str]:
    """Use the engine's per-symbol confirmed leadership, never cohort rank tenure."""
    return sorted(
        (
            symbol
            for symbol, score in leaders.items()
            if score.mature
            and any(
                strategic_candidate_confirmation(account=account, symbol=symbol, route=route)
                >= self.cfg.leader_tenure_days
                for route in (
                    "established",
                    "transition",
                    "transition_impulse",
                    "persistent_industry",
                    "reversal_industry",
                )
            )
            and score.confidence >= self.cfg.leader_min_confidence
            and score.industry != "unknown"
            and score.components.get("unknown_industry", 1.0) < 0.5
            and symbol in user_panel
            and date in user_panel[symbol].index
            and len(user_panel[symbol].loc[:date]) >= 121
            and self._structure_ok(user_panel[symbol], date)
            and self._liquidity_confirmed(user_panel[symbol], date)
        ),
        key=lambda symbol: (-leaders[symbol].score, symbol),
    )


def _degraded_transfer(
    self: PortfolioAllocator,
    *,
    challenger: str,
    proposed: dict[str, float],
    weights_now: dict[str, float],
    leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    account: AccountState,
) -> str | None:
    """A confirmed weak incumbent can release one bounded slice after prior fills."""
    if account.pending_orders or str(date.date()) in account.rotation_dates or not self._rotation_allowed(account, date, user_panel):
        return None
    held = [s for s, weight in weights_now.items() if weight > 0 and s in leaders and s in user_panel]
    if not held or len(held) >= self.cfg.max_positions:
        return None
    weakest = min(held, key=lambda s: (self._retention_score(s, leaders, account), s))
    position, frame = account.positions[weakest], user_panel[weakest]
    row = frame.loc[date]
    broken = (
        scalar(row, "close") < scalar(row, f"ma{self.cfg.trend_fast}")
        and scalar(row, f"ret{self.cfg.trend_fast}") < 0
    )
    winner_penalty = min(0.20, 0.50 * max(0.0, position.highest_close / max(position.avg_cost, 1e-12) - 1.0))
    score = leaders[challenger]
    edge = (
        score.score
        - leaders[weakest].score
        - 0.01
        - winner_penalty
        - (0.15 if score.industry == leaders[weakest].industry else 0.0)
        - 0.05 * max(0.0, 1.0 - score.confidence)
        + (0.08 if broken else 0.0)
    )
    key = f"core_transfer:{weakest}->{challenger}"
    clock = f"core_transfer_session:{weakest}->{challenger}"
    observed = date.toordinal()
    previous = frame.loc[:date].index[-2].toordinal() if len(frame.loc[:date]) > 1 else 0
    last_observed = account.candidate_tenure.get(clock, 0)
    if last_observed > observed:
        raise RuntimeError("transfer observation session moved backwards")
    if last_observed != observed:
        streak = account.replacement_tenure.get(key, 0) if last_observed == previous else 0
        account.replacement_tenure[key] = streak + 1 if broken and edge >= self.cfg.replacement_edge else 0
        account.candidate_tenure[clock] = observed
    held_sessions = len(frame.loc[pd.Timestamp(position.entry_date) : date]) if position.entry_date else 0
    if (
        account.replacement_tenure[key] < self.cfg.replacement_confirm_days
        or held_sessions < self.cfg.min_hold_days
    ):
        return None
    remaining = max(0.0, proposed.get(weakest, 0.0) - self.cfg.replacement_transfer_cap)
    proposed[weakest] = remaining
    for rights in (
        account.strategic_cohort_targets,
        account.strategic_restore_weights,
        account.protected_weights,
    ):
        if weakest in rights:
            rights[weakest] = min(rights[weakest], remaining)
    if weakest in account.strategic_exit_bands:
        bands = account.strategic_exit_bands[weakest]
        scale = min(1.0, remaining / max(sum(bands), 1e-12))
        account.strategic_exit_bands[weakest] = [weight * scale for weight in bands]
    account.rotation_dates.append(str(date.date()))
    return weakest


def _allocate_strategy(
    self: PortfolioAllocator,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> tuple[Target, ...]:
    """Retain each filled owner, then allocate only available common capital."""
    weights_now, _ = current_weights(account, prices)
    self._release_stale_recovery_anchor(risk=risk, account=account, weights_now=weights_now)
    if risk.state is Risk.CRISIS and any(
        marker in risk.reasons for marker in (
            "capital drawdown relapse in restored holdings",
            "market-backed portfolio break in incomplete restoration",
            "capital guard cooldown after failed restoration",
        )
    ):
        self._release_recovery_anchor(account)
        account.protected_weights.clear()
        for symbol in tuple(account.strategic_cohort_targets):
            self._retire_strategic_member(account, symbol)
        account.candidate_tenure["post_shock_restore_complete"] = 0
    frozen = (
        risk.freeze_new_risk
        or bool(risk.evidence.get("freeze_new_risk", False))
        or risk.state in {Risk.RISK_OFF, Risk.CRISIS}
    )
    owned = (
        set(account.strategic_cohort_symbols)
        | {s for s, position in account.positions.items() if position.grant_id or position.epoch_id}
        | {order.symbol for order in account.pending_orders if order.grant_id or order.epoch_id}
    )
    strategic = self._strategic_cohort_targets(
        date=date,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        prices=prices,
        weights_now=weights_now,
        admission_open=not frozen
        and risk.state is Risk.NORMAL
        and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND},
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    owned.update(account.strategic_cohort_symbols)
    strategic_targets = {target.symbol: target for target in strategic or () if target.symbol in owned}
    proposed = dict(weights_now)
    proposed.update({s: min(weights_now.get(s, 0.0), target.weight) for s, target in strategic_targets.items()})
    grant = account.strategic_grant
    strategic_blocked = bool(
        grant is not None and grant.status != "ACTIVE"
        and account.strategic_qualification.deployment_blocked
    )
    bounded_restore = self._bounded_strategic_restore_risk_open(
        risk=risk, account=account
    ) or strategic_cash_rearm_grant_open(account=account, risk=risk, cfg=self.cfg)
    committed, cash_room = committed_capital(account=account, prices=prices, proposed=proposed)
    unresolved = bool(assess_strategic_capital_authority(account).late_fill_order_ids)
    # A fully qualified founding cohort retains its existing funding allowance
    # only while it is the entire committed book. Other holdings share normal caps.
    founding_cap = (
        self.cfg.max_gross
        if grant is not None and grant.qualification_quorum == "FULL_COHORT"
        and not {s for s, weight in committed.items() if weight > 0} - owned
        else None
    )
    if not strategic_blocked and (not frozen or bounded_restore) and not unresolved:
        for symbol in sorted(strategic_targets, key=lambda s: (s != (grant.candidate_symbol if grant else ""), s)):
            desired = strategic_targets[symbol].weight
            current = weights_now.get(symbol, 0.0)
            if desired <= current or symbol not in leaders or symbol not in user_panel:
                continue
            reserved = max(0.0, committed.get(symbol, 0.0) - current)
            other_commitments = {**committed, symbol: current}
            increment = min(
                desired - current,
                cash_room + reserved,
                admission_room(
                    cfg=self.cfg, symbol=symbol, committed=other_commitments,
                    leaders=leaders, user_panel=user_panel, date=date,
                    gross_cap=min(self.cfg.max_gross, risk.target_gross_cap),
                    symbol_cap=symbol_weight_cap(self.cfg, account, symbol),
                    concentration_cap=founding_cap,
                ),
            )
            proposed[symbol] = current + increment
            committed[symbol] = max(committed.get(symbol, 0.0), proposed[symbol])
            cash_room -= max(0.0, increment - reserved)
    candidates = _core_candidates(self, date=date, user_panel=user_panel, leaders=leaders, account=account)
    reasons: dict[str, str] = {}
    mechanisms: dict[str, AttributionMechanism] = {}
    replacements: dict[str, str] = {}
    for symbol, position in account.positions.items():
        if symbol in owned or position.shares <= 0:
            continue
        exit_confirmed = self._leader_lifecycle_exit_confirmed(
            symbol=symbol, date=date, user_panel=user_panel, leaders=leaders, account=account
        )
        if exit_confirmed:
            proposed[symbol] = 0.0
            reset_strategic_candidate_eligibility(account=account, symbol=symbol)
            account.protected_weights.pop(symbol, None)
            reasons[symbol] = "leader lifecycle exit: confirmed structural deterioration"
            mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_EXIT
    # Pending reductions remain reductions; their proceeds are not cash until filled.
    for order in account.pending_orders:
        if order.side == "SELL":
            proposed[order.symbol] = min(proposed.get(order.symbol, 0.0), order.target_weight)
        elif not frozen and order.symbol not in owned and order.symbol in candidates:
            proposed[order.symbol] = max(proposed.get(order.symbol, 0.0), order.target_weight)
    if not frozen:
        gross_cap = min(self.cfg.max_gross, risk.target_gross_cap)
        committed, cash_room = committed_capital(account=account, prices=prices, proposed=proposed)
        authority = assess_strategic_capital_authority(account)
        if authority.late_fill_order_ids:
            cash_room = 0.0  # Unacknowledged physical liability cannot fund another order.
        episode = pd.Timestamp(account.last_shock_date).toordinal() if account.last_shock_date else 0
        for symbol, desired in sorted(account.protected_weights.items()):
            marker = f"core_restored:{symbol}"
            if (
                symbol in owned
                or symbol not in user_panel
                or symbol not in leaders
                or mechanisms.get(symbol) is AttributionMechanism.LEADER_LIFECYCLE_EXIT
                or account.candidate_tenure.get(marker, -1) == episode
                or not self._structure_ok(user_panel[symbol], date)
            ):
                continue
            current = weights_now.get(symbol, 0.0)
            wanted = min(self.cfg.max_symbol_weight, desired)
            pending = any(order.symbol == symbol and order.side == "BUY" for order in account.pending_orders)
            if not pending and wanted - current < self.cfg.protected_restore_min_trade_weight:
                account.candidate_tenure[marker] = episode
                continue
            room = min(
                cash_room,
                admission_room(
                    cfg=self.cfg,
                    symbol=symbol,
                    committed=committed,
                    leaders=leaders,
                    user_panel=user_panel,
                    date=date,
                    gross_cap=gross_cap,
                ),
            )
            increment = min(room, max(0.0, wanted - committed.get(symbol, 0.0)))
            if increment >= self.cfg.protected_restore_min_trade_weight:
                proposed[symbol] = committed.get(symbol, 0.0) + increment
                committed[symbol] = proposed[symbol]
                cash_room -= increment
                mechanisms[symbol] = AttributionMechanism.POST_SHOCK_RESTORATION
                reasons[symbol] = "filled core restoration after account risk repair"
        for symbol in candidates:
            if symbol in owned or weights_now.get(symbol, 0.0) > 0 or committed.get(symbol, 0.0) > 0:
                continue
            if (
                opportunity not in {Opportunity.TREND, Opportunity.STRONG_TREND}
                or risk.state is not Risk.NORMAL
            ):
                continue
            room = min(
                cash_room,
                admission_room(
                    cfg=self.cfg,
                    symbol=symbol,
                    committed=committed,
                    leaders=leaders,
                    user_panel=user_panel,
                    date=date,
                    gross_cap=gross_cap,
                ),
            )
            weight = min(self.cfg.core_admission_weight, room)
            if weight + 1e-12 < self.cfg.core_admission_weight:
                weak = _degraded_transfer(
                    self,
                    challenger=symbol,
                    proposed=proposed,
                    weights_now=weights_now,
                    leaders=leaders,
                    user_panel=user_panel,
                    date=date,
                    account=account,
                )
                if weak is not None:
                    reasons[weak] = "leader rotation: bounded transfer after confirmed deterioration"
                    mechanisms[weak] = AttributionMechanism.LEADER_ROTATION
                    break  # One transfer intent; settlement precedes the next capital allocation.
                continue
            proposed[symbol] = weight
            committed[symbol] = weight
            cash_room -= weight
            reasons[symbol] = "confirmed core admitted from available account capital"
            transfers = [
                key
                for key, tenure in account.replacement_tenure.items()
                if key.startswith("core_transfer:")
                and key.endswith("->" + symbol)
                and tenure >= self.cfg.replacement_confirm_days
            ]
            if transfers:
                transfer = sorted(transfers)[0]
                replacements[symbol] = transfer.split(":", 1)[1].split("->", 1)[0]
                mechanisms[symbol] = AttributionMechanism.LEADER_ROTATION
                reasons[symbol] = "leader rotation after the prior reduction filled"
                account.replacement_tenure[transfer] = 0
    account.active_leaders = sorted(s for s, weight in proposed.items() if weight > 0 and s not in owned)
    targets = self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="retained core holding",
        origin_subsystem=OriginSubsystem.LEADER,
        mechanism=AttributionMechanism.LEADER_SELECTION,
        reasons=reasons,
        mechanisms=mechanisms,
        replaces_symbols=replacements,
    )
    targets = tuple(
        replace(
            strategic_targets[t.symbol],
            weight=t.weight,
            reason=reasons.get(t.symbol, strategic_targets[t.symbol].reason),
            mechanism=mechanisms[t.symbol].value
            if t.symbol in mechanisms
            else strategic_targets[t.symbol].mechanism,
        )
        if t.symbol in strategic_targets
        else t
        for t in targets
    )
    pending_buys = {order.symbol: order for order in account.pending_orders if order.side == "BUY"}
    targets = tuple(
        replace(
            target,
            lifecycle=retained.lifecycle,
            reason=retained.reason,
            reduction_policy=retained.reduction_policy,
            reason_code=retained.reason_code,
            exit_kind=retained.exit_kind,
            event_id=retained.event_id,
            origin_subsystem=retained.origin_subsystem,
            mechanism=retained.mechanism,
            origin_lifecycle=retained.origin_lifecycle,
            replaces_symbol=retained.replaces_symbol,
            industry_at_entry=retained.industry_at_entry,
            industry_manifest_sha256=retained.industry_manifest_sha256,
            grant_id=retained.grant_id,
            epoch_id=retained.epoch_id,
        )
        if (retained := pending_buys.get(target.symbol)) is not None
        and target.weight > weights_now.get(target.symbol, 0.0) + 1e-12
        and target.weight + 1e-12 >= retained.target_weight
        and abs(target.weight - retained.target_weight) < self.cfg.min_trade_weight
        else target
        for target in targets
    )
    if frozen:
        bounded = bounded_restore
        frozen_targets = self._frozen_existing_targets(
            strategy_targets=targets, leaders=leaders, account=account, weights_now=weights_now
        )
        permitted = {t.symbol: t for t in targets if t.symbol in owned} if bounded else {}
        frozen_book = {t.symbol: t for t in frozen_targets}
        frozen_book.update(permitted)
        targets = tuple(frozen_book[symbol] for symbol in sorted(frozen_book))
    return targets


allocate_strategy = _allocate_strategy
