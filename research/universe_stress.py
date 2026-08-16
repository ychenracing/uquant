"""Repository-local adapter to the canonical generalization scenario contract."""

from __future__ import annotations

from dataclasses import dataclass

from uquant.validation.generalization import PreWindowEvidence
from uquant.validation.generalization_contract import build_official_scenarios, official_windows


@dataclass(frozen=True, slots=True)
class UniverseCase:
    """Legacy research view of one canonical economic scenario."""

    name: str
    symbols: tuple[str, ...]
    family: str
    seed: int | None = None


def canonical_universe_cases(
    *,
    evidence: PreWindowEvidence,
    window_name: str,
) -> tuple[UniverseCase, ...]:
    """Return economic cases without owning any universe or sampling rules."""
    window = official_windows((window_name,))[0]
    return tuple(
        UniverseCase(
            name=scenario.name,
            symbols=scenario.symbols,
            family=scenario.family,
            seed=scenario.seed_index,
        )
        for scenario in build_official_scenarios(window=window, evidence=evidence)
        if scenario.economic
    )
