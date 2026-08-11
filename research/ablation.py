"""Deterministic one-capability-at-a-time ablation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .candidate_search import CandidateEvaluation, Scalar, validate_shared_config


@dataclass(frozen=True, slots=True)
class AblationCase:
    name: str
    parameters: tuple[tuple[str, Scalar], ...]

    def config(self) -> dict[str, Scalar]:
        return dict(self.parameters)


@dataclass(frozen=True, slots=True)
class AblationDelta:
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
    """Express every ablation as candidate-minus-production deltas."""
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
