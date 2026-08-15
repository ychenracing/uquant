"""Fail-closed evidence types for independent subsystem ablations."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from .candidate_search import CandidateEvaluation, Scalar, validate_shared_config

_CELL_STATUSES = frozenset({"VALID", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"})


@dataclass(frozen=True, slots=True)
class AblationMetrics:
    """Raw material-value dimensions retained for one economic replay."""

    final_wealth: float
    max_drawdown: float
    account_orders: int
    acute_return: float | None
    gross_turnover: float
    annual_turnover: float
    top1_concentration: float
    top3_concentration: float
    pnl_hhi: float

    def __post_init__(self) -> None:
        numeric = (
            self.final_wealth,
            self.max_drawdown,
            self.gross_turnover,
            self.annual_turnover,
            self.top1_concentration,
            self.top3_concentration,
            self.pnl_hhi,
        )
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("ablation metrics must be finite")
        if self.final_wealth <= 0:
            raise ValueError("ablation final wealth must be positive")
        if not 0 <= self.max_drawdown <= 1:
            raise ValueError("ablation drawdown must be in [0, 1]")
        if (
            isinstance(self.account_orders, bool)
            or not isinstance(self.account_orders, int)
            or self.account_orders < 0
        ):
            raise ValueError("ablation account orders must be a nonnegative integer")
        if self.acute_return is not None and (
            not math.isfinite(self.acute_return) or self.acute_return <= -1
        ):
            raise ValueError("ablation acute return must be finite and greater than -1")
        if self.gross_turnover < 0 or self.annual_turnover < 0:
            raise ValueError("ablation turnover cannot be negative")
        concentrations = (
            self.top1_concentration,
            self.top3_concentration,
            self.pnl_hhi,
        )
        if any(not 0 <= value <= 1 for value in concentrations):
            raise ValueError("ablation concentration must be in [0, 1]")
        if self.top1_concentration > self.top3_concentration:
            raise ValueError("ablation top-1 concentration cannot exceed top-3")

    def to_dict(self) -> dict[str, float | int | None]:
        """Return the exact JSON evidence payload."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AblationCell:
    """One fixed-contract record, including frozen non-economic statuses."""

    contract: str
    cell_id: str
    status: str
    metrics: AblationMetrics | None

    def __post_init__(self) -> None:
        if not self.contract or not self.cell_id:
            raise ValueError("ablation cell requires contract and identifier")
        if self.status not in _CELL_STATUSES:
            raise ValueError("ablation cell status is invalid")
        if (self.status == "VALID") != (self.metrics is not None):
            raise ValueError("only valid ablation cells may carry metrics")


@dataclass(frozen=True, slots=True)
class AblationMetricDelta:
    """Variant-minus-baseline raw differences; Task 8 owns classification."""

    final_wealth: float
    max_drawdown: float
    account_orders: int
    acute_return: float | None
    gross_turnover: float
    annual_turnover: float
    top1_concentration: float
    top3_concentration: float
    pnl_hhi: float

    def to_dict(self) -> dict[str, float | int | None]:
        """Return only raw dimensions, never a conclusion."""
        return asdict(self)


def compare_cells(baseline: AblationCell, variant: AblationCell) -> AblationMetricDelta:
    """Return variant-minus-baseline dimensions for an identical valid cell."""
    if (baseline.contract, baseline.cell_id) != (variant.contract, variant.cell_id):
        raise ValueError("ablation comparison requires the identical contract cell")
    if baseline.status != "VALID" or variant.status != "VALID":
        raise ValueError("ablation comparison requires two valid economic cells")
    if baseline.metrics is None or variant.metrics is None:
        raise ValueError("ablation comparison metrics are missing")
    left = baseline.metrics
    right = variant.metrics
    if (left.acute_return is None) != (right.acute_return is None):
        raise ValueError("ablation acute-return coverage differs")
    acute: float | None = None
    if left.acute_return is not None:
        if right.acute_return is None:  # pragma: no cover - guarded by parity above
            raise AssertionError("ablation acute return parity invariant failed")
        acute = right.acute_return - left.acute_return
    return AblationMetricDelta(
        final_wealth=right.final_wealth - left.final_wealth,
        max_drawdown=right.max_drawdown - left.max_drawdown,
        account_orders=right.account_orders - left.account_orders,
        acute_return=acute,
        gross_turnover=right.gross_turnover - left.gross_turnover,
        annual_turnover=right.annual_turnover - left.annual_turnover,
        top1_concentration=right.top1_concentration - left.top1_concentration,
        top3_concentration=right.top3_concentration - left.top3_concentration,
        pnl_hhi=right.pnl_hhi - left.pnl_hhi,
    )


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("ablation aggregate requires valid economic cells")
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def aggregate_dimensions(cells: Iterable[AblationCell]) -> dict[str, float | int]:
    """Aggregate raw dimensions, including generalization tails and concentration."""
    ordered = tuple(cells)
    if not ordered or any(cell.status != "VALID" or cell.metrics is None for cell in ordered):
        raise ValueError("ablation aggregate requires valid economic cells")
    metrics = tuple(cell.metrics for cell in ordered if cell.metrics is not None)
    wealth = [item.final_wealth for item in metrics]
    drawdown = [item.max_drawdown for item in metrics]
    orders = [float(item.account_orders) for item in metrics]
    gross = [item.gross_turnover for item in metrics]
    annual = [item.annual_turnover for item in metrics]
    top1 = [item.top1_concentration for item in metrics]
    top3 = [item.top3_concentration for item in metrics]
    hhi = [item.pnl_hhi for item in metrics]
    acute = [item.acute_return for item in metrics if item.acute_return is not None]
    result: dict[str, float | int] = {
        "economic_cells": len(metrics),
        "median_final_wealth": float(median(wealth)),
        "worst_final_wealth": min(wealth),
        "p10_final_wealth": _quantile(wealth, 0.10),
        "median_max_drawdown": float(median(drawdown)),
        "worst_max_drawdown": max(drawdown),
        "p90_max_drawdown": _quantile(drawdown, 0.90),
        "total_account_orders": int(sum(orders)),
        "median_account_orders": float(median(orders)),
        "p90_account_orders": _quantile(orders, 0.90),
        "median_gross_turnover": float(median(gross)),
        "p90_gross_turnover": _quantile(gross, 0.90),
        "worst_gross_turnover": max(gross),
        "median_annual_turnover": float(median(annual)),
        "p90_annual_turnover": _quantile(annual, 0.90),
        "worst_annual_turnover": max(annual),
        "median_top1_concentration": float(median(top1)),
        "worst_top1_concentration": max(top1),
        "median_top3_concentration": float(median(top3)),
        "worst_top3_concentration": max(top3),
        "median_pnl_hhi": float(median(hhi)),
        "worst_pnl_hhi": max(hhi),
    }
    if acute:
        result.update(
            median_acute_return=float(median(acute)),
            worst_acute_return=min(acute),
            p10_acute_return=_quantile(acute, 0.10),
        )
    return result


@dataclass(frozen=True, slots=True)
class DecisionPoint:
    """Canonical economic state for first-divergence comparison."""

    date: str
    fields: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        names = tuple(name for name, _ in self.fields)
        if not self.date or names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("ablation decision point must be canonical")


@dataclass(frozen=True, slots=True)
class DecisionDivergence:
    """The first aligned decision whose economic fields differ."""

    date: str
    changed_fields: tuple[str, ...]
    baseline: DecisionPoint
    variant: DecisionPoint


def first_decision_divergence(
    baseline: Sequence[DecisionPoint],
    variant: Sequence[DecisionPoint],
    *,
    require: bool = False,
) -> DecisionDivergence | None:
    """Find first divergence and fail closed on misalignment or a required no-op."""
    left_dates = tuple(item.date for item in baseline)
    right_dates = tuple(item.date for item in variant)
    if left_dates != right_dates:
        raise ValueError("ablation decision traces require aligned dates")
    for left, right in zip(baseline, variant, strict=True):
        left_fields = dict(left.fields)
        right_fields = dict(right.fields)
        if set(left_fields) != set(right_fields):
            raise ValueError("ablation decision traces require aligned fields")
        changed = tuple(name for name in sorted(left_fields) if left_fields[name] != right_fields[name])
        if changed:
            return DecisionDivergence(left.date, changed, left, right)
    if require:
        raise ValueError("ablation experiment has no behavior divergence")
    return None


def validate_complete_coverage(
    expected: Sequence[tuple[str, str]],
    observed: Iterable[AblationCell],
) -> None:
    """Require exact cell identifiers and immutable known statuses."""
    expected_map = dict(expected)
    if len(expected_map) != len(expected):
        raise ValueError("ablation expected coverage contains duplicate cells")
    rows = tuple(observed)
    identifiers = tuple(cell.cell_id for cell in rows)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("ablation observed coverage contains duplicate cells")
    observed_map = {cell.cell_id: cell.status for cell in rows}
    if set(observed_map) != set(expected_map):
        raise ValueError("ablation observed coverage differs from the fixed contract")
    changed = sorted(
        identifier for identifier, status in expected_map.items() if observed_map[identifier] != status
    )
    if changed:
        raise ValueError(f"ablation observed status differs from frozen evidence: {changed}")


# Phase 1 compatibility helpers remain repository-local and are not used by
# the immutable Phase 2 registry.
@dataclass(frozen=True, slots=True)
class AblationCase:
    """One named shared configuration in a capability ablation set."""

    name: str
    parameters: tuple[tuple[str, Scalar], ...]

    def config(self) -> dict[str, Scalar]:
        """Return an independent parameter mapping."""
        return dict(self.parameters)


@dataclass(frozen=True, slots=True)
class AblationDelta:
    """Candidate-minus-baseline deltas for the legacy research helper."""

    name: str
    score: float
    wealth: float
    drawdown: float
    orders: float


def build_ablations(
    base: Mapping[str, Scalar],
    capabilities: Iterable[str],
    *,
    disabled_values: Mapping[str, Scalar] | None = None,
) -> tuple[AblationCase, ...]:
    """Build a stable baseline plus one disabled-capability config per case."""
    clean = validate_shared_config(base)
    disabled = dict(disabled_values or {})
    names = tuple(sorted(set(capabilities)))
    unknown = sorted(set(names) - set(clean))
    if unknown:
        raise ValueError(f"ablation capabilities are absent from base config: {unknown}")
    cases = [AblationCase("baseline", tuple(clean.items()))]
    for name in names:
        candidate = dict(clean)
        candidate[name] = disabled.get(name, False)
        candidate = validate_shared_config(candidate)
        cases.append(AblationCase(f"without_{name}", tuple(candidate.items())))
    return tuple(cases)


def compare_ablations(
    baseline: CandidateEvaluation,
    variants: Iterable[tuple[str, CandidateEvaluation]],
) -> tuple[AblationDelta, ...]:
    """Express every legacy ablation as candidate-minus-production deltas."""
    return tuple(
        AblationDelta(
            name=name,
            score=evaluation.score - baseline.score,
            wealth=evaluation.median_final_wealth - baseline.median_final_wealth,
            drawdown=evaluation.worst_drawdown - baseline.worst_drawdown,
            orders=evaluation.median_orders - baseline.median_orders,
        )
        for name, evaluation in sorted(variants, key=lambda item: item[0])
    )
