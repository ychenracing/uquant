"""Causal market-formation observations independent of account execution."""
from __future__ import annotations

from typing import Any

import pandas as pd

from ..config import SystemConfig
from ..contracts.universe import decision_ai_universe
from ..data import DataStore
from ..leader import apply_opportunity_alpha, compute_structural_leaders
from ..portfolio import PortfolioAllocator
from ..types import LeaderScore, Opportunity


def recent_reversal_observations(
    *, data: DataStore, cfg: SystemConfig, date: pd.Timestamp,
    panel: dict[str, pd.DataFrame], tech: pd.DataFrame, allocator: PortfolioAllocator,
    score_cache: dict[tuple[object, ...], dict[str, LeaderScore]],
    observation_cache: dict[tuple[object, ...], list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Observe market setups, without simulating holdings, orders or account rights."""
    from ..portfolio.strategic.qualification_candidates import decisive_reversal, reversal_candidates
    dates = tech.index[tech.index <= date][-cfg.trend_fast:]
    observations: dict[str, dict[str, Any]] = {}
    universe = decision_ai_universe()
    for observed in dates:
        visible = universe.symbols_as_of(str(observed.date()))
        past_panel = {s: f for s, f in panel.items() if s in visible and observed in f.index}
        # DataStore is the workspace's loaded-data authority. Replacing
        # it, the policy, visible roles or universe identity invalidates this key.
        key = (data, cfg, universe.sha256, tuple(past_panel), observed)
        if key not in observation_cache:
            events: list[dict[str, Any]] = []
            leaders = apply_opportunity_alpha(compute_structural_leaders(
                past_panel, as_of=observed, tech=tech, cfg=cfg,
                score_cache=score_cache), opportunity=Opportunity.CHOPPY, cfg=cfg)
            snapshots = allocator._strategic_qualification_snapshots(
                date=observed, user_panel=past_panel, leaders=leaders)
            groups: dict[str, list[str]] = {}
            for symbol in reversal_candidates(allocator, snapshots, leaders):
                groups.setdefault(leaders[symbol].industry, []).append(symbol)
            for group in groups.values():
                if len(group) < cfg.strategic_cohort_min_size:
                    continue
                synchronized = sum(snapshots[s]['ret20'] for s in group[:2])/2 >= cfg.strategic_reversal_min_median_ret20
                owner, pair = decisive_reversal(allocator, synchronized=synchronized,
                    reversal_groups=[group], snapshots=snapshots, leaders=leaders, anchor_state_observed=True)
                if owner is None:
                    continue
                invalidation = float(panel[owner].loc[:observed, 'low'].tail(cfg.trend_fast).min())
                events.append({'observed_session': str(observed.date()), 'owner': owner,
                    'witnesses': group[:cfg.strategic_cohort_size], 'dominant_pair': pair,
                    'invalidation_price': invalidation,
                    'owner_score_at_observation': leaders[owner].score})
            observation_cache[key] = events
        for event in observation_cache[key]:
            owner = event['owner']
            since = panel[owner].loc[observed:date]
            # Invalidation is always evaluated through today's observable close.
            if (not (since['close'] < event['invalidation_price']).any()
                    and float(since['close'].iloc[-1]) >= float(since['close'].iloc[0])):
                observations[owner] = {**event, 'as_of': str(date.date())}
    return observations

