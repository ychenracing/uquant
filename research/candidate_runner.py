"""Production-backed daily decision traces for causal candidate diagnosis."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd

from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.data import normalize_symbol
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.leader import REFERENCE_UNIVERSE
from uquant.types import AccountState
from uquant.validation.ai_era import require_ai_era_interval


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    """Canonical economic state immediately after one close decision."""

    date: str
    opportunity: str
    risk: str
    transition_damage: float
    family_votes: tuple[str, ...]
    sector_guard_active: bool
    capital_budget_level: int
    leaders: tuple[tuple[str, float, str, bool, bool], ...]
    strategic_tag: str
    targets: tuple[tuple[str, float, str, str], ...]
    orders: tuple[tuple[str, str, float, str, str], ...]
    fills: tuple[tuple[str, str, str, int, float, str], ...]
    equity: float
    reference_evidence: tuple[tuple[str, str], ...] = ()
    risk_evidence: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CellTrace:
    """One universe/window replay expressed as immutable daily observations."""

    universe: str
    scenario: str
    observations: tuple[DecisionTrace, ...]


@dataclass(frozen=True, slots=True)
class TraceDivergence:
    """The earliest aligned date whose economic decision state differs."""

    date: str
    changed_fields: tuple[str, ...]
    left: DecisionTrace
    right: DecisionTrace


def first_divergence(left: CellTrace, right: CellTrace) -> TraceDivergence | None:
    """Return the first changed decision, rejecting incomparable calendars."""
    left_dates = tuple(item.date for item in left.observations)
    right_dates = tuple(item.date for item in right.observations)
    if left_dates != right_dates:
        raise ValueError("decision traces require aligned dates")
    comparable = tuple(field.name for field in fields(DecisionTrace) if field.name != "date")
    for left_item, right_item in zip(left.observations, right.observations, strict=True):
        changed = tuple(
            name for name in comparable if getattr(left_item, name) != getattr(right_item, name)
        )
        if changed:
            return TraceDivergence(left_item.date, changed, left_item, right_item)
    return None


class CandidateRunner:
    """Replay the sole production engine while retaining causal daily evidence."""

    def __init__(self, data_dir: str | Path, cfg: SystemConfig = DEFAULT_CONFIG) -> None:
        self.data_dir = Path(data_dir)
        self.cfg = cfg

    def trace_cell(
        self,
        *,
        symbols: tuple[str, ...],
        start: str,
        end: str,
        universe: str = "candidate",
        scenario: str | None = None,
    ) -> CellTrace:
        """Replay one cell and retain its causal close-decision observations."""

        start, end = require_ai_era_interval(start, end)
        normalized = tuple(sorted({normalize_symbol(symbol) for symbol in symbols}))
        if not normalized:
            raise ValueError("candidate trace requires a non-empty universe")
        engine = ProductionEngine(self.data_dir, self.cfg)
        engine._load(set(normalized) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS))
        sessions = engine._raw["sh000300"].index.intersection(engine._raw["sh000682"].index)
        sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
        if len(sessions) < 2:
            raise RuntimeError("candidate trace window has fewer than two sessions")
        account = AccountState.empty(self.cfg.initial_cash)
        raw_user_panel = {symbol: engine._raw[symbol] for symbol in normalized}
        observations: list[DecisionTrace] = []
        for date in sessions:
            fill_start = len(account.fills)
            engine.execution.execute_open(date=date, account=account, panel=raw_user_panel)
            equity = engine.equity(account, date)
            decision = engine.decide(
                symbols=normalized,
                as_of=str(date.date()),
                account=account,
            )
            account.pending_orders = list(decision.pending_orders)
            family_votes = decision.risk_summary.get("family_votes", {})
            if isinstance(family_votes, dict):
                active_families = tuple(sorted(str(name) for name, active in family_votes.items() if active))
            else:
                active_families = ()
            raw_leaders = decision.risk_summary.get("leader_ranking", [])
            leaders = tuple(
                (
                    str(item["symbol"]),
                    round(float(item["score"]), 12),
                    str(item["industry"]),
                    bool(item["mature"]),
                    bool(item["emerging"]),
                )
                for item in raw_leaders
                if isinstance(item, dict)
            )
            frozen_evidence = tuple(
                (str(name), json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))
                for name, value in sorted(decision.risk_summary.items())
                if name not in {"leader_ranking", "effective_config_sha256"}
            )
            observations.append(
                DecisionTrace(
                    date=decision.date,
                    opportunity=decision.opportunity.value,
                    risk=decision.risk.value,
                    transition_damage=float(decision.risk_summary.get("transition_damage", 0.0)),
                    family_votes=active_families,
                    sector_guard_active=bool(decision.risk_summary.get("sector_guard_active", False)),
                    capital_budget_level=int(decision.risk_summary.get("capital_budget_level", 0)),
                    leaders=leaders,
                    strategic_tag=str(decision.risk_summary.get("strategic_candidate_signature", "")),
                    targets=tuple(
                        (target.symbol, round(target.weight, 12), target.lifecycle, target.reason_code)
                        for target in decision.targets
                    ),
                    orders=tuple(
                        (
                            order.side,
                            order.symbol,
                            round(order.target_weight, 12),
                            order.reason_code,
                            order.exit_kind,
                        )
                        for order in decision.pending_orders
                    ),
                    fills=tuple(
                        (
                            fill.fill_date,
                            fill.side,
                            fill.symbol,
                            fill.shares,
                            round(fill.price, 8),
                            fill.reason_code,
                        )
                        for fill in account.fills[fill_start:]
                    ),
                    equity=float(equity),
                    reference_evidence=tuple(
                        item for item in frozen_evidence if item[0].startswith("reference_")
                    ),
                    risk_evidence=frozen_evidence,
                )
            )
        return CellTrace(
            universe=universe,
            scenario=scenario or f"{start}_{end}",
            observations=tuple(observations),
        )
