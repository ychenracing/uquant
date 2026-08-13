"""Causal, deterministic universe perturbation construction."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UniverseCase:
    """One named deterministic symbol universe used by an offline stress."""

    name: str
    symbols: tuple[str, ...]
    family: str
    seed: int | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.symbols:
            raise ValueError("universe cases need a name and at least one symbol")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("universe case symbols must be unique")


def _symbols(values: Iterable[str]) -> tuple[str, ...]:
    result = tuple(sorted(set(values)))
    if not result:
        raise ValueError("universe cannot be empty")
    return result


def remove_core_cases(
    universe: Iterable[str],
    core_symbols: Iterable[str],
) -> tuple[UniverseCase, ...]:
    """Create every remove-one, remove-pair, and remove-all dependency case."""
    base = _symbols(universe)
    core = tuple(sorted(set(core_symbols) & set(base)))
    if not core:
        raise ValueError("none of the requested core symbols are in the universe")
    removals: list[tuple[str, ...]] = [(symbol,) for symbol in core]
    removals.extend(
        (core[left], core[right]) for left in range(len(core)) for right in range(left + 1, len(core))
    )
    removals.append(core)
    cases: list[UniverseCase] = []
    for removed in removals:
        remaining = tuple(symbol for symbol in base if symbol not in set(removed))
        if remaining:
            cases.append(
                UniverseCase(
                    name=f"remove_{'_'.join(removed)}",
                    symbols=remaining,
                    family="core_dependency",
                )
            )
    return tuple(cases)


def exclude_industry_case(
    universe: Iterable[str],
    industries: Mapping[str, str],
    excluded: str,
) -> UniverseCase:
    """Build a universe that excludes every symbol in one industry."""

    remaining = tuple(symbol for symbol in _symbols(universe) if industries.get(symbol) != excluded)
    return UniverseCase(
        name=f"no_{excluded}",
        symbols=remaining,
        family="industry_exclusion",
    )


def industry_only_cases(
    universe: Iterable[str],
    industries: Mapping[str, str],
) -> tuple[UniverseCase, ...]:
    """Build one non-empty universe for each known industry."""

    base = _symbols(universe)
    grouped: dict[str, list[str]] = {}
    for symbol in base:
        grouped.setdefault(industries.get(symbol, "unknown"), []).append(symbol)
    return tuple(
        UniverseCase(
            name=f"industry_{industry}",
            symbols=tuple(symbols),
            family="industry_only",
        )
        for industry, symbols in sorted(grouped.items())
        if industry != "unknown" and symbols
    )


def balanced_industry_case(
    universe: Iterable[str],
    industries: Mapping[str, str],
    *,
    per_industry: int = 2,
) -> UniverseCase:
    """Select a stable, equally bounded prefix from every known industry."""

    if per_industry < 1:
        raise ValueError("per_industry must be positive")
    grouped: dict[str, list[str]] = {}
    for symbol in _symbols(universe):
        industry = industries.get(symbol, "unknown")
        if industry != "unknown":
            grouped.setdefault(industry, []).append(symbol)
    selected = tuple(
        sorted(symbol for industry in sorted(grouped) for symbol in sorted(grouped[industry])[:per_industry])
    )
    return UniverseCase("balanced_industry", selected, "balanced")


def _derived_seed(base_seed: int, size: int, seed: int) -> int:
    digest = hashlib.sha256(f"{base_seed}:{size}:{seed}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def random_universe_cases(
    universe: Iterable[str],
    *,
    sizes: Iterable[int] = (6, 12, 24),
    seeds: Iterable[int] = range(100),
    base_seed: int = 20260810,
) -> tuple[UniverseCase, ...]:
    """Generate local-RNG subsets without touching process-global randomness."""
    base = _symbols(universe)
    ordered_sizes = tuple(sorted(set(sizes)))
    ordered_seeds = tuple(sorted(set(seeds)))
    cases: list[UniverseCase] = []
    for size in ordered_sizes:
        if size < 1 or size > len(base):
            raise ValueError(f"random universe size is outside [1, {len(base)}]: {size}")
        for seed in ordered_seeds:
            chosen = tuple(
                sorted(
                    random.Random(_derived_seed(base_seed, size, seed)).sample(  # nosec B311
                        base,
                        size,
                    )
                )
            )
            cases.append(
                UniverseCase(
                    name=f"random_{size}_{seed:04d}",
                    symbols=chosen,
                    family="random",
                    seed=seed,
                )
            )
    return tuple(cases)


def leave_top_k_out_cases(
    universe: Iterable[str],
    pre_window_ranking: Sequence[str],
    *,
    values: Iterable[int] = (1, 2, 3, 5),
) -> tuple[UniverseCase, ...]:
    """Remove leaders supplied by a caller-controlled pre-window ranking.

    Ranking calculation is deliberately outside this module so callers must
    make the causal selection date explicit rather than using test-window PnL.
    """
    base = _symbols(universe)
    ranked = tuple(symbol for symbol in pre_window_ranking if symbol in set(base))
    if len(ranked) != len(set(ranked)):
        raise ValueError("pre-window ranking contains duplicate symbols")
    cases: list[UniverseCase] = []
    for top_k in sorted(set(values)):
        if top_k < 1 or top_k > len(ranked):
            raise ValueError("leave-top-k value exceeds the causal ranking")
        removed = set(ranked[:top_k])
        remaining = tuple(symbol for symbol in base if symbol not in removed)
        if remaining:
            cases.append(
                UniverseCase(
                    name=f"leave_top_{top_k}_out",
                    symbols=remaining,
                    family="leave_top_k_out",
                )
            )
    return tuple(cases)
