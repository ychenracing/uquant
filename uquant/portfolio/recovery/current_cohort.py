"""Confirmed recovery ownership inside the existing daily capital book."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

from ...holding_history import recovery_owner_open
from ...risk.pullback import pullback_book_settled
from ...types import (
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    PendingOrder,
    Risk,
    Target,
)
from .cohort_admission import cohort_admission_targets, scan_recovery_evidence

if TYPE_CHECKING:
    from ..allocation_book import AllocationBook


def _filled_members(book: AllocationBook) -> set[str]:
    """An anchor label alone cannot borrow another position's holding rights."""
    return {
        symbol for symbol, position in book.account.positions.items()
        if symbol in book.account.anchor_weights and position.shares > 0
        and not position.grant_id and not position.epoch_id
        and recovery_owner_open(book.account, symbol)
    }


def _retain_members(book: AllocationBook, members: set[str], *, graduated: bool = False) -> None:
    targets = book.policy._targets(
        proposed={symbol: book.weights_now.get(symbol, 0.0) for symbol in members},
        leaders=book.leaders, account=book.account,
        lifecycle=Lifecycle.CORE if graduated else Lifecycle.RECOVERY,
        reason="graduated recovery cohort; retain price drift" if graduated else "mature anchored leader",
        origin_subsystem=OriginSubsystem.RECOVERY, mechanism=AttributionMechanism.RECOVERY_COHORT,
    )
    book.recovery_targets.update({target.symbol: target for target in targets if target.symbol in members})


def _graduate(book: AllocationBook, members: set[str], opportunity: Opportunity, weak: bool) -> bool:
    account, policy = book.account, book.policy
    if (not members or not account.recovery_anchor_date
            or opportunity not in {Opportunity.CHOPPY, Opportunity.TREND, Opportunity.STRONG_TREND}
            or book.risk.state not in {Risk.NORMAL, Risk.CAUTION}):
        return False
    elapsed = policy._session_distance(policy._session_clock(book.user_panel, book.date),
                                       account.recovery_anchor_date, book.date)
    duration = policy.cfg.recovery_cohort_weak_graduation_days if weak else policy.cfg.recovery_cohort_graduation_days
    if elapsed < duration:
        return False
    policy._release_recovery_anchor(account)
    for symbol in members:
        position = account.positions[symbol]
        position.lifecycle = Lifecycle.CORE.value
        for tranche in position.tranches:
            tranche.lifecycle = Lifecycle.CORE.value
    _retain_members(book, members, graduated=True)
    return True


def _observation_is_new(book: AllocationBook) -> bool:
    """The original member confirmation uses real consecutive observations."""
    account = book.account
    key = "confirmed_recovery_observed_session"
    current = book.date.toordinal()
    previous = account.candidate_tenure.get(key, 0)
    if previous > current:
        raise RuntimeError("recovery observation session moved backwards")
    if previous == current:
        return False
    clock = book.policy._session_clock(book.user_panel, book.date)
    earlier = clock[clock < book.date]
    prior_session = earlier[-1].toordinal() if len(earlier) else 0
    if previous != prior_session:
        for name in account.replacement_tenure:
            if name.startswith("recovery_admission:"):
                account.replacement_tenure[name] = 0
    account.candidate_tenure[key] = current
    return True


def _pending_members(book: AllocationBook) -> list[PendingOrder]:
    account = book.account
    return [order for order in account.pending_orders
               if order.side == "BUY" and order.mechanism == AttributionMechanism.RECOVERY_COHORT.value
               and order.origin_subsystem == OriginSubsystem.RECOVERY.value
               and order.symbol in account.anchor_weights and not order.grant_id and not order.epoch_id
               and order.order_id and order.event_id
               and any(record.order_id == order.order_id and record.event_id == order.event_id
                       and record.symbol == order.symbol and record.side == "BUY"
                       for record in account.order_ledger)]


def _book_available(book: AllocationBook, members: set[str], restorable: set[str],
                    pending: list[PendingOrder]) -> bool:
    live = {symbol for symbol, weight in book.weights_now.items() if weight > 0}
    if book.owned or live - members:
        return False
    return bool(members or pending or restorable or pullback_book_settled(book.account))


def _prune_anchors(book: AllocationBook, known: set[str]) -> None:
    account, policy = book.account, book.policy
    if account.anchor_weights:
        account.anchor_weights = {symbol: weight for symbol, weight in account.anchor_weights.items() if symbol in known}
        account.candidate_tenure["recovery_cohort_locked"] = int(len(known) >= min(3, policy.cfg.max_positions))


def _weak_market(book: AllocationBook) -> bool | None:
    evidence = [book.risk.evidence.get(key) for key in ("broad_ret120", "tech_ret120")]
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)
           for value in evidence):
        return None
    return max(cast(float, value) for value in evidence) <= book.policy.cfg.recovery_cohort_weak_market_ret120


def _actual_leaders(book: AllocationBook) -> dict[str, LeaderScore]:
    return {symbol: score for symbol, score in book.leaders.items()
               if symbol in book.user_panel and book.date in book.user_panel[symbol].index
               and score.industry not in {"", "unknown"} and score.confidence >= book.policy.cfg.leader_min_confidence}


def _continue_members(book: AllocationBook, pending: list[PendingOrder], members: set[str],
                      leaders: dict[str, LeaderScore], *, frozen: bool) -> None:
    policy, account = book.policy, book.account
    candidates, _ = scan_recovery_evidence(policy, date=book.date, user_panel=book.user_panel,
                                           leaders=leaders, account=account)
    current = {score.symbol for score in candidates}
    for order in pending:
        book.record(order.symbol)["recovery_pending_evaluated"] = True
        if not frozen and order.symbol in current and book.fund(
            order.symbol, order.target_weight, phase="RECOVERY_COHORT_REMAINDER",
            concentration_cap=policy.cfg.recovery_target_gross,
        ):
            retained_targets = policy._targets(
                proposed={order.symbol: book.proposed[order.symbol]}, leaders=book.leaders,
                account=account, lifecycle=Lifecycle.RECOVERY, reason=order.reason,
                origin_subsystem=OriginSubsystem.RECOVERY, mechanism=AttributionMechanism.RECOVERY_COHORT,
            )
            book.recovery_targets.update({item.symbol: item for item in retained_targets if item.symbol == order.symbol})
        else:
            book.proposed[order.symbol] = book.weights_now.get(order.symbol, 0.0)
            if order.symbol not in members:
                account.anchor_weights.pop(order.symbol, None)
    if not account.anchor_weights:
        policy._release_recovery_anchor(account)


def _fund_members(book: AllocationBook, targets: tuple[Target, ...], members: set[str],
                   leaders: dict[str, LeaderScore]) -> None:
    account, policy = book.account, book.policy
    funded = set(members)
    for target in targets:
        if target.symbol not in leaders or target.weight <= book.weights_now.get(target.symbol, 0.0):
            continue
        if book.fund(target.symbol, target.weight, phase="CONFIRMED_RECOVERY_ADMISSION",
                     minimum=policy.cfg.min_trade_weight, concentration_cap=policy.cfg.recovery_target_gross):
            funded.add(target.symbol)
            book.recovery_targets[target.symbol] = target
    if account.anchor_weights:
        _prune_anchors(book, funded)
        if not funded:
            policy._release_recovery_anchor(account)


def allocate_confirmed_recovery(book: AllocationBook, *, opportunity: Opportunity, frozen: bool) -> bool:
    """Return whether this session belongs to an actual confirmed recovery book."""
    account, policy, risk = book.account, book.policy, book.risk
    members = _filled_members(book)
    restorable = {symbol for symbol, weight in account.protected_weights.items()
                  if weight > 0 and recovery_owner_open(account, symbol, account.last_shock_date)}
    pending = _pending_members(book)
    if not _book_available(book, members, restorable, pending):
        return False
    _prune_anchors(book, members | restorable | {order.symbol for order in pending})
    book.recovery_restore_symbols = restorable
    weak = _weak_market(book)
    if weak is None:
        _retain_members(book, members | restorable)
        return bool(members or restorable)
    if _graduate(book, members, opportunity, weak):
        return True
    _retain_members(book, members | restorable)
    leaders = _actual_leaders(book)
    if pending:
        _continue_members(book, pending, members, leaders, frozen=frozen)
        return True
    if restorable:
        return True  # Saved risk rights use the shared restoration checks, never fresh admission.
    if frozen or opportunity is not Opportunity.RECOVERY:
        return bool(members or restorable)
    if not _observation_is_new(book):
        return bool(members or restorable)
    targets = cohort_admission_targets(
        policy, date=book.date, risk=risk, user_panel=book.user_panel, leaders=leaders,
        account=account, weights_now=book.weights_now,
        anchored_held={symbol: book.weights_now[symbol] for symbol in members},
        bounded_recovery_repair=False, freeze_active=False, level1_recovery_repair=False,
        risk_neutral_recovery_handoff=False, risk_neutral_recovery_transfer=False,
        weak_secular_market=weak,
    )
    if targets is None:
        return bool(members or restorable)
    _fund_members(book, targets, members, leaders)
    return True
