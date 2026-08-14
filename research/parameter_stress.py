"""Deterministic shared-parameter perturbation cases."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .candidate_search import (
    Scalar,
    enumerate_candidates,
    validate_economic_parameter_names,
    validate_shared_config,
)


@dataclass(frozen=True, slots=True)
class ParameterCase:
    """One named shared configuration in a parameter stress set."""

    name: str
    parameters: tuple[tuple[str, Scalar], ...]

    def config(self) -> dict[str, Scalar]:
        """Return an independent parameter mapping."""

        return dict(self.parameters)


def _bounded(value: float, bounds: tuple[float | None, float | None]) -> float:
    lower, upper = bounds
    if lower is not None:
        value = max(lower, value)
    if upper is not None:
        value = min(upper, value)
    return value


def one_at_a_time_perturbations(
    base: Mapping[str, Scalar],
    *,
    relative_deltas: Iterable[float] = (-0.10, 0.10),
    parameters: Iterable[str] | None = None,
    bounds: Mapping[str, tuple[float | None, float | None]] | None = None,
    include_base: bool = True,
) -> tuple[ParameterCase, ...]:
    """Vary one numeric knob at a time while every pool shares the result."""
    clean = validate_shared_config(base)
    selected = tuple(sorted(set(parameters or clean)))
    validate_economic_parameter_names(selected)
    limits = dict(bounds or {})
    cases: list[ParameterCase] = []
    if include_base:
        cases.append(ParameterCase("baseline", tuple(clean.items())))
    for name in selected:
        if name not in clean:
            raise ValueError(f"parameter stress key is absent from base config: {name}")
        original = clean[name]
        if isinstance(original, bool) or not isinstance(original, (int, float)):
            raise ValueError(f"parameter stress requires a numeric key: {name}")
        for delta in sorted(set(relative_deltas)):
            changed = _bounded(float(original) * (1.0 + delta), limits.get(name, (None, None)))
            value: int | float = round(changed) if isinstance(original, int) else changed
            candidate = validate_shared_config({**clean, name: value})
            direction = "minus" if delta < 0 else "plus"
            cases.append(
                ParameterCase(
                    f"{name}_{direction}_{abs(delta):.6g}",
                    tuple(candidate.items()),
                )
            )
    unique: dict[tuple[tuple[str, Scalar], ...], ParameterCase] = {}
    for case in cases:
        unique.setdefault(case.parameters, case)
    return tuple(unique.values())


def factorial_perturbations(
    base: Mapping[str, Scalar],
    grid: Mapping[str, Iterable[Scalar]],
    *,
    max_cases: int = 10_000,
) -> tuple[ParameterCase, ...]:
    """Build a bounded deterministic factorial stress set."""
    candidates = enumerate_candidates(grid, base=base)
    if len(candidates) > max_cases:
        raise ValueError(f"parameter grid expands to {len(candidates)} cases; limit is {max_cases}")
    return tuple(
        ParameterCase(f"factorial_{index:05d}", tuple(candidate.items()))
        for index, candidate in enumerate(candidates)
    )
