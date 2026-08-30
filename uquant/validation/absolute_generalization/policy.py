"""Literal seven-component policy over independently validated evidence."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

from uquant.contracts.strict_json import canonical_json_sha256
from uquant.validation.statistics import linear_quantile

from ._acceptance_evidence import (
    crown_industries,
    healthy_retry_sessions,
    repair_healthy_sessions,
    terminal_projection,
)
from .artifacts import CellArtifact
from .contract import AbsoluteGeneralizationContract

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHAMPION_PATHS: Mapping[str, str] = MappingProxyType(
    {
        "equity": "654142a4a217d243c53104ac6636a1778314c2e04497cfd0456a6385ea3aab39",
        "fills": "e4927cfbce9202e488dfc3c0cbadf412c527a68314b499eab4e9d916d5037fd1",
        "orders": "85f9a3cabd7964a1c8a1315fa7732ce5ddd593480f34619d925c92c5b4c2fa75",
        "positions": "8819f3e2c32e9076bf6007040510c93ae02cbef8d6c41159bf12ffccec9782d0",
        "targets": "7f33eca7246df9af6895865b526e7e754f9a3a78ffc5dd9b7a293d78cd8c0f95",
    }
)
_RECOVERABLE_REASONS = frozenset(
    {
        "broker_rejection",
        "candidate_not_tradable",
        "candidate_or_route_no_longer_qualified",
        "candidate_removed_from_allowed_universe",
        "limit_blocked",
        "order_timeout",
        "qualification_observation_window_elapsed",
        "unfilled_probe_timeout",
    }
)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze(item) for item in value)
    return value


def _policy_thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _policy_thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_policy_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ComponentResult:
    """One independently evidenced literal capability result."""

    name: str
    passed: bool
    failures: tuple[str, ...]
    evidence_sha256: str
    evidence: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or type(self.passed) is not bool
            or type(self.failures) is not tuple
            or any(type(item) is not str or not item for item in self.failures)
            or self.passed is not (not self.failures)
            or not _SHA256.fullmatch(self.evidence_sha256)
            or not isinstance(self.evidence, Mapping)
            or canonical_json_sha256(_policy_thaw(self.evidence))
            != self.evidence_sha256
        ):
            raise ValueError("absolute generalization component result is malformed")
        object.__setattr__(self, "evidence", cast(Mapping[str, object], _freeze(self.evidence)))


def _component(
    name: str,
    failures: Sequence[str],
    evidence: Mapping[str, object],
) -> ComponentResult:
    unique = tuple(dict.fromkeys(failures))
    return ComponentResult(
        name=name,
        passed=not unique,
        failures=unique,
        evidence_sha256=canonical_json_sha256(evidence),
        evidence=evidence,
    )


def _metrics(cells: Sequence[CellArtifact]) -> tuple[tuple[CellArtifact, object], ...]:
    return tuple((cell, cell.metrics) for cell in cells if cell.metrics is not None)


def _literal_cell_failures(
    cell: CellArtifact,
    *,
    critical: bool,
) -> list[str]:
    metrics = cell.metrics
    if metrics is None:
        return [f"{cell.cell_id} has no complete metrics"]
    failures: list[str] = []
    if metrics.positive_strategic_target_sessions < 1:
        failures.append(f"{cell.cell_id} has no positive strategic Target")
    if metrics.actual_strategic_epoch_count < 1:
        failures.append(f"{cell.cell_id} has no Fill-gated epoch")
    if cell.removed_symbol in metrics.owner_symbols:
        failures.append(f"{cell.cell_id} retained the removed owner")
    if cell.intervention_provenance:
        failures.append(f"{cell.cell_id} contains intervention provenance")
    if not cell.accounting_reconciled or not cell.target_order_fill_identity_reconciled:
        failures.append(f"{cell.cell_id} reconciliation failed")
    if any(
        count != 0
        for count in (
            cell.duplicate_grant_count,
            cell.duplicate_order_count,
            cell.duplicate_epoch_count,
        )
    ):
        failures.append(f"{cell.cell_id} contains duplicate execution identities")
    if critical:
        prefix = f"critical {cell.removed_symbol}"
        if not metrics.final_wealth > 1.0:
            failures.append(f"{prefix} wealth is not strictly positive")
        if metrics.max_drawdown > 0.30:
            failures.append(f"{prefix} drawdown exceeds 0.30")
        if metrics.longest_healthy_zero_total_target_streak > 60:
            failures.append(f"{prefix} healthy cash vacancy exceeds 60")
    return failures


def evaluate_absolute_strategic_robustness(
    cells: Sequence[CellArtifact],
    contract: AbsoluteGeneralizationContract,
) -> ComponentResult:
    """Apply every literal 34-cell and critical-removal economic gate."""

    failures: list[str] = []
    expected = set(contract.canonical_universe)
    observed = [cell.removed_symbol for cell in cells]
    if len(cells) != 34 or set(observed) != expected or len(set(observed)) != 34:
        failures.append("absolute robustness requires exactly 34 canonical cells")
    critical = set(contract.critical_removals)
    for cell in cells:
        failures.extend(_literal_cell_failures(cell, critical=cell.removed_symbol in critical))
    complete = [cell.metrics for cell in cells if cell.metrics is not None]
    evidence: dict[str, object] = {
        "cell_count": len(cells),
        "positive_strategic_target_cells": sum(
            item.positive_strategic_target_sessions > 0 for item in complete
        ),
        "actual_strategic_epoch_cells": sum(
            item.actual_strategic_epoch_count > 0 for item in complete
        ),
    }
    if len(complete) == 34:
        wealth = [item.final_wealth for item in complete]
        drawdowns = [item.max_drawdown for item in complete]
        vacancy = [float(item.longest_healthy_zero_total_target_streak) for item in complete]
        positive = sum(
            value > contract.thresholds.positive_return_final_wealth_exclusive_minimum
            for value in wealth
        )
        fraction = positive / 34
        p10 = linear_quantile(wealth, 0.10)
        p90_drawdown = linear_quantile(drawdowns, 0.90)
        p90_vacancy = linear_quantile(vacancy, 0.90)
        worst_vacancy = int(max(vacancy))
        evidence.update(
            positive_return_count=positive,
            positive_return_fraction=fraction,
            p10_final_wealth=p10,
            p90_max_drawdown=p90_drawdown,
            p90_healthy_zero_total_target_streak=p90_vacancy,
            worst_healthy_zero_total_target_streak=worst_vacancy,
        )
        if fraction < contract.thresholds.minimum_positive_return_fraction:
            failures.append("positive return fraction is below 0.90")
        if p10 < contract.thresholds.minimum_p10_final_wealth:
            failures.append("p10 final wealth is below 1.0")
        if p90_drawdown > contract.thresholds.maximum_p90_drawdown:
            failures.append("p90 max drawdown exceeds 0.30")
        if p90_vacancy > contract.thresholds.maximum_p90_healthy_zero_total_target_streak:
            failures.append("p90 healthy zero-total-target streak exceeds 60")
        if worst_vacancy > contract.thresholds.maximum_worst_healthy_zero_total_target_streak:
            failures.append("worst healthy zero-total-target streak exceeds 120")
    else:
        failures.append("absolute robustness requires 34 complete metric cells")
    return _component("absolute_strategic_robustness", failures, evidence)


def _witness_success(cell: CellArtifact) -> bool:
    metrics = cell.metrics
    return bool(
        metrics is not None
        and metrics.intentional_role_absent_symbols == (cell.removed_symbol,)
        and not metrics.expected_but_unavailable_symbols
        and metrics.role_identity_consistent
        and metrics.qualification_coverage == 1.0
        and metrics.risk_coverage == 1.0
        and metrics.final_wealth > 1.0
        and metrics.max_drawdown <= 0.30
        and metrics.longest_healthy_zero_total_target_streak <= 60
        and metrics.positive_strategic_target_sessions >= 1
        and metrics.strategic_order_count >= 1
        and metrics.strategic_fill_count >= 1
        and metrics.actual_strategic_epoch_count >= 1
        and not cell.intervention_provenance
    )


def evaluate_witness_resilience(
    cells: Sequence[CellArtifact],
    contract: AbsoluteGeneralizationContract,
) -> ComponentResult:
    by_symbol = {cell.removed_symbol: cell for cell in cells}
    witnesses = contract.required_witnesses
    numerator = sum(
        symbol in by_symbol and _witness_success(by_symbol[symbol]) for symbol in witnesses
    )
    denominator = len(witnesses)
    fraction = numerator / denominator
    failures = [] if fraction >= contract.thresholds.minimum_witness_fraction else [
        f"witness recovery is {numerator}/{denominator}, not 5/5"
    ]
    return _component(
        "witness_resilience",
        failures,
        {"numerator": numerator, "denominator": denominator, "fraction": fraction},
    )


def evaluate_complete_literal_metrics(
    cells: Sequence[CellArtifact],
    contract: AbsoluteGeneralizationContract,
) -> ComponentResult:
    complete = sum(cell.metrics is not None and cell.status == "COMPLETE" for cell in cells)
    accounting = sum(cell.accounting_reconciled is True for cell in cells)
    attribution = sum(cell.target_order_fill_identity_reconciled is True for cell in cells)
    intervention_free = sum(not cell.intervention_provenance for cell in cells)
    evidence = {
        "expected_cells": len(contract.canonical_universe),
        "complete_metric_cells": complete,
        "accounting_reconciled_cells": accounting,
        "attribution_reconciled_cells": attribution,
        "intervention_free_cells": intervention_free,
    }
    failures: list[str] = []
    if len(cells) != 34 or complete != 34:
        failures.append("complete literal metrics require 34/34 cells")
    if accounting != 34:
        failures.append("complete literal metrics require reconciled accounting")
    if attribution != 34:
        failures.append("complete literal metrics require reconciled attribution")
    if intervention_free != 34:
        failures.append("complete literal metrics require intervention-free evidence")
    return _component("complete_literal_metrics", failures, evidence)


def _champion_component(
    raw: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> ComponentResult:
    metrics = cast(Mapping[str, object], raw["metrics"])
    paths = cast(Mapping[str, object], raw["path_sha256"])
    report = cast(Mapping[str, object], raw["report_13"])
    wealth = float(cast(float, metrics["final_wealth"]))
    drawdown = float(cast(float, metrics["max_drawdown"]))
    failures: list[str] = []
    if wealth < contract.frozen_baseline.champion_minimum_final_wealth:
        failures.append("champion wealth is below the frozen 95% floor")
    if drawdown > contract.frozen_baseline.champion_maximum_drawdown:
        failures.append("champion drawdown exceeds 0.30")
    if dict(paths) != _CHAMPION_PATHS:
        failures.append("champion Target/Order/Fill/Position/Equity path differs")
    failures.extend(
        f"champion duplicate {label} count is nonzero"
        for label in ("grant", "order", "epoch")
        if raw[f"duplicate_{label}_count"] != 0
    )
    if raw["incumbent_epoch_count"] != 1 or raw[
        "successor_capital_before_incumbent_exit_count"
    ] != 0:
        failures.append("champion incumbent lifecycle was preempted")
    final_equity = float(cast(float, report["final_equity"]))
    if not math.isclose(
        final_equity,
        float(cast(float, report["cash"]))
        + float(cast(float, report["position_market_value"])),
    ) or not math.isclose(
        final_equity - float(cast(float, report["initial_cash"])),
        float(cast(float, report["realized_pnl"]))
        + float(cast(float, report["open_pnl"])),
    ):
        failures.append("champion report-13 accounting differs")
    if float(cast(float, report["maximum_target_gross"])) > float(
        cast(float, report["minimum_risk_target_gross_cap"])
    ):
        failures.append("champion report-13 capital authority expanded")
    if not cast(Sequence[object], report["owner_symbols"]) or cast(
        Sequence[object], report["unexpected_owner_symbols"]
    ):
        failures.append("champion report-13 owner lifecycle differs")
    return _component(
        "champion_non_regression",
        failures,
        {
            "final_wealth": wealth,
            "max_drawdown": drawdown,
            "paths": dict(paths),
            "strategic_grant_acceptance": True,
            "strategic_ownership_acceptance": True,
            "relative_generalization_non_regression": True,
            "report_13_runner_success": True,
        },
    )


def _failed_predecessor_is_valid(
    grant: Mapping[str, object], epoch: Mapping[str, object]
) -> bool:
    return not (
        grant["status"] not in {"EXPIRED", "CANCELLED"}
        or grant["filled_shares"] != 0
        or grant["expiry_reason"] not in _RECOVERABLE_REASONS
        or epoch["realized_status"] != "EXPIRED"
        or epoch["grant_id"] != grant["grant_id"]
        or epoch["owner_symbol"] != grant["candidate_symbol"]
        or epoch["first_fill_session"]
        or epoch["active_session"]
        or epoch["close_reason"] != grant["expiry_reason"]
    )


def _failed_successor_is_valid(
    first_grant: Mapping[str, object],
    first_epoch: Mapping[str, object],
    second_grant: Mapping[str, object],
    second_epoch: Mapping[str, object],
) -> bool:
    return not (
        second_grant["candidate_symbol"] == first_grant["candidate_symbol"]
        or second_grant["grant_id"] == first_grant["grant_id"]
        or second_grant["previous_grant_id"] != first_grant["grant_id"]
        or second_epoch["previous_epoch_id"] != first_epoch["epoch_id"]
        or second_epoch["grant_id"] != second_grant["grant_id"]
        or second_epoch["owner_symbol"] != second_grant["candidate_symbol"]
        or second_grant["authorization_id"] == first_grant["authorization_id"]
    )


def _failed_outlet_is_valid(
    second_grant: Mapping[str, object],
    second_epoch: Mapping[str, object],
    target: Mapping[str, object],
    order: Mapping[str, object],
    fill: Mapping[str, object],
) -> bool:
    identity = (
        second_grant["candidate_symbol"],
        second_grant["grant_id"],
        second_epoch["epoch_id"],
    )
    return not (
        (target["symbol"], target["grant_id"], target["epoch_id"]) != identity
        or (order["symbol"], order["grant_id"], order["epoch_id"]) != identity
        or (fill["symbol"], fill["grant_id"], fill["epoch_id"]) != identity
        or target["origin_subsystem"] != "STRATEGIC"
        or float(cast(float, target["weight"])) <= 0.0
        or order["origin_subsystem"] != "STRATEGIC"
        or order["side"] != "BUY"
        or float(cast(float, order["target_weight"])) <= 0.0
        or fill["origin_subsystem"] != "STRATEGIC"
        or fill["side"] != "BUY"
        or int(cast(int, fill["shares"])) <= 0
        or fill["order_id"] != order["order_id"]
        or not cast(str, order["submitted_date"]) < cast(str, fill["fill_date"])
        or second_epoch["first_fill_session"] != fill["fill_date"]
        or second_epoch["active_session"] != fill["fill_date"]
    )


def _failed_grant_component(
    raw: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> ComponentResult:
    first_grant = cast(Mapping[str, object], raw["first_grant"])
    first_epoch = cast(Mapping[str, object], raw["first_epoch"])
    second_grant = cast(Mapping[str, object], raw["second_grant"])
    second_epoch = cast(Mapping[str, object], raw["second_epoch"])
    target = cast(Mapping[str, object], raw["target"])
    order = cast(Mapping[str, object], raw["order"])
    fill = cast(Mapping[str, object], raw["fill"])
    failures: list[str] = []
    if not _failed_predecessor_is_valid(first_grant, first_epoch):
        failures.append("failed grant predecessor is not terminally unfilled")
    if not _failed_successor_is_valid(
        first_grant, first_epoch, second_grant, second_epoch
    ):
        failures.append("failed grant successor identity chain differs")
    if not _failed_outlet_is_valid(second_grant, second_epoch, target, order, fill):
        failures.append("failed grant successor Target/Order/Fill/epoch outlet differs")
    retry = healthy_retry_sessions(raw)
    if retry > contract.thresholds.maximum_failed_grant_retry_healthy_sessions:
        failures.append("failed grant retry exceeds 20 distinct healthy sessions")
    return _component(
        "failed_grant_recovery",
        failures,
        {
            "first_grant_id": first_grant["grant_id"],
            "second_grant_id": second_grant["grant_id"],
            "first_epoch_id": first_epoch["epoch_id"],
            "second_epoch_id": second_epoch["epoch_id"],
            "healthy_retry_sessions": retry,
            "state_transition_digest": canonical_json_sha256(
                _policy_thaw(raw["observations"])
            ),
        },
    )


def _cross_crowning_incomplete(
    epochs: Sequence[Mapping[str, object]], industries: Sequence[str]
) -> bool:
    return any(
        len(values) < 2
        for values in (
            {cast(str, item["owner_symbol"]) for item in epochs},
            set(industries),
            {cast(str, item["epoch_id"]) for item in epochs},
            {cast(str, item["grant_id"]) for item in epochs},
            {cast(str, item["fill_id"]) for item in epochs},
        )
    )


def _repeated_component(
    cells: Sequence[CellArtifact],
    historical: Mapping[str, object],
    cross: Mapping[str, object],
    contract: AbsoluteGeneralizationContract,
) -> ComponentResult:
    historical_epochs = cast(Sequence[Mapping[str, object]], historical["epochs"])
    cross_epochs = cast(Sequence[Mapping[str, object]], cross["epochs"])
    owners = [cast(str, item["owner_symbol"]) for item in historical_epochs]
    epochs = [cast(str, item["epoch_id"]) for item in historical_epochs]
    grants = [cast(str, item["grant_id"]) for item in historical_epochs]
    fills = [cast(str, item["fill_id"]) for item in historical_epochs]
    qualifications = [
        cast(str, item["qualification_signature"]) for item in historical_epochs
    ]
    cross_owners = [cast(str, item["owner_symbol"]) for item in cross_epochs]
    industries = crown_industries(cross)
    failures: list[str] = []
    minimum_epochs = contract.thresholds.minimum_repeated_crowning_actual_epochs
    minimum_owners = contract.thresholds.minimum_repeated_crowning_distinct_owners
    if len(epochs) < minimum_epochs or len(set(epochs)) < minimum_epochs:
        failures.append("historical crowning requires two Fill-gated epochs")
    if len(set(owners)) < minimum_owners:
        failures.append("historical crowning requires two distinct owners")
    if any(
        len(values) < 2 or len(set(values)) < 2
        for values in (grants, fills, qualifications)
    ):
        failures.append("historical crowning lacks independent grant/fill/qualification facts")
    source = cast(str, historical["source_cell_id"])
    if not any(cell.cell_id == source and cell.status == "COMPLETE" for cell in cells):
        failures.append("historical crowning source cell is not complete")
    if _cross_crowning_incomplete(cross_epochs, industries):
        failures.append("cross-industry production-semantic crowning is incomplete")
    return _component(
        "repeated_crowning",
        failures,
        {
            "historical_owner_count": len(set(owners)),
            "historical_epoch_count": len(set(epochs)),
            "historical_trace_sha256": canonical_json_sha256(
                _policy_thaw(historical_epochs)
            ),
            "cross_industry_owner_count": len(set(cross_owners)),
            "cross_industry_count": len(set(industries)),
            "cross_industry_trace_sha256": canonical_json_sha256(
                _policy_thaw(cross_epochs)
            ),
        },
    )


def _bounded_component(
    cells: Sequence[CellArtifact],
    repairs: Sequence[Mapping[str, object]],
    scc: Mapping[str, object],
    contract: AbsoluteGeneralizationContract,
) -> ComponentResult:
    expected = {
        (item.persisted_damage_level, item.target_budget_level): item.maximum_healthy_sessions
        for item in contract.thresholds.repair_bounds
    }
    observed: dict[tuple[int, int], Mapping[str, object]] = {}
    failures: list[str] = []
    for item in repairs:
        key = (
            int(cast(int, item["persisted_damage_level"])),
            int(cast(int, item["target_budget_level"])),
        )
        observed[key] = item
    if set(observed) != set(expected):
        failures.append("capital repair evidence does not cover all four literal bounds")
    for key, maximum in expected.items():
        repair = observed.get(key)
        if repair is None:
            continue
        actual = repair_healthy_sessions(repair)
        if actual > maximum:
            failures.append(f"repair {key[0]}->{key[1]}/{maximum} exceeded its literal bound")
    scc_projection = terminal_projection(scc)
    durations = list(scc_projection.durations)
    maximum_scc = max(durations, default=0)
    if maximum_scc > contract.thresholds.maximum_terminal_zero_strategic_target_scc_sessions:
        failures.append("terminal SCC exceeds 60 healthy sessions")
    complete = [cell.metrics for cell in cells if cell.metrics is not None]
    vacancy = [float(item.longest_healthy_zero_total_target_streak) for item in complete]
    if len(vacancy) != 34:
        failures.append("bounded cash vacancy requires 34 complete cells")
        p90 = 0.0
        worst = 0
    else:
        p90 = linear_quantile(vacancy, 0.90)
        worst = int(max(vacancy))
        if p90 > contract.thresholds.maximum_p90_healthy_zero_total_target_streak:
            failures.append("p90 healthy cash vacancy exceeds 60")
        if worst > contract.thresholds.maximum_worst_healthy_zero_total_target_streak:
            failures.append("worst healthy cash vacancy exceeds 120")
    return _component(
        "bounded_healthy_cash_vacancy",
        failures,
        {
            "repair_bounds": [
                {
                    "persisted_damage_level": key[0],
                    "target_budget_level": key[1],
                    "maximum_healthy_sessions": maximum,
                    "actual_healthy_sessions_to_ready": (
                        -1
                        if observed.get(key) is None
                        else repair_healthy_sessions(observed[key])
                    ),
                }
                for key, maximum in expected.items()
            ],
            "maximum_terminal_scc_duration": maximum_scc,
            "terminal_scc_violation_count": sum(
                value > contract.thresholds.maximum_terminal_zero_strategic_target_scc_sessions
                for value in durations
            ),
            "terminal_state_count": scc_projection.state_count,
            "terminal_edge_count": scc_projection.edge_count,
            "terminal_transition_sha256": scc_projection.transition_sha256,
            "p90_healthy_zero_total_target_streak": p90,
            "worst_healthy_zero_total_target_streak": worst,
        },
    )


def evaluate_literal_components(
    *,
    cells: Sequence[CellArtifact],
    champion: Mapping[str, object] | None,
    failed_grant: Mapping[str, object] | None,
    historical_crowning: Mapping[str, object] | None,
    terminal_scc: Mapping[str, object] | None,
    repair_bounds: Sequence[Mapping[str, object]],
    cross_industry_crowning: Mapping[str, object] | None,
    contract: AbsoluteGeneralizationContract,
) -> tuple[ComponentResult, ...]:
    """Return all seven components in the frozen contract order."""

    def missing(name: str) -> ComponentResult:
        return _component(name, (f"{name} evidence is unavailable",), {})
    results = {
        "champion_non_regression": _champion_component(champion, contract)
        if champion is not None
        else missing("champion_non_regression"),
        "absolute_strategic_robustness": evaluate_absolute_strategic_robustness(
            cells, contract
        ),
        "failed_grant_recovery": _failed_grant_component(failed_grant, contract)
        if failed_grant is not None
        else missing("failed_grant_recovery"),
        "witness_resilience": evaluate_witness_resilience(cells, contract),
        "repeated_crowning": _repeated_component(
            cells, historical_crowning, cross_industry_crowning, contract
        )
        if historical_crowning is not None and cross_industry_crowning is not None
        else missing("repeated_crowning"),
        "bounded_healthy_cash_vacancy": _bounded_component(
            cells, repair_bounds, terminal_scc, contract
        )
        if terminal_scc is not None
        else missing("bounded_healthy_cash_vacancy"),
        "complete_literal_metrics": evaluate_complete_literal_metrics(cells, contract),
    }
    return tuple(results[name] for name in contract.components)


__all__ = (
    "ComponentResult",
    "evaluate_absolute_strategic_robustness",
    "evaluate_complete_literal_metrics",
    "evaluate_literal_components",
    "evaluate_witness_resilience",
)
