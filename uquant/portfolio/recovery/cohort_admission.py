"""Recovery-cohort candidate, weighting, and ownership stages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import pandas as pd

from ...features import scalar
from ...types import AccountState, LeaderScore, Risk, RiskAssessment, Target
from .targets import (
    awaiting_recovery_cohort_targets,
    locked_recovery_cohort_targets,
    recovery_cohort_targets,
)

if TYPE_CHECKING:
    from ...portfolio_leaders import LeaderPortfolioPolicy

    type RecoveryPortfolioPolicy = LeaderPortfolioPolicy


class _RecoveryGrossPolicy(Protocol):
    def _confirmed_recovery_gross(
        self,
        *,
        risk: RiskAssessment,
        account: AccountState,
    ) -> float: ...


@dataclass(frozen=True, slots=True)
class RecoverySelection:
    previous_members: set[str]
    selected: list[str]
    candidates: list[LeaderScore]
    crash_depth: dict[str, float]
    recovery_elapsed: int
    lead: str
    secondaries: list[str]


def _locked_cohort_targets(
    self: RecoveryPortfolioPolicy,
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    proposed: dict[str, float],
    bounded_recovery_repair: bool,
) -> tuple[Target, ...] | None:
    if account.candidate_tenure.get("recovery_cohort_locked", 0) != 1:
        return None
    pending_buys = {
        order.symbol
        for order in account.pending_orders
        if order.side == "BUY" and order.symbol in account.anchor_weights
    }
    unfinished = {
        symbol: min(self.cfg.max_symbol_weight, max(0.0, target_weight))
        for symbol, target_weight in account.anchor_weights.items()
        if symbol not in proposed or symbol in pending_buys
    }
    if risk.freeze_new_risk and not bounded_recovery_repair:
        unfinished = {}
    gross_budget = min(
        self.cfg.max_gross,
        cast(_RecoveryGrossPolicy, self)._confirmed_recovery_gross(risk=risk, account=account),
    )
    held_gross = sum(min(self.cfg.max_symbol_weight, max(0.0, weight)) for weight in proposed.values())
    requested = sum(
        max(0.0, target_weight - proposed.get(symbol, 0.0)) for symbol, target_weight in unfinished.items()
    )
    remaining = max(0.0, gross_budget - held_gross)
    scale = min(1.0, remaining / requested) if requested > 0 else 0.0
    proposed.update(
        {
            symbol: proposed.get(symbol, 0.0) + max(0.0, target_weight - proposed.get(symbol, 0.0)) * scale
            for symbol, target_weight in unfinished.items()
            if proposed.get(symbol, 0.0) + max(0.0, target_weight - proposed.get(symbol, 0.0)) * scale > 1e-12
        }
    )
    return locked_recovery_cohort_targets(
        self=self,
        proposed=proposed,
        leaders=leaders,
        account=account,
    )


def _scan_recovery_evidence(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> tuple[list[LeaderScore], dict[str, float]]:
    candidates: list[LeaderScore] = []
    crash_depth: dict[str, float] = {}
    for symbol, score in leaders.items():
        if symbol not in user_panel or date not in user_panel[symbol].index:
            continue
        frame = user_panel[symbol].loc[:date]
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{self.cfg.trend_fast}")
        ret120 = scalar(row, f"ret{self.cfg.trend_slow}", 0.0)
        previous_high = float(frame["close"].iloc[-11:-1].max()) if len(frame) >= 11 else float("nan")
        if (
            math.isfinite(close)
            and math.isfinite(ma20)
            and math.isfinite(previous_high)
            and close >= ma20
            and close >= previous_high
            and ret120 < 0
            and self._liquidity_confirmed(user_panel[symbol], date)
        ):
            candidates.append(score)
            crash_depth[symbol] = ret120
        elif symbol in account.anchor_weights and math.isfinite(ret120):
            crash_depth[symbol] = ret120
    return candidates, crash_depth


def _filter_recovery_candidates(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    candidates: list[LeaderScore],
    crash_depth: dict[str, float],
    level1_recovery_repair: bool,
    risk_neutral_recovery_transfer: bool,
    weak_secular_market: bool,
) -> tuple[list[LeaderScore], int, int, float]:
    recovery_elapsed = 0
    deep_count = sum(value <= -0.30 for value in crash_depth.values())
    admission_depth = (
        -0.15 if deep_count >= 2 or (deep_count >= 1 and bool(account.anchor_weights)) else -0.30
    )
    candidates = [item for item in candidates if crash_depth.get(item.symbol, 0.0) <= admission_depth]
    if (
        weak_secular_market
        and not account.anchor_weights
        and (
            len(candidates) < 2
            or max(
                float(risk.evidence.get("broad_ret60", -math.inf)),
                float(risk.evidence.get("tech_ret60", -math.inf)),
            )
            < self.cfg.recovery_weak_market_min_index_ret60
        )
    ):
        candidates = []
    candidates.sort(key=lambda item: (crash_depth.get(item.symbol, 0.0), -item.score, item.symbol))
    continuous_freeze = bool(risk.evidence.get("freeze_new_risk", False))
    if continuous_freeze and not level1_recovery_repair and not risk_neutral_recovery_transfer:
        candidates = []
    if account.anchor_weights and account.recovery_anchor_date:
        recovery_elapsed = self._session_distance(
            self._session_clock(user_panel, date),
            account.recovery_anchor_date,
            date,
        )
        if recovery_elapsed > self.cfg.recovery_add_window_days:
            candidates = []
    return candidates, recovery_elapsed, deep_count, admission_depth


def _await_recovery_confirmation(
    self: RecoveryPortfolioPolicy,
    *,
    risk: RiskAssessment,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    anchored_held: dict[str, float],
    previous_members: set[str],
    candidate_members: set[str],
    crash_depth: dict[str, float],
    deep_count: int,
    admission_depth: float,
    freeze_active: bool,
) -> tuple[Target, ...] | None:
    independently_deep_empty_entry = bool(
        freeze_active
        and risk.state is Risk.CAUTION
        and not previous_members
        and len(candidate_members) == 1
        and deep_count == 1
        and all(crash_depth.get(symbol, 0.0) <= admission_depth for symbol in candidate_members)
    )
    if (
        candidate_members == previous_members
        or len(candidate_members) >= min(3, self.cfg.max_positions)
        or independently_deep_empty_entry
    ):
        return None
    admission_key = "recovery_admission:" + ",".join(sorted(candidate_members))
    for tenure_key in tuple(account.replacement_tenure):
        if tenure_key.startswith("recovery_admission:") and tenure_key != admission_key:
            account.replacement_tenure[tenure_key] = 0
    account.replacement_tenure[admission_key] = account.replacement_tenure.get(admission_key, 0) + 1
    if account.replacement_tenure[admission_key] < self.cfg.recovery_member_confirm_days:
        return awaiting_recovery_cohort_targets(
            self=self,
            anchored_held=anchored_held,
            leaders=leaders,
            account=account,
        )
    return None


def _recovery_selection(
    self: RecoveryPortfolioPolicy,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    anchored_held: dict[str, float],
    candidates: list[LeaderScore],
    crash_depth: dict[str, float],
    recovery_elapsed: int,
    deep_count: int,
    admission_depth: float,
    risk: RiskAssessment,
    freeze_active: bool,
) -> tuple[RecoverySelection | None, tuple[Target, ...] | None]:
    if not candidates:
        for tenure_key in tuple(account.replacement_tenure):
            if tenure_key.startswith("recovery_admission:"):
                account.replacement_tenure[tenure_key] = 0
        return None, None
    previous_members = set(account.anchor_weights)
    cohort = set(account.anchor_weights) | {item.symbol for item in candidates}
    selected = sorted(
        cohort,
        key=lambda symbol: (
            crash_depth.get(symbol, 0.0),
            -leaders[symbol].score,
            symbol,
        ),
    )[: min(3, self.cfg.max_positions)]
    candidate_members = set(selected)
    targets = _await_recovery_confirmation(
        self,
        risk=risk,
        leaders=leaders,
        account=account,
        anchored_held=anchored_held,
        previous_members=previous_members,
        candidate_members=candidate_members,
        crash_depth=crash_depth,
        deep_count=deep_count,
        admission_depth=admission_depth,
        freeze_active=freeze_active,
    )
    if targets is not None:
        return None, targets
    incumbent_order = [symbol for symbol in account.anchor_weights if symbol in selected]
    lead = incumbent_order[0] if incumbent_order else selected[0]
    secondaries = [symbol for symbol in selected if symbol != lead]
    return (
        RecoverySelection(
            previous_members=previous_members,
            selected=selected,
            candidates=candidates,
            crash_depth=crash_depth,
            recovery_elapsed=recovery_elapsed,
            lead=lead,
            secondaries=secondaries,
        ),
        None,
    )


def _initial_cohort_weights(
    self: RecoveryPortfolioPolicy,
    *,
    selection: RecoverySelection,
) -> dict[str, float]:
    lead = selection.lead
    secondaries = selection.secondaries
    proposed = {
        lead: min(
            self.cfg.max_symbol_weight,
            self.cfg.tactical_rebound_weight,
            self.cfg.recovery_target_gross,
        )
    }
    if len(secondaries) == 1:
        proposed[secondaries[0]] = min(
            0.20,
            max(0.0, self.cfg.recovery_target_gross - proposed[lead]),
        )
    return proposed


def _multi_secondary_weights(
    self: RecoveryPortfolioPolicy,
    *,
    risk: RiskAssessment,
    account: AccountState,
    selection: RecoverySelection,
    proposed: dict[str, float],
) -> dict[str, float]:
    lead = selection.lead
    secondaries = selection.secondaries
    if len(secondaries) < 2:
        return proposed
    independent_recovery_breadth = bool(
        int(risk.evidence.get("risk_anchor_group_count", 0)) >= self.cfg.risk_anchor_min_groups
    )
    if selection.previous_members or not independent_recovery_breadth:
        secondary_weight = max(
            0.0,
            self.cfg.recovery_target_gross - proposed[lead],
        ) / len(secondaries)
        proposed.update({symbol: secondary_weight for symbol in secondaries})
        return proposed
    cohort_gross = cast(_RecoveryGrossPolicy, self)._confirmed_recovery_gross(risk=risk, account=account)
    ambiguous_empty_cohort = bool(
        not selection.previous_members and len(selection.candidates) > len(selection.selected)
    )
    if ambiguous_empty_cohort:
        cohort_gross = min(cohort_gross, self.cfg.recovery_expansive_universe_gross)
    if self.cfg.recovery_conviction_weighting_enabled:
        lead_weight = min(
            self.cfg.max_symbol_weight,
            self.cfg.tactical_rebound_weight,
            cohort_gross * self.cfg.tactical_rebound_weight / self.cfg.recovery_target_gross,
        )
        secondary_weight = max(0.0, cohort_gross - lead_weight) / len(secondaries)
        return {
            lead: lead_weight,
            **{symbol: secondary_weight for symbol in secondaries},
        }
    member_weight = cohort_gross / len(selection.selected)
    return {symbol: member_weight for symbol in selection.selected}


def _late_pair_weights(
    self: RecoveryPortfolioPolicy,
    *,
    account: AccountState,
    selection: RecoverySelection,
    proposed: dict[str, float],
) -> None:
    secondaries = selection.secondaries
    if (
        len(secondaries) == 1
        and account.recovery_anchor_date
        and selection.recovery_elapsed > self.cfg.recovery_add_window_days
    ):
        proposed[secondaries[0]] = max(
            proposed[secondaries[0]],
            self.cfg.recovery_target_gross - proposed[selection.lead],
        )
        account.candidate_tenure["confirmed_anchor_pair"] = 1


def _transfer_recovery_owner(
    self: RecoveryPortfolioPolicy,
    *,
    risk: RiskAssessment,
    account: AccountState,
    weights_now: dict[str, float],
    proposed: dict[str, float],
    risk_neutral_recovery_handoff: bool,
    risk_neutral_recovery_transfer: bool,
) -> tuple[dict[str, float], dict[str, float]]:
    owner_targets = dict(proposed)
    if risk_neutral_recovery_transfer:
        handoff_gross = min(
            sum(max(0.0, weight) for weight in weights_now.values()),
            self.cfg.max_gross,
            max(0.0, risk.target_gross_cap),
        )
        requested_gross = sum(max(0.0, weight) for weight in proposed.values())
        if requested_gross > 0:
            scale = min(1.0, handoff_gross / requested_gross)
            proposed = {
                symbol: max(0.0, weight) * scale
                for symbol, weight in proposed.items()
                if max(0.0, weight) * scale > 1e-12
            }
            if risk_neutral_recovery_handoff:
                account.protected_weights.clear()
                account.candidate_tenure["post_shock_restore_complete"] = 0
                account.candidate_tenure["post_shock_restore_submitted"] = 0
            account.candidate_tenure["recovery_owner_handoff"] = 1
    return owner_targets, proposed


def _commit_recovery_cohort(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    account: AccountState,
    selection: RecoverySelection,
    owner_targets: dict[str, float],
) -> bool:
    account.anchor_weights = owner_targets
    if self.cfg.recovery_conviction_weighting_enabled:
        account.recovery_conviction_symbol = selection.lead
    account.candidate_tenure["recovery_cohort_graduated"] = 0
    if len(selection.selected) == 2 and all(
        selection.crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selection.selected
    ):
        account.candidate_tenure["confirmed_anchor_pair"] = 1
    if len(selection.selected) == min(3, self.cfg.max_positions) and all(
        selection.crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selection.selected
    ):
        account.candidate_tenure["recovery_cohort_locked"] = 1
    if not account.recovery_anchor_date:
        account.recovery_anchor_date = str(date.date())
        account.candidate_tenure["recovery_reserve_qualified"] = 0
        account.candidate_tenure["recovery_substitution_pending"] = 0
        account.candidate_tenure["recovery_substitution_completed"] = 0
    return set(selection.selected) != selection.previous_members


def _cohort_targets(
    self: RecoveryPortfolioPolicy,
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    proposed: dict[str, float],
    recovery_elapsed: int,
    cohort_changed: bool,
) -> tuple[Target, ...] | None:
    if not proposed:
        return None
    if not cohort_changed:
        for symbol in account.anchor_weights:
            if weights_now.get(symbol, 0.0) > 0:
                proposed[symbol] = weights_now[symbol]
    capped = False
    if recovery_elapsed > self.cfg.recovery_add_window_days:
        proposed, capped = self._cap_underdiversified(proposed, account)
    return recovery_cohort_targets(
        self=self,
        proposed=proposed,
        leaders=leaders,
        account=account,
        capped=capped,
        cohort_changed=cohort_changed,
    )


def cohort_admission_targets(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    anchored_held: dict[str, float],
    bounded_recovery_repair: bool,
    freeze_active: bool,
    level1_recovery_repair: bool,
    risk_neutral_recovery_handoff: bool,
    risk_neutral_recovery_transfer: bool,
    weak_secular_market: bool,
) -> tuple[Target, ...] | None:
    """Evaluate locked, candidate, weighting, and commit stages in order."""

    proposed = dict(anchored_held)
    locked = _locked_cohort_targets(
        self,
        risk=risk,
        leaders=leaders,
        account=account,
        proposed=proposed,
        bounded_recovery_repair=bounded_recovery_repair,
    )
    if locked is not None:
        return locked
    candidates, crash_depth = _scan_recovery_evidence(
        self,
        date=date,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
    )
    candidates, recovery_elapsed, deep_count, admission_depth = _filter_recovery_candidates(
        self,
        date=date,
        risk=risk,
        user_panel=user_panel,
        account=account,
        candidates=candidates,
        crash_depth=crash_depth,
        level1_recovery_repair=level1_recovery_repair,
        risk_neutral_recovery_transfer=risk_neutral_recovery_transfer,
        weak_secular_market=weak_secular_market,
    )
    selection, targets = _recovery_selection(
        self,
        leaders=leaders,
        account=account,
        anchored_held=anchored_held,
        candidates=candidates,
        crash_depth=crash_depth,
        recovery_elapsed=recovery_elapsed,
        deep_count=deep_count,
        admission_depth=admission_depth,
        risk=risk,
        freeze_active=freeze_active,
    )
    cohort_changed = False
    if targets is not None:
        return targets
    if selection is not None:
        proposed = _initial_cohort_weights(self, selection=selection)
        proposed = _multi_secondary_weights(
            self,
            risk=risk,
            account=account,
            selection=selection,
            proposed=proposed,
        )
        _late_pair_weights(self, account=account, selection=selection, proposed=proposed)
        owner_targets, proposed = _transfer_recovery_owner(
            self,
            risk=risk,
            account=account,
            weights_now=weights_now,
            proposed=proposed,
            risk_neutral_recovery_handoff=risk_neutral_recovery_handoff,
            risk_neutral_recovery_transfer=risk_neutral_recovery_transfer,
        )
        cohort_changed = _commit_recovery_cohort(
            self,
            date=date,
            account=account,
            selection=selection,
            owner_targets=owner_targets,
        )
    return _cohort_targets(
        self,
        leaders=leaders,
        account=account,
        weights_now=weights_now,
        proposed=proposed,
        recovery_elapsed=recovery_elapsed,
        cohort_changed=cohort_changed,
    )
