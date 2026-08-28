"""Causal, production-backed forced strategic-owner evidence matrix.

The helpers in this module deliberately keep selection evidence separate from
economic replay.  Selection reads only the decision-date state; every economic
row delegates to :func:`run_replay`, which owns the production close/open loop,
execution planner, and durable account ledger.
"""

from __future__ import annotations

import gzip
import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from math import isfinite
from pathlib import Path
from typing import Any

import pandas as pd

from research.candidate_runner import CandidateRunner, CausalReplayDataStore
from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.engine import ProductionEngine
from uquant.leader import apply_opportunity_alpha, compute_structural_leaders
from uquant.market import ReplayHarness
from uquant.reference_registry import resolve_reference_symbols
from uquant.types import AccountState

from .contract import StrategicEvidenceContract
from .intervention import StrategicOwnerIntervention
from .models import canonical_sha256, require_sha256
from .provenance import read_gzip_shard, validate_provenance, write_gzip_shard
from .replay import (
    ReplayRequest,
    ReplayResult,
    common_activation_date,
    common_activation_target_gross,
    run_replay,
    validate_replay_accounting,
)
from .trace import RouteTraceRow, strip_intervention_provenance

COMMON_ACTIVATION_DATE = "COMMON_ACTIVATION_DATE"
NATIVE_ELIGIBILITY_DATE = "NATIVE_ELIGIBILITY_DATE"
NO_NATIVE_ELIGIBILITY = "NO_NATIVE_ELIGIBILITY"
_ECONOMIC_STATUSES = frozenset({"SUCCESS", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"})
_NEGATIVE_RULES = (
    "LOWEST_LIQUID_LEADER_SCORE",
    "NEGATIVE_RET120_AND_WEAK_TREND",
    "LOWEST_SECULAR_CONFIDENCE_FAILING_ABSOLUTE",
)


@dataclass(frozen=True, slots=True)
class EligibilityObservation:
    """One symbol's point-in-time absolute-owner evidence at one close."""

    symbol: str
    date: str
    visible_sessions: int
    liquidity_confirmed: bool
    leader_score: float
    leader_confidence: float
    secular_score: float
    secular_confidence: float
    momentum60: float
    momentum120: float
    relative_strength: float
    trend_persistence: float
    ret120: float
    risk: str
    opportunity: str
    independent_market_confirmation: bool

    @property
    def native_eligible(self) -> bool:
        """Apply every frozen one-owner predicate without a future observation."""

        return bool(
            self.visible_sessions >= 241
            and self.liquidity_confirmed
            and self.leader_score >= DEFAULT_CONFIG.strategic_one_name_min_score
            and self.secular_score >= DEFAULT_CONFIG.strategic_one_name_min_secular_score
            and self.leader_confidence >= DEFAULT_CONFIG.leader_min_confidence
            and self.momentum60 >= DEFAULT_CONFIG.strategic_current_factor_floor
            and self.momentum120 >= DEFAULT_CONFIG.strategic_current_factor_floor
            and self.relative_strength >= DEFAULT_CONFIG.strategic_current_factor_floor
            and self.trend_persistence >= 2 / 3
            and self.risk == "NORMAL"
            and self.opportunity in {"TREND", "STRONG_TREND"}
            and self.independent_market_confirmation
        )

    def evidence(self) -> dict[str, Any]:
        """Return literal causal inputs and frozen threshold results."""

        payload = asdict(self)
        nonfinite_fields = sorted(
            key
            for key, value in payload.items()
            if isinstance(value, float) and not isfinite(value)
        )
        for field in nonfinite_fields:
            payload[field] = None
        payload["nonfinite_fields"] = nonfinite_fields
        payload["native_eligible"] = self.native_eligible
        payload["thresholds"] = {
            "strategic_one_name_min_score": DEFAULT_CONFIG.strategic_one_name_min_score,
            "strategic_one_name_min_secular_score": DEFAULT_CONFIG.strategic_one_name_min_secular_score,
            "leader_min_confidence": DEFAULT_CONFIG.leader_min_confidence,
            "strategic_current_factor_floor": DEFAULT_CONFIG.strategic_current_factor_floor,
            "trend_persistence_floor": 2 / 3,
        }
        return payload


@dataclass(frozen=True, slots=True)
class ForcedOwnerControl:
    """One frozen control identity; owner overlap does not collapse experiments."""

    control_id: str
    owner: str
    owner_role: str


@dataclass(frozen=True, slots=True)
class ForcedOwnerCell:
    """A retained required matrix cell, including terminal non-economic rows."""

    control_id: str
    owner: str
    mode: str
    intervention_date: str | None
    status: str
    selection_evidence: Mapping[str, Any]
    metrics: Mapping[str, Any] | None
    metric_null_reasons: Mapping[str, str]
    final_account_sha256: str | None
    trace_sha256: str | None
    intervention_count: int
    intervention_provenance: Mapping[str, Any] | None
    error: str | None = None

    @property
    def cell_id(self) -> str:
        return f"{self.control_id}:{self.owner}:{self.mode}"

    @classmethod
    def no_native(
        cls,
        *,
        control_id: str,
        owner: str,
        selection_evidence: Mapping[str, Any],
    ) -> ForcedOwnerCell:
        """Create the literal no-date terminal cell with no intervention audit."""

        return cls(
            control_id=control_id,
            owner=owner,
            mode=NATIVE_ELIGIBILITY_DATE,
            intervention_date=None,
            status=NO_NATIVE_ELIGIBILITY,
            selection_evidence=selection_evidence,
            metrics=None,
            metric_null_reasons={"all_economic_metrics": NO_NATIVE_ELIGIBILITY},
            final_account_sha256=None,
            trace_sha256=None,
            intervention_count=0,
            intervention_provenance=None,
        )

    def compact(self) -> dict[str, Any]:
        """Serialize one cell without embedding a large route trace."""

        return {
            "cell_id": self.cell_id,
            "control_id": self.control_id,
            "owner": self.owner,
            "mode": self.mode,
            "intervention_date": self.intervention_date,
            "status": self.status,
            "selection_evidence": dict(self.selection_evidence),
            "metrics": None if self.metrics is None else dict(self.metrics),
            "metric_null_reasons": dict(self.metric_null_reasons),
            "final_account_sha256": self.final_account_sha256,
            "trace_sha256": self.trace_sha256,
            "intervention_count": self.intervention_count,
            "intervention_provenance": (
                None
                if self.intervention_provenance is None
                else dict(self.intervention_provenance)
            ),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ForcedOwnerTraceReadback:
    """Canonical full-route shard readback used by execution and resume."""

    metadata: Mapping[str, Any]
    provenance: Mapping[str, Any]
    cells: tuple[ForcedOwnerCell, ...]
    results: Mapping[str, ReplayResult]


def activation_from_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    """Return the first causal strategic activation from ordered baseline facts."""

    previous_epoch = 0
    for row in rows:
        date = row.get("date")
        epoch = row.get("strategic_epoch", 0)
        targets = row.get("strategic_targets", ())
        if not isinstance(date, str) or not date:
            raise ValueError("activation evidence requires a date")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise ValueError("activation evidence strategic epoch is malformed")
        if not isinstance(targets, (tuple, list)):
            raise ValueError("activation evidence strategic targets are malformed")
        if epoch > previous_epoch or bool(targets):
            return date
        previous_epoch = epoch
    raise ValueError("baseline has no causal strategic activation")


def select_negative_controls(observations: Sequence[EligibilityObservation]) -> dict[str, str]:
    """Deterministically select every preregistered negative category at D only."""

    if not observations:
        raise ValueError("negative controls require activation-date observations")
    liquid = [item for item in observations if item.visible_sessions >= 241 and item.liquidity_confirmed]
    weak = [item for item in observations if item.ret120 < 0.0 and item.trend_persistence < 2 / 3]
    failing = [item for item in observations if not item.native_eligible]
    groups = (liquid, weak, failing)
    if any(not group for group in groups):
        missing = _NEGATIVE_RULES[next(index for index, group in enumerate(groups) if not group)]
        raise ValueError(f"negative control category has no causal candidate: {missing}")
    return {
        _NEGATIVE_RULES[0]: min(liquid, key=lambda item: (item.leader_score, item.symbol)).symbol,
        _NEGATIVE_RULES[1]: min(weak, key=lambda item: item.symbol).symbol,
        _NEGATIVE_RULES[2]: min(
            failing, key=lambda item: (item.secular_confidence, item.leader_score, item.symbol)
        ).symbol,
    }


def enumerate_forced_owner_controls(
    *,
    positive_controls: Sequence[str],
    negative_controls: Mapping[str, str],
) -> tuple[ForcedOwnerControl, ...]:
    """Enumerate five positive and three labeled negative controls without owner deduplication."""

    positives = tuple(positive_controls)
    if len(positives) != 5 or len(set(positives)) != 5:
        raise ValueError("forced-owner controls require five unique positive symbols")
    if set(negative_controls) != set(_NEGATIVE_RULES):
        raise ValueError("forced-owner negative control identities differ")
    controls = (
        *(
            ForcedOwnerControl(
                control_id=f"POSITIVE_CONTROL:{owner}",
                owner=owner,
                owner_role="POSITIVE_CONTROL",
            )
            for owner in positives
        ),
        *(
            ForcedOwnerControl(
                control_id=rule,
                owner=negative_controls[rule],
                owner_role=rule,
            )
            for rule in _NEGATIVE_RULES
        ),
    )
    if len({control.control_id for control in controls}) != 8:
        raise ValueError("forced-owner control identities contain duplicates")
    return tuple(controls)


def required_forced_owner_cell_ids(
    controls: Sequence[ForcedOwnerControl],
) -> tuple[str, ...]:
    """Return the exact frozen control-by-mode cell identifiers."""

    identifiers = tuple(
        f"{control.control_id}:{control.owner}:{mode}"
        for control in controls
        for mode in (COMMON_ACTIVATION_DATE, NATIVE_ELIGIBILITY_DATE)
    )
    if len(identifiers) != 16 or len(set(identifiers)) != 16:
        raise ValueError("forced-owner matrix must contain exactly 16 unique cell identifiers")
    return identifiers


def _independent_market_confirmation(risk: Mapping[str, Any], cfg: SystemConfig) -> bool:
    """Mirror the production discovery predicate over the decision's own evidence."""

    return bool(
        float(risk.get("breadth20", cfg.high_confidence_entry_breadth)) >= cfg.high_confidence_entry_breadth
        and float(risk.get("broad_ret20", 0.0)) >= cfg.strategic_transition_impulse_min_market_ret20
        and float(risk.get("tech_ret20", 0.0)) >= cfg.strategic_transition_impulse_min_market_ret20
        and max(float(risk.get("broad_ret120", 0.0)), float(risk.get("tech_ret120", 0.0)))
        > cfg.recovery_transition_weak_leg_ret120
        and max(
            float(risk.get("broad_ret120", risk.get("tech_ret120", float("inf")))),
            float(risk.get("tech_ret120", float("inf"))),
        )
        <= cfg.strategic_long_cycle_max_tech_ret120
    )


def _observations_from_engine(
    *,
    engine: ProductionEngine,
    owners: Sequence[str],
    owner_roles: Mapping[str, Sequence[str]] | None = None,
    session: pd.Timestamp,
    decision_risk: Mapping[str, Any],
    opportunity: str,
) -> tuple[EligibilityObservation, ...]:
    """Extract the production factor families visible at this close only."""

    references = engine.workspace.filter_reference_symbols(resolve_reference_symbols(session))
    loaded = set(engine.workspace.loaded_symbols)
    panel = {
        symbol: engine.workspace.feature_frame(symbol)
        for symbol in set(references) | set(owners)
        if symbol in loaded
    }
    structural = compute_structural_leaders(
        panel,
        as_of=session,
        tech=engine.workspace.feature_frame("sh000682"),
        cfg=engine.cfg,
    )
    alpha = apply_opportunity_alpha(structural, opportunity=opportunity, cfg=engine.cfg)
    observations: list[EligibilityObservation] = []
    for owner in owners:
        frame = panel.get(owner)
        leader = alpha.get(owner)
        if frame is None or leader is None or session not in frame.index:
            continue
        components = leader.components
        history = int(frame.index.searchsorted(session, side="right"))
        row = frame.loc[session]
        observations.append(
            EligibilityObservation(
                symbol=owner,
                date=str(session.date()),
                visible_sessions=history,
                liquidity_confirmed=engine.allocator._liquidity_confirmed(frame, session),
                leader_score=float(leader.score),
                leader_confidence=float(leader.confidence),
                secular_score=float(components.get("secular_score", 0.0)),
                secular_confidence=float(components.get("secular_confidence", 0.0)),
                momentum60=float(components.get("momentum60", 0.0)),
                momentum120=float(components.get("momentum120", 0.0)),
                relative_strength=float(components.get("relative_strength", 0.0)),
                trend_persistence=float(components.get("trend_persistence", 0.0)),
                ret120=float(row.get(f"ret{engine.cfg.trend_slow}", 0.0)),
                risk=str(decision_risk.get("state", "")),
                opportunity=opportunity,
                independent_market_confirmation=_independent_market_confirmation(decision_risk, engine.cfg),
            )
        )
    return tuple(observations)


def scan_native_eligibility(
    data_dir: str | Path,
    *,
    symbols: tuple[str, ...],
    owner: str,
    start: str,
    end: str,
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> tuple[EligibilityObservation, ...]:
    """Run production decisions daily and retain only point-in-time owner evidence."""

    return scan_native_eligibilities(
        data_dir, symbols=symbols, owners=(owner,), start=start, end=end, cfg=cfg
    ).get(owner, ())


def scan_native_eligibilities(
    data_dir: str | Path,
    *,
    symbols: tuple[str, ...],
    owners: Sequence[str],
    start: str,
    end: str,
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> dict[str, tuple[EligibilityObservation, ...]]:
    """Scan all owners in one production daily pass, avoiding selection drift."""

    engine = ProductionEngine(data_dir, cfg)
    engine.data = CausalReplayDataStore(data_dir)
    harness = ReplayHarness(workspace=engine.workspace, universe=CandidateRunner(data_dir, cfg).replay_universe(symbols))
    sessions = harness.sessions(start=start, end=end)
    if len(sessions) < 2:
        raise RuntimeError("native eligibility scan has fewer than two sessions")
    account = AccountState.empty(cfg.initial_cash)
    panel = harness.raw_panel(symbols)
    observations: dict[str, list[EligibilityObservation]] = {owner: [] for owner in owners}
    for session in sessions:
        engine.execution.execute_open(date=session, account=account, panel=panel)
        decision = engine.decide(symbols=symbols, as_of=str(session.date()), account=account)
        account.pending_orders = list(decision.pending_orders)
        items = _observations_from_engine(
            engine=engine,
            owners=owners,
            session=session,
            decision_risk={"state": decision.risk.value, **decision.risk_summary},
            opportunity=decision.opportunity.value,
        )
        for item in items:
            observations[item.symbol].append(item)
    return {owner: tuple(items) for owner, items in observations.items()}


def first_native_eligibility(observations: Sequence[EligibilityObservation]) -> EligibilityObservation | None:
    """Select the first qualifying date in chronological scan order."""

    return next((item for item in observations if item.native_eligible), None)


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _trace_metrics(result: ReplayResult, *, initial_cash: float) -> tuple[dict[str, Any], dict[str, str]]:
    """Complete the preregistered compact field set from durable replay facts."""

    raw = result.metrics
    trace = result.trace
    targets = [row.target_gross for row in trace]
    positive = [value for value in targets if value > 0.0]
    reductions = [
        row.date
        for prior, row in pairwise(trace)
        if row.target_gross + 1e-12 < prior.target_gross
    ]
    zero_runs: list[int] = []
    run = 0
    for value in targets:
        run = run + 1 if value <= 0.0 else 0
        zero_runs.append(run)
    positive_runs: list[int] = []
    run = 0
    for value in targets:
        run = run + 1 if value > 0.0 else 0
        positive_runs.append(run)
    final = float(raw.get("final_equity", 0.0))
    saw_positive = False
    full_exit: str | None = None
    for row in trace:
        if row.target_gross > 0.0:
            saw_positive = True
        elif saw_positive:
            full_exit = row.date
            break
    metrics: dict[str, Any] = {
        "final_wealth": _safe_ratio(final, initial_cash),
        "final_equity": final,
        "total_return": raw.get("total_return"),
        "cagr": raw.get("cagr"),
        "max_drawdown": raw.get("max_drawdown"),
        "sharpe": raw.get("sharpe"),
        "calmar": raw.get("calmar"),
        "orders": raw.get("account_orders"),
        "fills": len(result.final_account.get("fills", ())),
        "gross_turnover": raw.get("gross_turnover"),
        "annual_turnover": raw.get("annual_turnover"),
        "fees": raw.get("fees"),
        "slippage_cost": raw.get("slippage_cost"),
        "partial_fill_loss": None,
        "active_target_sessions": len(positive),
        "positive_target_sessions": len(positive),
        "first_reduction": reductions[0] if reductions else None,
        "full_exit": full_exit,
        "longest_hold_sessions": max(positive_runs, default=0),
        "longest_zero_target_sessions": max(zero_runs, default=0),
        "mean_target_gross": sum(targets) / len(targets) if targets else None,
        "max_target_gross": max(targets, default=None),
        "restore_events": sum(1 for event in raw.get("risk_events", ()) if "restore" in str(event).lower()),
        "sector_guard_events": sum(1 for event in raw.get("risk_events", ()) if "sector_guard" in str(event).lower()),
        "capital_budget_level_time": dict(Counter(str(row.risk.get("capital_budget_level", 0)) for row in trace)),
        "risk_state_time": dict(Counter(str(row.risk.get("state", "")) for row in trace)),
        "realized_pnl": None,
        "open_pnl": None,
        "accounting_reconciled": True,
        "top1_pnl_concentration": None,
        "tracking_error": None,
    }
    nulls = {
        "partial_fill_loss": "production ledger does not attribute opportunity loss from partial fills",
        "realized_pnl": "durable account ledger retains fills but not a lot-level realized PnL allocation",
        "open_pnl": "durable account ledger retains marks but not a lot-level open PnL allocation",
        "top1_pnl_concentration": "durable fill ledger has no lot-level realized PnL attribution",
        "tracking_error": "contract does not preregister a daily benchmark return series for this cell",
    }
    return metrics, nulls


def replay_trace_sha256(result: ReplayResult) -> str | None:
    """Seal the economic route while excluding audit-only intervention provenance."""

    if not result.trace:
        return None
    return canonical_sha256({"trace": [asdict(row) for row in strip_intervention_provenance(result.trace)]})


def routes_canonically_equal(
    left: Sequence[RouteTraceRow],
    right: Sequence[RouteTraceRow],
) -> bool:
    """Compare economic routes after canonical JSON tuple/list normalization."""

    left_rows = [asdict(row) for row in strip_intervention_provenance(left)]
    right_rows = [asdict(row) for row in strip_intervention_provenance(right)]
    return canonical_sha256({"trace": left_rows}) == canonical_sha256({"trace": right_rows})


def forced_owner_cell_from_result(
    *,
    control_id: str,
    owner: str,
    mode: str,
    intervention_date: str,
    selection_evidence: Mapping[str, Any],
    result: ReplayResult,
    initial_cash: float = DEFAULT_CONFIG.initial_cash,
) -> ForcedOwnerCell:
    """Retain one replay result with its one-shot audit, including terminal errors."""

    provenance = (
        None
        if result.intervention_provenance is None
        else dict(result.intervention_provenance)
    )
    intervention_count = int(bool(provenance and provenance.get("applied") is True))
    trace_sha256 = replay_trace_sha256(result)
    if result.status != "SUCCESS":
        return ForcedOwnerCell(
            control_id=control_id,
            owner=owner,
            mode=mode,
            intervention_date=intervention_date,
            status=result.status,
            selection_evidence=selection_evidence,
            metrics=None,
            metric_null_reasons={"all_economic_metrics": result.status},
            final_account_sha256=None,
            trace_sha256=trace_sha256,
            intervention_count=intervention_count,
            intervention_provenance=provenance,
            error=result.error,
        )
    validate_replay_accounting(result)
    metrics, nulls = _trace_metrics(result, initial_cash=initial_cash)
    if intervention_count != 1:
        raise ValueError("successful forced-owner cell lacks its one-shot intervention audit")
    return ForcedOwnerCell(
        control_id=control_id,
        owner=owner,
        mode=mode,
        intervention_date=intervention_date,
        status="SUCCESS",
        selection_evidence=selection_evidence,
        metrics=metrics,
        metric_null_reasons=nulls,
        final_account_sha256=result.trace[-1].account_sha256,
        trace_sha256=trace_sha256,
        intervention_count=intervention_count,
        intervention_provenance=provenance,
    )


def run_forced_owner_economic_cell(
    data_dir: str | Path,
    *,
    control_id: str,
    symbols: tuple[str, ...],
    owner: str,
    mode: str,
    date: str,
    target_gross: float,
    selection_evidence: Mapping[str, Any],
    start: str,
    end: str,
    cfg: SystemConfig,
) -> tuple[ForcedOwnerCell, ReplayResult]:
    result = run_replay(
        data_dir,
        ReplayRequest(
            symbols=symbols,
            start=start,
            end=end,
            scenario=f"forced-owner:{control_id}:{owner}:{mode}",
            intervention_date=date,
        ),
        cfg=cfg,
        intervention=StrategicOwnerIntervention(
            owner=owner,
            target_gross=target_gross,
            intervention_date=date,
        ),
    )
    cell = forced_owner_cell_from_result(
        control_id=control_id,
        owner=owner,
        mode=mode,
        intervention_date=date,
        selection_evidence=selection_evidence,
        result=result,
        initial_cash=cfg.initial_cash,
    )
    return cell, result


def build_forced_owner_matrix(
    data_dir: str | Path,
    *,
    contract: StrategicEvidenceContract,
    symbols: tuple[str, ...],
    controls: Sequence[ForcedOwnerControl],
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> tuple[ForcedOwnerCell, ...]:
    """Build both required modes, retaining no-native-eligibility cells literally."""

    start, end = contract.window["start"], contract.window["end"]
    baseline = run_replay(data_dir, ReplayRequest(symbols=symbols, start=start, end=end), cfg=cfg)
    if baseline.status != "SUCCESS":
        raise RuntimeError(f"baseline required replay failed: {baseline.status}: {baseline.error}")
    activation = common_activation_date(baseline)
    target_gross = common_activation_target_gross(baseline)
    cells: list[ForcedOwnerCell] = []
    required_forced_owner_cell_ids(controls)
    unique_owners = tuple(dict.fromkeys(control.owner for control in controls))
    scans = scan_native_eligibilities(
        data_dir, symbols=symbols, owners=unique_owners, start=start, end=end, cfg=cfg
    )
    for control in controls:
        owner = control.owner
        common_evidence = {
            "baseline_activation_date": activation,
            "baseline_target_gross": target_gross,
            "owner_role": control.owner_role,
        }
        common_cell, _ = run_forced_owner_economic_cell(
            data_dir,
            control_id=control.control_id,
            symbols=symbols,
            owner=owner,
            mode=COMMON_ACTIVATION_DATE,
            date=activation,
            target_gross=target_gross,
            selection_evidence=common_evidence,
            start=start,
            end=end,
            cfg=cfg,
        )
        cells.append(common_cell)
        observations = scans[owner]
        native = first_native_eligibility(observations)
        if native is None:
            cells.append(
                ForcedOwnerCell.no_native(
                    control_id=control.control_id,
                    owner=owner,
                    selection_evidence={
                    "daily_scan_sessions": len(observations),
                    "first_native_eligibility": None,
                        "owner_role": control.owner_role,
                    },
                )
            )
        else:
            native_cell, _ = run_forced_owner_economic_cell(
                data_dir,
                control_id=control.control_id,
                symbols=symbols,
                owner=owner,
                mode=NATIVE_ELIGIBILITY_DATE,
                date=native.date,
                target_gross=target_gross,
                selection_evidence={**native.evidence(), "owner_role": control.owner_role},
                start=start,
                end=end,
                cfg=cfg,
            )
            cells.append(native_cell)
    validate_required_coverage(cells, controls=controls)
    return tuple(cells)


def validate_required_coverage(
    cells: Sequence[ForcedOwnerCell],
    *,
    controls: Sequence[ForcedOwnerControl],
) -> None:
    """Fail closed when a mode, terminal result, or complete-cell metric is absent."""

    expected = set(required_forced_owner_cell_ids(controls))
    present = {cell.cell_id for cell in cells}
    if present != expected or len(present) != len(cells):
        raise ValueError("forced-owner required cell coverage differs")
    for cell in cells:
        if cell.status not in _ECONOMIC_STATUSES | {NO_NATIVE_ELIGIBILITY}:
            raise ValueError("forced-owner cell status is not terminal")
        if cell.status == "SUCCESS":
            if cell.metrics is None or cell.final_account_sha256 is None or cell.trace_sha256 is None:
                raise ValueError("complete forced-owner cell lacks economic evidence")
            required = {"final_wealth", "total_return", "cagr", "max_drawdown", "sharpe", "calmar", "orders", "fills", "fees", "slippage_cost", "accounting_reconciled"}
            if not required <= set(cell.metrics):
                raise ValueError("complete forced-owner cell lacks preregistered metrics")
        elif cell.metrics is not None:
            raise ValueError("terminal non-economic forced-owner cell carries metrics")
        if cell.status == NO_NATIVE_ELIGIBILITY:
            if cell.intervention_count != 0 or cell.intervention_provenance is not None:
                raise ValueError("no-native forced-owner cell carries intervention audit")
        elif cell.intervention_provenance is not None:
            applied = cell.intervention_provenance.get("applied") is True
            if cell.intervention_count != int(applied):
                raise ValueError("forced-owner intervention count and audit differ")


def compact_summary(
    *,
    contract: StrategicEvidenceContract,
    cells: Sequence[ForcedOwnerCell],
    controls: Sequence[ForcedOwnerControl],
    symbols: Sequence[str],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a sealed compact checkpoint payload; large traces remain external."""

    validate_required_coverage(cells, controls=controls)
    payload = {
        "schema_version": 1,
        "contract_payload_sha256": contract.payload_sha256,
        "provenance": validate_provenance(provenance),
        "window": {**contract.window, "future_holdout_boundary": contract.future_holdout_boundary},
        "universe": list(symbols),
        "controls": [asdict(control) for control in controls],
        "large_traces_committed": False,
        "large_trace_absence_reason": "full route JSONL-gzip shards are resumable workflow artifacts outside Git",
        "status_counts": dict(sorted(Counter(cell.status for cell in cells).items())),
        "cells": [cell.compact() for cell in cells],
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    return payload


def write_forced_owner_shard(
    path: str | Path,
    *,
    cells: Sequence[ForcedOwnerCell],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Write and immediately read back the resumable, trace-free matrix shard."""

    envelope = write_gzip_shard(path, rows=(cell.compact() for cell in cells), provenance=provenance)
    readback = read_gzip_shard(path)
    if readback["rows"] != tuple(cell.compact() for cell in cells):
        raise ValueError("forced-owner shard readback differs")
    return envelope


def write_forced_owner_trace_shard(
    path: str | Path,
    *,
    cells: Sequence[ForcedOwnerCell],
    results: Mapping[str, ReplayResult],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal cell headers plus every available full route row outside Git."""

    cell_by_id = {cell.cell_id: cell for cell in cells}
    if len(cell_by_id) != len(cells):
        raise ValueError("forced-owner trace shard cell identifiers contain duplicates")
    required_result_ids = {
        cell.cell_id for cell in cells if cell.status != NO_NATIVE_ELIGIBILITY
    }
    if set(results) != required_result_ids:
        raise ValueError("forced-owner trace shard result linkage differs")
    rows: list[dict[str, Any]] = []
    for cell_id in sorted(cell_by_id):
        cell = cell_by_id[cell_id]
        result = results.get(cell_id)
        trace = () if result is None else result.trace
        if result is not None and result.status != cell.status:
            raise ValueError("forced-owner trace shard result status differs from cell")
        result_payload = None
        if result is not None:
            result_payload = {
                "request": asdict(result.request),
                "metrics": dict(result.metrics),
                "final_account": dict(result.final_account),
                "intervention_provenance": (
                    None
                    if result.intervention_provenance is None
                    else dict(result.intervention_provenance)
                ),
                "status": result.status,
                "error": result.error,
            }
        rows.append(
            _seal_trace_record(
                {
                    "record_type": "CELL",
                    "cell_id": cell_id,
                    "cell": cell.compact(),
                    "result": result_payload,
                    "route_row_count": len(trace),
                    "full_trace_sha256": canonical_sha256(
                        {"trace": [asdict(row) for row in trace]}
                    ),
                }
            )
        )
        rows.extend(
            _seal_trace_record(
                {
                    "record_type": "ROUTE",
                    "cell_id": cell_id,
                    "route_index": index,
                    "trace": asdict(row),
                }
            )
            for index, row in enumerate(trace)
        )
    write_gzip_shard(path, rows=rows, provenance=provenance)
    return dict(
        verify_forced_owner_trace_shard(
            path,
            expected_cells=cells,
            expected_provenance=provenance,
        ).metadata
    )


def _seal_trace_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(payload)
    sealed["row_sha256"] = canonical_sha256({"forced_owner_trace_row": sealed})
    return sealed


def _verify_trace_record(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("forced-owner trace shard row must be an object")
    payload = dict(value)
    seal = require_sha256(payload.pop("row_sha256", None), field="forced-owner row_sha256")
    if seal != canonical_sha256({"forced_owner_trace_row": payload}):
        raise ValueError("forced-owner trace shard row seal differs")
    return payload


def forced_owner_cell_from_compact(value: object) -> ForcedOwnerCell:
    if not isinstance(value, Mapping):
        raise ValueError("forced-owner trace shard compact cell is malformed")
    raw = dict(value)
    expected_fields = {
        "cell_id",
        "control_id",
        "owner",
        "mode",
        "intervention_date",
        "status",
        "selection_evidence",
        "metrics",
        "metric_null_reasons",
        "final_account_sha256",
        "trace_sha256",
        "intervention_count",
        "intervention_provenance",
        "error",
    }
    if set(raw) != expected_fields:
        raise ValueError("forced-owner trace shard compact cell fields differ")
    selection = raw["selection_evidence"]
    metrics = raw["metrics"]
    nulls = raw["metric_null_reasons"]
    intervention = raw["intervention_provenance"]
    if (
        not isinstance(selection, Mapping)
        or (metrics is not None and not isinstance(metrics, Mapping))
        or not isinstance(nulls, Mapping)
        or (intervention is not None and not isinstance(intervention, Mapping))
    ):
        raise ValueError("forced-owner trace shard compact cell shape differs")
    count = raw["intervention_count"]
    if isinstance(count, bool) or not isinstance(count, int):
        raise ValueError("forced-owner trace shard intervention count is malformed")
    cell = ForcedOwnerCell(
        control_id=str(raw["control_id"]),
        owner=str(raw["owner"]),
        mode=str(raw["mode"]),
        intervention_date=(
            None if raw["intervention_date"] is None else str(raw["intervention_date"])
        ),
        status=str(raw["status"]),
        selection_evidence=dict(selection),
        metrics=None if metrics is None else dict(metrics),
        metric_null_reasons={str(key): str(item) for key, item in nulls.items()},
        final_account_sha256=(
            None
            if raw["final_account_sha256"] is None
            else str(raw["final_account_sha256"])
        ),
        trace_sha256=None if raw["trace_sha256"] is None else str(raw["trace_sha256"]),
        intervention_count=count,
        intervention_provenance=(
            None if intervention is None else dict(intervention)
        ),
        error=None if raw["error"] is None else str(raw["error"]),
    )
    if raw["cell_id"] != cell.cell_id:
        raise ValueError("forced-owner trace shard cell identity is inconsistent")
    return cell


def _route_from_mapping(value: object) -> RouteTraceRow:
    if not isinstance(value, Mapping):
        raise ValueError("forced-owner route row is malformed")
    raw = dict(value)
    try:
        return RouteTraceRow(
            date=str(raw["date"]),
            reference_context=dict(raw["reference_context"]),
            leaders=tuple(dict(item) for item in raw["leaders"]),
            risk=dict(raw["risk"]),
            opportunity=str(raw["opportunity"]),
            targets=tuple(dict(item) for item in raw["targets"]),
            orders=tuple(dict(item) for item in raw["orders"]),
            fills=tuple(dict(item) for item in raw["fills"]),
            account_sha256=str(raw["account_sha256"]),
            equity=float(raw["equity"]),
            target_gross=raw["target_gross"],
            intervention_provenance=(
                None
                if raw["intervention_provenance"] is None
                else dict(raw["intervention_provenance"])
            ),
            cash=float(raw["cash"]),
            position_shares={str(key): int(item) for key, item in raw["position_shares"].items()},
            close_marks={str(key): float(item) for key, item in raw["close_marks"].items()},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("forced-owner route row is malformed") from exc


def _result_from_payload(value: object, *, trace: tuple[RouteTraceRow, ...]) -> ReplayResult:
    if not isinstance(value, Mapping):
        raise ValueError("forced-owner trace shard result is malformed")
    raw = dict(value)
    if set(raw) != {
        "request",
        "metrics",
        "final_account",
        "intervention_provenance",
        "status",
        "error",
    }:
        raise ValueError("forced-owner trace shard result fields differ")
    request = raw["request"]
    if not isinstance(request, Mapping):
        raise ValueError("forced-owner trace shard request is malformed")
    request_raw = dict(request)
    symbols = request_raw.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("forced-owner trace shard request symbols are malformed")
    request_raw["symbols"] = tuple(str(symbol) for symbol in symbols)
    metrics = raw["metrics"]
    final_account = raw["final_account"]
    intervention = raw["intervention_provenance"]
    if (
        not isinstance(metrics, Mapping)
        or not isinstance(final_account, Mapping)
        or (intervention is not None and not isinstance(intervention, Mapping))
    ):
        raise ValueError("forced-owner trace shard result shape differs")
    return ReplayResult(
        request=ReplayRequest(**request_raw),
        metrics=dict(metrics),
        trace=trace,
        final_account=dict(final_account),
        intervention_provenance=None if intervention is None else dict(intervention),
        status=str(raw["status"]),
        error=None if raw["error"] is None else str(raw["error"]),
    )


def verify_forced_owner_trace_shard(
    path: str | Path,
    *,
    expected_cells: Sequence[ForcedOwnerCell] | None = None,
    expected_provenance: Mapping[str, Any] | None = None,
) -> ForcedOwnerTraceReadback:
    """Canonically read back route rows, per-row seals, linkage, and byte identity."""

    shard_path = Path(path).resolve()
    raw_bytes = shard_path.read_bytes()
    try:
        uncompressed = gzip.decompress(raw_bytes)
    except (OSError, gzip.BadGzipFile) as exc:
        raise ValueError("forced-owner trace shard gzip is unreadable") from exc
    if raw_bytes != gzip.compress(uncompressed, compresslevel=9, mtime=0):
        raise ValueError("forced-owner trace shard gzip bytes are not canonical")
    generic = read_gzip_shard(shard_path)
    provenance = generic["provenance"]
    if expected_provenance is not None and provenance != validate_provenance(expected_provenance):
        raise ValueError("forced-owner trace shard provenance differs")
    headers: dict[str, dict[str, Any]] = {}
    routes: dict[str, list[tuple[int, Any]]] = {}
    for raw_row in generic["rows"]:
        row = _verify_trace_record(raw_row)
        record_type = row.get("record_type")
        cell_id = row.get("cell_id")
        if not isinstance(cell_id, str) or not cell_id:
            raise ValueError("forced-owner trace shard row lacks cell linkage")
        if record_type == "CELL":
            if set(row) != {
                "record_type",
                "cell_id",
                "cell",
                "result",
                "route_row_count",
                "full_trace_sha256",
            } or cell_id in headers:
                raise ValueError("forced-owner trace shard cell headers differ")
            headers[cell_id] = row
        elif record_type == "ROUTE":
            if set(row) != {"record_type", "cell_id", "route_index", "trace"}:
                raise ValueError("forced-owner trace shard route fields differ")
            index = row["route_index"]
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError("forced-owner trace shard route index is malformed")
            routes.setdefault(cell_id, []).append((index, _route_from_mapping(row["trace"])))
        else:
            raise ValueError("forced-owner trace shard record type differs")
    if set(routes) - set(headers):
        raise ValueError("forced-owner trace shard route references an absent cell")
    cells: list[ForcedOwnerCell] = []
    results: dict[str, ReplayResult] = {}
    route_counts: dict[str, int] = {}
    for cell_id in sorted(headers):
        header = headers[cell_id]
        cell = forced_owner_cell_from_compact(header["cell"])
        if cell_id != cell.cell_id:
            raise ValueError("forced-owner trace shard header linkage differs")
        indexed = sorted(routes.get(cell_id, ()), key=lambda item: item[0])
        if [index for index, _ in indexed] != list(range(len(indexed))):
            raise ValueError("forced-owner trace shard route indices differ")
        trace = tuple(row for _, row in indexed)
        if header["route_row_count"] != len(trace):
            raise ValueError("forced-owner trace shard route row count differs")
        require_sha256(header["full_trace_sha256"], field="full_trace_sha256")
        if header["full_trace_sha256"] != canonical_sha256(
            {"trace": [asdict(row) for row in trace]}
        ):
            raise ValueError("forced-owner trace shard full trace seal differs")
        result_payload = header["result"]
        if result_payload is None:
            if cell.status != NO_NATIVE_ELIGIBILITY or trace:
                raise ValueError("forced-owner trace shard missing an economic result")
        else:
            result = _result_from_payload(result_payload, trace=trace)
            if result.status != cell.status:
                raise ValueError("forced-owner trace shard result and cell statuses differ")
            if result.intervention_provenance != cell.intervention_provenance:
                raise ValueError("forced-owner trace shard intervention audits differ")
            if replay_trace_sha256(result) != cell.trace_sha256:
                raise ValueError("forced-owner trace shard economic trace seal differs")
            results[cell_id] = result
        cells.append(cell)
        route_counts[cell_id] = len(trace)
    if expected_cells is not None:
        expected = {cell.cell_id: cell.compact() for cell in expected_cells}
        observed = {cell.cell_id: cell.compact() for cell in cells}
        if len(expected) != len(expected_cells) or observed != expected:
            raise ValueError("forced-owner trace shard expected cell coverage differs")
    linkage = {
        cell_id: {
            "route_row_count": route_counts[cell_id],
            "status": headers[cell_id]["cell"]["status"],
        }
        for cell_id in sorted(headers)
    }
    metadata = {
        "path": str(shard_path),
        "byte_size": len(raw_bytes),
        "bytes_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "envelope_payload_sha256": generic["payload_sha256"],
        "provenance_sha256": canonical_sha256(dict(provenance)),
        "rows_sha256": generic["rows_sha256"],
        "row_count": generic["row_count"],
        "row_seal_count": len(generic["rows"]),
        "cell_count": len(headers),
        "cell_route_row_counts": route_counts,
        "cell_linkage_sha256": canonical_sha256({"cell_linkage": linkage}),
    }
    return ForcedOwnerTraceReadback(
        metadata=metadata,
        provenance=dict(provenance),
        cells=tuple(cells),
        results=results,
    )


__all__ = (
    "COMMON_ACTIVATION_DATE",
    "NATIVE_ELIGIBILITY_DATE",
    "NO_NATIVE_ELIGIBILITY",
    "EligibilityObservation",
    "ForcedOwnerCell",
    "ForcedOwnerControl",
    "ForcedOwnerTraceReadback",
    "activation_from_rows",
    "build_forced_owner_matrix",
    "compact_summary",
    "enumerate_forced_owner_controls",
    "first_native_eligibility",
    "forced_owner_cell_from_compact",
    "forced_owner_cell_from_result",
    "replay_trace_sha256",
    "required_forced_owner_cell_ids",
    "routes_canonically_equal",
    "run_forced_owner_economic_cell",
    "scan_native_eligibilities",
    "scan_native_eligibility",
    "select_negative_controls",
    "validate_required_coverage",
    "verify_forced_owner_trace_shard",
    "write_forced_owner_shard",
    "write_forced_owner_trace_shard",
)


def main() -> int:
    """Delegate the committed module entrypoint to the focused Task-3 runner."""

    from .forced_owner_runner import main as runner_main

    return runner_main()


if __name__ == "__main__":
    raise SystemExit(main())
