"""Deterministic universe-generalization diagnostics and frozen references.

The production engine remains the only strategy implementation.  This module
only constructs causal universe perturbations, replays them through a supplied
runner, and aggregates dependency evidence.  It deliberately has no API that
writes a baseline file: reference updates must remain an explicit, reviewed
repository change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ...config_governance import GOVERNANCE_PATH

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
