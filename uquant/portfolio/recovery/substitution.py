"""Recovery-anchor substitution with unchanged confirmation and transfer rules."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pandas as pd

from ...features import scalar
from ...leader import credible_recovery_reserve
from ...types import AccountState, LeaderScore, RiskAssessment, Target
from .targets import (
    confirmed_recovery_substitution_targets as _confirmed_recovery_substitution_targets,
)
from .targets import (
    pending_recovery_substitution_targets as _pending_recovery_substitution_targets,
)

if TYPE_CHECKING:
    from .admission import RecoveryPortfolioPolicy


@dataclass(slots=True)
class _SubstitutionContext:
    policy: RecoveryPortfolioPolicy
    date: pd.Timestamp
    risk: RiskAssessment
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    weights_now: dict[str, float]
    anchor_elapsed: int
    risk_neutral_only: bool


def _reset_substitution_streaks(
    account: AccountState,
    *,
    keep: str = "",
    keep_handoff: str = "",
) -> None:
    for key in tuple(account.replacement_tenure):
        if key.startswith("recovery_substitution:") and key != keep:
            account.replacement_tenure[key] = 0
    for key in tuple(account.candidate_tenure):
        if key.startswith("recovery_substitution_handoff:") and key != keep_handoff:
            account.candidate_tenure[key] = 0


def _pending_substitution(ctx: _SubstitutionContext) -> tuple[bool, tuple[Target, ...] | None]:
    if ctx.account.candidate_tenure.get("recovery_substitution_pending", 0) != 1:
        return False, None
    missing = {
        symbol: weight
        for symbol, weight in ctx.account.anchor_weights.items()
        if ctx.weights_now.get(symbol, 0.0) <= 0
    }
    if not missing:
        ctx.account.candidate_tenure["recovery_substitution_pending"] = 0
        return False, None
    _reset_substitution_streaks(ctx.account)
    proposed = {
        symbol: ctx.weights_now.get(symbol, 0.0)
        for symbol in ctx.account.anchor_weights
        if ctx.weights_now.get(symbol, 0.0) > 0
    }
    proposed.update(missing)
    structured: dict[str, str] = {}
    for event in reversed(ctx.account.replacement_events):
        if event.get("route") != "recovery_anchor_substitution":
            continue
        new_symbol = event.get("new_symbol")
        old_symbol = event.get("old_symbol")
        if (
            isinstance(new_symbol, str)
            and isinstance(old_symbol, str)
            and new_symbol in missing
            and new_symbol not in structured
        ):
            structured[new_symbol] = old_symbol
    return True, _pending_recovery_substitution_targets(
        self=ctx.policy,
        proposed=proposed,
        leaders=ctx.leaders,
        account=ctx.account,
        structured_replacements=structured,
    )


def _substitution_eligible(ctx: _SubstitutionContext) -> bool:
    self = ctx.policy
    account = ctx.account
    return bool(
        len(account.anchor_weights) in {2, 3}
        and account.candidate_tenure.get("recovery_substitution_completed", 0) < 1
        and ctx.anchor_elapsed > self.cfg.recovery_add_window_days
        and not account.protected_weights
        and ctx.risk.state.value in {"NORMAL", "CAUTION"}
    )


def _broken_secondaries(
    ctx: _SubstitutionContext,
    *,
    held_anchors: list[str],
    lead: str,
) -> list[tuple[LeaderScore, pd.DataFrame]]:
    self = ctx.policy
    broken_secondaries: list[tuple[LeaderScore, pd.DataFrame]] = []
    for symbol in held_anchors:
        if symbol == lead:
            continue
        score = ctx.leaders.get(symbol)
        frame = ctx.user_panel.get(symbol)
        if score is None or frame is None or ctx.date not in frame.index:
            continue
        row = frame.loc[ctx.date]
        structure_broken = (
            scalar(row, "close") < scalar(row, f"ma{self.cfg.trend_fast}")
            and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) < 0
        )
        sessions_since_shock = math.inf
        if ctx.account.last_shock_date:
            sessions_since_shock = len(frame.loc[pd.Timestamp(ctx.account.last_shock_date) : ctx.date]) - 1
        medium_broken = scalar(row, f"ret{self.cfg.trend_medium}", 0.0) < 0
        broken = (structure_broken and (not score.mature or medium_broken)) or (
            not score.mature and sessions_since_shock <= self.cfg.recovery_substitution_shock_window
        )
        if broken:
            broken_secondaries.append((score, frame))
    return broken_secondaries


@dataclass(frozen=True, slots=True)
class _SubstitutionPair:
    industry_handoff: bool
    edge: float
    incumbent_score: LeaderScore
    challenger: LeaderScore
    incumbent_frame: pd.DataFrame


def _substitution_pairs(
    ctx: _SubstitutionContext,
    *,
    broken_secondaries: list[tuple[LeaderScore, pd.DataFrame]],
    lead: str,
) -> list[_SubstitutionPair]:
    self = ctx.policy
    pairs: list[_SubstitutionPair] = []
    completed = ctx.account.candidate_tenure.get("recovery_substitution_completed", 0)
    for incumbent_score, incumbent_frame in broken_secondaries:
        incumbent = incumbent_score.symbol
        occupied = {
            ctx.leaders[symbol].industry
            for symbol in ctx.account.anchor_weights
            if symbol != incumbent and symbol in ctx.leaders
        }
        for challenger in ctx.leaders.values():
            if (
                challenger.symbol in ctx.account.anchor_weights
                or not challenger.mature
                or challenger.symbol not in ctx.user_panel
                or not credible_recovery_reserve(
                    score=challenger,
                    frame=ctx.user_panel[challenger.symbol],
                    date=ctx.date,
                    occupied_industries=occupied,
                    cfg=self.cfg,
                )
                or scalar(
                    ctx.user_panel[challenger.symbol].loc[ctx.date],
                    f"ret{self.cfg.trend_fast}",
                    math.inf,
                )
                > self.cfg.recovery_substitution_max_ret20
            ):
                continue
            edge = challenger.score - incumbent_score.score
            handoff = self._industry_handoff(challenger=challenger, incumbent=incumbent_score)
            if incumbent == lead:
                continue
            if completed >= 1:
                continue
            pairs.append(
                _SubstitutionPair(
                    handoff,
                    edge,
                    incumbent_score,
                    challenger,
                    incumbent_frame,
                )
            )
    return pairs


def _confirmed_substitution_pair(
    ctx: _SubstitutionContext,
    *,
    pairs: list[_SubstitutionPair],
    lead: str,
) -> _SubstitutionPair | None:
    self = ctx.policy
    pair = max(
        pairs,
        key=lambda item: (
            item.industry_handoff,
            item.edge,
            item.challenger.score,
            -item.incumbent_score.score,
            item.challenger.symbol,
        ),
    )
    incumbent = pair.incumbent_score.symbol
    key = f"recovery_substitution:{incumbent}->{pair.challenger.symbol}"
    handoff_key = f"recovery_substitution_handoff:{incumbent}->{pair.challenger.symbol}"
    _reset_substitution_streaks(ctx.account, keep=key, keep_handoff=handoff_key)
    handoff_confirmed = bool(pair.industry_handoff or ctx.account.candidate_tenure.get(handoff_key, 0) == 1)
    ctx.account.candidate_tenure[handoff_key] = int(handoff_confirmed)
    confirmed = bool(
        handoff_confirmed and pair.edge >= self.cfg.recovery_substitution_edge and incumbent != lead
    )
    if not confirmed:
        ctx.account.candidate_tenure[handoff_key] = 0
    ctx.account.replacement_tenure[key] = ctx.account.replacement_tenure.get(key, 0) + 1 if confirmed else 0
    if ctx.account.replacement_tenure[key] < self.cfg.replacement_confirm_days:
        return None
    if not self._rotation_allowed(ctx.account, ctx.date, ctx.user_panel):
        return None
    return replace(pair, industry_handoff=handoff_confirmed)


def _risk_neutral_targets(
    ctx: _SubstitutionContext,
    *,
    targets: tuple[Target, ...],
    retained: dict[str, float],
) -> tuple[Target, ...]:
    adjusted = tuple(
        replace(
            target,
            weight=max(
                target.weight,
                ctx.weights_now.get(target.symbol, 0.0) if target.symbol in retained else 0.0,
            ),
        )
        for target in targets
    )
    if sum(target.weight for target in adjusted) > ctx.policy.cfg.max_gross + 1e-12:
        raise RuntimeError("risk-neutral substitution increased live gross")
    return adjusted


def _commit_substitution(
    ctx: _SubstitutionContext,
    *,
    pair: _SubstitutionPair,
) -> tuple[Target, ...]:
    self = ctx.policy
    account = ctx.account
    incumbent = pair.incumbent_score.symbol
    challenger = pair.challenger
    intended_transfer = min(
        self.cfg.max_symbol_weight,
        self.cfg.replacement_transfer_cap,
        max(
            account.anchor_weights.get(incumbent, 0.0),
            ctx.weights_now.get(incumbent, 0.0),
        ),
    )
    retained = {symbol: weight for symbol, weight in account.anchor_weights.items() if symbol != incumbent}
    retained_current = {
        symbol: min(self.cfg.max_symbol_weight, ctx.weights_now.get(symbol, 0.0))
        for symbol in retained
        if symbol != challenger.symbol and ctx.weights_now.get(symbol, 0.0) > 0
    }
    intended_transfer = min(
        intended_transfer,
        max(0.0, self.cfg.max_gross - sum(retained_current.values())),
    )
    transfer = (
        min(intended_transfer, ctx.weights_now.get(incumbent, 0.0))
        if ctx.risk_neutral_only
        else intended_transfer
    )
    retained[challenger.symbol] = intended_transfer
    account.anchor_weights = retained
    account.candidate_tenure["recovery_substitution_pending"] = 1
    account.candidate_tenure["recovery_substitution_completed"] = (
        account.candidate_tenure.get("recovery_substitution_completed", 0) + 1
    )
    account.rotation_dates.append(str(ctx.date.date()))
    account.replacement_events.append(
        {
            "signal_date": str(ctx.date.date()),
            "old_symbol": incumbent,
            "new_symbol": challenger.symbol,
            "old_close": scalar(pair.incumbent_frame.loc[ctx.date], "close"),
            "new_close": scalar(ctx.user_panel[challenger.symbol].loc[ctx.date], "close"),
            "edge": pair.edge,
            "route": "recovery_anchor_substitution",
            "industry_handoff": pair.industry_handoff,
        }
    )
    proposed = dict(retained_current)
    proposed[challenger.symbol] = transfer
    targets = _confirmed_recovery_substitution_targets(
        self=self,
        proposed=proposed,
        leaders=ctx.leaders,
        account=account,
        incumbent=incumbent,
        challenger=challenger,
    )
    return (
        _risk_neutral_targets(ctx, targets=targets, retained=retained) if ctx.risk_neutral_only else targets
    )


def _recovery_anchor_substitution(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    anchor_elapsed: int,
    risk_neutral_only: bool = False,
) -> tuple[Target, ...] | None:
    """Replace a broken secondary in an incomplete recovery cohort.

    The lead anchor remains sticky.  A secondary can rotate only after its
    own price structure is broken and a liquid, mature challenger has held
    a material score edge for the normal replacement confirmation period.
    """

    ctx = _SubstitutionContext(
        self,
        date,
        risk,
        user_panel,
        leaders,
        account,
        weights_now,
        anchor_elapsed,
        risk_neutral_only,
    )
    if risk.freeze_new_risk and not (risk_neutral_only and risk.state.value == "CAUTION"):
        _reset_substitution_streaks(account)
        return None
    handled, pending = _pending_substitution(ctx)
    if handled:
        return pending
    if not _substitution_eligible(ctx):
        _reset_substitution_streaks(account)
        return None
    held_anchors = [symbol for symbol in account.anchor_weights if weights_now.get(symbol, 0.0) > 0]
    if len(held_anchors) != len(account.anchor_weights):
        _reset_substitution_streaks(account)
        return None
    lead = max(
        held_anchors,
        key=lambda symbol: (
            account.anchor_weights.get(symbol, 0.0),
            leaders[symbol].score if symbol in leaders else -math.inf,
            symbol,
        ),
    )
    broken = _broken_secondaries(ctx, held_anchors=held_anchors, lead=lead)
    if not broken:
        _reset_substitution_streaks(account)
        return None
    pairs = _substitution_pairs(ctx, broken_secondaries=broken, lead=lead)
    if not pairs:
        _reset_substitution_streaks(account)
        return None
    pair = _confirmed_substitution_pair(ctx, pairs=pairs, lead=lead)
    return _commit_substitution(ctx, pair=pair) if pair is not None else None


recovery_anchor_substitution = _recovery_anchor_substitution
