"""One capital budget for retained holdings, restoration and core admissions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..features import scalar
from ..holding_history import holding_spans_date
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
from .capital import committed_capital, funded_increment
from .strategic.authority import assess_strategic_capital_authority
from .strategic.discovery import current_core_qualification
from .strategic.qualification_candidates import (
    reset_strategic_candidate_eligibility,
    strategic_candidate_confirmation,
)
from .strategic.rearm import strategic_cash_rearm_grant_open

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def _candidate_market_block(self: PortfolioAllocator, *, symbol: str, score: LeaderScore,
                            date: pd.Timestamp, user_panel: dict[str, pd.DataFrame]) -> str:
    if not score.confidence >= self.cfg.leader_min_confidence:
        return "CONFIDENCE_BELOW_MINIMUM"
    if score.industry == "unknown" or not score.components.get("unknown_industry", 1.0) < .5:
        return "INDUSTRY_NOT_VERIFIED"
    if symbol not in user_panel or date not in user_panel[symbol].index:
        return "CURRENT_MARKET_DATA_UNAVAILABLE"
    if len(user_panel[symbol].loc[:date]) < 121:
        return "INSUFFICIENT_HISTORY"
    if not self._structure_ok(user_panel[symbol], date):
        return "STRUCTURE_NOT_REPAIRED"
    if not self._liquidity_confirmed(user_panel[symbol], date):
        return "LIQUIDITY_NOT_CONFIRMED"
    return "READY"


def _candidate_entry(self: PortfolioAllocator, *, symbol: str, score: LeaderScore,
                     date: pd.Timestamp, user_panel: dict[str, pd.DataFrame],
                     account: AccountState, confirmation_days: int,
                     certificate: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {"required_confirmation": confirmation_days}
    if certificate is not None:
        return {**certificate, "block": _candidate_market_block(
            self, symbol=symbol, score=score, date=date, user_panel=user_panel)}
    if not score.mature:
        return {**evidence, "block": "NOT_MATURE"}
    streak = strategic_candidate_confirmation(account=account, symbol=symbol, route="independent_core")
    observed = {"independent_core": streak}
    if streak < confirmation_days:
        return {**evidence, "confirmations": observed, "block": "CONFIRMATION_INCOMPLETE"}
    evidence.update(confirmations=observed, block=_candidate_market_block(
        self, symbol=symbol, score=score, date=date, user_panel=user_panel))
    return evidence


def _core_candidates(
    self: PortfolioAllocator, *, date: pd.Timestamp, user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore], account: AccountState,
    trace: dict[str, dict[str, Any]] | None = None,
    certificates: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Record the same short-circuit predicates that decide core entry eligibility."""
    candidates = []
    for symbol, score in leaders.items():
        entry = _candidate_entry(self, symbol=symbol, score=score, date=date,
                                 user_panel=user_panel, account=account,
                                 confirmation_days=self.cfg.leader_tenure_days,
                                 certificate=(certificates or {}).get(symbol))
        if trace is not None:
            trace.setdefault(symbol, {}).update(entry=entry, rank_score=score.score)
        if entry["block"] == "READY":
            candidates.append(symbol)
    return sorted(candidates, key=lambda symbol: (-leaders[symbol].score, symbol))


def _transfer_sell_filled(account: AccountState, key: str, date: pd.Timestamp) -> bool:
    """A recorded observation becomes a transfer only through its real sell."""
    symbol = key.split(":", 1)[1].split("->", 1)[0]
    signal = account.candidate_tenure.get(key.replace("core_transfer:", "core_transfer_session:", 1))
    return any(
        fill.side == "SELL" and fill.shares > 0 and fill.symbol == symbol
        and fill.mechanism == AttributionMechanism.LEADER_ROTATION.value
        and pd.Timestamp(fill.signal_date).toordinal() == signal
        and fill.fill_date <= str(date.date())
        for fill in account.fills
    )


def _observe_transfer(self: PortfolioAllocator, *, weakest: str, challenger: str,
                      frame: pd.DataFrame, date: pd.Timestamp, account: AccountState,
                      broken: bool, edge: float) -> str:
    key = f"core_transfer:{weakest}->{challenger}"
    clock = f"core_transfer_session:{weakest}->{challenger}"
    observed = date.toordinal()
    previous = frame.loc[:date].index[-2].toordinal() if len(frame.loc[:date]) > 1 else 0
    last_observed = account.candidate_tenure.get(clock, 0)
    if last_observed > observed:
        raise RuntimeError("transfer observation session moved backwards")
    if (account.replacement_tenure.get(key, 0) >= self.cfg.replacement_confirm_days
            and _transfer_sell_filled(account, key, date)):
        return key
    if last_observed != observed:
        streak = account.replacement_tenure.get(key, 0) if last_observed == previous else 0
        account.replacement_tenure[key] = streak + 1 if broken and edge >= self.cfg.replacement_edge else 0
        account.candidate_tenure[clock] = observed
    return key


def _available_transfer_incumbent(
    self: PortfolioAllocator, *, account: AccountState, proposed: dict[str, float],
    weights_now: dict[str, float], leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame], date: pd.Timestamp,
) -> str | None:
    """Select an incumbent only while settlement, rotation and position limits permit."""
    if (account.pending_orders or any(proposed.get(s, 0.0) < weight for s, weight in weights_now.items())
            or str(date.date()) in account.rotation_dates or not self._rotation_allowed(account, date, user_panel)):
        return None
    held = [s for s, weight in weights_now.items() if weight > 0 and s in leaders and s in user_panel]
    if not held or len(held) >= self.cfg.max_positions:
        return None
    return min(held, key=lambda s: (self._retention_score(s, leaders, account), s))


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
    committed: dict[str, float],
    cash_room: float,
    gross_cap: float,
    diagnostics: dict[str, Any] | None = None,
) -> str | None:
    """A confirmed weak incumbent can release one bounded slice after prior fills."""
    weakest = _available_transfer_incumbent(
        self, account=account, proposed=proposed, weights_now=weights_now,
        leaders=leaders, user_panel=user_panel, date=date,
    )
    if weakest is None:
        return None
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
    key = _observe_transfer(self, weakest=weakest, challenger=challenger, frame=frame,
                            date=date, account=account, broken=broken, edge=edge)
    if _transfer_sell_filled(account, key, date):
        if diagnostics is not None:
            diagnostics["block"] = "TRANSFER_SETTLED_AWAIT_ADMISSION"
        return None
    held_sessions = len(frame.loc[pd.Timestamp(position.entry_date) : date]) if position.entry_date else 0
    if (
        account.replacement_tenure[key] < self.cfg.replacement_confirm_days
        or held_sessions < self.cfg.min_hold_days
    ):
        return None
    remaining = max(0.0, proposed.get(weakest, 0.0) - self.cfg.replacement_transfer_cap)
    released = weights_now[weakest] - remaining
    detail = diagnostics if diagnostics is not None else {}
    detail.update(released_weight=released, required_weight=self.cfg.core_admission_weight)
    if released + 1e-12 < self.cfg.min_trade_weight:
        detail["block"] = "TRANSFER_BELOW_TRADE_MINIMUM"
        return None
    # This is an optimistic feasibility check, not spendable proceeds. Even
    # a complete fill must resolve the cash, risk and concentration shortfall.
    feasible = funded_increment(
        cfg=self.cfg, symbol=challenger, desired=self.cfg.core_admission_weight,
        current=weights_now.get(challenger, 0.0),
        committed={**committed, weakest: remaining}, cash_room=cash_room + released,
        leaders=leaders, user_panel=user_panel, date=date, gross_cap=gross_cap,
        diagnostics=detail,
    )
    if feasible + 1e-12 < self.cfg.core_admission_weight:
        detail["block"] = "TRANSFER_CANNOT_FUND_ADMISSION"
        return None
    detail["block"] = "FEASIBLE_AFTER_SETTLEMENT"
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
    account.candidate_tenure[key.replace("core_transfer:", "core_transfer_session:", 1)] = date.toordinal()
    for observed in account.replacement_tenure:
        if observed.startswith("core_transfer:") and observed != key:
            account.replacement_tenure[observed] = 0
    return weakest


@dataclass
class _AllocationBook:
    """One daily working book shared by every sequential allocation step."""

    policy: PortfolioAllocator
    date: pd.Timestamp
    risk: RiskAssessment
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    prices: dict[str, float]
    weights_now: dict[str, float]
    owned: set[str]
    strategic_targets: dict[str, Target]
    proposed: dict[str, float]
    committed: dict[str, float]
    cash_room: float
    reasons: dict[str, str] = field(default_factory=dict)
    mechanisms: dict[str, AttributionMechanism] = field(default_factory=dict)
    replacements: dict[str, str] = field(default_factory=dict)
    trace: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def gross_cap(self) -> float:
        return min(self.policy.cfg.max_gross, self.risk.target_gross_cap)

    def record(self, symbol: str) -> dict[str, Any]:
        row = self.trace.setdefault(symbol, {})
        row.setdefault("held_weight", self.weights_now.get(symbol, 0.0))
        return row

    def fund(
        self, symbol: str, desired: float, *, phase: str, minimum: float = 0.0,
        symbol_cap: float | None = None, concentration_cap: float | None = None,
    ) -> bool:
        current = self.weights_now.get(symbol, 0.0)
        reserved = max(0.0, self.committed.get(symbol, 0.0) - current)
        diagnostic: dict[str, Any] = {"phase": phase, "minimum_increment": minimum}
        increment = funded_increment(
            cfg=self.policy.cfg, symbol=symbol, desired=desired, current=current,
            committed=self.committed, cash_room=self.cash_room, leaders=self.leaders,
            user_panel=self.user_panel, date=self.date, gross_cap=self.gross_cap,
            symbol_cap=symbol_cap, concentration_cap=concentration_cap, diagnostics=diagnostic,
        )
        row = self.record(symbol)
        row.setdefault("budget_checks", []).append(diagnostic)
        accepted = increment > 0 and increment + 1e-12 >= minimum
        diagnostic["accepted"] = accepted
        row["allocation_reason"] = phase if accepted else "CAPITAL_LIMIT"
        if accepted:
            self.proposed[symbol] = current + increment
            self.committed[symbol] = max(self.committed.get(symbol, 0.0), self.proposed[symbol])
            self.cash_room -= max(0.0, increment - reserved)
        return accepted


def _prepare_account(self: PortfolioAllocator, *, risk: RiskAssessment,
                     account: AccountState, weights_now: dict[str, float]) -> None:
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


def _fund_strategic_owners(book: _AllocationBook, *, frozen: bool,
                           bounded_restore: bool, unresolved: bool) -> None:
    grant = book.account.strategic_grant
    blocked = bool(grant is not None and (grant.status in {"EXPIRED", "CANCELLED"}
                   or (grant.status != "ACTIVE" and book.account.strategic_qualification.deployment_blocked)))
    founding_cap = (
        book.policy.cfg.max_gross
        if grant is not None and grant.qualification_quorum == "FULL_COHORT"
        and not {s for s, weight in book.committed.items() if weight > 0} - book.owned
        else None
    )
    for symbol in sorted(book.strategic_targets, key=lambda s: (s != (grant.candidate_symbol if grant else ""), s)):
        row = book.record(symbol)
        row["allocation_reason"] = "STRATEGIC_OWNER"
        if blocked or (frozen and not bounded_restore) or unresolved:
            row["increase_block"] = "UNRESOLVED_LIABILITY" if unresolved else "OWNER_DEPLOYMENT_BLOCK" if blocked else "NEW_RISK_FROZEN"
            continue
        desired = book.strategic_targets[symbol].weight
        if desired <= book.weights_now.get(symbol, 0.0):
            continue
        if symbol not in book.leaders or symbol not in book.user_panel:
            row["increase_block"] = "OWNER_EVIDENCE_UNAVAILABLE"
            continue
        book.fund(symbol, desired, phase="STRATEGIC_FUNDING",
                  symbol_cap=symbol_weight_cap(book.policy.cfg, book.account, symbol),
                  concentration_cap=founding_cap)


def _ordinary_exits(book: _AllocationBook) -> None:
    account = book.account
    for symbol, position in account.positions.items():
        if symbol in book.owned or position.shares <= 0:
            continue
        book.record(symbol)["allocation_reason"] = "RETAINED_HOLDING"
        if not book.policy._leader_lifecycle_exit_confirmed(
            symbol=symbol, date=book.date, user_panel=book.user_panel,
            leaders=book.leaders, account=account,
        ):
            continue
        book.proposed[symbol] = 0.0
        reset_strategic_candidate_eligibility(account=account, symbol=symbol)
        for rights in (account.protected_weights, account.strategic_restore_weights, account.anchor_weights):
            rights.pop(symbol, None)
        if account.tactical_anchor_symbol == symbol:
            account.tactical_anchor_symbol = ""
            account.candidate_tenure["tactical_active"] = 0
            account.candidate_tenure["tactical_promotable"] = 0
        if account.recovery_conviction_symbol == symbol:
            account.recovery_conviction_symbol = ""
        book.reasons[symbol] = "leader lifecycle exit: confirmed structural deterioration"
        book.mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_EXIT
        book.record(symbol)["allocation_reason"] = "CONFIRMED_STRUCTURAL_EXIT"


def _pending_intents(book: _AllocationBook, *, candidates: list[str], buy_open: bool,
                     certificates: dict[str, dict[str, Any]] | None = None) -> None:
    for order in book.account.pending_orders:
        if order.side == "SELL":
            book.proposed[order.symbol] = min(book.proposed.get(order.symbol, 0.0), order.target_weight)
            book.record(order.symbol)["allocation_reason"] = "PENDING_REDUCTION"
        elif (buy_open and order.symbol not in book.owned
              and order.mechanism != AttributionMechanism.POST_SHOCK_RESTORATION.value):
            if order.symbol not in candidates:
                current = book.weights_now.get(order.symbol, 0.0)
                if (current <= 0 or book.proposed.get(order.symbol, 0.0) < current
                        or order.symbol not in book.leaders):
                    book.record(order.symbol)["pending_buy_rejected"] = True
                    continue
                evidence = _candidate_entry(
                    book.policy, symbol=order.symbol, score=book.leaders[order.symbol],
                    date=book.date, user_panel=book.user_panel, account=book.account,
                    confirmation_days=1,
                    certificate=(certificates or {}).get(order.symbol),
                )
                book.record(order.symbol)["pending_entry"] = evidence
                if evidence["block"] != "READY":
                    book.record(order.symbol)["pending_buy_rejected"] = True
                    continue
            book.fund(order.symbol, order.target_weight, phase="PENDING_CORE_BUY")


def _restore_ordinary_holdings(book: _AllocationBook) -> None:
    account, cfg = book.account, book.policy.cfg
    episode = pd.Timestamp(account.last_shock_date).toordinal() if account.last_shock_date else 0
    for symbol, desired in sorted(account.protected_weights.items()):
        if symbol in book.owned:
            continue
        row = book.record(symbol)
        if book.weights_now.get(symbol, 0.0) <= 0:
            row["restore_block"] = "NEW_ENTRY_REQUIRES_QUALIFICATION"
            continue
        if not holding_spans_date(account, symbol, account.last_shock_date):
            row["restore_block"] = "RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING"
            continue
        pending_buy = next((order for order in account.pending_orders
                            if order.symbol == symbol and order.side == "BUY"), None)
        if pending_buy is not None and pending_buy.mechanism != AttributionMechanism.POST_SHOCK_RESTORATION.value:
            row["restore_block"] = "PENDING_CORE_BUY_ALREADY_EVALUATED"
            continue
        marker = f"core_restored:{symbol}"
        if symbol not in book.user_panel or symbol not in book.leaders:
            row["restore_block"] = "RESTORATION_EVIDENCE_UNAVAILABLE"
            continue
        if book.mechanisms.get(symbol) is AttributionMechanism.LEADER_LIFECYCLE_EXIT:
            continue
        if account.candidate_tenure.get(marker, -1) == episode:
            row["restore_block"] = "RESTORATION_COMPLETED_RETAIN_DRIFT"
            continue
        if not book.policy._structure_ok(book.user_panel[symbol], book.date):
            row["restore_block"] = "STRUCTURE_NOT_REPAIRED"
            continue
        current = book.weights_now.get(symbol, 0.0)
        wanted = min(cfg.max_symbol_weight, desired)
        pending = pending_buy is not None
        if not pending and wanted - current + 1e-12 < cfg.protected_restore_min_trade_weight:
            account.candidate_tenure[marker] = episode
            row["restore_block"] = "RESTORATION_COMPLETED_RETAIN_DRIFT"
            continue
        if book.fund(symbol, wanted, phase="POST_SHOCK_RESTORATION",
                     minimum=0.0 if pending else cfg.protected_restore_min_trade_weight):
            book.mechanisms[symbol] = AttributionMechanism.POST_SHOCK_RESTORATION
            book.reasons[symbol] = "core restoration after account risk repair"


def _record_completed_transfer(book: _AllocationBook, symbol: str) -> None:
    transfers = [key for key, tenure in book.account.replacement_tenure.items()
                 if key.startswith("core_transfer:") and key.endswith("->" + symbol)
                 and tenure >= book.policy.cfg.replacement_confirm_days
                 and _transfer_sell_filled(book.account, key, book.date)]
    if transfers:
        transfer = sorted(transfers)[0]
        book.replacements[symbol] = transfer.split(":", 1)[1].split("->", 1)[0]
        book.mechanisms[symbol] = AttributionMechanism.LEADER_ROTATION
        book.reasons[symbol] = "leader rotation after the prior reduction filled"
        book.account.replacement_tenure[transfer] = 0


def _admit_new_cores(book: _AllocationBook, *, candidates: list[str], opportunity: Opportunity) -> None:
    if opportunity not in {Opportunity.TREND, Opportunity.STRONG_TREND} or book.risk.state is not Risk.NORMAL:
        block = "OPPORTUNITY_NOT_OPEN" if opportunity not in {Opportunity.TREND, Opportunity.STRONG_TREND} else "RISK_NOT_NORMAL"
        for symbol in candidates:
            book.record(symbol)["entry_gate"] = block
        return
    for symbol in candidates:
        if symbol in book.owned or book.weights_now.get(symbol, 0.0) > 0 or book.committed.get(symbol, 0.0) > 0:
            book.record(symbol)["entry_gate"] = "EXISTING_HOLDING_OR_COMMITMENT"
            continue
        weight = book.policy.cfg.core_admission_weight
        if book.fund(symbol, weight, phase="CORE_ADMISSION", minimum=weight):
            book.account.protected_weights.pop(symbol, None)
            book.reasons[symbol] = "confirmed core admitted from available account capital"
            _record_completed_transfer(book, symbol)
            continue
        weak = _degraded_transfer(
            book.policy, challenger=symbol, proposed=book.proposed, weights_now=book.weights_now,
            leaders=book.leaders, user_panel=book.user_panel, date=book.date, account=book.account,
            committed=book.committed, cash_room=book.cash_room, gross_cap=book.gross_cap,
            diagnostics=book.record(symbol).setdefault("transfer_budget", {}),
        )
        if weak is not None:
            book.reasons[weak] = "leader rotation: bounded transfer after confirmed deterioration"
            book.mechanisms[weak] = AttributionMechanism.LEADER_ROTATION
            book.record(weak)["allocation_reason"] = "CONFIRMED_BOUNDED_TRANSFER"
            book.record(symbol)["entry_gate"] = "AWAIT_REDUCTION_SETTLEMENT"
            break


def _retained_order_identity(book: _AllocationBook, target: Target) -> Target:
    current = book.weights_now.get(target.symbol, 0.0)
    side = "BUY" if target.weight > current + 1e-12 else "SELL"
    retained = next((order for order in book.account.pending_orders
                     if order.side == side and order.symbol == target.symbol), None)
    if retained is None:
        return target
    continued = (
        target.weight + 1e-12 >= retained.target_weight
        and abs(target.weight - retained.target_weight) < book.policy.cfg.min_trade_weight
    ) if side == "BUY" else (
        target.weight < current - 1e-12 and abs(target.weight - retained.target_weight) <= 1e-12
    )
    if not continued:
        return target
    return replace(target, **{name: getattr(retained, name) for name in (
        "lifecycle", "reason", "reduction_policy", "reason_code", "exit_kind", "event_id",
        "origin_subsystem", "mechanism", "origin_lifecycle", "replaces_symbol",
        "industry_at_entry", "industry_manifest_sha256", "grant_id", "epoch_id",
    )})


def _book_targets(book: _AllocationBook) -> tuple[Target, ...]:
    book.account.active_leaders = sorted(s for s, weight in book.proposed.items() if weight > 0 and s not in book.owned)
    targets = book.policy._targets(
        proposed=book.proposed, leaders=book.leaders, account=book.account,
        lifecycle=Lifecycle.CORE, reason="retained core holding",
        origin_subsystem=OriginSubsystem.LEADER, mechanism=AttributionMechanism.LEADER_SELECTION,
        reasons=book.reasons, mechanisms=book.mechanisms, replaces_symbols=book.replacements,
    )
    merged = []
    for target in targets:
        if target.symbol in book.strategic_targets:
            strategic = book.strategic_targets[target.symbol]
            target = replace(strategic, weight=target.weight,
                             reason=book.reasons.get(target.symbol, strategic.reason),
                             mechanism=book.mechanisms[target.symbol].value
                             if target.symbol in book.mechanisms else strategic.mechanism)
        if target.mechanism == AttributionMechanism.POST_SHOCK_RESTORATION.value:
            target = replace(target, origin_subsystem=OriginSubsystem.RECOVERY.value)
        merged.append(_retained_order_identity(book, target))
    return tuple(merged)


def _allocate_strategy(
    self: PortfolioAllocator, *, date: pd.Timestamp, opportunity: Opportunity,
    risk: RiskAssessment, user_panel: dict[str, pd.DataFrame], leaders: dict[str, LeaderScore],
    account: AccountState, prices: dict[str, float],
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> tuple[Target, ...]:
    """Retain each filled owner, then allocate only available common capital."""
    risk.evidence.pop("core_allocation", None)
    weights_now, _ = current_weights(account, prices)
    _prepare_account(self, risk=risk, account=account, weights_now=weights_now)
    frozen = (risk.freeze_new_risk or bool(risk.evidence.get("freeze_new_risk", False))
              or risk.state in {Risk.RISK_OFF, Risk.CRISIS})
    owned = (set(account.strategic_cohort_symbols)
             | {s for s, position in account.positions.items() if position.grant_id or position.epoch_id}
             | {order.symbol for order in account.pending_orders if order.grant_id or order.epoch_id})
    strategic = self._strategic_cohort_targets(
        date=date, risk=risk, user_panel=user_panel, leaders=leaders, account=account,
        prices=prices, weights_now=weights_now,
        admission_open=not frozen and risk.state is Risk.NORMAL
        and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND},
        qualification_panel=qualification_panel, qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    owned.update(account.strategic_cohort_symbols)
    strategic_targets = {target.symbol: target for target in strategic or () if target.symbol in owned}
    proposed = dict(weights_now)
    proposed.update({s: min(weights_now.get(s, 0.0), target.weight) for s, target in strategic_targets.items()})
    committed, cash_room = committed_capital(account=account, prices=prices, proposed=proposed)
    book = _AllocationBook(self, date, risk, user_panel, leaders, account, prices, weights_now,
                           owned, strategic_targets, proposed, committed, cash_room)
    bounded_restore = self._bounded_strategic_restore_risk_open(risk=risk, account=account) or strategic_cash_rearm_grant_open(
        account=account, risk=risk, cfg=self.cfg)
    liabilities = assess_strategic_capital_authority(account).late_fill_order_ids
    _fund_strategic_owners(book, frozen=frozen, bounded_restore=bounded_restore, unresolved=bool(liabilities))
    certificates = current_core_qualification(
        self, date=date, user_panel=user_panel, leaders=leaders, account=account, risk=risk,
        qualification_panel=qualification_panel, qualification_leaders=qualification_leaders,
        strategic_universe=strategic_universe,
    )
    candidates = _core_candidates(self, date=date, user_panel=user_panel, leaders=leaders, account=account,
                                  trace=book.trace, certificates=certificates)
    _ordinary_exits(book)
    _pending_intents(book, candidates=candidates, buy_open=not frozen and not liabilities, certificates=certificates)
    if not frozen and not liabilities:
        book.committed, book.cash_room = committed_capital(account=account, prices=prices, proposed=proposed)
        _restore_ordinary_holdings(book)
        _admit_new_cores(book, candidates=candidates, opportunity=opportunity)
    else:
        for symbol in candidates:
            book.record(symbol)["entry_gate"] = "NEW_RISK_FROZEN" if frozen else "UNRESOLVED_LIABILITY"
    targets = _book_targets(book)
    if frozen:
        frozen_targets = self._frozen_existing_targets(
            strategy_targets=targets, leaders=leaders, account=account, weights_now=weights_now)
        permitted = {t.symbol: t for t in targets if t.symbol in owned} if bounded_restore else {}
        frozen_book = {t.symbol: t for t in frozen_targets}
        frozen_book.update(permitted)
        targets = tuple(frozen_book[symbol] for symbol in sorted(frozen_book))
    for symbol in book.trace:
        book.record(symbol)["proposal_weight"] = book.proposed.get(symbol, 0.0)
    risk.evidence["core_allocation"] = {
        "as_of": str(date.date()), "scope": "ALLOCATOR_PROPOSAL", "symbols": book.trace,
        "gross_cap": book.gross_cap, "unreserved_cash_before": cash_room,
        "unreserved_cash_after": book.cash_room, "freeze_new_risk": frozen,
        "late_fill_order_ids": list(liabilities),
    }
    return targets


allocate_strategy = _allocate_strategy
