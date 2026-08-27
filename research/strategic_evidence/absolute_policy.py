"""Literal, fail-closed evaluation of the preregistered absolute policy."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

from .contract import StrategicEvidenceContract

_TERMINAL_STATUSES = {
    "SUCCESS",
    "REPLAY_ERROR",
    "INSUFFICIENT_SAMPLE",
    "NO_NATIVE_ELIGIBILITY",
}


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    """One auditable literal comparison and its machine-readable failure reasons."""

    check_id: str
    passed: bool
    actual: Any
    threshold: Any
    reason_codes: tuple[str, ...] = ()

    def compact(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "actual": self.actual,
            "threshold": self.threshold,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class AbsolutePolicyResult:
    """Runner/evidence completion is deliberately independent from capability."""

    runner_success: bool
    capability_pass: bool
    checks: tuple[PolicyCheck, ...]

    @property
    def failed_check_ids(self) -> tuple[str, ...]:
        return tuple(check.check_id for check in self.checks if not check.passed)

    def check(self, check_id: str) -> PolicyCheck:
        for check in self.checks:
            if check.check_id == check_id:
                return check
        raise KeyError(check_id)

    def compact(self) -> dict[str, Any]:
        return {
            "schema_version": "uquant.strategic-evidence-absolute-policy.v1",
            "runner_success": self.runner_success,
            "capability_pass": self.capability_pass,
            "failed_check_ids": list(self.failed_check_ids),
            "checks": [check.compact() for check in self.checks],
        }


def _mapping_list(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _status_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("status", "MISSING")) for row in rows).items()))


def _reason(passed: bool, code: str) -> tuple[str, ...]:
    return () if passed else (code,)


def _forced_owner_checks(
    contract: StrategicEvidenceContract,
    payload: Mapping[str, Any],
) -> tuple[PolicyCheck, ...]:
    cells = _mapping_list(payload.get("cells"))
    cell_ids = tuple(str(cell.get("cell_id", "")) for cell in cells)
    declared = payload.get("required_cell_ids")
    declared_ids = tuple(str(item) for item in declared) if isinstance(declared, list) else ()
    controls = _mapping_list(payload.get("controls"))
    expected_positive = {
        (f"POSITIVE_CONTROL:{owner}", owner, "POSITIVE_CONTROL") for owner in contract.positive_controls
    }
    expected_negative_roles = {
        "LOWEST_LIQUID_LEADER_SCORE",
        "NEGATIVE_RET120_AND_WEAK_TREND",
        "LOWEST_SECULAR_CONFIDENCE_FAILING_ABSOLUTE",
    }
    observed_controls = {
        (
            str(control.get("control_id", "")),
            str(control.get("owner", "")),
            str(control.get("owner_role", "")),
        )
        for control in controls
    }
    positive_controls = {control for control in observed_controls if control[2] == "POSITIVE_CONTROL"}
    negative_controls = {control for control in observed_controls if control[2] in expected_negative_roles}
    controls_valid = (
        len(controls) == 8
        and len(observed_controls) == 8
        and positive_controls == expected_positive
        and {control[0] for control in negative_controls} == expected_negative_roles
        and {control[2] for control in negative_controls} == expected_negative_roles
        and all(control[1] for control in negative_controls)
    )
    expected_ids = tuple(
        f"{control_id}:{owner}:{mode}"
        for control_id, owner, _ in (
            (
                str(control.get("control_id", "")),
                str(control.get("owner", "")),
                str(control.get("owner_role", "")),
            )
            for control in controls
        )
        for mode in ("COMMON_ACTIVATION_DATE", "NATIVE_ELIGIBILITY_DATE")
    )
    exact = (
        controls_valid
        and len(cells) == 16
        and len(cell_ids) == len(set(cell_ids))
        and len(expected_ids) == 16
        and declared_ids == expected_ids
        and set(cell_ids) == set(expected_ids)
    )
    counts = _status_counts(cells)
    terminal = all(str(cell.get("status")) in _TERMINAL_STATUSES for cell in cells)
    all_success = exact and all(cell.get("status") == "SUCCESS" for cell in cells)
    return (
        PolicyCheck(
            "forced_owner.exact_coverage",
            exact,
            {"cell_count": len(cells), "declared_count": len(declared_ids)},
            {"required_cell_count": 16},
            _reason(exact, "MISSING_OR_DUPLICATE_REQUIRED_CELL"),
        ),
        PolicyCheck(
            "forced_owner.terminal_outcomes_preserved",
            terminal,
            counts,
            {"allowed": sorted(_TERMINAL_STATUSES)},
            _reason(terminal, "MALFORMED_REQUIRED_CELL_STATUS"),
        ),
        PolicyCheck(
            "forced_owner.required_cells_success",
            all_success,
            {"status_counts": counts},
            {"SUCCESS": 16},
            _reason(all_success, "REQUIRED_CELL_TERMINAL_FAILURE"),
        ),
    )


def _canonical_cells(
    contract: StrategicEvidenceContract,
    payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    rows = _mapping_list(payload.get("initial_cells"))
    expected = {f"CANONICAL_LEAVE_ONE_OUT:{symbol}:FULL_REMOVAL" for symbol in contract.canonical_universe}
    return tuple(row for row in rows if str(row.get("cell_id", "")) in expected)


def _metric_completeness(
    cells: Sequence[Mapping[str, Any]],
    names: Sequence[str],
) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for cell in cells:
        cell_id = str(cell.get("cell_id", ""))
        metrics = cell.get("metrics")
        if not isinstance(metrics, Mapping):
            missing.extend(f"{cell_id}:{name}" for name in names)
            continue
        for name in names:
            value = metrics.get(name)
            if not _is_finite_number(value):
                missing.append(f"{cell_id}:{name}")
    return not missing, sorted(missing)


def _witness_checks(
    contract: StrategicEvidenceContract,
    payload: Mapping[str, Any],
) -> tuple[PolicyCheck, ...]:
    cells = _canonical_cells(contract, payload)
    expected_ids = {
        f"CANONICAL_LEAVE_ONE_OUT:{symbol}:FULL_REMOVAL" for symbol in contract.canonical_universe
    }
    ids = [str(cell.get("cell_id", "")) for cell in cells]
    exact = len(ids) == len(expected_ids) and set(ids) == expected_ids
    statuses = _status_counts(cells)
    terminal = exact and all(str(cell.get("status")) in _TERMINAL_STATUSES for cell in cells)
    success = exact and all(cell.get("status") == "SUCCESS" for cell in cells)
    metric_names = (
        "final_wealth",
        "max_drawdown",
        "longest_healthy_zero_target_streak",
        "positive_target_sessions",
    )
    metrics_complete, missing = _metric_completeness(cells, metric_names)
    critical_symbols = tuple(str(item) for item in contract.raw.get("matrix", {}).get("critical_symbols", []))
    critical = tuple(
        cell for cell in cells if str(cell.get("spec", {}).get("subject", "")) in critical_symbols
    )
    critical_complete, critical_missing = _metric_completeness(critical, metric_names)
    canonical_thresholds = contract.absolute_thresholds["canonical_leave_one_out"]
    wealth_raw = [
        cell.get("metrics", {}).get("final_wealth") if isinstance(cell.get("metrics"), Mapping) else None
        for cell in cells
    ]
    wealth_values = [float(value) for value in wealth_raw if _is_finite_number(value)]
    wealth_complete = exact and len(wealth_values) == len(wealth_raw)
    positive_fraction = (
        sum(float(value) > 1.0 for value in wealth_values) / len(wealth_values)
        if wealth_complete and wealth_values
        else None
    )
    positive_fraction_pass = (
        positive_fraction is not None
        and positive_fraction >= canonical_thresholds["positive_return_fraction"]
    )
    streak_raw = [
        cell.get("metrics", {}).get("longest_healthy_zero_target_streak")
        if isinstance(cell.get("metrics"), Mapping)
        else None
        for cell in cells
    ]
    streak_values = [float(value) for value in streak_raw if _is_finite_number(value)]
    streak_complete = exact and len(streak_values) == len(streak_raw)
    worst_streak = max(streak_values) if streak_complete and streak_values else None
    worst_streak_pass = (
        worst_streak is not None
        and worst_streak <= canonical_thresholds["worst_longest_healthy_zero_target_streak"]
    )
    critical_thresholds = contract.absolute_thresholds["critical_removal"]
    threshold_rules = {
        "final_wealth": ("min", critical_thresholds["min_final_wealth"]),
        "max_drawdown": ("max", critical_thresholds["max_drawdown"]),
        "longest_healthy_zero_target_streak": (
            "max",
            critical_thresholds["longest_healthy_zero_target_streak"],
        ),
        "positive_target_sessions": ("min", critical_thresholds["min_positive_targets"]),
    }
    critical_failures: list[dict[str, Any]] = []
    for cell in critical:
        symbol = str(cell.get("spec", {}).get("subject", ""))
        metrics = cell.get("metrics")
        for metric, (direction, threshold) in threshold_rules.items():
            value = metrics.get(metric) if isinstance(metrics, Mapping) else None
            passed = _is_finite_number(value) and (
                float(value) >= threshold if direction == "min" else float(value) <= threshold
            )
            if not passed:
                critical_failures.append(
                    {
                        "symbol": symbol,
                        "metric": metric,
                        "actual": value,
                        "threshold": threshold,
                    }
                )
    critical_thresholds_pass = len(critical) == len(critical_symbols) and not critical_failures
    return (
        PolicyCheck(
            "canonical.exact_coverage",
            exact,
            {"cell_count": len(ids)},
            {"required_cell_count": len(expected_ids)},
            _reason(exact, "MISSING_OR_DUPLICATE_REQUIRED_CELL"),
        ),
        PolicyCheck(
            "canonical.terminal_outcomes_preserved",
            terminal,
            statuses,
            {"allowed": sorted(_TERMINAL_STATUSES)},
            _reason(terminal, "MALFORMED_REQUIRED_CELL_STATUS"),
        ),
        PolicyCheck(
            "canonical.required_cells_success",
            success,
            {"status_counts": statuses},
            {"SUCCESS": len(expected_ids)},
            _reason(success, "REQUIRED_CELL_TERMINAL_FAILURE"),
        ),
        PolicyCheck(
            "canonical.literal_metrics_complete",
            metrics_complete,
            {"missing_or_null": missing},
            {"required_metrics": list(metric_names)},
            _reason(metrics_complete, "MISSING_OR_NULL_LITERAL_METRIC"),
        ),
        PolicyCheck(
            "critical_removal.literal_metrics_complete",
            critical_complete and len(critical) == len(critical_symbols),
            {
                "cell_count": len(critical),
                "missing_or_null": critical_missing,
            },
            {
                "required_symbols": list(critical_symbols),
                "required_metrics": list(metric_names),
            },
            _reason(
                critical_complete and len(critical) == len(critical_symbols),
                "MISSING_OR_NULL_LITERAL_METRIC",
            ),
        ),
        PolicyCheck(
            "canonical.positive_return_fraction",
            positive_fraction_pass,
            {
                "fraction": positive_fraction,
                "complete_cell_count": len(wealth_values) if wealth_complete else 0,
            },
            {"minimum_fraction": canonical_thresholds["positive_return_fraction"]},
            _reason(positive_fraction_pass, "LITERAL_THRESHOLD_FAILED_OR_NULL"),
        ),
        PolicyCheck(
            "canonical.worst_healthy_zero_target_streak",
            worst_streak_pass,
            {"worst_streak": worst_streak},
            {"maximum_streak": canonical_thresholds["worst_longest_healthy_zero_target_streak"]},
            _reason(worst_streak_pass, "LITERAL_THRESHOLD_FAILED_OR_NULL"),
        ),
        PolicyCheck(
            "critical_removal.literal_thresholds",
            critical_thresholds_pass,
            {"failures": critical_failures},
            {
                "min_final_wealth": critical_thresholds["min_final_wealth"],
                "max_drawdown": critical_thresholds["max_drawdown"],
                "max_healthy_zero_target_streak": critical_thresholds["longest_healthy_zero_target_streak"],
                "min_positive_targets": critical_thresholds["min_positive_targets"],
            },
            _reason(critical_thresholds_pass, "LITERAL_THRESHOLD_FAILED_OR_NULL"),
        ),
        PolicyCheck(
            "canonical.percentile_method_preregistered",
            False,
            {"method": None},
            {
                "p10_final_wealth": "method required",
                "p90_max_drawdown": "method required",
                "p90_longest_healthy_zero_target_streak": "method required",
            },
            ("UNFROZEN_PERCENTILE_METHOD",),
        ),
    )


def _finding_observed(row: Mapping[str, Any], observation_id: str) -> bool:
    analysis = row.get("analysis")
    if not isinstance(analysis, Mapping):
        return False
    findings = _mapping_list(analysis.get("findings"))
    return any(
        finding.get("observation_id") == observation_id and finding.get("observed") is True
        for finding in findings
    )


def _reach_metrics(row: Mapping[str, Any]) -> Mapping[str, Any]:
    analysis = row.get("analysis")
    if not isinstance(analysis, Mapping):
        return {}
    metrics = analysis.get("metrics")
    return metrics if isinstance(metrics, Mapping) else {}


def _reachability_checks(
    contract: StrategicEvidenceContract,
    rows_value: Sequence[Mapping[str, Any]],
) -> tuple[PolicyCheck, ...]:
    rows = tuple(rows_value)
    expected = {
        (state_id, path_id) for state_id in contract.initial_state_ids for path_id in contract.path_ids
    }
    identities = [(str(row.get("state_id", "")), str(row.get("path_id", ""))) for row in rows]
    exact = len(identities) == len(expected) and set(identities) == expected
    statuses = _status_counts(rows)
    terminal = exact and all(str(row.get("status")) in _TERMINAL_STATUSES for row in rows)
    success = exact and all(row.get("status") == "SUCCESS" for row in rows)
    diagnostic = exact and all(row.get("evidence_class") == "DIAGNOSTIC_ONLY" for row in rows)

    thresholds = contract.absolute_thresholds["reachability"]
    budget_rules = {
        "S06": ("1_to_0", thresholds["capital_budget_0_healthy_sessions"]),
        "S07": ("2_to_1", thresholds["capital_budget_1_healthy_sessions"]),
        "S08": ("3_to_2", thresholds["capital_budget_2_3_healthy_sessions"]),
        "S09": ("4_to_3", thresholds["capital_budget_2_3_healthy_sessions"]),
    }
    budget_failures: list[dict[str, Any]] = []
    for row in rows:
        rule = budget_rules.get(str(row.get("state_id", "")))
        if rule is None:
            continue
        transition, limit = rule
        budget = _reach_metrics(row).get("budget_repair_healthy_sessions")
        value = budget.get(transition) if isinstance(budget, Mapping) else None
        if not _is_finite_number(value) or value > limit:
            budget_failures.append(
                {
                    "state_id": row.get("state_id"),
                    "path_id": row.get("path_id"),
                    "transition": transition,
                    "actual": value,
                    "limit": limit,
                }
            )

    retry_limit = thresholds["failed_grant_retry_healthy_sessions"]
    retry_failures = [
        {"state_id": row.get("state_id"), "path_id": row.get("path_id"), "actual": value}
        for row in rows
        if (
            not _is_finite_number(value := _reach_metrics(row).get("failed_grant_retry_healthy_sessions"))
            or value > retry_limit
        )
    ]
    terminal_limit = thresholds["terminal_scc_healthy_zero_target_limit"]
    terminal_failures = [
        {"state_id": row.get("state_id"), "path_id": row.get("path_id"), "actual": value}
        for row in rows
        if (
            not _is_finite_number(
                value := _reach_metrics(row).get("terminal_scc_healthy_zero_target_duration")
            )
            or value > terminal_limit
        )
    ]
    witness_min = thresholds["witness_missing_recovery_fraction"]
    witness_failures = [
        {"state_id": row.get("state_id"), "path_id": row.get("path_id"), "actual": value}
        for row in rows
        if (
            not _is_finite_number(value := _reach_metrics(row).get("witness_missing_recovery_fraction"))
            or value < witness_min
        )
    ]
    r7_observed = [
        f"{row.get('state_id')}/{row.get('path_id')}" for row in rows if _finding_observed(row, "R7")
    ]
    crowning_min = contract.absolute_thresholds["repeated_crowning"]
    crowning_failures: list[dict[str, Any]] = []
    for row in rows:
        analysis = row.get("analysis")
        repeated = analysis.get("repeated_crowning") if isinstance(analysis, Mapping) else None
        owners = repeated.get("distinct_owners") if isinstance(repeated, Mapping) else None
        epochs = repeated.get("strategic_epochs") if isinstance(repeated, Mapping) else None
        if (
            not isinstance(owners, list)
            or not isinstance(epochs, list)
            or len(set(str(value) for value in owners)) < crowning_min["minimum_distinct_owners"]
            or len(set(str(value) for value in epochs)) < crowning_min["minimum_strategic_epochs"]
        ):
            crowning_failures.append(
                {
                    "state_id": row.get("state_id"),
                    "path_id": row.get("path_id"),
                    "distinct_owner_count": len(owners) if isinstance(owners, list) else None,
                    "strategic_epoch_count": len(epochs) if isinstance(epochs, list) else None,
                }
            )

    return (
        PolicyCheck(
            "reachability.execution_coverage",
            exact and terminal,
            {"cell_count": len(rows), "status_counts": statuses},
            {"cell_count": len(expected), "terminal_statuses": sorted(_TERMINAL_STATUSES)},
            _reason(exact and terminal, "MISSING_OR_MALFORMED_REACHABILITY_CELL"),
        ),
        PolicyCheck(
            "reachability.required_cells_success",
            success,
            {"status_counts": statuses},
            {"SUCCESS": len(expected)},
            _reason(success, "REQUIRED_CELL_TERMINAL_FAILURE"),
        ),
        PolicyCheck(
            "reachability.synthetic_classification",
            diagnostic,
            {"all_diagnostic_only": diagnostic},
            {"evidence_class": "DIAGNOSTIC_ONLY"},
            _reason(diagnostic, "SYNTHETIC_EVIDENCE_CLASS_MISMATCH"),
        ),
        PolicyCheck(
            "reachability.capital_budget_repair",
            exact and not budget_failures,
            {"failures": budget_failures},
            budget_rules,
            _reason(exact and not budget_failures, "LITERAL_REPAIR_LATENCY_FAILED_OR_NULL"),
        ),
        PolicyCheck(
            "reachability.failed_grant_retry",
            exact and not retry_failures,
            {"failures": retry_failures},
            {"max_healthy_sessions": retry_limit},
            _reason(exact and not retry_failures, "LITERAL_RETRY_LATENCY_FAILED_OR_NULL"),
        ),
        PolicyCheck(
            "reachability.terminal_scc_healthy_zero_target",
            exact and not terminal_failures,
            {"failures": terminal_failures},
            {"max_healthy_sessions": terminal_limit},
            _reason(exact and not terminal_failures, "TERMINAL_SCC_LIMIT_FAILED_OR_NULL"),
        ),
        PolicyCheck(
            "reachability.witness_missing_recovery_fraction",
            exact and not witness_failures,
            {"failures": witness_failures},
            {"minimum_fraction": witness_min},
            _reason(exact and not witness_failures, "WITNESS_MISSING_RECOVERY_FAILED_OR_NULL"),
        ),
        PolicyCheck(
            "reachability.R7_coverage",
            bool(r7_observed),
            {"observed_cells": r7_observed, "observed_count": len(r7_observed)},
            {"minimum_observed_cells": 1},
            _reason(bool(r7_observed), "REACH_OBSERVATION_UNCOVERED"),
        ),
        PolicyCheck(
            "reachability.repeated_crowning_all_cells",
            exact and not crowning_failures,
            {"failures": crowning_failures, "passing_count": len(rows) - len(crowning_failures)},
            {
                "minimum_distinct_owners": crowning_min["minimum_distinct_owners"],
                "minimum_strategic_epochs": crowning_min["minimum_strategic_epochs"],
                "scope": "all required cells",
            },
            _reason(exact and not crowning_failures, "REPEATED_CROWNING_NOT_UNIVERSAL"),
        ),
    )


def evaluate_absolute_policy(
    contract: StrategicEvidenceContract,
    *,
    forced_owner: Mapping[str, Any],
    witness: Mapping[str, Any],
    reachability_rows: Sequence[Mapping[str, Any]],
) -> AbsolutePolicyResult:
    """Evaluate exactly what v1 froze; missing semantics or evidence fail closed."""

    checks = (
        *_forced_owner_checks(contract, forced_owner),
        *_witness_checks(contract, witness),
        *_reachability_checks(contract, reachability_rows),
    )
    runner_check_ids = {
        "forced_owner.exact_coverage",
        "forced_owner.terminal_outcomes_preserved",
        "canonical.exact_coverage",
        "canonical.terminal_outcomes_preserved",
        "reachability.execution_coverage",
    }
    runner_success = all(check.passed for check in checks if check.check_id in runner_check_ids)
    capability_pass = runner_success and all(check.passed for check in checks)
    return AbsolutePolicyResult(
        runner_success=runner_success,
        capability_pass=capability_pass,
        checks=checks,
    )


__all__ = (
    "AbsolutePolicyResult",
    "PolicyCheck",
    "evaluate_absolute_policy",
)
