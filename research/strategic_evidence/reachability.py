"""Research-only state reachability and cash-vacancy evidence."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Any, Self

from uquant.account import account_from_dict, economic_state_sha256
from uquant.contracts.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe
from uquant.types import (
    AccountOrder,
    AccountState,
    Fill,
    Opportunity,
    Position,
    Risk,
    Tranche,
    derive_attribution_event_id,
)

from .models import canonical_sha256, require_sha256
from .provenance import (
    read_gzip_shard,
    seal_payload,
    verify_sealed_payload,
    write_gzip_shard,
)

INITIAL_STATE_IDS = tuple(f"S{index:02d}" for index in range(1, 15))
PATH_IDS = tuple(f"P{index:02d}" for index in range(1, 7))
REACH_NODE_IDS = tuple(f"R{index}" for index in range(1, 9))
FUTURE_HOLDOUT_BOUNDARY = "2026-08-06"

_GRAPH_DIMENSIONS = (
    "risk",
    "opportunity",
    "capital_budget_level",
    "chronic_level",
    "freeze_new_risk",
    "strategic_epoch",
    "strategic_active",
    "qualification_streak",
    "long_cycle_open",
    "recovery_owner",
    "protected_or_anchor",
    "positive_target",
    "positive_position",
)


def validate_account_checkpoint(payload: Mapping[str, Any]) -> AccountState:
    """Round-trip a research checkpoint through the production account codec."""

    decoded = account_from_dict(dict(payload), require_hashes=False)
    if decoded.to_dict() != dict(payload):
        raise RuntimeError("account checkpoint does not round-trip through the production codec")
    return decoded


@dataclass(frozen=True, slots=True)
class SyntheticBar:
    """One causally visible synthetic OHLCV session."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    visible_through: str
    candidate_owner: str


@dataclass(frozen=True, slots=True)
class SyntheticPath:
    """A deterministic diagnostic-only path with explicit synthetic provenance."""

    path_id: str
    source: str
    bars: tuple[SyntheticBar, ...]
    provenance: Mapping[str, Any]


def _path_return(path_id: str, index: int) -> float:
    if path_id == "P01":
        return (0.008, 0.006, 0.004, 0.003)[index % 4]
    if path_id == "P02":
        if index < 6:
            return 0.006
        if index < 9:
            return (-0.07, -0.05, -0.03)[index - 6]
        if index < 25:
            return 0.025
        return 0.006
    if path_id == "P03":
        return (-0.018, -0.014, -0.010, 0.002)[index % 4]
    if path_id == "P04":
        return 0.0 if index < 6 else 0.007
    if path_id == "P05":
        return 0.007
    if path_id == "P06":
        return (0.009, 0.006, 0.004)[index % 3]
    raise ValueError("synthetic path id differs")


def _business_dates(start: date, count: int) -> tuple[date, ...]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)


def _causal_jitter(*, seed: int, path_id: str, index: int) -> float:
    digest = hashlib.sha256(f"{seed}:{path_id}:{index}".encode()).digest()
    bucket = int.from_bytes(digest[:2], "big") / 65535.0
    return (bucket - 0.5) * 0.002


def build_synthetic_paths(
    *,
    seed: int,
    start: str,
    session_count: int,
) -> tuple[SyntheticPath, ...]:
    """Build the six frozen causal paths without observing Future Holdout data."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("synthetic seed must be an integer")
    if isinstance(session_count, bool) or not isinstance(session_count, int) or session_count < 2:
        raise ValueError("synthetic paths require at least two sessions")
    try:
        sessions = _business_dates(date.fromisoformat(start), session_count)
    except ValueError as exc:
        raise ValueError("synthetic path start must be ISO-8601") from exc
    if sessions[-1] >= date.fromisoformat(FUTURE_HOLDOUT_BOUNDARY):
        raise ValueError("synthetic path reaches Future Holdout")
    result: list[SyntheticPath] = []
    for path_id in PATH_IDS:
        previous_close = 100.0
        bars: list[SyntheticBar] = []
        for index, session in enumerate(sessions):
            open_price = previous_close
            session_return = _path_return(path_id, index) + _causal_jitter(
                seed=seed,
                path_id=path_id,
                index=index,
            )
            if path_id == "P04" and index < 6:
                session_return = 0.0
            close_price = round(open_price * (1.0 + session_return), 6)
            execution_lock = path_id == "P04" and index < 6
            spread = 0.0 if execution_lock else 0.004 + abs(session_return) * 0.1
            high = round(max(open_price, close_price) * (1.0 + spread), 6)
            low = round(min(open_price, close_price) * (1.0 - spread), 6)
            session_text = session.isoformat()
            bars.append(
                SyntheticBar(
                    date=session_text,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close_price,
                    volume=(10_000 if path_id == "P05" and index < 20 else 1_000_000 + index * 10_000),
                    visible_through=session_text,
                    candidate_owner=(
                        "sz300502" if path_id == "P06" and index >= 30 else "sz300308"
                    ),
                )
            )
            previous_close = close_price
        scenario = {
            "path_id": path_id,
            "seed": seed,
            "start": sessions[0].isoformat(),
            "end": sessions[-1].isoformat(),
            "session_count": session_count,
        }
        bars_payload_sha = canonical_sha256(
            {"bars": [asdict(bar) for bar in bars]}
        )
        result.append(
            SyntheticPath(
                path_id=path_id,
                source="SYNTHETIC",
                bars=tuple(bars),
                provenance={
                    "source": "SYNTHETIC",
                    "synthetic_seed": seed,
                    "causal_rule": "bar[t] depends only on bar[t-1], path id, seed, and t",
                    "selection_cutoff": "2026-08-05",
                    "bars_sha256": bars_payload_sha,
                    "synthetic_historical_return_claims": "FORBIDDEN",
                    "scenario_sha256": canonical_sha256(scenario),
                },
            )
        )
    return tuple(result)


def path_after_checkpoint(
    *,
    state: ReachabilityState,
    path: SyntheticPath,
) -> SyntheticPath:
    """Re-date a historical-state path to its first deterministic later session."""

    if state.source != "HISTORICAL":
        return path
    checkpoint = date.fromisoformat(state.date)
    sessions = _business_dates(checkpoint + timedelta(days=1), len(path.bars))
    if sessions[-1] >= date.fromisoformat(FUTURE_HOLDOUT_BOUNDARY):
        raise ValueError("historical reachability path reaches Future Holdout")
    bars = tuple(
        SyntheticBar(
            date=session.isoformat(),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            visible_through=session.isoformat(),
            candidate_owner=bar.candidate_owner,
        )
        for bar, session in zip(path.bars, sessions, strict=True)
    )
    scenario = {
        "path_id": path.path_id,
        "seed": path.provenance.get("synthetic_seed"),
        "start": bars[0].date,
        "end": bars[-1].date,
        "session_count": len(bars),
    }
    provenance = dict(path.provenance)
    provenance.update(
        {
            "scenario_sha256": canonical_sha256(scenario),
            "bars_sha256": canonical_sha256(
                {"bars": [asdict(bar) for bar in bars]}
            ),
            "rebased_from_scenario_sha256": path.provenance.get("scenario_sha256"),
            "checkpoint_date": state.date,
            "state_id": state.state_id,
        }
    )
    return SyntheticPath(
        path_id=path.path_id,
        source=path.source,
        bars=bars,
        provenance=provenance,
    )


@dataclass(frozen=True, slots=True)
class ReachNode:
    """One graph node containing every dimension frozen by the v1 contract."""

    node_id: str
    risk: str
    opportunity: str
    capital_budget_level: int
    chronic_level: int
    freeze_new_risk: bool
    strategic_epoch: int
    strategic_active: bool
    qualification_streak: int
    long_cycle_open: bool
    recovery_owner: str
    protected_or_anchor: bool
    positive_target: bool
    positive_position: bool

    def __post_init__(self) -> None:
        if self.risk not in {item.value for item in Risk}:
            raise ValueError("risk is invalid")
        if self.opportunity not in {item.value for item in Opportunity}:
            raise ValueError("opportunity is invalid")
        if isinstance(self.capital_budget_level, bool) or not 0 <= self.capital_budget_level <= 4:
            raise ValueError("capital_budget_level is outside 0..4")
        if isinstance(self.chronic_level, bool) or not 0 <= self.chronic_level <= 3:
            raise ValueError("chronic_level is outside 0..3")
        for name in ("strategic_epoch", "qualification_streak"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "freeze_new_risk",
            "strategic_active",
            "long_cycle_open",
            "protected_or_anchor",
            "positive_target",
            "positive_position",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if not self.recovery_owner.strip():
            raise ValueError("recovery_owner must be non-empty")
        require_sha256(self.node_id, field="reach node_id")
        expected = canonical_sha256(self.dimensions())
        if self.node_id != expected:
            raise ValueError("reach node identity differs from frozen dimensions")

    @classmethod
    def create(cls, **dimensions: Any) -> Self:
        """Create a node whose identity is canonical over only the 13 frozen dimensions."""

        return cls(
            node_id=canonical_sha256(dimensions),
            **dimensions,
        )

    def dimensions(self) -> dict[str, Any]:
        """Return graph dimensions in frozen contract order."""

        return {name: getattr(self, name) for name in _GRAPH_DIMENSIONS}


@dataclass(frozen=True, order=True, slots=True)
class ReachEdge:
    """One directed observed transition between reach nodes."""

    source: str
    target: str

    def __post_init__(self) -> None:
        require_sha256(self.source, field="reach edge source")
        require_sha256(self.target, field="reach edge target")


@dataclass(frozen=True, order=True, slots=True)
class SccClassification:
    """A deterministic SCC classification and terminal-dead-state result."""

    node_ids: tuple[str, ...]
    terminal: bool
    has_positive_position: bool
    has_positive_position_exit: bool
    dead_state: bool


def _tarjan_components(
    node_ids: tuple[str, ...],
    adjacency: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indices[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target in adjacency[node_id]:
            if target not in indices:
                visit(target)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target])
            elif target in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indices[target])
        if lowlinks[node_id] != indices[node_id]:
            return
        component: list[str] = []
        while True:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == node_id:
                break
        components.append(tuple(sorted(component)))

    for node_id in node_ids:
        if node_id not in indices:
            visit(node_id)
    return tuple(sorted(components))


def analyze_terminal_sccs(
    nodes: Iterable[ReachNode],
    edges: Iterable[ReachEdge],
) -> tuple[SccClassification, ...]:
    """Run deterministic NetworkX-free Tarjan analysis and classify dead SCCs."""

    materialized_nodes = tuple(nodes)
    by_id = {node.node_id: node for node in materialized_nodes}
    if not by_id:
        raise ValueError("reachability graph requires nodes")
    if len(by_id) != len(materialized_nodes):
        raise ValueError("reachability graph has duplicate nodes")
    normalized_edges = tuple(sorted(set(edges)))
    unknown = {
        node_id
        for edge in normalized_edges
        for node_id in (edge.source, edge.target)
        if node_id not in by_id
    }
    if unknown:
        raise ValueError("reachability graph edge references an unknown node")
    adjacency = {
        node_id: tuple(sorted(edge.target for edge in normalized_edges if edge.source == node_id))
        for node_id in sorted(by_id)
    }
    components = _tarjan_components(tuple(sorted(by_id)), adjacency)
    result: list[SccClassification] = []
    for component in components:
        members = set(component)
        outgoing = {
            target
            for source in component
            for target in adjacency[source]
            if target not in members
        }
        terminal = not outgoing
        has_position = any(by_id[node_id].positive_position for node_id in component)
        has_position_exit = any(by_id[node_id].positive_position for node_id in outgoing)
        result.append(
            SccClassification(
                node_ids=component,
                terminal=terminal,
                has_positive_position=has_position,
                has_positive_position_exit=has_position_exit,
                dead_state=terminal and not has_position and not has_position_exit,
            )
        )
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class HistoricalCheckpoint:
    """A codec-validated historical account checkpoint selected before holdout."""

    state_id: str
    date: str
    account: Mapping[str, Any]
    account_sha256: str
    provenance: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReachabilityState:
    """One frozen initial state, historical when available and synthetic otherwise."""

    state_id: str
    date: str
    source: str
    account: Mapping[str, Any]
    account_sha256: str
    dimensions: Mapping[str, Any]
    provenance: Mapping[str, Any]


def _complete_dimensions(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "risk": "NORMAL",
        "opportunity": "CHOPPY",
        "capital_budget_level": 0,
        "chronic_level": 0,
        "freeze_new_risk": False,
        "strategic_epoch": 0,
        "strategic_active": False,
        "qualification_streak": 0,
        "long_cycle_open": False,
        "recovery_owner": "NONE",
        "protected_or_anchor": False,
        "positive_target": False,
        "positive_position": False,
    }
    result.update(overrides)
    if tuple(result) != _GRAPH_DIMENSIONS:
        raise ValueError("reachability state dimensions differ from frozen order")
    return result


_STATE_BLUEPRINTS: Mapping[str, Mapping[str, Any]] = {
    "S01": _complete_dimensions(),
    "S02": _complete_dimensions(opportunity="TREND"),
    "S03": _complete_dimensions(opportunity="TREND", qualification_streak=1),
    "S04": _complete_dimensions(
        opportunity="TREND",
        qualification_streak=3,
        long_cycle_open=True,
    ),
    "S05": _complete_dimensions(
        opportunity="STRONG_TREND",
        qualification_streak=3,
        long_cycle_open=True,
        strategic_epoch=1,
        strategic_active=True,
        positive_target=True,
    ),
    "S06": _complete_dimensions(
        opportunity="TREND",
        capital_budget_level=1,
        qualification_streak=3,
        long_cycle_open=True,
    ),
    "S07": _complete_dimensions(
        opportunity="TREND",
        capital_budget_level=2,
        qualification_streak=3,
        long_cycle_open=True,
    ),
    "S08": _complete_dimensions(
        opportunity="TREND",
        capital_budget_level=3,
        qualification_streak=3,
        long_cycle_open=True,
    ),
    "S09": _complete_dimensions(
        opportunity="TREND",
        capital_budget_level=4,
        freeze_new_risk=True,
        qualification_streak=3,
        long_cycle_open=True,
    ),
    "S10": _complete_dimensions(opportunity="TREND", chronic_level=1),
    "S11": _complete_dimensions(
        opportunity="TREND",
        chronic_level=3,
        freeze_new_risk=True,
    ),
    "S12": _complete_dimensions(risk="CAUTION", freeze_new_risk=True),
    "S13": _complete_dimensions(risk="RISK_OFF", freeze_new_risk=True),
    "S14": _complete_dimensions(
        risk="CRISIS",
        freeze_new_risk=True,
        recovery_owner="sz300308",
        protected_or_anchor=True,
    ),
}


def derive_state_dimensions(account: AccountState) -> dict[str, Any]:
    """Derive all 13 graph dimensions only from codec-validated durable account state."""

    validated = validate_account_checkpoint(account.to_dict())
    qualification = validated.candidate_tenure.get("strategic_candidate_streak", 0)
    long_cycle_open = validated.candidate_tenure.get("strategic_long_cycle_open", 0) > 0
    recovery_owner = (
        validated.recovery_conviction_symbol
        or validated.tactical_anchor_symbol
        or "NONE"
    )
    return _complete_dimensions(
        risk=validated.risk,
        opportunity=validated.opportunity,
        capital_budget_level=validated.capital_budget_level,
        chronic_level=validated.chronic_level,
        freeze_new_risk=(
            validated.risk != "NORMAL"
            or validated.capital_budget_level == 4
            or validated.chronic_level == 3
        ),
        strategic_epoch=validated.strategic_epoch,
        strategic_active=bool(
            validated.strategic_cohort_symbols or validated.strategic_cohort_targets
        ),
        qualification_streak=qualification,
        long_cycle_open=long_cycle_open,
        recovery_owner=recovery_owner,
        protected_or_anchor=bool(
            validated.protected_weights
            or validated.anchor_weights
            or validated.risk_anchor_symbols
        ),
        positive_target=any(
            weight > 0.0 for weight in validated.strategic_cohort_targets.values()
        ),
        positive_position=bool(validated.positions),
    )


def extract_historical_checkpoints(
    records: Iterable[Mapping[str, Any]],
    *,
    selection_cutoff: str = "2026-08-05",
) -> tuple[HistoricalCheckpoint, ...]:
    """Extract account checkpoints without reading or selecting Future Holdout data."""

    try:
        cutoff = date.fromisoformat(selection_cutoff)
    except ValueError as exc:
        raise ValueError("historical checkpoint cutoff must be ISO-8601") from exc
    if cutoff >= date.fromisoformat(FUTURE_HOLDOUT_BOUNDARY):
        raise ValueError("historical checkpoint cutoff reaches Future Holdout")
    checkpoints: list[HistoricalCheckpoint] = []
    seen: set[str] = set()
    previous_date: date | None = None
    for raw in records:
        state_id = raw.get("state_id")
        if state_id not in INITIAL_STATE_IDS:
            raise ValueError("historical checkpoint state_id is outside S01-S14")
        if state_id in seen:
            raise ValueError("historical checkpoints contain a duplicate state_id")
        raw_date = raw.get("date")
        if not isinstance(raw_date, str):
            raise ValueError("historical checkpoint date must be ISO-8601")
        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError("historical checkpoint date must be ISO-8601") from exc
        if parsed > cutoff or parsed >= date.fromisoformat(FUTURE_HOLDOUT_BOUNDARY):
            raise ValueError("historical checkpoint reaches Future Holdout")
        if previous_date is not None and parsed < previous_date:
            raise ValueError("historical checkpoints must be date sorted")
        account = raw.get("account")
        provenance = raw.get("provenance")
        if not isinstance(account, Mapping) or not isinstance(provenance, Mapping):
            raise ValueError("historical checkpoint account/provenance is malformed")
        if provenance.get("source") != "HISTORICAL":
            raise ValueError("historical checkpoint provenance source differs")
        decoded = validate_account_checkpoint(account)
        blueprint = _STATE_BLUEPRINTS[str(state_id)]
        observed_dimensions = derive_state_dimensions(decoded)
        if observed_dimensions != dict(blueprint):
            raise ValueError(
                f"historical checkpoint {state_id} differs from its complete durable blueprint"
            )
        source_provenance_sha = canonical_sha256(dict(provenance))
        selection_sha = canonical_sha256(
            {
                "state_id": state_id,
                "date": raw_date,
                "account_sha256": economic_state_sha256(decoded),
                "selection_cutoff": selection_cutoff,
                "source_provenance_sha256": source_provenance_sha,
            }
        )
        checkpoint_provenance = dict(provenance)
        checkpoint_provenance.update(
            {
                "source": "HISTORICAL",
                "selection_cutoff": selection_cutoff,
                "account_sha256": economic_state_sha256(decoded),
                "source_provenance_sha256": source_provenance_sha,
                "selection_sha256": selection_sha,
            }
        )
        checkpoints.append(
            HistoricalCheckpoint(
                state_id=state_id,
                date=raw_date,
                account=decoded.to_dict(),
                account_sha256=economic_state_sha256(decoded),
                provenance=checkpoint_provenance,
            )
        )
        seen.add(state_id)
        previous_date = parsed
    return tuple(checkpoints)


def _synthetic_account(
    *,
    blueprint: Mapping[str, Any],
    initial_cash: float,
) -> AccountState:
    account = AccountState.empty(initial_cash)
    account.risk = str(blueprint.get("risk", "NORMAL"))
    account.opportunity = str(blueprint.get("opportunity", "CHOPPY"))
    account.capital_budget_level = int(blueprint.get("capital_budget_level", 0))
    account.chronic_level = int(blueprint.get("chronic_level", 0))
    account.strategic_epoch = int(blueprint.get("strategic_epoch", 0))
    qualification = int(blueprint.get("qualification_streak", 0))
    if qualification:
        account.candidate_tenure["strategic_candidate_streak"] = qualification
    if blueprint.get("long_cycle_open"):
        account.candidate_tenure["strategic_long_cycle_open"] = 1
    if blueprint.get("strategic_active"):
        account.strategic_cohort_symbols = ["sz300308"]
    if blueprint.get("positive_target"):
        account.strategic_cohort_targets = {"sz300308": 0.5}
    recovery_owner = str(blueprint.get("recovery_owner", "NONE"))
    if recovery_owner != "NONE":
        account.recovery_conviction_symbol = recovery_owner
    if blueprint.get("protected_or_anchor"):
        account.protected_weights = {"sz300308": 0.1}
    return validate_account_checkpoint(account.to_dict())


def build_initial_states(
    *,
    checkpoints: Iterable[HistoricalCheckpoint],
    initial_cash: float,
    synthetic_date: str = "2023-01-03",
    synthetic_seed: int = 20260826,
) -> tuple[ReachabilityState, ...]:
    """Materialize exact S01-S14 coverage with an explicit synthetic fallback."""

    if initial_cash <= 0.0:
        raise ValueError("reachability initial_cash must be positive")
    try:
        parsed_synthetic_date = date.fromisoformat(synthetic_date)
    except ValueError as exc:
        raise ValueError("synthetic state date must be ISO-8601") from exc
    if parsed_synthetic_date >= date.fromisoformat(FUTURE_HOLDOUT_BOUNDARY):
        raise ValueError("synthetic state reaches Future Holdout")
    materialized_checkpoints = tuple(checkpoints)
    by_id = {checkpoint.state_id: checkpoint for checkpoint in materialized_checkpoints}
    if len(by_id) != len(materialized_checkpoints):
        raise ValueError("initial-state checkpoints contain duplicates")
    result: list[ReachabilityState] = []
    for state_id in INITIAL_STATE_IDS:
        blueprint = dict(_STATE_BLUEPRINTS[state_id])
        historical = by_id.get(state_id)
        if historical is not None:
            result.append(
                ReachabilityState(
                    state_id=state_id,
                    date=historical.date,
                    source="HISTORICAL",
                    account=dict(historical.account),
                    account_sha256=historical.account_sha256,
                    dimensions=derive_state_dimensions(
                        validate_account_checkpoint(historical.account)
                    ),
                    provenance=dict(historical.provenance),
                )
            )
            continue
        account = _synthetic_account(blueprint=blueprint, initial_cash=initial_cash)
        account_sha = economic_state_sha256(account)
        dimensions = derive_state_dimensions(account)
        if dimensions != blueprint:
            raise ValueError(f"synthetic state {state_id} differs from its complete blueprint")
        selection_sha = canonical_sha256(
            {
                "state_id": state_id,
                "date": synthetic_date,
                "account_sha256": account_sha,
                "synthetic_seed": synthetic_seed,
                "selection_cutoff": "2026-08-05",
            }
        )
        result.append(
            ReachabilityState(
                state_id=state_id,
                date=synthetic_date,
                source="SYNTHETIC",
                account=account.to_dict(),
                account_sha256=account_sha,
                dimensions=dimensions,
                provenance={
                    "source": "SYNTHETIC",
                    "synthetic_seed": synthetic_seed,
                    "fallback_reason": "HISTORICAL_CHECKPOINT_UNAVAILABLE",
                    "account_codec": "uquant.account.account_from_dict",
                    "account_sha256": account_sha,
                    "selection_cutoff": "2026-08-05",
                    "selection_sha256": selection_sha,
                    "synthetic_historical_return_claims": "FORBIDDEN",
                },
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CandidateFacts:
    """Absolute-owner facts computed from only causally visible OHLCV rows."""

    visible_through: str
    owner: str
    current_factor: float
    leader_score: float
    leader_confidence: float
    secular_score: float
    trend_persistence: float
    liquidity_confirmation: bool
    market_confirmation: bool
    coverage_complete: bool
    market_wide_execution_block: bool
    witness_missing: bool
    eligible: bool


@dataclass(frozen=True, slots=True)
class SessionObservation:
    """One dated reach-node observation and its frozen health classification."""

    date: str
    node: ReachNode
    healthy: bool
    blockers: tuple[str, ...]
    strategic_owner: str | None = None
    account_sha256: str = "0" * 64
    path_bar_sha256: str = "0" * 64
    candidate_facts: CandidateFacts | None = None
    grant_attempted: bool = False
    grant_succeeded: bool = False

    def __post_init__(self) -> None:
        try:
            parsed = date.fromisoformat(self.date)
        except ValueError as exc:
            raise ValueError("session observation date must be ISO-8601") from exc
        if parsed >= date.fromisoformat(FUTURE_HOLDOUT_BOUNDARY):
            raise ValueError("session observation reaches Future Holdout")
        if type(self.healthy) is not bool:
            raise ValueError("session observation healthy must be boolean")
        if tuple(sorted(set(self.blockers))) != self.blockers:
            raise ValueError("session observation blockers must be sorted and unique")
        if self.healthy and self.blockers:
            raise ValueError("healthy session observation cannot carry blockers")
        if self.strategic_owner is not None and not self.strategic_owner:
            raise ValueError("strategic owner must be non-empty when present")
        require_sha256(self.account_sha256, field="session observation account_sha256")
        require_sha256(self.path_bar_sha256, field="session observation path_bar_sha256")
        if self.candidate_facts is not None and self.candidate_facts.visible_through != self.date:
            raise ValueError("candidate facts visibility differs from observation date")
        if type(self.grant_attempted) is not bool or type(self.grant_succeeded) is not bool:
            raise ValueError("grant event flags must be boolean")
        if self.grant_succeeded and not self.grant_attempted:
            raise ValueError("successful grant requires an explicit attempt")


@dataclass(frozen=True, slots=True)
class BlockerTimeline:
    blockers: tuple[str, ...]
    start: str
    end: str
    calendar_sessions: int
    healthy_sessions: int


@dataclass(frozen=True, slots=True)
class RepairLatency:
    from_level: int
    to_level: int
    started_on: str
    recovered_on: str
    calendar_sessions: int
    healthy_sessions: int


@dataclass(frozen=True, slots=True)
class RepeatedCrowningTrace:
    distinct_owners: tuple[str, ...]
    strategic_epochs: tuple[int, ...]
    transitions: tuple[tuple[str, int, str], ...]
    satisfied: bool


@dataclass(frozen=True, slots=True)
class ReachabilityFinding:
    observation_id: str
    observed: bool
    first_date: str | None
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReachabilityMetrics:
    """Literal policy metrics computed from chronological transition rows."""

    budget_repair_healthy_sessions: Mapping[str, int | None]
    failed_grant_retry_healthy_sessions: int | None
    longest_healthy_zero_target_streak: int
    terminal_scc_healthy_zero_target_duration: int
    witness_missing_recovery_fraction: float
    failed_grant_retry_trace: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ReachabilityAnalysis:
    nodes: tuple[ReachNode, ...]
    edges: tuple[ReachEdge, ...]
    sccs: tuple[SccClassification, ...]
    blocker_timelines: tuple[BlockerTimeline, ...]
    capital_budget_repair: RepairLatency | None
    repeated_crowning: RepeatedCrowningTrace
    findings: tuple[ReachabilityFinding, ...]
    healthy_sessions: int
    metrics: ReachabilityMetrics


@dataclass(frozen=True, slots=True)
class HealthySessionClassification:
    """Literal v1 healthy-session outcome and ordered blocker evidence."""

    healthy: bool
    blockers: tuple[str, ...]


def classify_healthy_session(
    *,
    risk: str,
    opportunity: str,
    candidate_eligible: bool,
    coverage_complete: bool,
    market_wide_execution_block: bool,
    target_gross_cap: float,
) -> HealthySessionClassification:
    """Evaluate the frozen health definition without strategy reinterpretation."""

    if risk not in {item.value for item in Risk}:
        raise ValueError("healthy-session risk is invalid")
    if opportunity not in {item.value for item in Opportunity}:
        raise ValueError("healthy-session opportunity is invalid")
    for name, value in (
        ("candidate_eligible", candidate_eligible),
        ("coverage_complete", coverage_complete),
        ("market_wide_execution_block", market_wide_execution_block),
    ):
        if type(value) is not bool:
            raise ValueError(f"healthy-session {name} must be boolean")
    if isinstance(target_gross_cap, bool) or not isinstance(target_gross_cap, (int, float)):
        raise ValueError("healthy-session target_gross_cap must be numeric")
    blockers: list[str] = []
    if risk != "NORMAL":
        blockers.append("RISK_NOT_NORMAL")
    if opportunity not in {"TREND", "STRONG_TREND"}:
        blockers.append("OPPORTUNITY_NOT_TREND")
    if not candidate_eligible:
        blockers.append("NO_ABSOLUTE_OWNER_CANDIDATE")
    if not coverage_complete:
        blockers.append("DATA_OR_REFERENCE_COVERAGE")
    if market_wide_execution_block:
        blockers.append("MARKET_WIDE_EXECUTION_BLOCK")
    if target_gross_cap <= 0.0:
        blockers.append("NO_TARGET_GROSS_CAPACITY")
    ordered = tuple(sorted(blockers))
    return HealthySessionClassification(healthy=not ordered, blockers=ordered)


_CAPITAL_BUDGET_TARGET_GROSS_CAP = {
    0: 0.95,
    1: 0.75,
    2: 0.50,
    3: 0.25,
    4: 0.0,
}


def _execute_pending_strategic_entry(account: AccountState, bar: SyntheticBar) -> None:
    """Materialize a prior-session grant as a codec-valid next-open entry."""

    owner = account.strategic_candidate_signature
    signal_date = account.strategic_rearm_date
    if not owner or not signal_date or signal_date >= bar.date or owner in account.positions:
        return
    target_weight = account.strategic_cohort_targets.get(owner, 0.0)
    if target_weight <= 0.0:
        account.strategic_candidate_signature = ""
        account.strategic_rearm_date = ""
        return
    shares = min(100, int(account.cash // bar.open // 100) * 100)
    if shares <= 0:
        return
    gross_value = shares * bar.open
    industry = default_ai_universe().industry_of(owner, signal_date)
    if industry == "unknown":
        raise ValueError("strategic entry industry mapping is unavailable")
    event_id = derive_attribution_event_id(
        signal_date=signal_date,
        symbol=owner,
        target_weight=target_weight,
        lifecycle="CORE",
        origin_lifecycle="CORE",
        origin_subsystem="STRATEGIC",
        mechanism="STRATEGIC_COHORT",
        replaces_symbol=None,
        industry_at_entry=industry,
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy="FIFO",
        reason_code="strategic_reachability",
        exit_kind="strategy",
    )
    order_id = f"O{account.next_order_sequence:09d}"
    account.order_ledger.append(
        AccountOrder(
            order_id=order_id,
            signal_date=signal_date,
            submitted_date=signal_date,
            symbol=owner,
            side="BUY",
            target_weight=target_weight,
            reason="strategic reachability diagnostic",
            lifecycle="CORE",
            status="FILLED",
            requested_shares=shares,
            filled_shares=shares,
            remaining_shares=0,
            last_update_date=bar.date,
            last_event="FILLED",
            reason_code="strategic_reachability",
            entry_score=1.0,
            entry_confidence=1.0,
            entry_regime=account.opportunity,
            entry_industry_strength=1.0,
            event_id=event_id,
            origin_subsystem="STRATEGIC",
            mechanism="STRATEGIC_COHORT",
            origin_lifecycle="CORE",
            replaces_symbol=None,
            industry_at_entry=industry,
            industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        )
    )
    account.fills.append(
        Fill(
            signal_date=signal_date,
            fill_date=bar.date,
            symbol=owner,
            side="BUY",
            shares=shares,
            price=bar.open,
            gross_value=gross_value,
            commission=0.0,
            stamp_duty=0.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            reason="strategic reachability diagnostic",
            lifecycle="CORE",
            order_id=order_id,
            fill_id=f"{order_id}-F1",
            reason_code="strategic_reachability",
            event_id=event_id,
            origin_subsystem="STRATEGIC",
            mechanism="STRATEGIC_COHORT",
            origin_lifecycle="CORE",
            replaces_symbol=None,
            industry_at_entry=industry,
            industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        )
    )
    tranche = Tranche(
        tranche_id=f"{order_id}-T1",
        lifecycle="CORE",
        shares=shares,
        avg_cost=bar.open,
        entry_date=bar.date,
        sellable_date=(date.fromisoformat(bar.date) + timedelta(days=1)).isoformat(),
        highest_close=bar.close,
        lowest_close=bar.close,
        entry_score=1.0,
        entry_confidence=1.0,
        entry_regime=account.opportunity,
        entry_industry_strength=1.0,
        event_id=event_id,
        origin_subsystem="STRATEGIC",
        mechanism="STRATEGIC_COHORT",
        origin_lifecycle="CORE",
        replaces_symbol=None,
        industry_at_entry=industry,
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
    )
    account.positions[owner] = Position(
        symbol=owner,
        shares=shares,
        avg_cost=bar.open,
        entry_date=bar.date,
        highest_close=bar.close,
        lifecycle="CORE",
        tranches=[tranche],
    )
    account.cash -= gross_value
    account.next_order_sequence += 1
    account.strategic_candidate_signature = ""
    account.strategic_rearm_date = ""


def build_diagnostic_observations(
    *,
    state: ReachabilityState,
    path: SyntheticPath,
) -> tuple[SessionObservation, ...]:
    """Run a deterministic per-session causal diagnostic transition harness.

    The harness consumes only the OHLCV prefix visible on each session, mutates
    a production-codec-valid durable account, and deliberately remains
    ``DIAGNOSTIC_ONLY``.  It makes no historical-return or production-policy
    claim.
    """

    if path.source != "SYNTHETIC":
        raise ValueError("diagnostic transition currently requires a synthetic path")
    if not path.bars:
        raise ValueError("diagnostic transition path is empty")
    try:
        checkpoint_date = date.fromisoformat(state.date)
    except ValueError as exc:
        raise ValueError("reachability checkpoint date must be ISO-8601") from exc
    if date.fromisoformat(path.bars[0].date) <= checkpoint_date:
        raise ValueError("diagnostic path must start after checkpoint date")
    if state.source not in {"HISTORICAL", "SYNTHETIC"}:
        raise ValueError("reachability state source differs")
    account = validate_account_checkpoint(state.account)
    if economic_state_sha256(account) != state.account_sha256:
        raise ValueError("reachability state account identity differs")
    derived_initial_dimensions = derive_state_dimensions(account)
    if derived_initial_dimensions != dict(state.dimensions):
        raise ValueError("reachability state dimensions differ from durable account")
    if state.state_id not in INITIAL_STATE_IDS or derived_initial_dimensions != dict(
        _STATE_BLUEPRINTS[state.state_id]
    ):
        raise ValueError("reachability state label differs from durable blueprint")
    if state.provenance.get("source") != state.source:
        raise ValueError("reachability state provenance source differs")
    state_selection = require_sha256(
        state.provenance.get("selection_sha256"),
        field="state selection",
    )
    if state.source == "SYNTHETIC":
        expected_state_selection = canonical_sha256(
            {
                "state_id": state.state_id,
                "date": state.date,
                "account_sha256": state.account_sha256,
                "synthetic_seed": state.provenance.get("synthetic_seed"),
                "selection_cutoff": state.provenance.get("selection_cutoff"),
            }
        )
    else:
        source_provenance_sha = require_sha256(
            state.provenance.get("source_provenance_sha256"),
            field="historical source provenance",
        )
        expected_state_selection = canonical_sha256(
            {
                "state_id": state.state_id,
                "date": state.date,
                "account_sha256": state.account_sha256,
                "selection_cutoff": state.provenance.get("selection_cutoff"),
                "source_provenance_sha256": source_provenance_sha,
            }
        )
    if state_selection != expected_state_selection:
        raise ValueError("reachability state selection identity differs")
    path_scenario = require_sha256(
        path.provenance.get("scenario_sha256"),
        field="path scenario",
    )
    expected_path_scenario = canonical_sha256(
        {
            "path_id": path.path_id,
            "seed": path.provenance.get("synthetic_seed"),
            "start": path.bars[0].date,
            "end": path.bars[-1].date,
            "session_count": len(path.bars),
        }
    )
    if path_scenario != expected_path_scenario:
        raise ValueError("reachability path scenario identity differs")
    if path.provenance.get("source") != path.source:
        raise ValueError("reachability path provenance source differs")

    observations: list[SessionObservation] = []
    closes: list[float] = []
    running_peak = 0.0
    previous_candidate = account.risk_anchor_candidate_signature
    for index, bar in enumerate(path.bars):
        if bar.visible_through != bar.date:
            raise ValueError("synthetic bar violates causal visibility")
        if index and bar.open != path.bars[index - 1].close:
            raise ValueError("synthetic path OHLCV chain differs")
        _execute_pending_strategic_entry(account, bar)
        closes.append(bar.close)
        running_peak = max(running_peak, bar.close)
        drawdown = 0.0 if running_peak <= 0.0 else bar.close / running_peak - 1.0
        recent = closes[-5:]
        returns = [right / left - 1.0 for left, right in pairwise(recent)]
        positive_fraction = (
            sum(item > 0.0 for item in returns) / len(returns) if returns else 0.0
        )
        rolling_return = recent[-1] / recent[0] - 1.0 if len(recent) > 1 else 0.0
        coverage_complete = (
            all(math.isfinite(value) and value > 0.0 for value in (bar.open, bar.high, bar.low, bar.close))
            and bar.high >= max(bar.open, bar.close)
            and bar.low <= min(bar.open, bar.close)
            and isinstance(bar.volume, int)
            and bar.volume >= 0
        )
        execution_block = bar.high == bar.low
        liquidity = bar.volume >= 250_000
        market_confirmation = rolling_return > 0.004 and positive_fraction >= 0.5
        current_factor = max(0.0, min(1.0, 0.5 + rolling_return * 4.0))
        leader_score = max(0.0, min(1.0, 0.88 + rolling_return * 3.0))
        leader_confidence = max(0.0, min(1.0, 0.65 + positive_fraction * 0.2))
        secular_score = max(0.0, min(1.0, 0.76 + rolling_return * 2.5))
        trend_persistence = positive_fraction

        if drawdown <= -0.16:
            account.risk = Risk.RISK_OFF.value
        elif drawdown <= -0.08:
            account.risk = Risk.CAUTION.value
        elif rolling_return > 0.015:
            account.risk = Risk.NORMAL.value
        if rolling_return > 0.035 and positive_fraction >= 0.75:
            account.opportunity = Opportunity.STRONG_TREND.value
        elif rolling_return > 0.004 and positive_fraction >= 0.5:
            account.opportunity = Opportunity.TREND.value
        elif rolling_return < -0.025:
            account.opportunity = Opportunity.WEAK.value
        else:
            account.opportunity = Opportunity.CHOPPY.value

        eligible = (
            current_factor >= 0.5
            and leader_score >= 0.9
            and leader_confidence >= 0.7
            and secular_score >= 0.8
            and trend_persistence >= 2.0 / 3.0
            and liquidity
            and market_confirmation
            and coverage_complete
            and not execution_block
            and account.risk == Risk.NORMAL.value
            and account.opportunity in {Opportunity.TREND.value, Opportunity.STRONG_TREND.value}
        )
        witness_missing = market_confirmation and not liquidity
        facts = CandidateFacts(
            visible_through=bar.date,
            owner=bar.candidate_owner,
            current_factor=current_factor,
            leader_score=leader_score,
            leader_confidence=leader_confidence,
            secular_score=secular_score,
            trend_persistence=trend_persistence,
            liquidity_confirmation=liquidity,
            market_confirmation=market_confirmation,
            coverage_complete=coverage_complete,
            market_wide_execution_block=execution_block,
            witness_missing=witness_missing,
            eligible=eligible,
        )
        if eligible:
            streak = (
                account.candidate_tenure.get("strategic_candidate_streak", 0) + 1
                if previous_candidate == bar.candidate_owner
                else 1
            )
            account.risk_anchor_candidate_signature = bar.candidate_owner
            previous_candidate = bar.candidate_owner
        else:
            streak = 0
            account.risk_anchor_candidate_signature = ""
            previous_candidate = ""
        account.candidate_tenure["strategic_candidate_streak"] = streak
        account.candidate_tenure["strategic_long_cycle_open"] = int(streak >= 3)

        preliminary_health = classify_healthy_session(
            risk=account.risk,
            opportunity=account.opportunity,
            candidate_eligible=eligible,
            coverage_complete=coverage_complete,
            market_wide_execution_block=execution_block,
            target_gross_cap=_CAPITAL_BUDGET_TARGET_GROSS_CAP[account.capital_budget_level],
        )
        if preliminary_health.healthy and account.capital_budget_level > 0:
            account.capital_budget_repair_streak += 1
            if account.capital_budget_repair_streak >= 3:
                account.capital_budget_level -= 1
                account.capital_budget_repair_streak = 0
        elif not preliminary_health.healthy:
            account.capital_budget_repair_streak = 0

        grant_attempted = False
        grant_succeeded = False
        existing_owner = (
            account.strategic_cohort_symbols[0]
            if account.strategic_cohort_symbols
            else None
        )
        missing_candidate_target = (
            account.strategic_cohort_targets.get(bar.candidate_owner, 0.0) <= 0.0
        )
        if streak >= 3 and (existing_owner != bar.candidate_owner or missing_candidate_target):
            grant_attempted = True
            if account.capital_budget_level < 4:
                grant_succeeded = True
                if existing_owner is not None and existing_owner != bar.candidate_owner:
                    account.strategic_previous_symbols.append(existing_owner)
                if existing_owner != bar.candidate_owner:
                    account.strategic_epoch += 1
                account.strategic_cohort_symbols = [bar.candidate_owner]
                account.strategic_cohort_targets = {bar.candidate_owner: 0.5}
                if bar.candidate_owner not in account.positions:
                    account.strategic_candidate_signature = bar.candidate_owner
                    account.strategic_rearm_date = bar.date
        if account.risk != Risk.NORMAL.value:
            account.strategic_cohort_targets = {}

        validated = validate_account_checkpoint(account.to_dict())
        account = validated
        dimensions = derive_state_dimensions(account)
        health = classify_healthy_session(
            risk=account.risk,
            opportunity=account.opportunity,
            candidate_eligible=facts.eligible,
            coverage_complete=facts.coverage_complete,
            market_wide_execution_block=facts.market_wide_execution_block,
            target_gross_cap=_CAPITAL_BUDGET_TARGET_GROSS_CAP[account.capital_budget_level],
        )
        observations.append(
            SessionObservation(
                date=bar.date,
                node=ReachNode.create(**dimensions),
                healthy=health.healthy,
                blockers=health.blockers,
                strategic_owner=(
                    account.strategic_cohort_symbols[0]
                    if account.strategic_cohort_symbols
                    else None
                ),
                account_sha256=economic_state_sha256(account),
                path_bar_sha256=canonical_sha256(asdict(bar)),
                candidate_facts=facts,
                grant_attempted=grant_attempted,
                grant_succeeded=grant_succeeded,
            )
        )
    return tuple(observations)


def _validated_observations(
    observations: Iterable[SessionObservation],
) -> tuple[SessionObservation, ...]:
    rows = tuple(observations)
    if not rows:
        raise ValueError("reachability observations are empty")
    dates = tuple(row.date for row in rows)
    if dates != tuple(sorted(set(dates))):
        raise ValueError("reachability observations require sorted unique dates")
    return rows


def build_reach_graph(
    observations: Iterable[SessionObservation],
) -> tuple[tuple[ReachNode, ...], tuple[ReachEdge, ...]]:
    """Build a deterministic graph from chronological observed transitions."""

    rows = _validated_observations(observations)
    nodes = tuple(sorted({row.node.node_id: row.node for row in rows}.values(), key=lambda item: item.node_id))
    edges = tuple(
        sorted(
            {
                ReachEdge(left.node.node_id, right.node.node_id)
                for left, right in pairwise(rows)
            }
        )
    )
    return nodes, edges


def _blocker_timelines(rows: tuple[SessionObservation, ...]) -> tuple[BlockerTimeline, ...]:
    timelines: list[BlockerTimeline] = []
    start = 0
    while start < len(rows):
        blockers = rows[start].blockers
        if not blockers:
            start += 1
            continue
        end = start
        while end + 1 < len(rows) and rows[end + 1].blockers == blockers:
            end += 1
        group = rows[start : end + 1]
        timelines.append(
            BlockerTimeline(
                blockers=blockers,
                start=group[0].date,
                end=group[-1].date,
                calendar_sessions=len(group),
                healthy_sessions=sum(row.healthy for row in group),
            )
        )
        start = end + 1
    return tuple(timelines)


def _capital_budget_repair(rows: tuple[SessionObservation, ...]) -> RepairLatency | None:
    start_index = next(
        (index for index, row in enumerate(rows) if row.node.capital_budget_level > 0),
        None,
    )
    if start_index is None:
        return None
    initial = rows[start_index]
    recovery_index = next(
        (
            index
            for index in range(start_index + 1, len(rows))
            if rows[index].node.capital_budget_level == 0
        ),
        None,
    )
    if recovery_index is None:
        return None
    window = rows[start_index + 1 : recovery_index + 1]
    return RepairLatency(
        from_level=initial.node.capital_budget_level,
        to_level=0,
        started_on=initial.date,
        recovered_on=rows[recovery_index].date,
        calendar_sessions=len(window),
        healthy_sessions=sum(row.healthy for row in window),
    )


def _repeated_crowning(rows: tuple[SessionObservation, ...]) -> RepeatedCrowningTrace:
    transitions: list[tuple[str, int, str]] = []
    previous: tuple[str, int] | None = None
    for row in rows:
        if row.strategic_owner is None or row.node.strategic_epoch <= 0:
            continue
        current = (row.strategic_owner, row.node.strategic_epoch)
        if current != previous:
            transitions.append((row.date, row.node.strategic_epoch, row.strategic_owner))
            previous = current
    owners = tuple(dict.fromkeys(item[2] for item in transitions))
    epochs = tuple(dict.fromkeys(item[1] for item in transitions))
    return RepeatedCrowningTrace(
        distinct_owners=owners,
        strategic_epochs=epochs,
        transitions=tuple(transitions),
        satisfied=len(owners) >= 2 and len(epochs) >= 2,
    )


def _literal_metrics(
    rows: tuple[SessionObservation, ...],
    sccs: tuple[SccClassification, ...],
) -> ReachabilityMetrics:
    repair: dict[str, int | None] = {
        f"{level}_to_{level - 1}": None for level in range(1, 5)
    }
    level_started: dict[int, int] = {}
    for index, row in enumerate(rows):
        level = row.node.capital_budget_level
        level_started.setdefault(level, index)
        if index == 0:
            continue
        previous_level = rows[index - 1].node.capital_budget_level
        if level == previous_level - 1:
            start = level_started.get(previous_level, index - 1)
            repair[f"{previous_level}_to_{level}"] = sum(
                item.healthy for item in rows[start + 1 : index + 1]
            )
            level_started.setdefault(level, index)

    retry_traces: list[tuple[str, str]] = []
    failed_index: int | None = None
    retry_latency: int | None = None
    for index, row in enumerate(rows):
        if failed_index is None and row.grant_attempted and not row.grant_succeeded:
            failed_index = index
        elif failed_index is not None and row.grant_attempted and row.grant_succeeded:
            retry_traces.append((rows[failed_index].date, row.date))
            if retry_latency is None:
                retry_latency = sum(item.healthy for item in rows[failed_index + 1 : index + 1])
            failed_index = None

    longest_zero = 0
    current_zero = 0
    for row in rows:
        if row.healthy and not row.node.positive_target:
            current_zero += 1
            longest_zero = max(longest_zero, current_zero)
        else:
            current_zero = 0

    terminal_nodes = {
        node_id
        for scc in sccs
        if scc.terminal
        for node_id in scc.node_ids
    }
    terminal_zero = sum(
        row.healthy
        and not row.node.positive_target
        and row.node.node_id in terminal_nodes
        for row in rows
    )

    witness_episode_ends: list[int] = []
    in_episode = False
    for index, row in enumerate(rows):
        missing = row.candidate_facts is not None and row.candidate_facts.witness_missing
        if missing:
            in_episode = True
        elif in_episode:
            witness_episode_ends.append(index - 1)
            in_episode = False
    if in_episode:
        witness_episode_ends.append(len(rows) - 1)
    recovered = sum(
        any(
            later.candidate_facts is not None
            and later.candidate_facts.eligible
            and later.node.positive_target
            for later in rows[end + 1 :]
        )
        for end in witness_episode_ends
    )
    recovery_fraction = (
        recovered / len(witness_episode_ends) if witness_episode_ends else 1.0
    )
    return ReachabilityMetrics(
        budget_repair_healthy_sessions=repair,
        failed_grant_retry_healthy_sessions=retry_latency,
        longest_healthy_zero_target_streak=longest_zero,
        terminal_scc_healthy_zero_target_duration=terminal_zero,
        witness_missing_recovery_fraction=recovery_fraction,
        failed_grant_retry_trace=tuple(retry_traces),
    )


def _first_date(rows: tuple[SessionObservation, ...], predicate: Any) -> str | None:
    return next((row.date for row in rows if predicate(row)), None)


def _reachability_findings(
    rows: tuple[SessionObservation, ...],
    repair: RepairLatency | None,
    crowning: RepeatedCrowningTrace,
    metrics: ReachabilityMetrics,
) -> tuple[ReachabilityFinding, ...]:
    retry_date = (
        metrics.failed_grant_retry_trace[0][1]
        if metrics.failed_grant_retry_trace
        else None
    )
    transition_date = next(
        (
            right.date
            for left, right in pairwise(rows)
            if left.node.node_id != right.node.node_id
        ),
        None,
    )
    transition_count = sum(
        left.node.node_id != right.node.node_id for left, right in pairwise(rows)
    )
    specifications = (
        (
            "R1",
            transition_date is not None,
            transition_date,
            {
                "node_count": len({row.node.node_id for row in rows}),
                "transition_count": transition_count,
            },
        ),
        (
            "R2",
            any(row.healthy for row in rows),
            _first_date(rows, lambda row: row.healthy),
            {"healthy_sessions": sum(row.healthy for row in rows)},
        ),
        (
            "R3",
            any(row.candidate_facts is not None and row.candidate_facts.eligible for row in rows),
            _first_date(
                rows,
                lambda row: row.candidate_facts is not None and row.candidate_facts.eligible,
            ),
            {"predicate": "actual_candidate_facts.eligible"},
        ),
        (
            "R4",
            any(row.node.positive_target for row in rows),
            _first_date(rows, lambda row: row.node.positive_target),
            {"target": "positive"},
        ),
        (
            "R5",
            any(row.node.positive_position for row in rows),
            _first_date(rows, lambda row: row.node.positive_position),
            {"position": "positive"},
        ),
        (
            "R6",
            repair is not None,
            None if repair is None else repair.recovered_on,
            {"capital_budget_repair": repair is not None},
        ),
        (
            "R7",
            retry_date is not None,
            retry_date,
            {
                "failed_grant_retry": retry_date is not None,
                "healthy_sessions": metrics.failed_grant_retry_healthy_sessions,
                "ordered_trace": [list(item) for item in metrics.failed_grant_retry_trace],
            },
        ),
        (
            "R8",
            crowning.satisfied,
            None if len(crowning.transitions) < 2 else crowning.transitions[1][0],
            {
                "distinct_owners": len(crowning.distinct_owners),
                "epochs": len(crowning.strategic_epochs),
                "ordered_transitions": [list(item) for item in crowning.transitions],
            },
        ),
    )
    return tuple(
        ReachabilityFinding(
            observation_id=observation_id,
            observed=observed,
            first_date=first_date,
            evidence=evidence,
        )
        for observation_id, observed, first_date, evidence in specifications
    )


def analyze_observations(
    observations: Iterable[SessionObservation],
) -> ReachabilityAnalysis:
    """Analyze health, vacancy blockers, repair, crowning, graph, and R1-R8."""

    rows = _validated_observations(observations)
    nodes, edges = build_reach_graph(rows)
    repair = _capital_budget_repair(rows)
    crowning = _repeated_crowning(rows)
    sccs = analyze_terminal_sccs(nodes, edges)
    metrics = _literal_metrics(rows, sccs)
    return ReachabilityAnalysis(
        nodes=nodes,
        edges=edges,
        sccs=sccs,
        blocker_timelines=_blocker_timelines(rows),
        capital_budget_repair=repair,
        repeated_crowning=crowning,
        findings=_reachability_findings(rows, repair, crowning, metrics),
        healthy_sessions=sum(row.healthy for row in rows),
        metrics=metrics,
    )


@dataclass(frozen=True, slots=True)
class ReachabilityCellSpec:
    """One frozen S01-S14 x P01-P06 matrix identity."""

    state_id: str
    path_id: str
    cell_id: str

    def __post_init__(self) -> None:
        if self.state_id not in INITIAL_STATE_IDS or self.path_id not in PATH_IDS:
            raise ValueError("reachability cell lies outside frozen matrix")
        expected = canonical_sha256({"state_id": self.state_id, "path_id": self.path_id})
        if self.cell_id != expected:
            raise ValueError("reachability cell identity differs")


def enumerate_reachability_specs() -> tuple[ReachabilityCellSpec, ...]:
    """Return exact deterministic 14 x 6 coverage."""

    return tuple(
        ReachabilityCellSpec(
            state_id=state_id,
            path_id=path_id,
            cell_id=canonical_sha256({"state_id": state_id, "path_id": path_id}),
        )
        for state_id in INITIAL_STATE_IDS
        for path_id in PATH_IDS
    )


@dataclass(frozen=True, slots=True)
class ReachabilityCellResult:
    """A success or preserved terminal outcome for one frozen cell."""

    spec: ReachabilityCellSpec
    state_source: str
    path_source: str
    status: str
    observation_count: int
    input_bindings: Mapping[str, str]
    analysis: Mapping[str, Any] | None
    analysis_sha256: str | None
    error: Mapping[str, str] | None

    def __post_init__(self) -> None:
        if self.state_source not in {"HISTORICAL", "SYNTHETIC"}:
            raise ValueError("reachability state source differs")
        if self.path_source not in {"HISTORICAL", "SYNTHETIC"}:
            raise ValueError("reachability path source differs")
        if self.status not in {"SUCCESS", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}:
            raise ValueError("reachability cell status differs")
        if isinstance(self.observation_count, bool) or self.observation_count < 0:
            raise ValueError("reachability observation_count is invalid")
        expected_binding_keys = {
            "state_account_sha256",
            "state_dimensions_sha256",
            "state_provenance_sha256",
            "path_scenario_sha256",
            "path_provenance_sha256",
            "path_bars_sha256",
            "cell_scenario_sha256",
        }
        if set(self.input_bindings) != expected_binding_keys:
            raise ValueError("reachability cell input bindings differ")
        for key, value in self.input_bindings.items():
            require_sha256(value, field=f"reachability {key}")
        if self.status == "SUCCESS":
            if (
                self.analysis is None
                or self.error is not None
                or self.observation_count == 0
                or self.analysis_sha256 != canonical_sha256(dict(self.analysis))
            ):
                raise ValueError("successful reachability cell evidence is incomplete")
        elif self.analysis is not None or self.analysis_sha256 is not None or not self.error:
            raise ValueError("terminal reachability cell evidence is malformed")
        elif set(self.error) != {"type", "message", "stage"} or self.error["stage"] not in {
            "TRANSITION",
            "ANALYSIS",
        }:
            raise ValueError("terminal reachability error envelope differs")

    def sealed_row(self) -> dict[str, Any]:
        """Return one independently sealed compact row."""

        return seal_payload(
            {
                "cell_id": self.spec.cell_id,
                "state_id": self.spec.state_id,
                "path_id": self.spec.path_id,
                "state_source": self.state_source,
                "path_source": self.path_source,
                "evidence_class": "DIAGNOSTIC_ONLY",
                "status": self.status,
                "observation_count": self.observation_count,
                "input_bindings": dict(self.input_bindings),
                "analysis": None if self.analysis is None else dict(self.analysis),
                "analysis_sha256": self.analysis_sha256,
                "error": self.error,
            }
        )


def _cell_input_bindings(
    spec: ReachabilityCellSpec,
    state: ReachabilityState,
    path: SyntheticPath,
) -> dict[str, str]:
    if state.state_id != spec.state_id or path.path_id != spec.path_id:
        raise ValueError("reachability cell input identity differs from specification")
    state_account_sha = require_sha256(
        state.account_sha256,
        field="reachability state account",
    )
    path_scenario_sha = require_sha256(
        path.provenance.get("scenario_sha256"),
        field="reachability path scenario",
    )
    bindings = {
        "state_account_sha256": state_account_sha,
        "state_dimensions_sha256": canonical_sha256(dict(state.dimensions)),
        "state_provenance_sha256": canonical_sha256(dict(state.provenance)),
        "path_scenario_sha256": path_scenario_sha,
        "path_provenance_sha256": canonical_sha256(dict(path.provenance)),
        "path_bars_sha256": canonical_sha256(
            {"bars": [asdict(bar) for bar in path.bars]}
        ),
    }
    bindings["cell_scenario_sha256"] = canonical_sha256(
        {
            "cell_id": spec.cell_id,
            "state_id": spec.state_id,
            "path_id": spec.path_id,
            "input_bindings": bindings,
        }
    )
    return bindings


def run_reachability_cell(
    spec: ReachabilityCellSpec,
    *,
    state: ReachabilityState,
    path: SyntheticPath,
    observe: Callable[[], Iterable[SessionObservation]],
) -> ReachabilityCellResult:
    """Analyze one cell while preserving terminal failures as result rows."""

    bindings = _cell_input_bindings(spec, state, path)
    stage = "TRANSITION"
    try:
        observations = tuple(observe())
        stage = "ANALYSIS"
        if not observations:
            return ReachabilityCellResult(
                spec=spec,
                state_source=state.source,
                path_source=path.source,
                status="INSUFFICIENT_SAMPLE",
                observation_count=0,
                input_bindings=bindings,
                analysis=None,
                analysis_sha256=None,
                error={
                    "type": "InsufficientSample",
                    "message": "reachability cell has no observations",
                    "stage": stage,
                },
            )
        analysis = analyze_observations(observations)
        analysis_payload = asdict(analysis)
        return ReachabilityCellResult(
            spec=spec,
            state_source=state.source,
            path_source=path.source,
            status="SUCCESS",
            observation_count=len(observations),
            input_bindings=bindings,
            analysis=analysis_payload,
            analysis_sha256=canonical_sha256(analysis_payload),
            error=None,
        )
    except Exception as exc:
        return ReachabilityCellResult(
            spec=spec,
            state_source=state.source,
            path_source=path.source,
            status="REPLAY_ERROR",
            observation_count=0,
            input_bindings=bindings,
            analysis=None,
            analysis_sha256=None,
            error={
                "type": type(exc).__name__,
                "message": str(exc),
                "stage": stage,
            },
        )


def _validate_cell_coverage(
    rows: Iterable[Mapping[str, Any]],
    expected_specs: Iterable[ReachabilityCellSpec],
) -> tuple[Mapping[str, Any], ...]:
    materialized = tuple(rows)
    expected = tuple(expected_specs)
    observed_ids = tuple(row.get("cell_id") for row in materialized)
    expected_ids = tuple(spec.cell_id for spec in expected)
    linked = all(
        row.get("cell_id") == spec.cell_id
        and row.get("state_id") == spec.state_id
        and row.get("path_id") == spec.path_id
        for row, spec in zip(materialized, expected, strict=True)
    ) if len(materialized) == len(expected) else False
    if (
        observed_ids != expected_ids
        or len(set(observed_ids)) != len(observed_ids)
        or not linked
    ):
        raise ValueError("reachability shard cell coverage differs")
    return materialized


def write_reachability_shard(
    path: str | Path,
    *,
    cells: Iterable[ReachabilityCellResult],
    provenance: Mapping[str, Any],
    expected_specs: Iterable[ReachabilityCellSpec],
    expected_states: Iterable[ReachabilityState],
    expected_paths: Iterable[SyntheticPath],
) -> dict[str, Any]:
    """Write and immediately read back one deterministic reachability shard."""

    expected = tuple(expected_specs)
    rows = tuple(cell.sealed_row() for cell in cells)
    _validate_cell_coverage(rows, expected)
    envelope = write_gzip_shard(path, rows=rows, provenance=provenance)
    read_reachability_shard(
        path,
        expected_specs=expected,
        expected_states=expected_states,
        expected_paths=expected_paths,
        expected_provenance=provenance,
    )
    return envelope


def _validate_analysis_payload(
    analysis: Mapping[str, Any],
    *,
    observation_count: int,
) -> None:
    expected_keys = {
        "nodes",
        "edges",
        "sccs",
        "blocker_timelines",
        "capital_budget_repair",
        "repeated_crowning",
        "findings",
        "healthy_sessions",
        "metrics",
    }
    if set(analysis) != expected_keys:
        raise ValueError("reachability analysis schema differs")
    healthy = analysis.get("healthy_sessions")
    if isinstance(healthy, bool) or not isinstance(healthy, int) or not 0 <= healthy <= observation_count:
        raise ValueError("reachability analysis healthy_sessions differs")
    nodes = analysis.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("reachability analysis nodes differ")
    validated_nodes: dict[str, ReachNode] = {}
    for raw in nodes:
        if not isinstance(raw, dict) or set(raw) != {"node_id", *_GRAPH_DIMENSIONS}:
            raise ValueError("reachability analysis node schema differs")
        node = ReachNode(**raw)
        if node.node_id in validated_nodes:
            raise ValueError("reachability analysis nodes contain duplicates")
        validated_nodes[node.node_id] = node
    if list(validated_nodes) != sorted(validated_nodes):
        raise ValueError("reachability analysis node order differs")
    raw_edges = analysis.get("edges")
    if not isinstance(raw_edges, list):
        raise ValueError("reachability analysis edges differ")
    edges: list[ReachEdge] = []
    for raw in raw_edges:
        if not isinstance(raw, dict) or set(raw) != {"source", "target"}:
            raise ValueError("reachability analysis edge schema differs")
        edge = ReachEdge(**raw)
        if edge.source not in validated_nodes or edge.target not in validated_nodes:
            raise ValueError("reachability analysis edge references unknown node")
        edges.append(edge)
    if edges != sorted(set(edges)):
        raise ValueError("reachability analysis edge order differs")
    raw_sccs = analysis.get("sccs")
    if not isinstance(raw_sccs, list):
        raise ValueError("reachability analysis SCC schema differs")
    expected_sccs = [
        {
            **asdict(item),
            "node_ids": list(item.node_ids),
        }
        for item in analyze_terminal_sccs(validated_nodes.values(), edges)
    ]
    if raw_sccs != expected_sccs:
        raise ValueError("reachability analysis SCC classification differs")
    timelines = analysis.get("blocker_timelines")
    if not isinstance(timelines, list):
        raise ValueError("reachability analysis blocker timelines differ")
    for raw in timelines:
        if not isinstance(raw, dict) or set(raw) != {
            "blockers",
            "start",
            "end",
            "calendar_sessions",
            "healthy_sessions",
        }:
            raise ValueError("reachability blocker timeline schema differs")
        blockers = raw["blockers"]
        if not isinstance(blockers, list) or blockers != sorted(set(blockers)) or not blockers:
            raise ValueError("reachability blocker timeline blockers differ")
        try:
            start = date.fromisoformat(raw["start"])
            end = date.fromisoformat(raw["end"])
        except (TypeError, ValueError) as exc:
            raise ValueError("reachability blocker timeline date differs") from exc
        if start > end:
            raise ValueError("reachability blocker timeline order differs")
        for key in ("calendar_sessions", "healthy_sessions"):
            value = raw[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("reachability blocker timeline count differs")
    repair = analysis.get("capital_budget_repair")
    if repair is not None:
        if not isinstance(repair, dict) or set(repair) != {
            "from_level",
            "to_level",
            "started_on",
            "recovered_on",
            "calendar_sessions",
            "healthy_sessions",
        }:
            raise ValueError("reachability capital repair schema differs")
        if not 0 <= repair["to_level"] < repair["from_level"] <= 4:
            raise ValueError("reachability capital repair levels differ")
    findings = analysis.get("findings")
    if not isinstance(findings, list) or [item.get("observation_id") for item in findings if isinstance(item, dict)] != list(REACH_NODE_IDS):
        raise ValueError("reachability analysis findings differ")
    if any(
        not isinstance(item, dict)
        or set(item) != {"observation_id", "observed", "first_date", "evidence"}
        or type(item["observed"]) is not bool
        or (item["first_date"] is not None and not isinstance(item["first_date"], str))
        or not isinstance(item["evidence"], dict)
        for item in findings
    ):
        raise ValueError("reachability analysis finding schema differs")
    repeated = analysis.get("repeated_crowning")
    if not isinstance(repeated, dict) or set(repeated) != {
        "distinct_owners",
        "strategic_epochs",
        "transitions",
        "satisfied",
    }:
        raise ValueError("reachability analysis repeated-crowning schema differs")
    transitions = repeated.get("transitions")
    if not isinstance(transitions, list) or transitions != sorted(transitions):
        raise ValueError("reachability analysis repeated-crowning order differs")
    if any(
        not isinstance(item, list)
        or len(item) != 3
        or not isinstance(item[0], str)
        or isinstance(item[1], bool)
        or not isinstance(item[1], int)
        or not isinstance(item[2], str)
        for item in transitions
    ):
        raise ValueError("reachability analysis repeated-crowning transition differs")
    expected_satisfied = (
        len(set(repeated["distinct_owners"])) >= 2
        and len(set(repeated["strategic_epochs"])) >= 2
    )
    if repeated["satisfied"] is not expected_satisfied:
        raise ValueError("reachability analysis repeated-crowning result differs")
    metrics = analysis.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != {
        "budget_repair_healthy_sessions",
        "failed_grant_retry_healthy_sessions",
        "longest_healthy_zero_target_streak",
        "terminal_scc_healthy_zero_target_duration",
        "witness_missing_recovery_fraction",
        "failed_grant_retry_trace",
    }:
        raise ValueError("reachability analysis metrics schema differs")
    budget = metrics.get("budget_repair_healthy_sessions")
    if not isinstance(budget, dict) or set(budget) != {
        "1_to_0",
        "2_to_1",
        "3_to_2",
        "4_to_3",
    }:
        raise ValueError("reachability budget metrics differ")
    for value in budget.values():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("reachability budget latency differs")
    retry_latency = metrics.get("failed_grant_retry_healthy_sessions")
    if retry_latency is not None and (
        isinstance(retry_latency, bool)
        or not isinstance(retry_latency, int)
        or retry_latency < 0
    ):
        raise ValueError("reachability failed-grant retry latency differs")
    for key in (
        "longest_healthy_zero_target_streak",
        "terminal_scc_healthy_zero_target_duration",
    ):
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"reachability {key} differs")
    fraction = metrics.get("witness_missing_recovery_fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0.0 <= fraction <= 1.0:
        raise ValueError("reachability witness recovery fraction differs")
    retry_trace = metrics.get("failed_grant_retry_trace")
    if (
        not isinstance(retry_trace, list)
        or retry_trace != sorted(retry_trace)
        or any(
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            or item[0] >= item[1]
            for item in retry_trace
        )
    ):
        raise ValueError("reachability failed-grant retry trace differs")


def _validate_expected_state(state: ReachabilityState) -> None:
    """Fail closed when a caller-provided frozen state is internally inconsistent."""

    if state.state_id not in INITIAL_STATE_IDS or state.source not in {
        "HISTORICAL",
        "SYNTHETIC",
    }:
        raise ValueError("reachability expected state identity differs")
    account = validate_account_checkpoint(state.account)
    observed_account_sha = economic_state_sha256(account)
    if state.account_sha256 != observed_account_sha:
        raise ValueError("reachability expected state account identity differs")
    if state.provenance.get("account_sha256") != observed_account_sha:
        raise ValueError("reachability expected state provenance account identity differs")
    dimensions = derive_state_dimensions(account)
    if dimensions != dict(state.dimensions) or dimensions != dict(
        _STATE_BLUEPRINTS[state.state_id]
    ):
        raise ValueError("reachability expected state dimensions differ")
    if state.provenance.get("source") != state.source:
        raise ValueError("reachability expected state source differs")
    selection = require_sha256(
        state.provenance.get("selection_sha256"),
        field="reachability expected state selection",
    )
    if state.source == "SYNTHETIC":
        expected_selection = canonical_sha256(
            {
                "state_id": state.state_id,
                "date": state.date,
                "account_sha256": observed_account_sha,
                "synthetic_seed": state.provenance.get("synthetic_seed"),
                "selection_cutoff": state.provenance.get("selection_cutoff"),
            }
        )
    else:
        source_sha = require_sha256(
            state.provenance.get("source_provenance_sha256"),
            field="reachability expected historical source provenance",
        )
        expected_selection = canonical_sha256(
            {
                "state_id": state.state_id,
                "date": state.date,
                "account_sha256": observed_account_sha,
                "selection_cutoff": state.provenance.get("selection_cutoff"),
                "source_provenance_sha256": source_sha,
            }
        )
    if selection != expected_selection:
        raise ValueError("reachability expected state selection identity differs")


def _validate_expected_path(path: SyntheticPath) -> None:
    """Fail closed when expected path provenance does not bind its exact bars."""

    if path.path_id not in PATH_IDS or path.source != "SYNTHETIC" or not path.bars:
        raise ValueError("reachability expected path identity differs")
    if path.provenance.get("source") != path.source:
        raise ValueError("reachability expected path source differs")
    dates = tuple(bar.date for bar in path.bars)
    if dates != tuple(sorted(set(dates))):
        raise ValueError("reachability expected path dates differ")
    for index, bar in enumerate(path.bars):
        if bar.visible_through != bar.date:
            raise ValueError("reachability expected path visibility differs")
        if index and bar.open != path.bars[index - 1].close:
            raise ValueError("reachability expected path OHLCV chain differs")
    bars_sha = canonical_sha256(
        {"bars": [asdict(bar) for bar in path.bars]}
    )
    if path.provenance.get("bars_sha256") != bars_sha:
        raise ValueError("reachability expected path payload identity differs")
    scenario_sha = canonical_sha256(
        {
            "path_id": path.path_id,
            "seed": path.provenance.get("synthetic_seed"),
            "start": path.bars[0].date,
            "end": path.bars[-1].date,
            "session_count": len(path.bars),
        }
    )
    if path.provenance.get("scenario_sha256") != scenario_sha:
        raise ValueError("reachability expected path scenario identity differs")


def read_reachability_shard(
    path: str | Path,
    *,
    expected_specs: Iterable[ReachabilityCellSpec],
    expected_states: Iterable[ReachabilityState],
    expected_paths: Iterable[SyntheticPath],
    expected_provenance: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Read back seals, provenance, statuses, and exact cell coverage."""

    payload = read_gzip_shard(path)
    if payload["provenance"] != dict(expected_provenance):
        raise ValueError("reachability shard provenance differs")
    rows = tuple(
        verify_sealed_payload(row, label="reachability cell")
        for row in payload["rows"]
    )
    expected = tuple(expected_specs)
    _validate_cell_coverage(rows, expected)
    materialized_states = tuple(expected_states)
    materialized_paths = tuple(expected_paths)
    state_by_id = {state.state_id: state for state in materialized_states}
    path_by_id = {path.path_id: path for path in materialized_paths}
    if len(state_by_id) != len(materialized_states) or len(path_by_id) != len(materialized_paths):
        raise ValueError("reachability expected inputs contain duplicates")
    for expected_state in materialized_states:
        _validate_expected_state(expected_state)
    for expected_path in materialized_paths:
        _validate_expected_path(expected_path)
    for row, spec in zip(rows, expected, strict=True):
        if set(row) != {
            "cell_id",
            "state_id",
            "path_id",
            "state_source",
            "path_source",
            "evidence_class",
            "status",
            "observation_count",
            "input_bindings",
            "analysis",
            "analysis_sha256",
            "error",
            "payload_sha256",
        }:
            raise ValueError("reachability shard row schema differs")
        if row.get("status") not in {"SUCCESS", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}:
            raise ValueError("reachability shard status differs")
        if row.get("evidence_class") != "DIAGNOSTIC_ONLY":
            raise ValueError("reachability shard evidence class differs")
        cell_state = state_by_id.get(spec.state_id)
        cell_path = path_by_id.get(spec.path_id)
        if cell_state is None or cell_path is None:
            raise ValueError("reachability expected input is missing")
        effective_path = path_after_checkpoint(state=cell_state, path=cell_path)
        _validate_expected_path(effective_path)
        if row.get("state_source") != cell_state.source or row.get("path_source") != effective_path.source:
            raise ValueError("reachability shard input source differs")
        bindings = row.get("input_bindings")
        if not isinstance(bindings, dict) or set(bindings) != {
            "state_account_sha256",
            "state_dimensions_sha256",
            "state_provenance_sha256",
            "path_scenario_sha256",
            "path_provenance_sha256",
            "path_bars_sha256",
            "cell_scenario_sha256",
        }:
            raise ValueError("reachability shard input bindings differ")
        for key, value in bindings.items():
            require_sha256(value, field=f"reachability shard {key}")
        expected_bindings = _cell_input_bindings(spec, cell_state, effective_path)
        if bindings != expected_bindings:
            raise ValueError("reachability shard input bindings differ from expected inputs")
        scenario = dict(bindings)
        observed_cell_scenario = scenario.pop("cell_scenario_sha256")
        expected_cell_scenario = canonical_sha256(
            {
                "cell_id": row["cell_id"],
                "state_id": row["state_id"],
                "path_id": row["path_id"],
                "input_bindings": scenario,
            }
        )
        if observed_cell_scenario != expected_cell_scenario:
            raise ValueError("reachability shard cell scenario differs")
        if row["status"] == "SUCCESS":
            analysis = row.get("analysis")
            if not isinstance(analysis, dict):
                raise ValueError("successful reachability shard row lacks analysis")
            analysis_sha = require_sha256(
                row.get("analysis_sha256"),
                field="reachability analysis",
            )
            if analysis_sha != canonical_sha256(analysis):
                raise ValueError("reachability analysis identity differs")
            if row.get("error") is not None:
                raise ValueError("successful reachability shard row has error")
            _validate_analysis_payload(
                analysis,
                observation_count=row["observation_count"],
            )
        else:
            error = row.get("error")
            if (
                not isinstance(error, dict)
                or set(error) != {"type", "message", "stage"}
                or error.get("stage") not in {"TRANSITION", "ANALYSIS"}
                or not all(isinstance(error.get(key), str) for key in error)
            ):
                raise ValueError("terminal reachability shard row lacks error envelope")
            if row.get("analysis") is not None or row.get("analysis_sha256") is not None:
                raise ValueError("terminal reachability shard row has analysis")
        expected_result = run_reachability_cell(
            spec,
            state=cell_state,
            path=effective_path,
            observe=partial(
                build_diagnostic_observations,
                state=cell_state,
                path=effective_path,
            ),
        )
        if canonical_sha256(row) != canonical_sha256(expected_result.sealed_row()):
            raise ValueError("reachability shard differs from deterministic result")
    return rows


__all__ = (
    "FUTURE_HOLDOUT_BOUNDARY",
    "INITIAL_STATE_IDS",
    "PATH_IDS",
    "REACH_NODE_IDS",
    "BlockerTimeline",
    "CandidateFacts",
    "HealthySessionClassification",
    "HistoricalCheckpoint",
    "ReachEdge",
    "ReachNode",
    "ReachabilityAnalysis",
    "ReachabilityCellResult",
    "ReachabilityCellSpec",
    "ReachabilityFinding",
    "ReachabilityMetrics",
    "ReachabilityState",
    "RepairLatency",
    "RepeatedCrowningTrace",
    "SccClassification",
    "SessionObservation",
    "SyntheticBar",
    "SyntheticPath",
    "analyze_observations",
    "analyze_terminal_sccs",
    "build_diagnostic_observations",
    "build_initial_states",
    "build_reach_graph",
    "build_synthetic_paths",
    "classify_healthy_session",
    "derive_state_dimensions",
    "enumerate_reachability_specs",
    "extract_historical_checkpoints",
    "path_after_checkpoint",
    "read_reachability_shard",
    "run_reachability_cell",
    "validate_account_checkpoint",
    "write_reachability_shard",
)
