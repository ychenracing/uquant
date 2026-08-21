"""Production-backed daily decision traces for causal candidate diagnosis."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd

import uquant.engine as engine_module
from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.data import DataContractError, DataManifest, DataStore, normalize_symbol
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


class _CausalReplayDataStore(DataStore):
    """Research-only manifest adapter that ignores symbols not yet observable."""

    def manifest(
        self,
        symbols: Iterable[str],
        *,
        source: str = "frozen",
        as_of: str | pd.Timestamp | None = None,
    ) -> DataManifest:
        bound = pd.Timestamp(as_of).normalize() if as_of is not None else None
        visible: list[str] = []
        for symbol in sorted({normalize_symbol(item) for item in symbols}):
            if not (self.root / f"{symbol}.csv").is_file():
                continue
            frame = self.load(symbol)
            if bound is not None and frame.loc[:bound].empty:
                continue
            visible.append(symbol)
        if not visible:
            raise DataContractError("causal replay manifest has no observable symbols")
        return super().manifest(visible, source=source, as_of=bound)


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

    def _causal_load_symbols(self, normalized: tuple[str, ...]) -> set[str]:
        """Exclude reference symbols that do not yet exist in the bounded data view."""

        visible_references = {
            symbol
            for symbol in REFERENCE_UNIVERSE
            if (self.data_dir / f"{symbol}.csv").is_file()
        }
        return set(normalized) | visible_references | set(INDEX_SYMBOLS)

    @contextlib.contextmanager
    def _causal_reference_scope(self) -> Iterator[None]:
        """Temporarily constrain the production module global inside an isolated replay."""

        visible = tuple(
            symbol
            for symbol in REFERENCE_UNIVERSE
            if (self.data_dir / f"{symbol}.csv").is_file()
        )
        previous = engine_module.REFERENCE_UNIVERSE  # type: ignore[attr-defined]
        engine_module.REFERENCE_UNIVERSE = visible  # type: ignore[attr-defined]
        try:
            yield
        finally:
            engine_module.REFERENCE_UNIVERSE = previous  # type: ignore[attr-defined]

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
        engine.data = _CausalReplayDataStore(self.data_dir)
        engine._load(self._causal_load_symbols(normalized))
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
            with self._causal_reference_scope():
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
