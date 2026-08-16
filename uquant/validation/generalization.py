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
import math
import random
import re
import shutil
import subprocess  # nosec B404
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from ..config import SystemConfig
from ..config_governance import GOVERNANCE_PATH
from ..engine import ProductionEngine
from .ai_era import require_ai_era_interval
from .manifest import verify_data_manifest

_REFERENCE_FIELDS = {"final_wealth", "max_drawdown", "account_orders"}
_BASELINE_SCHEMA_VERSION = 3
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_PROVENANCE_SECTIONS = {"data", "dataset", "execution", "production"}
_EXECUTION_CONTRACT: dict[str, Any] = {
    "engine": "uquant.engine.ProductionEngine",
    "decision": "daily_close_t",
    "execution": "next_tradable_open",
    "intraday_exit": False,
    "prelisting": "invisible_until_first_observable_row",
}
_COMPETITOR_BEST_FIELDS = {"metric", "scenario", "value", "provenance"}
_COMPETITOR_PROVENANCE_FIELDS = {
    "repository",
    "reference_path",
    "reference_commit",
    "reference_sha256",
}
_FIXED_PRODUCTION_PATHS = (
    "pyproject.toml",
    GOVERNANCE_PATH.as_posix(),
)
_POLICY_FIELDS = {
    "wealth_floor_ratio",
    "drawdown_tolerance",
    "order_tolerance",
    "order_ceiling_ratio",
    "dominance_wealth_regression",
    "dominance_drawdown_regression",
    "dominance_order_regression",
    "pareto_wealth_improvement",
    "pareto_drawdown_improvement",
    "pareto_order_improvement",
    "pareto_wealth_regression",
    "pareto_drawdown_regression",
    "pareto_order_regression",
    "remove_one_max_dependency",
    "remove_all_min_wealth",
    "remove_all_max_drawdown",
    "remove_all_competitor_ratio",
    "no_optical_min_wealth",
    "no_optical_max_drawdown",
    "random_min_positive_fraction",
    "random_p10_min_wealth",
    "optical_dependency_share_threshold",
}


@dataclass(frozen=True, slots=True)
class PreWindowEvidence:
    """Cross-sectional scores observed strictly before a replay window."""

    as_of: str
    scores: tuple[tuple[str, float], ...]
    ineligible_symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject ambiguous dates, duplicate symbols, and overlapping cohorts."""
        try:
            pd.Timestamp(self.as_of)
        except (TypeError, ValueError) as exc:
            raise ValueError("pre-window evidence requires a valid as_of date") from exc
        symbols = [symbol for symbol, _ in self.scores]
        if len(symbols) != len(set(symbols)):
            raise ValueError("pre-window evidence contains duplicate symbols")
        if any(
            not isinstance(symbol, str) or not symbol or not math.isfinite(score)
            for symbol, score in self.scores
        ):
            raise ValueError("pre-window evidence contains an invalid score")
        if any(not isinstance(symbol, str) or not symbol for symbol in self.ineligible_symbols):
            raise ValueError("pre-window evidence contains an invalid ineligible symbol")
        if tuple(sorted(self.ineligible_symbols)) != self.ineligible_symbols:
            raise ValueError("pre-window ineligible symbols are not canonical")
        if len(self.ineligible_symbols) != len(set(self.ineligible_symbols)):
            raise ValueError("pre-window evidence contains duplicate ineligible symbols")
        if set(symbols) & set(self.ineligible_symbols):
            raise ValueError("pre-window evidence marks a symbol both eligible and ineligible")

    @property
    def eligible_symbols(self) -> tuple[str, ...]:
        """Return canonical membership of the comparable evidence cohort."""
        return tuple(sorted(symbol for symbol, _ in self.scores))

    @property
    def ranking(self) -> tuple[str, ...]:
        """Return strongest-first ranking with a stable symbol tie-break."""
        return tuple(
            symbol
            for symbol, _ in sorted(
                self.scores,
                key=lambda item: (-item[1], item[0]),
            )
        )

    def score_map(self) -> dict[str, float]:
        """Return an independent symbol-to-score mapping."""
        return dict(self.scores)


@dataclass(frozen=True, slots=True)
class GeneralizationScenario:
    """One deterministic universe perturbation with auditable provenance."""

    name: str
    family: str
    symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...] = ()
    diagnostic: str = "standard"
    source_industries: tuple[str, ...] = ()
    seed: int | None = None
    evidence_as_of: str = ""
    evidence_eligible_symbols: tuple[str, ...] = ()
    evidence_ineligible_symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate canonical scenario membership and evidence provenance."""
        if not self.name or not self.family or not self.symbols:
            raise ValueError("generalization scenarios require name, family, and symbols")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError(f"generalization scenario repeats a symbol: {self.name}")
        if tuple(sorted(self.symbols)) != self.symbols:
            raise ValueError(f"generalization scenario symbols are not canonical: {self.name}")
        if len(self.removed_symbols) != len(set(self.removed_symbols)):
            raise ValueError(f"generalization scenario repeats a removed symbol: {self.name}")
        if set(self.symbols) & set(self.removed_symbols):
            raise ValueError(f"generalization scenario retains a removed symbol: {self.name}")
        for label, membership in (
            ("eligible", self.evidence_eligible_symbols),
            ("ineligible", self.evidence_ineligible_symbols),
        ):
            if tuple(sorted(membership)) != membership or len(membership) != len(set(membership)):
                raise ValueError(
                    f"generalization scenario evidence {label} symbols are not canonical: {self.name}"
                )
            if any(not isinstance(symbol, str) or not symbol for symbol in membership):
                raise ValueError(f"generalization scenario has invalid evidence {label} symbols: {self.name}")
        if set(self.evidence_eligible_symbols) & set(self.evidence_ineligible_symbols):
            raise ValueError(f"generalization scenario evidence membership overlaps: {self.name}")


@dataclass(frozen=True, slots=True)
class GeneralizationObservation:
    """Compact replay result plus exact symbol-level economic contribution."""

    name: str
    family: str
    final_wealth: float
    max_drawdown: float
    account_orders: int
    symbol_pnl: tuple[tuple[str, float], ...]
    deployed_exposure: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Validate performance ranges and canonical symbol-level results."""
        if not self.name or not self.family:
            raise ValueError("generalization observations require name and family")
        if self.final_wealth <= 0 or not math.isfinite(self.final_wealth):
            raise ValueError(f"invalid final wealth for {self.name}")
        if not math.isfinite(self.max_drawdown) or not 0 <= self.max_drawdown <= 1:
            raise ValueError(f"invalid maximum drawdown for {self.name}")
        if (
            isinstance(self.account_orders, bool)
            or not isinstance(self.account_orders, int)
            or self.account_orders < 0
        ):
            raise ValueError(f"invalid account-order count for {self.name}")
        symbols = [symbol for symbol, _ in self.symbol_pnl]
        if len(symbols) != len(set(symbols)):
            raise ValueError(f"duplicate symbol PnL for {self.name}")
        if any(not symbol or not math.isfinite(value) for symbol, value in self.symbol_pnl):
            raise ValueError(f"invalid symbol PnL for {self.name}")
        if tuple(sorted(set(self.deployed_exposure))) != self.deployed_exposure:
            raise ValueError(f"deployed exposure is not canonical for {self.name}")
        allowed_lifecycles = {
            "CORE",
            "ADD1",
            "ADD2",
            "SATELLITE",
            "RECOVERY",
            "STRATEGIC",
        }
        if any(
            not symbol or lifecycle not in allowed_lifecycles for symbol, lifecycle in self.deployed_exposure
        ):
            raise ValueError(f"invalid deployed exposure for {self.name}")

    def pnl_map(self) -> dict[str, float]:
        """Return an independent symbol-to-PnL mapping."""
        return dict(self.symbol_pnl)


@dataclass(frozen=True, slots=True)
class GeneralizationBaseline:
    """Validated, read-only snapshot of frozen scenario references."""

    sha256: str
    case_fingerprint: str
    validation_fingerprint: str
    provenance: dict[str, Any]
    competitor_best: dict[str, Any]
    policy: GeneralizationPolicy
    references: dict[str, dict[str, float | int]]


@dataclass(frozen=True, slots=True)
class GeneralizationPolicy:
    """Reviewed economic thresholds that turn diagnostics into a hard gate."""

    wealth_floor_ratio: float
    drawdown_tolerance: float
    order_tolerance: int
    order_ceiling_ratio: float
    dominance_wealth_regression: float
    dominance_drawdown_regression: float
    dominance_order_regression: float
    pareto_wealth_improvement: float
    pareto_drawdown_improvement: float
    pareto_order_improvement: float
    pareto_wealth_regression: float
    pareto_drawdown_regression: float
    pareto_order_regression: float
    remove_one_max_dependency: float
    remove_all_min_wealth: float
    remove_all_max_drawdown: float
    remove_all_competitor_ratio: float
    no_optical_min_wealth: float
    no_optical_max_drawdown: float
    random_min_positive_fraction: float
    random_p10_min_wealth: float
    optical_dependency_share_threshold: float

    def to_dict(self) -> dict[str, float | int]:
        """Return a stable JSON-compatible representation without defaults."""
        return {name: getattr(self, name) for name in sorted(_POLICY_FIELDS)}


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


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validation_fingerprint(
    *,
    case_fingerprint: str,
    provenance: Mapping[str, Any],
    competitor_best: Mapping[str, Any],
) -> str:
    """Bind the case design, replay inputs, and reviewed external objective."""
    return _fingerprint(
        {
            "case_fingerprint": case_fingerprint,
            "provenance": provenance,
            "competitor_best": competitor_best,
        }
    )


def _exact_fields(value: Any, expected: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"generalization {label} must be an object")
    observed = set(value)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise RuntimeError(f"generalization {label} is missing fields: {missing}")
    if unexpected:
        raise RuntimeError(f"generalization {label} has unexpected fields: {unexpected}")
    return value


def _nonempty_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"generalization {label} must be a non-empty string")
    return value


def _validated_provenance(value: Any) -> dict[str, Any]:
    """Validate and normalize every input needed to reproduce the replay."""
    root = _exact_fields(value, _PROVENANCE_SECTIONS, label="provenance")
    data = _exact_fields(
        root["data"],
        {"snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"},
        label="provenance.data",
    )
    snapshot_id = _nonempty_text(data["snapshot_id"], label="provenance.data.snapshot_id")
    files_verified = data["files_verified"]
    if isinstance(files_verified, bool) or not isinstance(files_verified, int) or files_verified < 1:
        raise RuntimeError("generalization provenance.data.files_verified must be a positive integer")
    data_hashes: dict[str, str] = {}
    for name in ("manifest_sha256", "checksums_sha256"):
        digest = _nonempty_text(data[name], label=f"provenance.data.{name}")
        if not _SHA256.fullmatch(digest):
            raise RuntimeError(f"generalization provenance.data.{name} must be SHA-256")
        data_hashes[name] = digest

    dataset = _exact_fields(
        root["dataset"],
        {"universe", "industries", "prior_symbols", "start", "end"},
        label="provenance.dataset",
    )
    universe_value = dataset["universe"]
    prior_value = dataset["prior_symbols"]
    if not isinstance(universe_value, list) or not isinstance(prior_value, list):
        raise RuntimeError("generalization provenance dataset memberships must be lists")
    try:
        universe = _canonical_symbols(universe_value, label="provenance dataset universe")
        priors = _canonical_symbols(prior_value, label="provenance dataset prior symbols")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if list(universe) != universe_value or list(priors) != prior_value:
        raise RuntimeError("generalization provenance dataset memberships must be canonical")
    if not set(priors) <= set(universe):
        raise RuntimeError("generalization provenance prior symbols are outside the universe")
    industries = dataset["industries"]
    if not isinstance(industries, Mapping):
        raise RuntimeError("generalization provenance.dataset.industries must be an object")
    normalized_industries = dict(sorted(industries.items(), key=lambda item: str(item[0])))
    try:
        _validate_industry_coverage(universe, normalized_industries)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    start = _nonempty_text(dataset["start"], label="provenance.dataset.start")
    end = _nonempty_text(dataset["end"], label="provenance.dataset.end")
    try:
        start_date = pd.Timestamp(start).normalize()
        end_date = pd.Timestamp(end).normalize()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("generalization provenance dataset window is invalid") from exc
    if start_date > end_date or str(start_date.date()) != start or str(end_date.date()) != end:
        raise RuntimeError("generalization provenance dataset window must be canonical and ordered")
    require_ai_era_interval(start, end)

    execution = _exact_fields(
        root["execution"],
        set(_EXECUTION_CONTRACT) | {"initial_cash"},
        label="provenance.execution",
    )
    for name, expected in _EXECUTION_CONTRACT.items():
        if execution[name] != expected:
            raise RuntimeError(f"generalization execution contract mismatch: {name}")
    initial_cash = execution["initial_cash"]
    if (
        isinstance(initial_cash, bool)
        or not isinstance(initial_cash, (int, float))
        or not math.isfinite(float(initial_cash))
        or float(initial_cash) <= 0
    ):
        raise RuntimeError("generalization execution initial_cash must be positive and finite")

    production = _exact_fields(
        root["production"],
        {"repository", "commit", "source_sha256"},
        label="provenance.production",
    )
    repository = _nonempty_text(production["repository"], label="provenance.production.repository")
    commit = _nonempty_text(production["commit"], label="provenance.production.commit")
    source_sha256 = _nonempty_text(production["source_sha256"], label="provenance.production.source_sha256")
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("generalization production commit must be immutable")
    if not _SHA256.fullmatch(source_sha256):
        raise RuntimeError("generalization production source_sha256 must be SHA-256")

    return {
        "data": {
            "snapshot_id": snapshot_id,
            "files_verified": files_verified,
            **data_hashes,
        },
        "dataset": {
            "universe": list(universe),
            "industries": normalized_industries,
            "prior_symbols": list(priors),
            "start": start,
            "end": end,
        },
        "execution": {
            **_EXECUTION_CONTRACT,
            "initial_cash": float(initial_cash),
        },
        "production": {
            "repository": repository,
            "commit": commit,
            "source_sha256": source_sha256,
        },
    }


def _validated_competitor_best(value: Any) -> dict[str, Any]:
    root = _exact_fields(value, _COMPETITOR_BEST_FIELDS, label="competitor_best")
    if root["metric"] != "final_wealth" or root["scenario"] != "remove_all_priors":
        raise RuntimeError("generalization competitor_best must be final_wealth for remove_all_priors")
    metric_value = root["value"]
    if (
        isinstance(metric_value, bool)
        or not isinstance(metric_value, (int, float))
        or not math.isfinite(float(metric_value))
        or float(metric_value) <= 0
    ):
        raise RuntimeError("generalization competitor_best.value must be positive and finite")
    provenance = _exact_fields(
        root["provenance"],
        _COMPETITOR_PROVENANCE_FIELDS,
        label="competitor_best.provenance",
    )
    normalized_provenance = {
        name: _nonempty_text(provenance[name], label=f"competitor_best.provenance.{name}")
        for name in sorted(_COMPETITOR_PROVENANCE_FIELDS)
    }
    if not _COMMIT.fullmatch(normalized_provenance["reference_commit"]):
        raise RuntimeError("generalization competitor reference_commit must be immutable")
    if not _SHA256.fullmatch(normalized_provenance["reference_sha256"]):
        raise RuntimeError("generalization competitor reference_sha256 must be SHA-256")
    return {
        "metric": "final_wealth",
        "scenario": "remove_all_priors",
        "value": float(metric_value),
        "provenance": normalized_provenance,
    }


def _production_source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [
        *(root / relative for relative in _FIXED_PRODUCTION_PATHS),
        *sorted((root / "uquant").rglob("*.py")),
    ]
    if any(not path.is_file() for path in paths):
        raise RuntimeError("cannot fingerprint generalization production source")
    for path in paths:
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve git executable for generalization provenance")
    return executable


def _git_stdout(root: Path, arguments: list[str], *, label: str) -> str:
    try:
        # Git is resolved explicitly; every caller supplies a fixed argument list.
        completed = subprocess.run(
            [_git_executable(), "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(label) from exc
    return completed.stdout


def _production_commit(root: Path) -> str:
    status = _git_stdout(
        root,
        [
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "uquant",
            *_FIXED_PRODUCTION_PATHS,
        ],
        label="cannot inspect generalization production source",
    )
    if status.strip():
        raise RuntimeError("generalization production provenance requires committed source")
    commit = _git_stdout(
        root,
        ["log", "-1", "--format=%H", "--", "uquant", *_FIXED_PRODUCTION_PATHS],
        label="cannot resolve immutable production commit",
    ).strip()
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("cannot resolve immutable production commit")
    return commit


@contextmanager
def _immutable_validation_inputs(
    *,
    baseline_path: Path,
    baseline_sha256: str,
    data_dir: str | Path,
    repository_root: Path,
    data_before: Mapping[str, Any],
    source_before: str,
) -> Iterator[None]:
    """Reject baseline, candidate-source, or frozen-data mutation during replay."""
    try:
        yield
    finally:
        try:
            current_baseline = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            data_after = verify_data_manifest(data_dir)
            source_after = _production_source_fingerprint(repository_root)
        except Exception as exc:
            raise RuntimeError("generalization source or data changed during validation") from exc
        if current_baseline != baseline_sha256:
            raise RuntimeError("generalization baseline changed during validation")
        if data_after != data_before or source_after != source_before:
            raise RuntimeError("generalization source or data changed during validation")


def build_generalization_provenance(
    *,
    data: Mapping[str, Any],
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    start: str,
    end: str,
    production_commit: str,
    production_source_sha256: str,
    repository: str = "ychenracing/uquant",
    initial_cash: float = 2_000_000.0,
) -> dict[str, Any]:
    """Build the exact reviewed provenance envelope for baseline evidence."""
    symbols = _canonical_symbols(universe, label="generalization universe")
    priors = _canonical_symbols(prior_symbols, label="prior symbols")
    return _validated_provenance(
        {
            "data": dict(data),
            "dataset": {
                "universe": list(symbols),
                "industries": dict(sorted(industries.items())),
                "prior_symbols": list(priors),
                "start": start,
                "end": end,
            },
            "execution": {
                **_EXECUTION_CONTRACT,
                "initial_cash": initial_cash,
            },
            "production": {
                "repository": repository,
                "commit": production_commit,
                "source_sha256": production_source_sha256,
            },
        }
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"generalization baseline contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise RuntimeError(f"generalization baseline contains a non-standard number: {value}")


def _policy_number(payload: Mapping[str, Any], name: str) -> float:
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"generalization policy field must be numeric: {name}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RuntimeError(f"generalization policy field must be finite: {name}")
    return numeric


def _parse_policy(value: Any) -> GeneralizationPolicy:
    """Parse a complete policy object and enforce every numeric bound."""
    if not isinstance(value, Mapping):
        raise RuntimeError("generalization baseline policy must be an object")
    observed = set(value)
    missing = sorted(_POLICY_FIELDS - observed)
    unexpected = sorted(observed - _POLICY_FIELDS)
    if missing:
        raise RuntimeError(f"generalization policy is missing fields: {missing}")
    if unexpected:
        raise RuntimeError(f"generalization policy has unexpected fields: {unexpected}")

    raw_order_tolerance = value["order_tolerance"]
    if (
        isinstance(raw_order_tolerance, bool)
        or not isinstance(raw_order_tolerance, int)
        or raw_order_tolerance < 0
    ):
        raise RuntimeError("generalization policy.order_tolerance must be a nonnegative integer")
    numbers = {name: _policy_number(value, name) for name in _POLICY_FIELDS - {"order_tolerance"}}
    if not 0 < numbers["wealth_floor_ratio"] <= 1:
        raise RuntimeError("generalization wealth_floor_ratio must be in (0, 1]")
    if not 0 <= numbers["drawdown_tolerance"] <= 1:
        raise RuntimeError("generalization drawdown_tolerance must be in [0, 1]")
    if numbers["order_ceiling_ratio"] < 1:
        raise RuntimeError("generalization order_ceiling_ratio cannot be below one")
    material_fields = {
        "dominance_wealth_regression",
        "dominance_drawdown_regression",
        "dominance_order_regression",
        "pareto_wealth_improvement",
        "pareto_drawdown_improvement",
        "pareto_order_improvement",
        "pareto_wealth_regression",
        "pareto_drawdown_regression",
        "pareto_order_regression",
    }
    if any(numbers[name] < 0 for name in material_fields):
        raise RuntimeError("generalization dominance/Pareto thresholds cannot be negative")
    bounded_material_fields = {
        "dominance_wealth_regression",
        "dominance_drawdown_regression",
        "pareto_drawdown_improvement",
        "pareto_wealth_regression",
        "pareto_drawdown_regression",
    }
    if any(numbers[name] > 1 for name in bounded_material_fields):
        raise RuntimeError("generalization bounded dominance/Pareto thresholds cannot exceed one")
    if not 0 <= numbers["remove_one_max_dependency"] <= 0.25:
        raise RuntimeError("generalization remove-one dependency ceiling must be in [0, 0.25]")
    if numbers["remove_all_min_wealth"] <= 1 or numbers["no_optical_min_wealth"] <= 1:
        raise RuntimeError("generalization removal scenarios must require positive return")
    if not 0 <= numbers["remove_all_max_drawdown"] <= 1:
        raise RuntimeError("generalization remove-all drawdown ceiling must be in [0, 1]")
    if not 0.95 <= numbers["remove_all_competitor_ratio"] <= 1.5:
        raise RuntimeError("generalization remove-all competitor ratio must be in [0.95, 1.5]")
    if not 0 <= numbers["no_optical_max_drawdown"] <= 1:
        raise RuntimeError("generalization no-optical drawdown ceiling must be in [0, 1]")
    if not 0.5 < numbers["random_min_positive_fraction"] <= 1:
        raise RuntimeError("generalization random positive fraction must be in (0.5, 1]")
    if numbers["random_p10_min_wealth"] < 1:
        raise RuntimeError("generalization random p10 wealth floor cannot be below one")
    if not 0 < numbers["optical_dependency_share_threshold"] <= 0.70:
        raise RuntimeError("generalization optical dependency threshold must be in (0, 0.70]")

    return GeneralizationPolicy(
        order_tolerance=raw_order_tolerance,
        **numbers,
    )


def _read_generalization_baseline(path: str | Path) -> tuple[bytes, dict[str, Any]]:
    source = Path(path)
    try:
        raw = source.read_bytes()
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"generalization baseline is missing or corrupt: {source}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("generalization baseline must be a JSON object")
    return raw, payload


def _validate_baseline_envelope(
    payload: Mapping[str, Any],
    *,
    expected_provenance: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], GeneralizationPolicy]:
    """Validate baseline sections, fingerprints, policy, and replay provenance."""
    expected_sections = {
        "schema_version",
        "case_fingerprint",
        "validation_fingerprint",
        "provenance",
        "competitor_best",
        "policy",
        "references",
    }
    missing_sections = sorted(expected_sections - set(payload))
    unexpected_sections = sorted(set(payload) - expected_sections)
    if missing_sections:
        raise RuntimeError(f"generalization baseline is missing sections: {missing_sections}")
    if unexpected_sections:
        raise RuntimeError(f"generalization baseline has unexpected sections: {unexpected_sections}")
    if payload.get("schema_version") != _BASELINE_SCHEMA_VERSION:
        raise RuntimeError("unsupported generalization baseline schema")
    provenance = _validated_provenance(payload["provenance"])
    competitor_best = _validated_competitor_best(payload["competitor_best"])
    case_fingerprint = payload["case_fingerprint"]
    if not isinstance(case_fingerprint, str) or not _SHA256.fullmatch(case_fingerprint):
        raise RuntimeError("generalization case_fingerprint must be SHA-256")
    validation_fingerprint = payload["validation_fingerprint"]
    if not isinstance(validation_fingerprint, str) or not _SHA256.fullmatch(validation_fingerprint):
        raise RuntimeError("generalization validation_fingerprint must be SHA-256")
    if validation_fingerprint != _validation_fingerprint(
        case_fingerprint=case_fingerprint,
        provenance=provenance,
        competitor_best=competitor_best,
    ):
        raise RuntimeError("generalization validation fingerprint is stale")
    if expected_provenance is not None:
        expected = _validated_provenance(expected_provenance)
        if provenance != expected:
            raise RuntimeError("generalization baseline provenance does not match this replay")
    policy = _parse_policy(payload["policy"])
    return provenance, competitor_best, policy


def load_generalization_baseline(
    path: str | Path,
    cases: Sequence[GeneralizationScenario],
    *,
    expected_provenance: Mapping[str, Any] | None = None,
) -> GeneralizationBaseline:
    """Load a frozen baseline strictly, without normalizing or rewriting it."""
    raw, payload = _read_generalization_baseline(path)
    provenance, competitor_best, policy = _validate_baseline_envelope(
        payload,
        expected_provenance=expected_provenance,
    )
    references = payload.get("references")
    if not isinstance(references, dict):
        raise RuntimeError("generalization baseline references must be an object")
    expected = {case.name for case in cases}
    observed = set(references)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise RuntimeError(f"generalization baseline is missing references: {missing}")
    if unexpected:
        raise RuntimeError(f"generalization baseline has unexpected references: {unexpected}")
    expected_fingerprint = scenario_fingerprint(cases)
    if payload.get("case_fingerprint") != expected_fingerprint:
        raise RuntimeError("generalization baseline case fingerprint is stale")

    validated: dict[str, dict[str, float | int]] = {}
    for name in sorted(expected):
        reference = references[name]
        if not isinstance(reference, dict):
            raise RuntimeError(f"generalization reference must be an object: {name}")
        missing_fields = sorted(_REFERENCE_FIELDS - set(reference))
        unexpected_fields = sorted(set(reference) - _REFERENCE_FIELDS)
        if missing_fields:
            raise RuntimeError(f"generalization reference is missing metrics: {name} {missing_fields}")
        if unexpected_fields:
            raise RuntimeError(f"generalization reference has unexpected metrics: {name} {unexpected_fields}")
        wealth = reference["final_wealth"]
        drawdown = reference["max_drawdown"]
        orders = reference["account_orders"]
        if (
            isinstance(wealth, bool)
            or not isinstance(wealth, (int, float))
            or not math.isfinite(float(wealth))
            or float(wealth) <= 0
        ):
            raise RuntimeError(f"generalization reference has invalid wealth: {name}")
        if (
            isinstance(drawdown, bool)
            or not isinstance(drawdown, (int, float))
            or not math.isfinite(float(drawdown))
            or not 0 <= float(drawdown) <= 1
        ):
            raise RuntimeError(f"generalization reference has invalid drawdown: {name}")
        if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
            raise RuntimeError(f"generalization reference has invalid order count: {name}")
        validated[name] = {
            "final_wealth": float(wealth),
            "max_drawdown": float(drawdown),
            "account_orders": orders,
        }
    return GeneralizationBaseline(
        sha256=hashlib.sha256(raw).hexdigest(),
        case_fingerprint=expected_fingerprint,
        validation_fingerprint=_validation_fingerprint(
            case_fingerprint=expected_fingerprint,
            provenance=provenance,
            competitor_best=competitor_best,
        ),
        provenance=provenance,
        competitor_best=competitor_best,
        policy=policy,
        references=validated,
    )


def reference_payload(
    cases: Sequence[GeneralizationScenario],
    observations: Sequence[GeneralizationObservation],
    *,
    policy: Mapping[str, Any],
    provenance: Mapping[str, Any],
    competitor_best: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a caller-specified reference payload; no policy is fabricated."""
    by_name = {item.name: item for item in observations}
    if len(by_name) != len(observations):
        raise ValueError("generalization observations contain duplicate names")
    expected = {case.name for case in cases}
    if set(by_name) != expected:
        raise ValueError("generalization observations do not exactly cover the case matrix")
    validated_policy = _parse_policy(policy)
    validated_provenance = _validated_provenance(provenance)
    validated_competitor = _validated_competitor_best(competitor_best)
    case_fingerprint = scenario_fingerprint(cases)
    return {
        "schema_version": _BASELINE_SCHEMA_VERSION,
        "case_fingerprint": case_fingerprint,
        "validation_fingerprint": _validation_fingerprint(
            case_fingerprint=case_fingerprint,
            provenance=validated_provenance,
            competitor_best=validated_competitor,
        ),
        "provenance": validated_provenance,
        "competitor_best": validated_competitor,
        "policy": validated_policy.to_dict(),
        "references": {
            name: {
                "final_wealth": by_name[name].final_wealth,
                "max_drawdown": by_name[name].max_drawdown,
                "account_orders": by_name[name].account_orders,
            }
            for name in sorted(by_name)
        },
    }


def symbol_pnl_from_result(
    result: Mapping[str, Any],
    final_prices: Mapping[str, float],
) -> dict[str, float]:
    """Attribute total portfolio profit by transaction cash flow and final marks."""
    account = result.get("final_account")
    if not isinstance(account, Mapping):
        raise ValueError("backtest result is missing final_account")
    raw_fills = account.get("fills", [])
    raw_positions = account.get("positions", {})
    if not isinstance(raw_fills, list) or not isinstance(raw_positions, Mapping):
        raise ValueError("backtest final_account has invalid fills or positions")
    pnl: dict[str, float] = {}
    for raw_fill in raw_fills:
        if not isinstance(raw_fill, Mapping):
            raise ValueError("backtest result contains an invalid fill")
        symbol = str(raw_fill.get("symbol", ""))
        side = str(raw_fill.get("side", ""))
        gross = float(raw_fill.get("gross_value", 0.0))
        fee_values = tuple(
            float(raw_fill.get(field, 0.0)) for field in ("commission", "stamp_duty", "transfer_fee")
        )
        fees = sum(fee_values)
        if (
            not symbol
            or not math.isfinite(gross)
            or gross < 0
            or any(not math.isfinite(value) or value < 0 for value in fee_values)
            or side not in {"BUY", "SELL"}
        ):
            raise ValueError("backtest result contains an invalid fill cash flow")
        cash_flow = -(gross + fees) if side == "BUY" else gross - fees
        pnl[symbol] = pnl.get(symbol, 0.0) + cash_flow
    for symbol, raw_position in raw_positions.items():
        if not isinstance(raw_position, Mapping):
            raise ValueError("backtest result contains an invalid final position")
        shares = int(raw_position.get("shares", 0))
        mark = float(final_prices.get(str(symbol), float("nan")))
        if shares < 0 or (shares > 0 and (not math.isfinite(mark) or mark <= 0)):
            raise ValueError(f"final mark is missing or invalid: {symbol}")
        pnl[str(symbol)] = pnl.get(str(symbol), 0.0) + shares * mark

    if "final_equity" in result and "initial_cash" in account:
        expected = float(result["final_equity"]) - float(account["initial_cash"])
        observed = sum(pnl.values())
        tolerance = max(1e-6, abs(expected) * 1e-10)
        if abs(observed - expected) > tolerance:
            raise ValueError(
                "symbol PnL does not reconcile to portfolio profit: "
                f"observed={observed:.8f}, expected={expected:.8f}"
            )
    return dict(sorted(pnl.items()))


def symbol_pnl_concentration(symbol_pnl: Mapping[str, float]) -> dict[str, float]:
    """Measure Top-1, Top-3, and HHI from exact absolute symbol PnL.

    Absolute contributions avoid signed cancellation.  A portfolio with no
    non-zero symbol PnL has no contribution concentration, represented by
    exact zeros rather than a fabricated or non-finite ratio.
    """
    if any(
        not isinstance(symbol, str) or not symbol or not math.isfinite(float(value))
        for symbol, value in symbol_pnl.items()
    ):
        raise ValueError("invalid symbol PnL for concentration")
    absolute = sorted((abs(float(value)) for value in symbol_pnl.values() if value != 0.0), reverse=True)
    denominator = sum(absolute)
    if denominator == 0.0:
        return {
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        }
    weights = [value / denominator for value in absolute]
    return {
        "top1_concentration": weights[0],
        "top3_concentration": sum(weights[:3]),
        "pnl_hhi": sum(weight * weight for weight in weights),
    }


def _deployment_from_result(result: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Extract every actually filled lifecycle, including strategic attribution."""
    explicit = result.get("deployed_exposure")
    if explicit is not None:
        if not isinstance(explicit, list):
            raise ValueError("scenario result deployed_exposure must be a list")
        deployed: set[tuple[str, str]] = set()
        for item in explicit:
            if not isinstance(item, Mapping) or set(item) != {"symbol", "lifecycle"}:
                raise ValueError("scenario result has invalid deployed_exposure item")
            symbol = item["symbol"]
            lifecycle = item["lifecycle"]
            if not isinstance(symbol, str) or not isinstance(lifecycle, str):
                raise ValueError("scenario result has invalid deployed_exposure item")
            deployed.add((symbol, lifecycle))
        return tuple(sorted(deployed))

    account = result.get("final_account")
    if not isinstance(account, Mapping):
        return ()
    fills = account.get("fills", [])
    if not isinstance(fills, list):
        raise ValueError("scenario result final_account.fills must be a list")
    deployed = set()
    for fill in fills:
        if not isinstance(fill, Mapping):
            raise ValueError("scenario result contains an invalid fill")
        side = fill.get("side")
        shares = fill.get("shares", 0)
        if side != "BUY" or isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
            continue
        symbol = fill.get("symbol")
        lifecycle = fill.get("lifecycle")
        if not isinstance(symbol, str) or not isinstance(lifecycle, str):
            raise ValueError("scenario result contains an invalid BUY deployment")
        deployed.add((symbol, lifecycle))
        if fill.get("reason_code") == "strategic_cohort":
            deployed.add((symbol, "STRATEGIC"))
    return tuple(sorted(deployed))


def observation_from_result(
    case: GeneralizationScenario,
    result: Mapping[str, Any],
    *,
    symbol_pnl: Mapping[str, float] | None = None,
) -> GeneralizationObservation:
    """Validate one engine/runner result and bind it to its scenario."""
    pnl_source = symbol_pnl if symbol_pnl is not None else result.get("symbol_pnl", {})
    if not isinstance(pnl_source, Mapping):
        raise ValueError(f"scenario result has invalid symbol_pnl: {case.name}")
    try:
        wealth = float(result["final_wealth"])
        drawdown = float(result["max_drawdown"])
        raw_orders = result["account_orders"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"scenario result is missing required metrics: {case.name}") from exc
    if isinstance(raw_orders, bool) or not isinstance(raw_orders, int):
        raise ValueError(f"scenario result has a non-integer order count: {case.name}")
    pnl_items = tuple(sorted((str(symbol), float(value)) for symbol, value in pnl_source.items()))
    unexpected_pnl = sorted({symbol for symbol, _ in pnl_items} - set(case.symbols))
    if unexpected_pnl:
        raise ValueError(f"scenario result attributes PnL outside its universe: {case.name} {unexpected_pnl}")
    deployed = _deployment_from_result(result)
    unexpected_deployment = sorted({symbol for symbol, _ in deployed} - set(case.symbols))
    if unexpected_deployment:
        raise ValueError(f"scenario result deploys outside its universe: {case.name} {unexpected_deployment}")
    return GeneralizationObservation(
        name=case.name,
        family=case.family,
        final_wealth=wealth,
        max_drawdown=drawdown,
        account_orders=raw_orders,
        symbol_pnl=pnl_items,
        deployed_exposure=deployed,
    )


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot aggregate an empty metric sequence")
    if not 0 <= probability <= 1:
        raise ValueError("quantile probability must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def aggregate_metrics(
    observations: Sequence[GeneralizationObservation],
) -> dict[str, float]:
    """Aggregate robust lower-tail wealth, upper-tail risk, and order burden."""
    if not observations:
        raise ValueError("generalization aggregation requires observations")
    wealth = [item.final_wealth for item in observations]
    drawdown = [item.max_drawdown for item in observations]
    orders = [float(item.account_orders) for item in observations]
    return {
        "p10_wealth": _quantile(wealth, 0.10),
        "median_wealth": float(median(wealth)),
        "p90_drawdown": _quantile(drawdown, 0.90),
        "worst_drawdown": max(drawdown),
        "median_orders": float(median(orders)),
        "p90_orders": _quantile(orders, 0.90),
    }


def prior_dependence(
    observations: Sequence[GeneralizationObservation],
) -> dict[str, float | str]:
    """Calculate PDI_1 and PDI_3 against the current full-universe wealth."""
    base = next((item for item in observations if item.family == "baseline"), None)
    remove_one = [item for item in observations if item.family == "remove_one"]
    remove_all = [item for item in observations if item.family == "remove_all"]
    if base is None or not remove_one or len(remove_all) != 1:
        raise ValueError("PDI requires one base, remove-one cases, and one remove-all case")
    weakest_one = min(remove_one, key=lambda item: (item.final_wealth, item.name))
    pdi_1 = max(0.0, 1.0 - weakest_one.final_wealth / base.final_wealth)
    pdi_3 = max(0.0, 1.0 - remove_all[0].final_wealth / base.final_wealth)
    return {
        "PDI_1": pdi_1,
        "PDI_3": pdi_3,
        "PDI_1_worst_case": weakest_one.name,
        "PDI_3_case": remove_all[0].name,
    }


def industry_pnl_shares(
    observation: GeneralizationObservation,
    industries: Mapping[str, str],
) -> dict[str, dict[str, float]]:
    """Return signed industry contribution as a share of net portfolio profit."""
    grouped: dict[str, float] = {}
    for symbol, value in observation.symbol_pnl:
        industry = industries.get(symbol, "unknown")
        grouped[industry] = grouped.get(industry, 0.0) + value
    net = sum(grouped.values())
    if abs(net) <= 1e-12:
        raise ValueError("industry PnL share is undefined when net PnL is zero")
    return {
        industry: {"pnl": pnl, "share_of_net_pnl": pnl / net} for industry, pnl in sorted(grouped.items())
    }


def _reference_aggregate(
    cases: Sequence[GeneralizationScenario],
    references: Mapping[str, Mapping[str, float | int]],
) -> dict[str, float]:
    names = [case.name for case in cases if case.family != "baseline"]
    if not names:
        raise ValueError("generalization gate requires stress scenarios")
    wealth = [float(references[name]["final_wealth"]) for name in names]
    drawdown = [float(references[name]["max_drawdown"]) for name in names]
    orders = [float(references[name]["account_orders"]) for name in names]
    return {
        "p10_wealth": _quantile(wealth, 0.10),
        "median_wealth": float(median(wealth)),
        "p90_drawdown": _quantile(drawdown, 0.90),
        "worst_drawdown": max(drawdown),
        "median_orders": float(median(orders)),
        "p90_orders": _quantile(orders, 0.90),
    }


def _relative_change(candidate: float, reference: float) -> float:
    if abs(reference) <= 1e-12:
        return candidate - reference
    return candidate / reference - 1.0


def _aggregate_gate_results(
    current: Mapping[str, float],
    reference: Mapping[str, float],
    policy: GeneralizationPolicy,
) -> dict[str, dict[str, Any]]:
    """Evaluate aggregate dominance and Pareto conditions against references."""
    wealth_change = _relative_change(current["median_wealth"], reference["median_wealth"])
    drawdown_change = current["worst_drawdown"] - reference["worst_drawdown"]
    order_change = _relative_change(current["median_orders"], reference["median_orders"])
    dominated = bool(
        wealth_change < -policy.dominance_wealth_regression
        and drawdown_change > policy.dominance_drawdown_regression
        and order_change > policy.dominance_order_regression
    )
    improvements = {
        "wealth": wealth_change >= policy.pareto_wealth_improvement,
        "drawdown": -drawdown_change >= policy.pareto_drawdown_improvement,
        "orders": -order_change >= policy.pareto_order_improvement,
    }
    acceptable = {
        "wealth": wealth_change >= -policy.pareto_wealth_regression,
        "drawdown": -drawdown_change >= -policy.pareto_drawdown_regression,
        "orders": -order_change >= -policy.pareto_order_regression,
    }
    pareto_passed = any(improvements.values()) and all(acceptable.values())
    deltas = {
        "median_wealth_relative": wealth_change,
        "worst_drawdown_additive": drawdown_change,
        "median_orders_relative": order_change,
    }
    return {
        "dominance": {
            "passed": not dominated,
            "deltas": deltas,
            "thresholds": {
                "wealth_regression": policy.dominance_wealth_regression,
                "drawdown_regression": policy.dominance_drawdown_regression,
                "order_regression": policy.dominance_order_regression,
            },
        },
        "pareto": {
            "passed": pareto_passed,
            "deltas": deltas,
            "material_improvements": improvements,
            "acceptable_regressions": acceptable,
            "thresholds": {
                "wealth_improvement": policy.pareto_wealth_improvement,
                "drawdown_improvement": policy.pareto_drawdown_improvement,
                "order_improvement": policy.pareto_order_improvement,
                "wealth_regression": policy.pareto_wealth_regression,
                "drawdown_regression": policy.pareto_drawdown_regression,
                "order_regression": policy.pareto_order_regression,
            },
        },
    }


def evaluate_generalization(
    cases: Sequence[GeneralizationScenario],
    runner: Callable[[GeneralizationScenario], Mapping[str, Any]],
    *,
    industries: Mapping[str, str],
    baseline: GeneralizationBaseline,
) -> dict[str, Any]:
    """Run a validated case matrix and enforce its reviewed economic policy."""
    case_by_name = {case.name: case for case in cases}
    if len(case_by_name) != len(cases):
        raise ValueError("generalization case matrix contains duplicate names")
    evidence_memberships = {
        (
            case.evidence_as_of,
            case.evidence_eligible_symbols,
            case.evidence_ineligible_symbols,
        )
        for case in cases
    }
    if len(evidence_memberships) != 1:
        raise ValueError("generalization scenarios contain inconsistent pre-window evidence membership")
    evidence_as_of, evidence_eligible, evidence_ineligible = next(iter(evidence_memberships))
    observations = tuple(observation_from_result(case, runner(case)) for case in cases)
    by_name = {item.name: item for item in observations}
    if len(by_name) != len(observations):
        raise RuntimeError("generalization runner produced duplicate observation names")
    stress = tuple(item for item in observations if item.family != "baseline")
    families = sorted({item.family for item in stress})
    base = next(item for item in observations if item.family == "baseline")
    policy = baseline.policy
    aggregate = aggregate_metrics(stress)
    reference_aggregate = _reference_aggregate(cases, baseline.references)
    gate_results = _aggregate_gate_results(aggregate, reference_aggregate, policy)
    failures: list[str] = []
    scenario_violations: dict[str, list[str]] = {name: [] for name in by_name}
    scenario_thresholds: dict[str, dict[str, float | int]] = {}
    dominated_scenarios: list[str] = []

    def add_scenario_violation(name: str, violation: str) -> None:
        """Record one scenario-local violation in both report indexes."""
        scenario_violations[name].append(violation)
        failures.append(f"{name}: {violation}")

    for name in sorted(by_name):
        item = by_name[name]
        reference = baseline.references[name]
        wealth_floor = float(reference["final_wealth"]) * policy.wealth_floor_ratio
        drawdown_ceiling = min(1.0, float(reference["max_drawdown"]) + policy.drawdown_tolerance)
        reference_orders = int(reference["account_orders"])
        order_ceiling = max(
            reference_orders + policy.order_tolerance,
            math.ceil(reference_orders * policy.order_ceiling_ratio),
        )
        scenario_thresholds[name] = {
            "final_wealth_floor": wealth_floor,
            "max_drawdown_ceiling": drawdown_ceiling,
            "account_orders_ceiling": order_ceiling,
        }
        if item.final_wealth < wealth_floor:
            add_scenario_violation(name, f"final_wealth below {wealth_floor:.6f}")
        if item.max_drawdown > drawdown_ceiling:
            add_scenario_violation(name, f"max_drawdown above {drawdown_ceiling:.6f}")
        if item.account_orders > order_ceiling:
            add_scenario_violation(name, f"account_orders above {order_ceiling}")
        wealth_change = _relative_change(item.final_wealth, float(reference["final_wealth"]))
        drawdown_change = item.max_drawdown - float(reference["max_drawdown"])
        order_change = _relative_change(item.account_orders, reference_orders)
        if (
            wealth_change < -policy.dominance_wealth_regression
            and drawdown_change > policy.dominance_drawdown_regression
            and order_change > policy.dominance_order_regression
        ):
            dominated_scenarios.append(name)
            add_scenario_violation(
                name,
                "dominance violation: wealth fell while drawdown and orders rose materially",
            )

    aggregate_dominance_passed = bool(gate_results["dominance"]["passed"])
    gate_results["dominance"]["aggregate_passed"] = aggregate_dominance_passed
    gate_results["dominance"]["dominated_scenarios"] = dominated_scenarios
    gate_results["dominance"]["passed"] = aggregate_dominance_passed and not dominated_scenarios

    dependency = prior_dependence(observations)
    pdi_1 = float(dependency["PDI_1"])
    if pdi_1 > policy.remove_one_max_dependency:
        worst_case = str(dependency["PDI_1_worst_case"])
        add_scenario_violation(
            worst_case,
            f"remove-one dependency {pdi_1:.6f} exceeds {policy.remove_one_max_dependency:.6f}",
        )

    remove_all = [item for item in observations if item.family == "remove_all"]
    no_optical = [item for item in observations if item.family == "no_optical"]
    random_observations = [item for item in observations if item.family == "random"]
    if len(remove_all) != 1 or len(no_optical) != 1 or not random_observations:
        raise ValueError("generalization gate requires remove-all, no-optical, and random scenarios")
    remove_all_item = remove_all[0]
    competitor_wealth_floor = float(baseline.competitor_best["value"]) * policy.remove_all_competitor_ratio
    scenario_thresholds[remove_all_item.name]["competitor_final_wealth_floor"] = competitor_wealth_floor
    if remove_all_item.final_wealth < policy.remove_all_min_wealth:
        add_scenario_violation(
            remove_all_item.name,
            f"final_wealth below positive-return floor {policy.remove_all_min_wealth:.6f}",
        )
    if remove_all_item.max_drawdown > policy.remove_all_max_drawdown:
        add_scenario_violation(
            remove_all_item.name,
            f"max_drawdown above removal ceiling {policy.remove_all_max_drawdown:.6f}",
        )
    if remove_all_item.final_wealth < competitor_wealth_floor:
        add_scenario_violation(
            remove_all_item.name,
            f"final_wealth below 95%+ reviewed competitor-best floor {competitor_wealth_floor:.6f}",
        )
    no_optical_item = no_optical[0]
    if no_optical_item.final_wealth < policy.no_optical_min_wealth:
        add_scenario_violation(
            no_optical_item.name,
            f"final_wealth below positive-return floor {policy.no_optical_min_wealth:.6f}",
        )
    if no_optical_item.max_drawdown > policy.no_optical_max_drawdown:
        add_scenario_violation(
            no_optical_item.name,
            f"max_drawdown above no-optical ceiling {policy.no_optical_max_drawdown:.6f}",
        )

    deployment_gate: dict[str, dict[str, Any]] = {}
    for item in observations:
        case = case_by_name[item.name]
        if item.family not in {"no_optical", "industry_only"}:
            continue
        expected_industries = (
            {industry for industry in set(industries.values()) if industry != "optical"}
            if item.family == "no_optical"
            else set(case.source_industries)
        )
        qualifying = tuple(
            (symbol, lifecycle)
            for symbol, lifecycle in item.deployed_exposure
            if lifecycle in {"CORE", "STRATEGIC"} and industries.get(symbol) in expected_industries
        )
        deployment_gate[item.name] = {
            "passed": bool(qualifying),
            "expected_industries": sorted(expected_industries),
            "qualifying_exposure": [
                {"symbol": symbol, "lifecycle": lifecycle} for symbol, lifecycle in qualifying
            ],
        }
        if not qualifying:
            expected_label = "non-optical" if item.family == "no_optical" else "expected-industry"
            add_scenario_violation(
                item.name,
                f"no deployed {expected_label} Core or Strategic exposure",
            )

    random_positive_fraction = sum(item.final_wealth > 1.0 for item in random_observations) / len(
        random_observations
    )
    random_p10_wealth = _quantile(
        [item.final_wealth for item in random_observations],
        0.10,
    )
    random_family_failures: list[str] = []
    if random_positive_fraction < policy.random_min_positive_fraction:
        violation = (
            f"positive fraction {random_positive_fraction:.6f} below "
            f"{policy.random_min_positive_fraction:.6f}"
        )
        random_family_failures.append(violation)
        failures.append(f"random: {violation}")
        for item in random_observations:
            if item.final_wealth <= 1.0:
                scenario_violations[item.name].append("random scenario is not profitable")
    if random_p10_wealth < policy.random_p10_min_wealth:
        violation = f"p10 wealth {random_p10_wealth:.6f} below {policy.random_p10_min_wealth:.6f}"
        random_family_failures.append(violation)
        failures.append(f"random: {violation}")
        for item in random_observations:
            if item.final_wealth < policy.random_p10_min_wealth:
                scenario_violations[item.name].append(
                    f"random tail wealth below {policy.random_p10_min_wealth:.6f}"
                )

    if not bool(gate_results["dominance"]["passed"]):
        failures.append("dominance: wealth, drawdown, and orders all materially regressed")
    if not bool(gate_results["pareto"]["passed"]):
        failures.append("pareto: no material improvement without material regression")

    pnl_shares = industry_pnl_shares(base, industries)
    optical_share = float(pnl_shares.get("optical", {}).get("share_of_net_pnl", 0.0))
    high_optical_dependency = optical_share > policy.optical_dependency_share_threshold
    diagnostics = (
        [
            "high industry dependency: optical PnL share "
            f"{optical_share:.6f} exceeds {policy.optical_dependency_share_threshold:.6f}"
        ]
        if high_optical_dependency
        else []
    )
    reference_deltas = {
        name: {
            "final_wealth": by_name[name].final_wealth - float(baseline.references[name]["final_wealth"]),
            "max_drawdown": by_name[name].max_drawdown - float(baseline.references[name]["max_drawdown"]),
            "account_orders": by_name[name].account_orders - int(baseline.references[name]["account_orders"]),
        }
        for name in sorted(by_name)
    }
    return {
        "passed": not failures,
        "failures": failures,
        "baseline_sha256": baseline.sha256,
        "case_fingerprint": baseline.case_fingerprint,
        "pre_window_evidence": {
            "as_of": evidence_as_of,
            "eligible_symbols": list(evidence_eligible),
            "ineligible_symbols": list(evidence_ineligible),
        },
        "policy": policy.to_dict(),
        "validation_fingerprint": baseline.validation_fingerprint,
        "provenance": baseline.provenance,
        "competitor_best": {
            **baseline.competitor_best,
            "required_ratio": policy.remove_all_competitor_ratio,
            "remove_all_wealth_floor": competitor_wealth_floor,
        },
        "scenario_count": len(observations),
        "aggregate": aggregate,
        "reference_aggregate": reference_aggregate,
        "gate_results": gate_results,
        "by_family": {
            family: aggregate_metrics(tuple(item for item in stress if item.family == family))
            for family in families
        },
        "prior_dependence": dependency,
        "industry_pnl_share": pnl_shares,
        "dependency_diagnostics": {
            "optical_pnl_share": optical_share,
            "optical_high_dependency": high_optical_dependency,
            "optical_dependency_share_threshold": policy.optical_dependency_share_threshold,
            "diagnostics": diagnostics,
        },
        "random_gate": {
            "passed": not random_family_failures,
            "positive_fraction": random_positive_fraction,
            "p10_wealth": random_p10_wealth,
            "violations": random_family_failures,
        },
        "deployment_gate": deployment_gate,
        "reference_deltas": reference_deltas,
        "scenarios": {
            item.name: {
                "passed": not scenario_violations[item.name],
                "violations": scenario_violations[item.name],
                "thresholds": scenario_thresholds[item.name],
                "family": item.family,
                "diagnostic": case_by_name[item.name].diagnostic,
                "source_industries": list(case_by_name[item.name].source_industries),
                "symbol_count": len(case_by_name[item.name].symbols),
                "removed_symbols": list(case_by_name[item.name].removed_symbols),
                "evidence_as_of": case_by_name[item.name].evidence_as_of,
                "final_wealth": item.final_wealth,
                "max_drawdown": item.max_drawdown,
                "account_orders": item.account_orders,
                "deployed_exposure": [
                    {"symbol": symbol, "lifecycle": lifecycle} for symbol, lifecycle in item.deployed_exposure
                ],
            }
            for item in observations
        },
    }


def run_generalization(
    *,
    data_dir: str | Path,
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    start: str,
    end: str,
    baseline_path: str | Path,
    pre_window_prices: Mapping[str, pd.Series | pd.DataFrame] | None = None,
    runner: Callable[[GeneralizationScenario], Mapping[str, Any]] | None = None,
    lookback_sessions: int = 120,
    random_sizes: Iterable[int] = (6, 12, 24),
    random_seeds: Iterable[int] = range(100),
    leave_top_k: Iterable[int] = (1, 2, 3, 5),
    balanced_per_industry: int = 2,
    industry_min_members: int = 2,
    base_seed: int = 20260810,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full read-only generalization gate through ProductionEngine."""
    start, end = require_ai_era_interval(start, end)
    symbols = _canonical_symbols(universe, label="generalization universe")
    priors = _canonical_symbols(prior_symbols, label="prior symbols")
    _validate_industry_coverage(symbols, industries)
    if provenance is not None and runner is None:
        raise ValueError("explicit generalization provenance is only valid with a custom runner")
    if provenance is None:
        repository_root = Path(__file__).resolve().parents[2]
        data_before = verify_data_manifest(data_dir)
        source_before = _production_source_fingerprint(repository_root)
        expected_provenance = build_generalization_provenance(
            data=data_before,
            universe=symbols,
            industries=industries,
            prior_symbols=priors,
            start=start,
            end=end,
            production_commit=_production_commit(repository_root),
            production_source_sha256=source_before,
            initial_cash=SystemConfig().initial_cash,
        )
    else:
        expected_provenance = _validated_provenance(provenance)
        expected_dataset = {
            "universe": list(symbols),
            "industries": dict(sorted(industries.items())),
            "prior_symbols": list(priors),
            "start": start,
            "end": end,
        }
        if expected_provenance["dataset"] != expected_dataset:
            raise RuntimeError("generalization supplied provenance does not match run inputs")

    baseline_source = Path(baseline_path)
    baseline_bytes, envelope = _read_generalization_baseline(baseline_source)
    _validate_baseline_envelope(envelope, expected_provenance=expected_provenance)
    guard: AbstractContextManager[None] = nullcontext()
    if provenance is None:
        guard = _immutable_validation_inputs(
            baseline_path=baseline_source,
            baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest(),
            data_dir=data_dir,
            repository_root=repository_root,
            data_before=data_before,
            source_before=source_before,
        )

    with guard:
        engine: ProductionEngine | None = None
        histories = pre_window_prices
        if histories is None:
            if runner is not None:
                raise ValueError("a custom runner must provide pre_window_prices")
            engine = ProductionEngine(data_dir)
            engine._load(symbols)  # Same causal source used by production replay.
            histories = {symbol: engine._raw[symbol]["close"] for symbol in symbols}
        evidence = compute_pre_window_evidence(
            histories,
            symbols,
            window_start=start,
            lookback_sessions=lookback_sessions,
        )
        cases = build_generalization_scenarios(
            symbols,
            industries,
            priors,
            window_start=start,
            pre_window_evidence=evidence,
            random_sizes=random_sizes,
            random_seeds=random_seeds,
            base_seed=base_seed,
            leave_top_k=leave_top_k,
            balanced_per_industry=balanced_per_industry,
            industry_min_members=industry_min_members,
        )
        baseline = load_generalization_baseline(
            baseline_source,
            cases,
            expected_provenance=expected_provenance,
        )

        selected_runner = runner
        if selected_runner is None:
            if engine is None:  # pragma: no cover - guarded above
                raise RuntimeError("production generalization runner was not initialized")

            def production_runner(case: GeneralizationScenario) -> Mapping[str, Any]:
                """Enrich one production replay with exact symbol-level PnL."""
                result = engine.backtest(symbols=case.symbols, start=start, end=end)
                final_date = pd.Timestamp(str(result["end"]))
                final_account = result.get("final_account", {})
                raw_positions = (
                    final_account.get("positions", {}) if isinstance(final_account, Mapping) else {}
                )
                if not isinstance(raw_positions, Mapping):
                    raise ValueError("backtest final positions are invalid")
                final_prices = {
                    str(symbol): engine._price(str(symbol), final_date) for symbol in raw_positions
                }
                enriched = dict(result)
                enriched["symbol_pnl"] = symbol_pnl_from_result(result, final_prices)
                return enriched

            selected_runner = production_runner

        report = evaluate_generalization(
            cases,
            selected_runner,
            industries=industries,
            baseline=baseline,
        )
        current_hash = hashlib.sha256(baseline_source.read_bytes()).hexdigest()
        if current_hash != baseline.sha256:
            raise RuntimeError("generalization baseline changed during validation")
        return report
