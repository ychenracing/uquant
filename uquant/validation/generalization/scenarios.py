"""Deterministic universe-generalization diagnostics and frozen references.

The production engine remains the only strategy implementation.  This module
only constructs causal universe perturbations, replays them through a supplied
runner, and aggregates dependency evidence.  It deliberately has no API that
writes a baseline file: reference updates must remain an explicit, reviewed
repository change.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterable, Mapping, Sequence

import pandas as pd

from .models import GeneralizationScenario, PreWindowEvidence


def _canonical_symbols(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    supplied = tuple(values)
    if any(not isinstance(symbol, str) or not symbol for symbol in supplied):
        raise ValueError(f"{label} contains an invalid symbol")
    if len(supplied) != len(set(supplied)):
        raise ValueError(f"{label} contains duplicate symbols")
    result = tuple(sorted(supplied))
    if not result:
        raise ValueError(f"{label} cannot be empty")
    return result


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not slug:
        raise ValueError(f"cannot derive a stable scenario label from {value!r}")
    return slug


def _validate_industry_coverage(
    universe: Sequence[str],
    industries: Mapping[str, str],
) -> None:
    """Require one and only one non-empty industry label per universe symbol."""
    expected = set(universe)
    observed = set(industries)
    missing = sorted(expected - observed)
    extra = sorted(observed - expected, key=str)
    if missing or extra:
        raise ValueError(
            f"industry map must exactly cover the generalization universe: missing={missing}, extra={extra}"
        )
    invalid = sorted(
        symbol
        for symbol in universe
        if not isinstance(industries[symbol], str) or not industries[symbol].strip()
    )
    if invalid:
        raise ValueError(f"industry map contains invalid labels for: {invalid}")


def compute_pre_window_evidence(
    prices: Mapping[str, pd.Series | pd.DataFrame],
    universe: Iterable[str],
    *,
    window_start: str,
    lookback_sessions: int = 120,
) -> PreWindowEvidence:
    """Rank historical leaders using closes strictly before ``window_start``.

    Every eligible symbol uses the same session lookback.  Symbols without
    comparable history are recorded as ineligible rather than ranked, but they
    remain available to the replay-universe scenarios.  Mutations on or after
    the replay start cannot affect the result.
    """
    if lookback_sessions < 1:
        raise ValueError("pre-window lookback must be positive")
    symbols = _canonical_symbols(universe, label="generalization universe")
    start = pd.Timestamp(window_start).normalize()
    scored: list[tuple[str, float]] = []
    evidence_dates: list[pd.Timestamp] = []
    ineligible: list[str] = []
    for symbol in symbols:
        raw = prices.get(symbol)
        if raw is None:
            ineligible.append(symbol)
            continue
        if isinstance(raw, pd.DataFrame):
            if "close" not in raw:
                raise ValueError(f"pre-window price frame has no close column: {symbol}")
            series = raw["close"]
        else:
            series = raw
        normalized = pd.Series(
            series.to_numpy(dtype=float),
            index=pd.DatetimeIndex(pd.to_datetime(series.index)).normalize(),
            dtype=float,
        ).sort_index()
        if normalized.index.has_duplicates:
            raise ValueError(f"pre-window prices contain duplicate sessions: {symbol}")
        bounded = normalized.loc[normalized.index < start].dropna()
        if len(bounded) < lookback_sessions + 1:
            ineligible.append(symbol)
            continue
        current = float(bounded.iloc[-1])
        previous = float(bounded.iloc[-1 - lookback_sessions])
        if current <= 0 or previous <= 0:
            raise ValueError(f"pre-window prices must be positive: {symbol}")
        scored.append((symbol, current / previous - 1.0))
        evidence_dates.append(pd.Timestamp(bounded.index[-1]))
    if len(scored) < 2:
        raise ValueError(
            "pre-window evidence has no meaningful eligible subset: "
            f"eligible={sorted(symbol for symbol, _ in scored)}, "
            f"ineligible={sorted(ineligible)}"
        )
    as_of = max(evidence_dates)
    if as_of >= start:
        raise ValueError("pre-window evidence reaches into the replay window")
    return PreWindowEvidence(
        as_of=str(as_of.date()),
        scores=tuple(sorted(scored)),
        ineligible_symbols=tuple(sorted(ineligible)),
    )


def _derived_seed(base_seed: int, size: int, seed: int) -> int:
    payload = f"{base_seed}:{size}:{seed}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _unique_integers(values: Iterable[int], *, label: str) -> tuple[int, ...]:
    supplied = tuple(values)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in supplied):
        raise ValueError(f"{label} must contain integers")
    if len(supplied) != len(set(supplied)):
        raise ValueError(f"{label} contains duplicates")
    return tuple(sorted(supplied))


def build_generalization_scenarios(
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    *,
    window_start: str,
    pre_window_evidence: PreWindowEvidence,
    random_sizes: Iterable[int] = (6, 12, 24),
    random_seeds: Iterable[int] = range(100),
    base_seed: int = 20260810,
    leave_top_k: Iterable[int] = (1, 2, 3, 5),
    balanced_per_industry: int = 2,
    industry_min_members: int = 2,
) -> tuple[GeneralizationScenario, ...]:
    """Build the complete deterministic universe-generalization matrix."""
    base = _canonical_symbols(universe, label="generalization universe")
    _validate_industry_coverage(base, industries)
    priors = _canonical_symbols(prior_symbols, label="prior symbols")
    missing_priors = sorted(set(priors) - set(base))
    if missing_priors:
        raise ValueError(f"prior symbols are outside the universe: {missing_priors}")
    start = pd.Timestamp(window_start).normalize()
    evidence_date = pd.Timestamp(pre_window_evidence.as_of).normalize()
    if evidence_date >= start:
        raise ValueError("leave-top-k evidence must predate the replay window")
    scores = pre_window_evidence.score_map()
    eligible = pre_window_evidence.eligible_symbols
    ineligible = pre_window_evidence.ineligible_symbols
    evidence_members = set(eligible) | set(ineligible)
    missing_evidence = sorted(set(base) - evidence_members)
    unexpected_evidence = sorted(evidence_members - set(base))
    if missing_evidence or unexpected_evidence:
        raise ValueError(
            "pre-window evidence must partition the generalization universe: "
            f"missing={missing_evidence}, extra={unexpected_evidence}"
        )
    if len(eligible) < 2:
        raise ValueError(
            "pre-window evidence has no meaningful eligible subset: "
            f"eligible={list(eligible)}, ineligible={list(ineligible)}"
        )
    if balanced_per_industry < 1 or industry_min_members < 2:
        raise ValueError("industry diagnostics require positive balanced size and min members >=2")

    def make_case(
        name: str,
        family: str,
        case_symbols: tuple[str, ...],
        *,
        removed_symbols: tuple[str, ...] = (),
        diagnostic: str = "standard",
        source_industries: tuple[str, ...] = (),
        seed: int | None = None,
    ) -> GeneralizationScenario:
        """Attach the shared pre-window evidence to one scenario."""
        return GeneralizationScenario(
            name=name,
            family=family,
            symbols=case_symbols,
            removed_symbols=removed_symbols,
            diagnostic=diagnostic,
            source_industries=source_industries,
            seed=seed,
            evidence_as_of=pre_window_evidence.as_of,
            evidence_eligible_symbols=eligible,
            evidence_ineligible_symbols=ineligible,
        )

    cases: list[GeneralizationScenario] = [make_case("base", "baseline", base)]

    for symbol in priors:
        remaining = tuple(item for item in base if item != symbol)
        cases.append(
            make_case(
                f"remove_one__{symbol}",
                "remove_one",
                remaining,
                removed_symbols=(symbol,),
            )
        )
    for left in range(len(priors)):
        for right in range(left + 1, len(priors)):
            pair_removed = (priors[left], priors[right])
            remaining = tuple(item for item in base if item not in set(pair_removed))
            if remaining:
                cases.append(
                    make_case(
                        f"remove_pair__{pair_removed[0]}__{pair_removed[1]}",
                        "remove_pair",
                        remaining,
                        removed_symbols=pair_removed,
                    )
                )
    remove_all = tuple(item for item in base if item not in set(priors))
    if not remove_all:
        raise ValueError("remove-all-priors scenario would have an empty universe")
    cases.append(
        make_case(
            "remove_all_priors",
            "remove_all",
            remove_all,
            removed_symbols=priors,
        )
    )

    no_optical = tuple(symbol for symbol in base if industries[symbol] != "optical")
    if not no_optical:
        raise ValueError("no-optical scenario would have an empty universe")
    cases.append(
        make_case(
            "no_optical",
            "no_optical",
            no_optical,
            removed_symbols=tuple(symbol for symbol in base if symbol not in no_optical),
            source_industries=("optical",),
        )
    )

    grouped: dict[str, list[str]] = {}
    for symbol in base:
        industry = industries[symbol]
        grouped.setdefault(industry, []).append(symbol)
    sparse: dict[str, tuple[str, ...]] = {}
    for industry, members in sorted(grouped.items()):
        canonical = tuple(sorted(members))
        is_sparse = len(canonical) < industry_min_members
        if is_sparse:
            sparse[industry] = canonical
        cases.append(
            make_case(
                f"industry_only__{_slug(industry)}",
                "industry_only",
                canonical,
                diagnostic="singleton" if len(canonical) == 1 else "sparse" if is_sparse else "standard",
                source_industries=(industry,),
            )
        )
    if len(sparse) >= 2:
        sparse_symbols = tuple(sorted(symbol for members in sparse.values() for symbol in members))
        cases.append(
            make_case(
                "industry_sparse_combined",
                "industry_only",
                sparse_symbols,
                diagnostic="combined_sparse",
                source_industries=tuple(sorted(sparse)),
            )
        )

    balanced = tuple(
        sorted(
            symbol
            for industry in sorted(grouped)
            for symbol in sorted(
                grouped[industry],
                key=lambda item: (item not in scores, -scores.get(item, 0.0), item),
            )[:balanced_per_industry]
        )
    )
    cases.append(
        make_case(
            "balanced_industries",
            "balanced",
            balanced,
            diagnostic="includes_singletons" if sparse else "standard",
            source_industries=tuple(sorted(grouped)),
        )
    )

    sizes = _unique_integers(random_sizes, label="random sizes")
    seeds = _unique_integers(random_seeds, label="random seeds")
    if not sizes or not seeds:
        raise ValueError("random scenarios require at least one size and seed")
    for size in sizes:
        if size < 1 or size > len(base):
            raise ValueError(f"random universe size is outside [1, {len(base)}]: {size}")
        for seed in seeds:
            chosen = tuple(
                sorted(
                    # Scenario reproducibility deliberately requires a non-cryptographic PRNG.
                    random.Random(_derived_seed(base_seed, size, seed)).sample(  # nosec B311
                        list(base),
                        size,
                    )
                )
            )
            cases.append(
                make_case(
                    f"random_{size:02d}__{seed:04d}",
                    "random",
                    chosen,
                    seed=seed,
                )
            )

    top_values = _unique_integers(leave_top_k, label="leave-top-k values")
    ranking = tuple(symbol for symbol in pre_window_evidence.ranking if symbol in set(base))
    for top_k in top_values:
        if top_k < 1 or top_k >= len(ranking):
            raise ValueError("leave-top-k value must leave at least one ranked symbol")
        removed = ranking[:top_k]
        remaining = tuple(symbol for symbol in base if symbol not in set(removed))
        cases.append(
            make_case(
                f"leave_top_{top_k}",
                "leave_top_k",
                remaining,
                removed_symbols=removed,
            )
        )

    names = [case.name for case in cases]
    if len(names) != len(set(names)):
        raise RuntimeError("generalization scenario construction produced duplicate names")
    return tuple(cases)


def scenario_fingerprint(cases: Sequence[GeneralizationScenario]) -> str:
    """Hash every economic and diagnostic property of an ordered case matrix."""
    payload = [
        {
            "name": case.name,
            "family": case.family,
            "symbols": list(case.symbols),
            "removed_symbols": list(case.removed_symbols),
            "diagnostic": case.diagnostic,
            "source_industries": list(case.source_industries),
            "seed": case.seed,
            "evidence_as_of": case.evidence_as_of,
            "evidence_eligible_symbols": list(case.evidence_eligible_symbols),
            "evidence_ineligible_symbols": list(case.evidence_ineligible_symbols),
        }
        for case in cases
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
