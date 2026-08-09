"""Recovery-anchor substitution policy with bounded causal rotation."""

from __future__ import annotations

import math

import pandas as pd

from .features import scalar
from .leader import credible_recovery_reserve
from .portfolio_leaders import LeaderPortfolioPolicy
from .types import AccountState, LeaderScore, Lifecycle, RiskAssessment, Target


class RecoveryPortfolioPolicy(LeaderPortfolioPolicy):
    """Replace a damaged recovery secondary without creating a second book."""

    def _recovery_anchor_substitution(
        self,
        *,
        date: pd.Timestamp,
        risk: RiskAssessment,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        weights_now: dict[str, float],
        anchor_elapsed: int,
    ) -> tuple[Target, ...] | None:
        """Replace a broken secondary in an incomplete recovery cohort.

        The lead anchor remains sticky.  A secondary can rotate only after its
        own price structure is broken and a liquid, mature challenger has held
        a material score edge for the normal replacement confirmation period.
        """
        if account.candidate_tenure.get("recovery_substitution_pending", 0) == 1:
            missing = {
                symbol: weight
                for symbol, weight in account.anchor_weights.items()
                if weights_now.get(symbol, 0.0) <= 0
            }
            if missing:
                proposed = {
                    symbol: weights_now.get(symbol, 0.0)
                    for symbol in account.anchor_weights
                    if weights_now.get(symbol, 0.0) > 0
                }
                proposed.update(missing)
                return self._targets(
                    proposed=proposed,
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.CORE,
                    reason="confirmed recovery anchor substitution",
                )
            account.candidate_tenure["recovery_substitution_pending"] = 0

        if (
            len(account.anchor_weights) not in {2, 3}
            or (
                len(account.anchor_weights) == 3
                and len(
                    set(account.anchor_weights)
                    & set(self.cfg.strategic_cohort_symbols)
                )
                >= 2
            )
            or account.candidate_tenure.get("recovery_substitution_completed", 0)
            >= 1
            or anchor_elapsed <= self.cfg.recovery_add_window_days
            or account.protected_weights
            or risk.state.value not in {"NORMAL", "CAUTION"}
        ):
            return None
        held_anchors = [
            symbol
            for symbol in account.anchor_weights
            if weights_now.get(symbol, 0.0) > 0
        ]
        if len(held_anchors) != len(account.anchor_weights):
            return None
        lead = max(
            held_anchors,
            key=lambda symbol: (
                account.anchor_weights.get(symbol, 0.0),
                leaders[symbol].score if symbol in leaders else -math.inf,
                symbol,
            ),
        )
        broken_secondaries: list[tuple[LeaderScore, pd.DataFrame]] = []
        for symbol in held_anchors:
            if symbol == lead:
                continue
            score = leaders.get(symbol)
            frame = user_panel.get(symbol)
            if score is None or frame is None or date not in frame.index:
                continue
            row = frame.loc[date]
            structure_broken = (
                scalar(row, "close")
                < scalar(row, f"ma{self.cfg.trend_fast}")
                and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) < 0
            )
            sessions_since_shock = math.inf
            if account.last_shock_date:
                sessions_since_shock = (
                    len(frame.loc[pd.Timestamp(account.last_shock_date) : date]) - 1
                )
            medium_term_broken = scalar(
                row, f"ret{self.cfg.trend_medium}", 0.0
            ) < 0
            broken = (
                (structure_broken and (not score.mature or medium_term_broken))
                or (
                    not score.mature
                    and sessions_since_shock
                    <= self.cfg.recovery_substitution_shock_window
                )
            )
            if broken:
                broken_secondaries.append((score, frame))
        if not broken_secondaries:
            return None
        completed = account.candidate_tenure.get(
            "recovery_substitution_completed", 0
        )
        pairs: list[
            tuple[bool, float, LeaderScore, LeaderScore, pd.DataFrame]
        ] = []
        for incumbent_score, incumbent_frame in broken_secondaries:
            incumbent = incumbent_score.symbol
            occupied_industries = {
                leaders[symbol].industry
                for symbol in account.anchor_weights
                if symbol != incumbent and symbol in leaders
            }
            for challenger in leaders.values():
                if (
                    challenger.symbol in account.anchor_weights
                    or not challenger.mature
                    or challenger.symbol not in user_panel
                    or not credible_recovery_reserve(
                        score=challenger,
                        frame=user_panel[challenger.symbol],
                        date=date,
                        occupied_industries=occupied_industries,
                        cfg=self.cfg,
                    )
                ):
                    continue
                edge = challenger.score - incumbent_score.score
                industry_handoff = self._industry_handoff(
                    challenger=challenger,
                    incumbent=incumbent_score,
                )
                if incumbent == lead:
                    continue
                if completed >= 1:
                    continue
                pairs.append(
                    (
                        industry_handoff,
                        edge,
                        incumbent_score,
                        challenger,
                        incumbent_frame,
                    )
                )
        if not pairs:
            return None
        (
            industry_handoff,
            edge,
            incumbent_score,
            challenger,
            incumbent_frame,
        ) = max(
            pairs,
            key=lambda item: (
                item[0],
                item[1],
                item[3].score,
                -item[2].score,
                item[3].symbol,
            ),
        )
        incumbent = incumbent_score.symbol
        incumbent_row = incumbent_frame.loc[date]
        key = (
            f"recovery_substitution:{incumbent}->{challenger.symbol}"
            if challenger is not None
            else f"recovery_substitution:{incumbent}->none"
        )
        confirmed = (
            edge >= self.cfg.recovery_substitution_edge
            and incumbent != lead
        )
        account.replacement_tenure[key] = (
            account.replacement_tenure.get(key, 0) + 1 if confirmed else 0
        )
        if (
            account.replacement_tenure[key] < self.cfg.replacement_confirm_days
            or not self._rotation_allowed(
                account,
                date,
                next(iter(user_panel.values())),
            )
        ):
            return None
        transfer = min(
            self.cfg.max_symbol_weight,
            max(
                account.anchor_weights.get(incumbent, 0.0),
                weights_now.get(incumbent, 0.0),
            ),
        )
        retained = {
            symbol: weight
            for symbol, weight in account.anchor_weights.items()
            if symbol != incumbent
        }
        retained_current = {
            symbol: min(
                self.cfg.max_symbol_weight,
                weights_now.get(symbol, 0.0),
            )
            for symbol in retained
            if symbol != challenger.symbol and weights_now.get(symbol, 0.0) > 0
        }
        transfer = min(
            transfer,
            max(0.0, self.cfg.max_gross - sum(retained_current.values())),
        )
        retained[challenger.symbol] = transfer
        account.anchor_weights = retained
        account.candidate_tenure["recovery_substitution_pending"] = 1
        account.candidate_tenure["recovery_substitution_completed"] = (
            account.candidate_tenure.get("recovery_substitution_completed", 0)
            + 1
        )
        account.rotation_dates.append(str(date.date()))
        account.replacement_events.append(
            {
                "signal_date": str(date.date()),
                "old_symbol": incumbent,
                "new_symbol": challenger.symbol,
                "old_close": scalar(incumbent_row, "close"),
                "new_close": scalar(user_panel[challenger.symbol].loc[date], "close"),
                "edge": edge,
                "route": "recovery_anchor_substitution",
                "industry_handoff": industry_handoff,
            }
        )
        proposed = dict(retained_current)
        proposed[challenger.symbol] = transfer
        return self._targets(
            proposed=proposed,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="confirmed recovery anchor substitution",
            reasons={
                incumbent: f"recovery anchor exit: {challenger.symbol} confirmed edge",
                challenger.symbol: f"recovery anchor entry: replaces {incumbent}",
            },
        )
