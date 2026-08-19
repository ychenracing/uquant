"""Immutable contracts emitted by the Independent Risk Sentinel."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Final

RISK_FAMILIES: Final = frozenset(
    {
        "market_velocity",
        "breadth_structure",
        "covariance_stress",
        "leadership_damage",
        "live_book_damage",
        "capital_damage",
    }
)


class SentinelLevel(str, Enum):
    """Standardized, observation-only Sentinel severity."""

    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    CRITICAL = "CRITICAL"
    NOT_READY = "NOT_READY"


class WarmupStatus(str, Enum):
    """Health of causal data required for one assessment."""

    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


def _unit_interval(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{label} must be a finite number in [0, 1]")
    return result


def _canonical_strings(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class CoverageHealth:
    """Coverage components and fail-closed warmup state."""

    status: WarmupStatus
    confidence: float
    component_observation: float
    subindustry_coverage: float
    held_industry_mapping: float
    reference_warmup: float
    missing_indices: tuple[str, ...]
    new_symbols: tuple[str, ...]
    stale_symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, WarmupStatus):
            raise ValueError("coverage status is invalid")
        for field in (
            "confidence",
            "component_observation",
            "subindustry_coverage",
            "held_industry_mapping",
            "reference_warmup",
        ):
            object.__setattr__(
                self,
                field,
                _unit_interval(getattr(self, field), label=f"coverage {field}"),
            )
        expected = (
            0.45 * self.component_observation
            + 0.35 * self.subindustry_coverage
            + 0.20 * self.held_industry_mapping
        )
        if not math.isclose(self.confidence, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("coverage confidence differs from the required formula")
        for field in ("missing_indices", "new_symbols", "stale_symbols"):
            object.__setattr__(
                self,
                field,
                _canonical_strings(getattr(self, field), label=f"coverage {field}"),
            )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "status": self.status.value,
            "confidence": self.confidence,
            "component_observation": self.component_observation,
            "subindustry_coverage": self.subindustry_coverage,
            "held_industry_mapping": self.held_industry_mapping,
            "reference_warmup": self.reference_warmup,
            "missing_indices": list(self.missing_indices),
            "new_symbols": list(self.new_symbols),
            "stale_symbols": list(self.stale_symbols),
        }


@dataclass(frozen=True, slots=True)
class SubindustryEvidence:
    """Robust per-subindustry evidence before equal-group aggregation."""

    industry: str
    member_count: int
    fast_return: float
    downside_breadth: float
    below_ma20: float
    volatility_ratio: float

    def __post_init__(self) -> None:
        if not isinstance(self.industry, str) or not self.industry:
            raise ValueError("subindustry name must be non-empty")
        if (
            isinstance(self.member_count, bool)
            or not isinstance(self.member_count, int)
            or self.member_count < 1
        ):
            raise ValueError("subindustry member_count must be positive")
        for field in ("fast_return", "volatility_ratio"):
            value = getattr(self, field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"subindustry {field} must be finite")
            object.__setattr__(self, field, float(value))
        for field in ("downside_breadth", "below_ma20"):
            object.__setattr__(
                self,
                field,
                _unit_interval(getattr(self, field), label=f"subindustry {field}"),
            )

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON fields."""

        return {
            "industry": self.industry,
            "member_count": self.member_count,
            "fast_return": self.fast_return,
            "downside_breadth": self.downside_breadth,
            "below_ma20": self.below_ma20,
            "volatility_ratio": self.volatility_ratio,
        }


@dataclass(frozen=True, slots=True)
class SentinelAssessment:
    """One deterministic, observation-only Sentinel opinion."""

    date: str
    level: SentinelLevel
    confidence: float
    suggested_gross_cap: float | None
    freeze_new_risk: bool
    evidence_families: tuple[str, ...]
    reasons: tuple[str, ...]
    first_evidence_date: str | None
    coverage: CoverageHealth
    metrics: dict[str, float]
    weakest_subindustries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            point = date.fromisoformat(self.date)
        except (TypeError, ValueError) as exc:
            raise ValueError("Sentinel date must be an ISO date") from exc
        if not isinstance(self.level, SentinelLevel):
            raise ValueError("Sentinel level is invalid")
        object.__setattr__(
            self,
            "confidence",
            _unit_interval(self.confidence, label="Sentinel confidence"),
        )
        if self.suggested_gross_cap is not None:
            object.__setattr__(
                self,
                "suggested_gross_cap",
                _unit_interval(
                    self.suggested_gross_cap,
                    label="Sentinel suggested gross cap",
                ),
            )
        if not isinstance(self.freeze_new_risk, bool):
            raise ValueError("Sentinel freeze_new_risk must be boolean")
        families = _canonical_strings(
            self.evidence_families,
            label="Sentinel evidence families",
        )
        if not set(families).issubset(RISK_FAMILIES):
            raise ValueError("Sentinel evidence family is unknown")
        object.__setattr__(self, "evidence_families", families)
        object.__setattr__(
            self,
            "reasons",
            _canonical_strings(self.reasons, label="Sentinel reasons"),
        )
        if self.first_evidence_date is not None:
            try:
                first = date.fromisoformat(self.first_evidence_date)
            except (TypeError, ValueError) as exc:
                raise ValueError("Sentinel first evidence date must be an ISO date") from exc
            if first > point:
                raise ValueError("Sentinel first evidence date cannot be in the future")
        if not isinstance(self.coverage, CoverageHealth):
            raise ValueError("Sentinel coverage is invalid")
        normalized: dict[str, float] = {}
        for key, value in sorted(self.metrics.items()):
            if (
                not isinstance(key, str)
                or not key
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError("Sentinel metric must be named and finite")
            normalized[key] = float(value)
        object.__setattr__(self, "metrics", normalized)
        object.__setattr__(
            self,
            "weakest_subindustries",
            _canonical_strings(
                self.weakest_subindustries,
                label="Sentinel weakest subindustries",
            ),
        )
        if self.level is SentinelLevel.NOT_READY and (
            self.suggested_gross_cap is not None or not self.freeze_new_risk
        ):
            raise ValueError("NOT_READY Sentinel cannot claim normal safety")

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "date": self.date,
            "level": self.level.value,
            "confidence": self.confidence,
            "suggested_gross_cap": self.suggested_gross_cap,
            "freeze_new_risk": self.freeze_new_risk,
            "evidence_families": list(self.evidence_families),
            "reasons": list(self.reasons),
            "first_evidence_date": self.first_evidence_date,
            "coverage": self.coverage.to_dict(),
            "metrics": dict(self.metrics),
            "weakest_subindustries": list(self.weakest_subindustries),
        }


_HISTORICAL_MARKET_FAMILIES: Final = frozenset(
    {"market_velocity", "breadth_structure", "covariance_stress"}
)


def _family_pairs(
    values: tuple[tuple[str, bool], ...],
    *,
    label: str,
) -> tuple[tuple[str, bool], ...]:
    names = tuple(name for name, _ in values)
    if any(
        not isinstance(name, str)
        or name not in _HISTORICAL_MARKET_FAMILIES
        or not isinstance(active, bool)
        for name, active in values
    ):
        raise ValueError(f"{label} contains an invalid market family")
    if len(names) != len(set(names)):
        raise ValueError(f"{label} contains duplicate market families")
    return tuple(sorted(values))


@dataclass(frozen=True, slots=True)
class SentinelMarketRow:
    """One account-free, point-in-time Sentinel market observation."""

    date: str
    coverage_status: WarmupStatus
    confidence: float
    level: SentinelLevel
    freeze_candidate: bool
    family_active: tuple[tuple[str, bool], ...]
    reasons: tuple[str, ...]
    weakest_subindustries: tuple[str, ...]
    severe_direct: bool = False

    def __post_init__(self) -> None:
        date.fromisoformat(self.date)
        if not isinstance(self.coverage_status, WarmupStatus):
            raise ValueError("Sentinel market-row coverage is invalid")
        if not isinstance(self.level, SentinelLevel):
            raise ValueError("Sentinel market-row level is invalid")
        object.__setattr__(
            self,
            "confidence",
            _unit_interval(self.confidence, label="Sentinel market-row confidence"),
        )
        if not isinstance(self.freeze_candidate, bool) or not isinstance(
            self.severe_direct,
            bool,
        ):
            raise ValueError("Sentinel market-row flags must be boolean")
        object.__setattr__(
            self,
            "family_active",
            _family_pairs(self.family_active, label="Sentinel market row"),
        )
        object.__setattr__(
            self,
            "reasons",
            _canonical_strings(self.reasons, label="Sentinel market-row reasons"),
        )
        object.__setattr__(
            self,
            "weakest_subindustries",
            _canonical_strings(
                self.weakest_subindustries,
                label="Sentinel market-row weakest subindustries",
            ),
        )

    @property
    def active_families(self) -> tuple[str, ...]:
        return tuple(name for name, active in self.family_active if active)


@dataclass(frozen=True, slots=True)
class BaseMarketRiskRow:
    """One point-in-time base-risk market-family observation."""

    date: str
    family_active: tuple[tuple[str, bool], ...]
    data_ready: bool

    def __post_init__(self) -> None:
        date.fromisoformat(self.date)
        object.__setattr__(
            self,
            "family_active",
            _family_pairs(self.family_active, label="base market row"),
        )
        if not isinstance(self.data_ready, bool):
            raise ValueError("base market-row readiness must be boolean")

    @property
    def active_families(self) -> tuple[str, ...]:
        return tuple(name for name, active in self.family_active if active)


@dataclass(frozen=True, slots=True)
class SentinelCausalState:
    """Folded confirmation and repair diagnostics with no production authority."""

    effective_level: SentinelLevel
    confirmed_since: str | None
    confirmation_days: int
    repair_days: int
    confirmation_history_trusted: bool
    trust_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RiskEvidenceTimeline:
    """Immutable base/Sentinel market history through one causal as-of date."""

    as_of: str
    sessions: tuple[str, ...]
    sentinel_rows: tuple[SentinelMarketRow, ...]
    base_rows: tuple[BaseMarketRiskRow, ...]
    sentinel_first_family_dates: tuple[tuple[str, str], ...]
    base_first_family_dates: tuple[tuple[str, str], ...]
    incremental_families: tuple[str, ...]
    earlier_families: tuple[str, ...]
    confirmation_days: int
    repair_days: int
    effective_level: SentinelLevel
    confirmed_since: str | None
    confirmation_history_trusted: bool
    trust_reasons: tuple[str, ...]
