"""Deterministic, shared-configuration candidate discovery.

This module never constructs a production engine. Callers provide replay
observations, or a replay callback, and every candidate is evaluated with one
flat parameter mapping across every pool and scenario.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median, pvariance

from uquant.config import DEFAULT_CONFIG
from uquant.config_governance import ParameterCategory, load_config_governance
from uquant.validation.ai_era import AI_ERA_WINDOWS

type Scalar = str | int | float | bool | None
type SharedConfig = Mapping[str, Scalar]

_PER_POOL_KEYS = {
    "per_pool",
    "per_pool_config",
    "pool_config",
    "pool_configs",
    "pool_profiles",
    "profiles",
    "universe_profiles",
}


def validate_economic_parameter_names(names: Iterable[str]) -> tuple[str, ...]:
    """Validate names before candidate values are expanded or replayed."""

    governance = load_config_governance()
    validated: list[str] = []
    for raw_name in names:
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("candidate parameter names must be non-empty strings")
        if raw_name != raw_name.strip():
            raise ValueError("candidate parameter names must be canonical exact strings")
        name = raw_name
        try:
            entry = governance.entry(name)
        except ValueError as exc:
            raise ValueError(
                f"candidate overrides must name declared ECONOMIC SystemConfig fields: {name}"
            ) from exc
        if entry.category is not ParameterCategory.ECONOMIC:
            raise ValueError(
                "candidate overrides must name declared ECONOMIC SystemConfig fields: "
                f"{name} is {entry.category.value}"
            )
        validated.append(name)
    return tuple(sorted(validated))


def _canonical(parameters: SharedConfig) -> str:
    return json.dumps(
        dict(sorted(parameters.items())),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def validate_shared_config(
    parameters: SharedConfig,
    *,
    pool_names: Iterable[str] = (),
) -> dict[str, Scalar]:
    """Return validated ECONOMIC overrides or reject all other freedom.

    SystemConfig is flat today. Restricting research candidates to scalar,
    governed ECONOMIC values makes it impossible to smuggle an A/B/C/D/E
    parameter table or a market-rule, safety, derived, compatibility, or
    unknown override into a replay candidate.
    """
    pools = {str(name).strip().lower() for name in pool_names}
    clean: dict[str, Scalar] = {}
    for raw_name, value in parameters.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("candidate parameter names must be non-empty strings")
        if raw_name != raw_name.strip():
            raise ValueError("candidate parameter names must be canonical exact strings")
        name = raw_name
        lowered = name.lower()
        if (
            lowered in _PER_POOL_KEYS
            or lowered in pools
            or lowered.startswith("pool.")
            or lowered.startswith("profile.")
        ):
            raise ValueError(f"per-pool candidate parameters are forbidden: {name}")
        validate_economic_parameter_names((name,))
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError(f"candidate parameters must be scalar: {name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"candidate parameter must be finite: {name}")
        clean[name] = value
    ordered = dict(sorted(clean.items()))
    try:
        DEFAULT_CONFIG.override(**ordered)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ECONOMIC SystemConfig override: {exc}") from exc
    return ordered


def _deduplicated_values(values: Iterable[Scalar], *, name: str) -> tuple[Scalar, ...]:
    unique: dict[str, Scalar] = {}
    for value in values:
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError(f"candidate grid values must be scalar: {name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"candidate grid value must be finite: {name}")
        token = json.dumps(value, sort_keys=True, allow_nan=False)
        unique[token] = value
    if not unique:
        raise ValueError(f"candidate grid cannot be empty: {name}")
    return tuple(unique[token] for token in sorted(unique))


def enumerate_candidates(
    parameter_grid: Mapping[str, Iterable[Scalar]],
    *,
    base: SharedConfig | None = None,
    pool_names: Iterable[str] = (),
) -> tuple[dict[str, Scalar], ...]:
    """Enumerate a factorial grid in a stable, input-order-independent order."""
    base_config = validate_shared_config(base or {}, pool_names=pool_names)
    names = validate_economic_parameter_names(parameter_grid)
    if len(names) != len(set(names)):
        raise ValueError("candidate grid parameter names must be unique")
    values = tuple(_deduplicated_values(parameter_grid[name], name=name) for name in names)
    candidates: dict[str, dict[str, Scalar]] = {}
    for combination in itertools.product(*values):
        candidate = {**base_config, **dict(zip(names, combination, strict=True))}
        candidate = validate_shared_config(candidate, pool_names=pool_names)
        candidates[_canonical(candidate)] = candidate
    if not names:
        candidates[_canonical(base_config)] = base_config
    return tuple(candidates[token] for token in sorted(candidates))


@dataclass(frozen=True, slots=True)
class ReplayObservation:
    """One candidate result on one universe and one official AI-era window."""

    universe: str
    window: str
    start: str
    end: str
    final_wealth: float
    max_drawdown: float
    annual_turnover: float
    account_orders: int
    urgent_return: float = 0.0
    prior_dependence: float = 0.0

    def __post_init__(self) -> None:
        """Validate identity, ranges, and finiteness of replay metrics."""

        numeric = (
            self.final_wealth,
            self.max_drawdown,
            self.annual_turnover,
            self.urgent_return,
            self.prior_dependence,
        )
        if not self.universe:
            raise ValueError("replay observation needs a universe name")
        if not isinstance(self.window, str) or self.window not in AI_ERA_WINDOWS:
            raise ValueError("replay observation must use an official AI-era window")
        if (self.start, self.end) != AI_ERA_WINDOWS[self.window]:
            raise ValueError("replay observation interval does not match official interval")
        if not all(math.isfinite(value) for value in numeric):
            raise ValueError("replay observation metrics must be finite")
        if self.final_wealth <= 0:
            raise ValueError("wealth must be positive")
        if not 0 <= self.max_drawdown <= 1:
            raise ValueError("max_drawdown must be in [0, 1]")
        if self.annual_turnover < 0 or self.account_orders < 0:
            raise ValueError("turnover and account_orders cannot be negative")
        if not 0 <= self.prior_dependence <= 1:
            raise ValueError("prior_dependence must be in [0, 1]")
        if self.urgent_return <= -1:
            raise ValueError("urgent_return must be greater than -1")

    @property
    def years(self) -> float:
        """Derive the annualization period from the frozen official dates."""

        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days / 365.25

    @property
    def calmar(self) -> float:
        """Return annualized excess wealth divided by maximum drawdown."""

        annualized = float(self.final_wealth ** (1.0 / self.years) - 1.0)
        return float(annualized / max(self.max_drawdown, 1e-6))

    @property
    def crash_protection(self) -> float:
        """Map a crash-period return to [0, 1], where one means no loss."""
        return max(0.0, min(1.0, 1.0 + min(0.0, self.urgent_return)))


@dataclass(frozen=True, slots=True)
class ObjectiveWeights:
    """Weights for the report's multi-objective research score."""

    calmar: float = 0.10
    crash_protection: float = 0.25
    max_drawdown: float = 1.00
    turnover: float = 0.05
    prior_dependence: float = 0.50
    universe_variance: float = 0.25

    def __post_init__(self) -> None:
        values = (
            self.calmar,
            self.crash_protection,
            self.max_drawdown,
            self.turnover,
            self.prior_dependence,
            self.universe_variance,
        )
        if any(value < 0 or not math.isfinite(value) for value in values):
            raise ValueError("objective weights must be finite and nonnegative")


DEFAULT_OBJECTIVE_WEIGHTS = ObjectiveWeights()


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Auditable aggregate metrics for one shared candidate configuration."""

    parameters: tuple[tuple[str, Scalar], ...]
    candidate_id: str
    observations: tuple[ReplayObservation, ...]
    score: float
    median_log_wealth: float
    median_final_wealth: float
    median_calmar: float
    median_crash_protection: float
    worst_drawdown: float
    median_turnover: float
    median_orders: float
    prior_dependence: float
    universe_variance: float

    def config(self) -> dict[str, Scalar]:
        """Return an independent parameter mapping."""

        return dict(self.parameters)


def evaluate_candidate(
    parameters: SharedConfig,
    observations: Sequence[ReplayObservation],
    *,
    weights: ObjectiveWeights = DEFAULT_OBJECTIVE_WEIGHTS,
    pool_names: Iterable[str] = (),
) -> CandidateEvaluation:
    """Calculate the full return/risk/turnover/generalization objective."""
    config = validate_shared_config(parameters, pool_names=pool_names)
    if not observations:
        raise ValueError("candidate evaluation requires replay observations")
    ordered = tuple(sorted(observations, key=lambda item: (item.universe, item.window)))
    log_wealth = [math.log(item.final_wealth) for item in ordered]
    median_log_wealth = median(log_wealth)
    median_calmar = median(item.calmar for item in ordered)
    median_crash = median(item.crash_protection for item in ordered)
    worst_drawdown = max(item.max_drawdown for item in ordered)
    median_turnover = median(item.annual_turnover for item in ordered)
    median_orders = median(item.account_orders for item in ordered)
    prior_dependence = max(item.prior_dependence for item in ordered)
    # Do not penalize the intentional difference between market windows.
    # Universe variance measures cross-pool dispersion *within the
    # same market window*, then aggregates those comparable dispersions.
    wealth_by_window: dict[str, list[float]] = {}
    for item in ordered:
        wealth_by_window.setdefault(item.window, []).append(math.log(item.final_wealth))
    window_variances = [
        pvariance(values) if len(values) > 1 else 0.0 for _, values in sorted(wealth_by_window.items())
    ]
    universe_variance = median(window_variances)
    score = (
        median_log_wealth
        + weights.calmar * median_calmar
        + weights.crash_protection * median_crash
        - weights.max_drawdown * worst_drawdown
        - weights.turnover * median_turnover
        - weights.prior_dependence * prior_dependence
        - weights.universe_variance * universe_variance
    )
    canonical = _canonical(config)
    return CandidateEvaluation(
        parameters=tuple(config.items()),
        candidate_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
        observations=ordered,
        score=score,
        median_log_wealth=median_log_wealth,
        median_final_wealth=median(item.final_wealth for item in ordered),
        median_calmar=median_calmar,
        median_crash_protection=median_crash,
        worst_drawdown=worst_drawdown,
        median_turnover=median_turnover,
        median_orders=median_orders,
        prior_dependence=prior_dependence,
        universe_variance=universe_variance,
    )


@dataclass(frozen=True, slots=True)
class GateMateriality:
    """Material improvements and tolerated regressions for a Pareto gate."""

    wealth_improvement: float = 0.05
    drawdown_improvement: float = 0.02
    orders_improvement: float = 0.10
    wealth_regression: float = 0.01
    drawdown_regression: float = 0.005
    orders_regression: float = 0.05

    def __post_init__(self) -> None:
        values = (
            self.wealth_improvement,
            self.drawdown_improvement,
            self.orders_improvement,
            self.wealth_regression,
            self.drawdown_regression,
            self.orders_regression,
        )
        if any(value < 0 or not math.isfinite(value) for value in values):
            raise ValueError("gate materiality values must be finite and nonnegative")


DEFAULT_GATE_MATERIALITY = GateMateriality()


def _relative_change(candidate: float, baseline: float) -> float:
    if abs(baseline) <= 1e-12:
        return candidate - baseline
    return candidate / baseline - 1.0


def dominance_gate(
    candidate: CandidateEvaluation,
    baseline: CandidateEvaluation,
    *,
    tolerance: float = 1e-12,
) -> bool:
    """Reject the report's dominated case: less wealth, more DD, more trades."""
    dominated = (
        candidate.median_final_wealth < baseline.median_final_wealth - tolerance
        and candidate.worst_drawdown > baseline.worst_drawdown + tolerance
        and candidate.median_orders > baseline.median_orders + tolerance
    )
    return not dominated


def pareto_gate(
    candidate: CandidateEvaluation,
    baseline: CandidateEvaluation,
    *,
    materiality: GateMateriality = DEFAULT_GATE_MATERIALITY,
) -> bool:
    """Require one material improvement with no material regression elsewhere."""
    wealth_delta = _relative_change(candidate.median_final_wealth, baseline.median_final_wealth)
    drawdown_delta = baseline.worst_drawdown - candidate.worst_drawdown
    orders_delta = -_relative_change(candidate.median_orders, baseline.median_orders)
    improves = (
        wealth_delta >= materiality.wealth_improvement
        or drawdown_delta >= materiality.drawdown_improvement
        or orders_delta >= materiality.orders_improvement
    )
    acceptable = (
        wealth_delta >= -materiality.wealth_regression
        and drawdown_delta >= -materiality.drawdown_regression
        and orders_delta >= -materiality.orders_regression
    )
    return improves and acceptable


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One evaluated candidate and its optional comparison-gate outcomes."""

    evaluation: CandidateEvaluation
    dominance_passed: bool | None
    pareto_passed: bool | None

    @property
    def accepted(self) -> bool:
        """Accept only candidates explicitly compared with a baseline."""
        return self.dominance_passed is True and self.pareto_passed is True


type ReplayRunner = Callable[[dict[str, Scalar], str, str], ReplayObservation]


def search_candidates(
    *,
    parameter_grid: Mapping[str, Iterable[Scalar]],
    pools: Iterable[str],
    windows: Iterable[str],
    runner: ReplayRunner,
    base: SharedConfig | None = None,
    baseline: CandidateEvaluation | None = None,
    weights: ObjectiveWeights = DEFAULT_OBJECTIVE_WEIGHTS,
    materiality: GateMateriality = DEFAULT_GATE_MATERIALITY,
) -> tuple[SearchResult, ...]:
    """Run every candidate on the Cartesian pool/official-window matrix.

    The same detached config values are passed to every cell. A runner cannot
    return a differently named cell silently, which keeps the matrix complete
    and makes accidental per-pool optimization visible.
    """
    pool_names = tuple(sorted(set(pools)))
    window_names = tuple(sorted(set(windows)))
    if not pool_names or not window_names:
        raise ValueError("candidate search needs pools and windows")
    unexpected_windows = sorted(set(window_names) - set(AI_ERA_WINDOWS))
    if unexpected_windows:
        raise ValueError(f"candidate search requires official AI-era windows: {unexpected_windows}")
    candidates = enumerate_candidates(
        parameter_grid,
        base=base,
        pool_names=pool_names,
    )
    results: list[SearchResult] = []
    for candidate in candidates:
        observations: list[ReplayObservation] = []
        for pool in pool_names:
            for window in window_names:
                observation = runner(dict(candidate), pool, window)
                if observation.universe != pool or observation.window != window:
                    raise ValueError("runner returned an observation for the wrong matrix cell")
                observations.append(observation)
        evaluation = evaluate_candidate(
            candidate,
            observations,
            weights=weights,
            pool_names=pool_names,
        )
        results.append(
            SearchResult(
                evaluation=evaluation,
                dominance_passed=(None if baseline is None else dominance_gate(evaluation, baseline)),
                pareto_passed=(
                    None
                    if baseline is None
                    else pareto_gate(
                        evaluation,
                        baseline,
                        materiality=materiality,
                    )
                ),
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda item: (
                not item.accepted,
                -item.evaluation.score,
                item.evaluation.candidate_id,
            ),
        )
    )
