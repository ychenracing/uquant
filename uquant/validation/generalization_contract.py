"""Immutable six-window AI-era generalization scenario contract."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .ai_era import AI_ERA_WINDOWS, require_ai_era_interval
from .generalization import (
    GeneralizationScenario,
    PreWindowEvidence,
    build_generalization_scenarios,
)
from .universe import AIUniverse, load_ai_universe

CORE_SYMBOLS: Final = ("sz300308", "sz300502", "sz300394")
RANDOM_BASE_SEED: Final = 20260810
RANDOM_SEED_INDEXES: Final = (0, 1, 2, 3, 4)
RANDOM_POOL_SIZES: Final = (5, 9, 15, 20)
INDUSTRY_MIN_SAMPLE: Final = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ScenarioStatus(str, Enum):
    """Whether a contract record is eligible for an economic replay."""

    READY = "READY"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


@dataclass(frozen=True, slots=True)
class GeneralizationWindow:
    """One exact official economic interval."""

    name: str
    start: str
    end: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("generalization window requires a name")
        normalized = require_ai_era_interval(self.start, self.end)
        if normalized != (self.start, self.end):
            raise ValueError("generalization window bounds must be canonical")


@dataclass(frozen=True, slots=True)
class ContractScenario:
    """One complete matrix record, including non-economic sample failures."""

    window: GeneralizationWindow
    name: str
    family: str
    symbols: tuple[str, ...]
    reference_symbols: tuple[str, ...]
    status: ScenarioStatus
    raw_scenario: GeneralizationScenario | None
    evidence_as_of: str
    evidence_scores: tuple[tuple[str, float], ...]
    evidence_eligible_symbols: tuple[str, ...]
    evidence_ineligible_symbols: tuple[str, ...]
    evidence_sha256: str
    lookback_sessions: int
    removed_symbols: tuple[str, ...] = ()
    industry: str | None = None
    pool_size: int | None = None
    seed_index: int | None = None
    derived_seed: int | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.family or not self.symbols:
            raise ValueError("contract scenario requires name, family, and symbols")
        if self.symbols != tuple(sorted(self.symbols)) or len(self.symbols) != len(set(self.symbols)):
            raise ValueError("contract scenario symbols must be unique and canonical")
        if self.reference_symbols != tuple(sorted(self.reference_symbols)):
            raise ValueError("contract scenario reference symbols must be canonical")
        if not set(self.symbols) <= set(self.reference_symbols):
            raise ValueError("contract scenario symbols are outside the reference context")
        if self.status is ScenarioStatus.READY and self.raw_scenario is None:
            raise ValueError("economic contract scenario requires a replay scenario")
        if self.status is ScenarioStatus.INSUFFICIENT_SAMPLE and self.raw_scenario is not None:
            raise ValueError("insufficient sample cannot be an economic replay")
        if self.lookback_sessions < 1:
            raise ValueError("contract scenario lookback must be positive")
        if self.evidence_scores != tuple(sorted(self.evidence_scores)):
            raise ValueError("contract scenario evidence scores must be canonical")
        if any(not math.isfinite(score) for _, score in self.evidence_scores):
            raise ValueError("contract scenario evidence scores must be finite")
        if not _SHA256.fullmatch(self.evidence_sha256):
            raise ValueError("contract scenario evidence identity must be SHA-256")
        if tuple(symbol for symbol, _ in self.evidence_scores) != self.evidence_eligible_symbols:
            raise ValueError("contract scenario evidence scores differ from eligible cohort")
        if (
            self.evidence_ineligible_symbols != tuple(sorted(self.evidence_ineligible_symbols))
            or set(self.evidence_eligible_symbols) & set(self.evidence_ineligible_symbols)
        ):
            raise ValueError("contract scenario evidence cohorts are not canonical")
        evidence_payload = {
            "as_of": self.evidence_as_of,
            "scores": [[symbol, score] for symbol, score in self.evidence_scores],
            "eligible_symbols": list(self.evidence_eligible_symbols),
            "ineligible_symbols": list(self.evidence_ineligible_symbols),
            "lookback_sessions": self.lookback_sessions,
        }
        observed_evidence_sha256 = hashlib.sha256(
            json.dumps(
                evidence_payload,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if observed_evidence_sha256 != self.evidence_sha256:
            raise ValueError("contract scenario evidence identity differs from its causal payload")
        random_fields = (self.pool_size, self.seed_index, self.derived_seed)
        if self.family == "random" and any(value is None for value in random_fields):
            raise ValueError("random contract scenario requires complete seed provenance")
        if self.family != "random" and any(value is not None for value in random_fields):
            raise ValueError("non-random contract scenario cannot carry seed provenance")

    @property
    def economic(self) -> bool:
        """Return whether this record must execute an economic replay."""
        return self.status is ScenarioStatus.READY


def official_windows(names: tuple[str, ...] | None = None) -> tuple[GeneralizationWindow, ...]:
    """Return all six official windows or an exact named shard in official order."""
    if len(AI_ERA_WINDOWS) != 6:
        raise RuntimeError("AI-era generalization requires exactly six official windows")
    available = tuple(
        GeneralizationWindow(name=name, start=bounds[0], end=bounds[1])
        for name, bounds in AI_ERA_WINDOWS.items()
    )
    if names is None:
        return available
    if not names or len(names) != len(set(names)):
        raise ValueError("window shard requires unique official window names")
    requested = set(names)
    unknown = sorted(requested - set(AI_ERA_WINDOWS))
    if unknown:
        raise ValueError(f"unknown official window: {unknown}")
    return tuple(window for window in available if window.name in requested)


def _derived_seed(size: int, seed_index: int) -> int:
    payload = f"{RANDOM_BASE_SEED}:{size}:{seed_index}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _evidence_payload(
    evidence: PreWindowEvidence,
    *,
    lookback_sessions: int,
) -> dict[str, object]:
    if lookback_sessions < 1:
        raise ValueError("generalization evidence lookback must be positive")
    scores = tuple(sorted((symbol, float(score)) for symbol, score in evidence.scores))
    if any(not math.isfinite(score) for _, score in scores):
        raise ValueError("generalization evidence scores must be finite")
    return {
        "as_of": evidence.as_of,
        "scores": [[symbol, score] for symbol, score in scores],
        "eligible_symbols": list(evidence.eligible_symbols),
        "ineligible_symbols": list(evidence.ineligible_symbols),
        "lookback_sessions": lookback_sessions,
    }


def evidence_contract_payload(scenario: ContractScenario) -> dict[str, object]:
    """Return the canonical causal evidence identity retained by every cell."""
    return {
        "as_of": scenario.evidence_as_of,
        "scores": [[symbol, score] for symbol, score in scenario.evidence_scores],
        "eligible_symbols": list(scenario.evidence_eligible_symbols),
        "ineligible_symbols": list(scenario.evidence_ineligible_symbols),
        "lookback_sessions": scenario.lookback_sessions,
        "sha256": scenario.evidence_sha256,
    }


def _renamed_scenario(
    source: GeneralizationScenario,
    *,
    name: str,
    family: str,
) -> GeneralizationScenario:
    return GeneralizationScenario(
        name=name,
        family=family,
        symbols=source.symbols,
        removed_symbols=source.removed_symbols,
        diagnostic=source.diagnostic,
        source_industries=source.source_industries,
        seed=source.seed,
        evidence_as_of=source.evidence_as_of,
        evidence_eligible_symbols=source.evidence_eligible_symbols,
        evidence_ineligible_symbols=source.evidence_ineligible_symbols,
    )


def _record(
    *,
    window: GeneralizationWindow,
    universe: AIUniverse,
    source: GeneralizationScenario,
    name: str,
    family: str,
    status: ScenarioStatus = ScenarioStatus.READY,
    industry: str | None = None,
    pool_size: int | None = None,
    seed_index: int | None = None,
    evidence: PreWindowEvidence,
    lookback_sessions: int,
) -> ContractScenario:
    raw = _renamed_scenario(source, name=name, family=family) if status is ScenarioStatus.READY else None
    evidence_payload = _evidence_payload(evidence, lookback_sessions=lookback_sessions)
    evidence_sha256 = hashlib.sha256(
        json.dumps(
            evidence_payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return ContractScenario(
        window=window,
        name=name,
        family=family,
        symbols=source.symbols,
        reference_symbols=universe.symbols,
        status=status,
        raw_scenario=raw,
        evidence_as_of=evidence.as_of,
        evidence_scores=tuple(sorted((symbol, float(score)) for symbol, score in evidence.scores)),
        evidence_eligible_symbols=evidence.eligible_symbols,
        evidence_ineligible_symbols=evidence.ineligible_symbols,
        evidence_sha256=evidence_sha256,
        lookback_sessions=lookback_sessions,
        removed_symbols=source.removed_symbols,
        industry=industry,
        pool_size=pool_size,
        seed_index=seed_index,
        derived_seed=(
            _derived_seed(pool_size, seed_index)
            if pool_size is not None and seed_index is not None
            else None
        ),
    )


def build_official_scenarios(
    *,
    window: GeneralizationWindow,
    evidence: PreWindowEvidence,
    universe: AIUniverse | None = None,
    lookback_sessions: int = 120,
) -> tuple[ContractScenario, ...]:
    """Build the complete fixed matrix for one official window."""
    if window not in official_windows():
        raise ValueError("scenario construction requires an exact official window")
    canonical = load_ai_universe() if universe is None else universe
    eligible_symbols = canonical.symbols_as_of(evidence.as_of)
    evidence_members = set(evidence.eligible_symbols) | set(evidence.ineligible_symbols)
    if evidence_members != set(eligible_symbols):
        raise ValueError(
            "pre-window evidence must exactly partition point-in-time AI membership"
        )
    if max(RANDOM_POOL_SIZES) > len(eligible_symbols):
        raise ValueError(
            "fixed random pool size exceeds point-in-time eligible universe: "
            f"max_size={max(RANDOM_POOL_SIZES)}, eligible={len(eligible_symbols)}"
        )
    industries = {
        symbol: canonical.industry_of(symbol, evidence.as_of) for symbol in eligible_symbols
    }
    if any(industry == "unknown" for industry in industries.values()):
        raise ValueError("point-in-time industry membership is incomplete")
    complete = build_generalization_scenarios(
        eligible_symbols,
        industries,
        CORE_SYMBOLS,
        window_start=window.start,
        pre_window_evidence=evidence,
        random_sizes=RANDOM_POOL_SIZES,
        random_seeds=RANDOM_SEED_INDEXES,
        base_seed=RANDOM_BASE_SEED,
        leave_top_k=(),
        balanced_per_industry=INDUSTRY_MIN_SAMPLE,
        industry_min_members=INDUSTRY_MIN_SAMPLE,
    )
    by_name = {scenario.name: scenario for scenario in complete}
    records: list[ContractScenario] = [
        _record(
            window=window,
            universe=canonical,
            source=by_name["base"],
            name="full",
            family="full",
            evidence=evidence,
            lookback_sessions=lookback_sessions,
        )
    ]
    for symbol in CORE_SYMBOLS:
        source = next(
            scenario
            for scenario in complete
            if scenario.family == "remove_one" and scenario.removed_symbols == (symbol,)
        )
        records.append(
            _record(
                window=window,
                universe=canonical,
                source=source,
                name=f"remove-one__{symbol}",
                family="remove_one",
                evidence=evidence,
                lookback_sessions=lookback_sessions,
            )
        )
    records.extend(
        (
            _record(
                window=window,
                universe=canonical,
                source=next(scenario for scenario in complete if scenario.family == "remove_all"),
                name="remove-all-core",
                family="remove_all_core",
                evidence=evidence,
                lookback_sessions=lookback_sessions,
            ),
            _record(
                window=window,
                universe=canonical,
                source=by_name["no_optical"],
                name="tradable-no-optical",
                family="tradable_no_optical",
                evidence=evidence,
                lookback_sessions=lookback_sessions,
            ),
            _record(
                window=window,
                universe=canonical,
                source=by_name["balanced_industries"],
                name="industry-balanced",
                family="industry_balanced",
                evidence=evidence,
                lookback_sessions=lookback_sessions,
            ),
        )
    )
    for industry in sorted(set(industries.values())):
        source = by_name[f"industry_only__{industry}"]
        status = (
            ScenarioStatus.READY
            if len(source.symbols) >= INDUSTRY_MIN_SAMPLE
            else ScenarioStatus.INSUFFICIENT_SAMPLE
        )
        records.append(
            _record(
                window=window,
                universe=canonical,
                source=source,
                name=f"subindustry__{industry}",
                family="subindustry",
                status=status,
                industry=industry,
                evidence=evidence,
                lookback_sessions=lookback_sessions,
            )
        )
    for size in RANDOM_POOL_SIZES:
        for seed_index in RANDOM_SEED_INDEXES:
            source = by_name[f"random_{size:02d}__{seed_index:04d}"]
            records.append(
                _record(
                    window=window,
                    universe=canonical,
                    source=source,
                    name=f"random__{size:02d}__{seed_index:04d}",
                    family="random",
                    pool_size=size,
                    seed_index=seed_index,
                    evidence=evidence,
                    lookback_sessions=lookback_sessions,
                )
            )
    names = tuple(record.name for record in records)
    if len(names) != len(set(names)):
        raise RuntimeError("official scenario contract produced duplicate cells")
    economic = sum(record.economic for record in records)
    if economic != 32 or len(records) != 39:
        raise RuntimeError(
            "canonical universe no longer produces the reviewed 32 economic and 7 sample records"
        )
    return tuple(records)


def scenario_contract_fingerprint(scenarios: tuple[ContractScenario, ...]) -> str:
    """Hash every ordered contract field, including insufficient sample records."""
    payload = [
        {
            "window": {
                "name": item.window.name,
                "start": item.window.start,
                "end": item.window.end,
            },
            "name": item.name,
            "family": item.family,
            "symbols": list(item.symbols),
            "reference_symbols": list(item.reference_symbols),
            "removed_symbols": list(item.removed_symbols),
            "status": item.status.value,
            "industry": item.industry,
            "pool_size": item.pool_size,
            "seed_index": item.seed_index,
            "derived_seed": item.derived_seed,
            "evidence": evidence_contract_payload(item),
        }
        for item in scenarios
    ]
    encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()
