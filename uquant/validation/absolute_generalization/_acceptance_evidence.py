"""Strict raw-evidence reconciliation for the final acceptance owners."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import cast

from uquant.contracts.strict_json import (
    canonical_json_bytes,
    canonical_json_sha256,
    strict_json_loads,
)
from uquant.contracts.universe import default_ai_universe
from uquant.validation.generalization_reference import (
    evaluate_generalization_policy_artifact,
    load_generalization_baseline,
    load_generalization_policy,
)

from .contract import AbsoluteGeneralizationContract

_ROOT = Path(__file__).resolve().parents[3]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENTITY_ID = re.compile(r"^(?:epoch|fill|grant|order|target|rearm)_[0-9a-f]{64}$")
_PHASES = frozenset({"POST_DECISION", "POST_OPEN"})
_REPAIR_STATES = frozenset({"ACCUMULATING", "READY"})
_CHAMPION_FIELDS = frozenset(
    {
        "metrics",
        "path_sha256",
        "duplicate_grant_count",
        "duplicate_order_count",
        "duplicate_epoch_count",
        "incumbent_epoch_count",
        "successor_capital_before_incumbent_exit_count",
        "report_13",
        "strategic_grant_acceptance",
        "strategic_ownership_acceptance",
        "relative_generalization",
        "evidence_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class TerminalProjection:
    """Graph facts recomputed from strict consecutive observed rows."""

    durations: tuple[int, ...]
    state_count: int
    edge_count: int
    transition_sha256: str


def _evidence_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return cast(Mapping[str, object], value)


def _evidence_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _evidence_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_evidence_json_value(item) for item in value]
    return value


def _evidence_sequence(value: object, *, label: str) -> Sequence[object]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return cast(Sequence[object], value)


def _evidence_fields(
    raw: Mapping[str, object], expected: Set[str], *, label: str
) -> None:
    if set(raw) != expected:
        raise ValueError(f"absolute generalization {label} evidence fields differ")


def _evidence_text(value: object, *, label: str, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return value


def _evidence_integer(value: object, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return value


def _evidence_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return number


def _evidence_date(value: object, *, label: str) -> str:
    text = _evidence_text(value, label=label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"absolute generalization {label} session is malformed") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"absolute generalization {label} session is malformed")
    return text


def _evidence_sha(value: object, *, label: str) -> str:
    text = _evidence_text(value, label=label)
    if not _SHA256.fullmatch(text):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return text


def _entity(value: object, *, label: str, empty: bool = False) -> str:
    text = _evidence_text(value, label=label, empty=empty)
    if text and not _ENTITY_ID.fullmatch(text):
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return text


def _predicate_rows(value: object, *, label: str) -> tuple[tuple[str, bool], ...]:
    rows = _evidence_sequence(value, label=label)
    if not rows:
        raise ValueError(f"absolute generalization {label} evidence is empty")
    result: list[tuple[str, bool]] = []
    for item in rows:
        row = _evidence_mapping(item, label=label)
        _evidence_fields(row, {"code", "satisfied"}, label=label)
        code = _evidence_text(row["code"], label=f"{label} predicate")
        satisfied = row["satisfied"]
        if type(satisfied) is not bool:
            raise ValueError(f"absolute generalization {label} predicate is malformed")
        result.append((code, satisfied))
    if len({code for code, _passed in result}) != len(result):
        raise ValueError(f"absolute generalization {label} predicate is duplicated")
    return tuple(result)


def _strict_sessions(values: Sequence[str], *, label: str) -> None:
    if not values or tuple(values) != tuple(sorted(set(values))):
        raise ValueError(f"absolute generalization {label} sessions are not observed order")


@lru_cache(maxsize=1)
def _grant_contract() -> Mapping[str, object]:
    raw = json.loads(
        (_ROOT / "benchmarks/strategic_grant_acceptance_contract.json").read_text(
            encoding="utf-8"
        )
    )
    return _evidence_mapping(raw, label="strategic grant contract")


@lru_cache(maxsize=1)
def _ownership_contract() -> Mapping[str, object]:
    raw = json.loads(
        (_ROOT / "benchmarks/strategic_ownership_acceptance_contract.json").read_text(
            encoding="utf-8"
        )
    )
    return _evidence_mapping(raw, label="strategic ownership contract")


def _validate_grant_acceptance(raw: object) -> None:
    evidence = _evidence_mapping(raw, label="strategic grant acceptance")
    _evidence_fields(
        evidence, {"baseline", "native_eligibility"}, label="strategic grant acceptance"
    )
    contract = _grant_contract()
    baseline_contract = _evidence_mapping(contract["baseline"], label="grant baseline")
    expected_baseline = {
        "first_positive_target_session": baseline_contract[
            "expected_first_positive_target_session"
        ],
        "metrics": baseline_contract["expected_metrics"],
        "sha256": baseline_contract["expected_sha256"],
    }
    if evidence["baseline"] != expected_baseline:
        raise ValueError("absolute generalization strategic grant baseline evidence differs")
    expected_native = {
        (str(item["owner"]), str(item["date"]))
        for item in cast(Sequence[Mapping[str, object]], contract["native_eligibility"])
    }
    observed: set[tuple[str, str]] = set()
    for item in _evidence_sequence(
        evidence["native_eligibility"], label="native eligibility"
    ):
        row = _evidence_mapping(item, label="native eligibility")
        _evidence_fields(
            row,
            {"owner", "date", "final_account_sha256", "trace_sha256"},
            label="native eligibility",
        )
        owner = _evidence_text(row["owner"], label="native eligibility owner")
        session = _evidence_date(row["date"], label="native eligibility")
        _evidence_sha(row["final_account_sha256"], label="native final account")
        _evidence_sha(row["trace_sha256"], label="native trace")
        observed.add((owner, session))
    if observed != expected_native or len(observed) != len(expected_native):
        raise ValueError("absolute generalization native eligibility evidence differs")


def _validate_ownership_acceptance(
    raw: object,
    contract: AbsoluteGeneralizationContract,
    champion: Mapping[str, object],
) -> None:
    evidence = _evidence_mapping(raw, label="strategic ownership acceptance")
    _evidence_fields(
        evidence,
        {"contract_sha256", "production_source_identity", "champion", "report_13"},
        label="strategic ownership acceptance",
    )
    ownership = _ownership_contract()
    if (
        evidence["contract_sha256"] != canonical_json_sha256(ownership)
        or evidence["contract_sha256"]
        != contract.frozen_baseline.strategic_ownership_contract_sha256
        or evidence["production_source_identity"]
        != contract.candidate.production_source_sha256
    ):
        raise ValueError("absolute generalization strategic ownership identity differs")
    champion_run = _evidence_mapping(evidence["champion"], label="ownership champion")
    _evidence_fields(
        champion_run,
        {
            "scenario_id",
            "owner_symbols",
            "grant_ids",
            "epoch_ids",
            "target_ids",
            "order_ids",
            "fill_ids",
            "trace_sha256",
        },
        label="ownership champion",
    )
    if champion_run["scenario_id"] != "champion-5":
        raise ValueError("absolute generalization ownership champion scenario differs")
    for name in (
        "owner_symbols",
        "grant_ids",
        "epoch_ids",
        "target_ids",
        "order_ids",
        "fill_ids",
    ):
        values = tuple(
            _evidence_text(item, label=f"ownership champion {name}")
            for item in _evidence_sequence(champion_run[name], label=name)
        )
        if not values or len(values) != len(set(values)):
            raise ValueError("absolute generalization ownership champion lifecycle differs")
    _evidence_sha(champion_run["trace_sha256"], label="ownership champion trace")
    report = _evidence_mapping(evidence["report_13"], label="ownership report-13")
    champion_metrics = _evidence_mapping(champion["metrics"], label="champion metrics")
    champion_report = _evidence_mapping(
        champion["report_13"], label="champion report-13"
    )
    _evidence_fields(
        report,
        {
            "scenario_id",
            "window_start",
            "window_end",
            "observed_sessions",
            "account_orders",
            "final_equity",
            "final_account_sha256",
            "trace_sha256",
        },
        label="ownership report-13",
    )
    if (
        report["scenario_id"] != "report-13"
        or _evidence_date(report["window_start"], label="report-13 start")
        != contract.window_start.isoformat()
        or _evidence_date(report["window_end"], label="report-13 end")
        != contract.window_end.isoformat()
        or _evidence_integer(
            report["observed_sessions"], label="report-13 sessions", minimum=1
        )
        < 1
        or _evidence_integer(report["account_orders"], label="report-13 orders")
        != champion_metrics["account_orders"]
        or _evidence_number(report["final_equity"], label="report-13 final equity")
        != champion_report["final_equity"]
    ):
        raise ValueError("absolute generalization report-13 completion evidence differs")
    _evidence_sha(report["final_account_sha256"], label="report-13 final account")
    _evidence_sha(report["trace_sha256"], label="report-13 trace")


@lru_cache(maxsize=2)
def _relative_report(payload: bytes) -> Mapping[str, object]:
    raw = strict_json_loads(payload)
    artifact = dict(_evidence_mapping(raw, label="relative generalization"))
    artifact["passed"] = False
    artifact["failures"] = []
    report = evaluate_generalization_policy_artifact(
        artifact,
        baseline=load_generalization_baseline(),
        policy=load_generalization_policy(),
    )
    return cast(Mapping[str, object], report)


def _validate_relative_generalization(raw: object) -> None:
    artifact = _evidence_mapping(raw, label="relative generalization")
    expected = {
        "schema_version",
        "gate",
        "provenance",
        "concentration_definition",
        "aggregates",
        "cells",
    }
    if artifact.get("schema_version") == 2:
        expected.add("attribution_definition")
    _evidence_fields(artifact, expected, label="relative generalization")
    report = _relative_report(canonical_json_bytes(artifact))
    if report["passed"] is not True or report["failures"] != []:
        raise ValueError("absolute generalization relative policy evidence failed")


def validate_champion_evidence(
    raw: object, contract: AbsoluteGeneralizationContract
) -> Mapping[str, object]:
    """Validate raw champion adjuncts through their existing public authorities."""

    champion = _evidence_mapping(raw, label="champion")
    _evidence_fields(champion, _CHAMPION_FIELDS, label="champion")
    _validate_grant_acceptance(champion["strategic_grant_acceptance"])
    _validate_ownership_acceptance(
        champion["strategic_ownership_acceptance"], contract, champion
    )
    _validate_relative_generalization(champion["relative_generalization"])
    evidence_sha256 = _evidence_sha(
        champion["evidence_sha256"], label="champion evidence"
    )
    if evidence_sha256 != canonical_json_sha256(
        {key: value for key, value in champion.items() if key != "evidence_sha256"}
    ):
        raise ValueError("absolute generalization champion evidence identity differs")
    return champion


def validate_failed_grant_evidence(raw: object) -> Mapping[str, object]:
    """Validate a literal successor chain and observed health-predicate rows."""

    evidence = _evidence_mapping(raw, label="failed-grant")
    expected = {
        "first_grant",
        "first_epoch",
        "second_grant",
        "second_epoch",
        "target",
        "order",
        "fill",
        "observations",
    }
    _evidence_fields(evidence, expected, label="failed-grant")
    observations = _evidence_sequence(evidence["observations"], label="failed-grant observations")
    sessions: list[str] = []
    for item in observations:
        row = _evidence_mapping(item, label="failed-grant observation")
        _evidence_fields(
            row,
            {"session", "phase", "edge_kind", "state_sha256", "predicate_results"},
            label="failed-grant observation",
        )
        session = _evidence_date(row["session"], label="failed-grant")
        sessions.append(session)
        if row["phase"] not in _PHASES or row["edge_kind"] != "OBSERVED":
            raise ValueError("absolute generalization failed-grant transition differs")
        predicates = _predicate_rows(
            row["predicate_results"], label="failed-grant health"
        )
        state_sha256 = _evidence_sha(
            row["state_sha256"], label="failed-grant state"
        )
        if state_sha256 != canonical_json_sha256(
            {
                "session": session,
                "phase": row["phase"],
                "predicate_results": [
                    {"code": code, "satisfied": satisfied}
                    for code, satisfied in predicates
                ],
            }
        ):
            raise ValueError("absolute generalization failed-grant state evidence differs")
    _strict_sessions(sessions, label="failed-grant")
    first_epoch = _evidence_mapping(evidence["first_epoch"], label="first epoch")
    fill = _evidence_mapping(evidence["fill"], label="successor fill")
    closed = _evidence_date(first_epoch["closed_session"], label="failed-grant close")
    filled = _evidence_date(fill["fill_date"], label="failed-grant fill")
    if not all(closed < session < filled for session in sessions):
        raise ValueError("absolute generalization failed-grant sessions are not causal")
    return evidence


def healthy_retry_sessions(raw: Mapping[str, object]) -> int:
    """Recompute the number of distinct all-predicate healthy sessions."""

    healthy = {
        cast(str, row["session"])
        for row in cast(Sequence[Mapping[str, object]], raw["observations"])
        if all(
            cast(bool, predicate["satisfied"])
            for predicate in cast(
                Sequence[Mapping[str, object]], row["predicate_results"]
            )
        )
    }
    return len(healthy)


def validate_crowning_evidence(
    raw: object,
    *,
    cross: bool,
    contract: AbsoluteGeneralizationContract,
) -> Mapping[str, object]:
    """Validate Fill-gated epoch rows and their predecessor identity chain."""

    evidence = _evidence_mapping(raw, label="crowning")
    expected = {"source_scenario_id", "epochs"} if cross else {"source_cell_id", "epochs"}
    _evidence_fields(evidence, expected, label="crowning")
    source_name = "source_scenario_id" if cross else "source_cell_id"
    source = _evidence_text(evidence[source_name], label="crowning source")
    if not cross and source not in {
        f"remove-{symbol}" for symbol in contract.canonical_universe
    }:
        raise ValueError("absolute generalization crowning source cell differs")
    epochs = _evidence_sequence(evidence["epochs"], label="crowning epochs")
    if len(epochs) < 2:
        raise ValueError("absolute generalization crowning evidence is incomplete")
    previous: Mapping[str, object] | None = None
    universe = default_ai_universe()
    for item in epochs:
        row = _evidence_mapping(item, label="crowning epoch")
        _evidence_fields(
            row,
            {
                "owner_symbol",
                "epoch_id",
                "grant_id",
                "previous_epoch_id",
                "previous_grant_id",
                "qualification_signature",
                "qualification_session",
                "grant_session",
                "fill_id",
                "order_id",
                "fill_session",
                "fill_shares",
                "exit_session",
            },
            label="crowning epoch",
        )
        owner = _evidence_text(row["owner_symbol"], label="crowning owner")
        if owner not in contract.canonical_universe:
            raise ValueError("absolute generalization crowning owner differs")
        for name in ("epoch_id", "grant_id", "fill_id", "order_id"):
            _entity(row[name], label=f"crowning {name}")
        previous_epoch = _entity(
            row["previous_epoch_id"], label="crowning previous epoch", empty=True
        )
        previous_grant = _entity(
            row["previous_grant_id"], label="crowning previous grant", empty=True
        )
        qualification = _evidence_date(
            row["qualification_session"], label="crowning qualification"
        )
        grant = _evidence_date(row["grant_session"], label="crowning grant")
        filled = _evidence_date(row["fill_session"], label="crowning fill")
        exited = _evidence_date(row["exit_session"], label="crowning exit")
        if (
            not qualification <= grant <= filled <= exited
            or _evidence_integer(row["fill_shares"], label="crowning fill", minimum=1)
            < 1
            or not _evidence_text(
                row["qualification_signature"], label="crowning qualification"
            )
        ):
            raise ValueError("absolute generalization crowning chronology differs")
        universe.industry_of(owner, grant)
        if previous is None:
            if previous_epoch or previous_grant:
                raise ValueError("absolute generalization first crowning predecessor differs")
        elif (
            previous_epoch != previous["epoch_id"]
            or previous_grant != previous["grant_id"]
            or cast(str, previous["exit_session"]) >= grant
        ):
            raise ValueError("absolute generalization crowning identity chain differs")
        previous = row
    return evidence


def crown_industries(raw: Mapping[str, object]) -> tuple[str, ...]:
    universe = default_ai_universe()
    return tuple(
        universe.industry_of(
            cast(str, row["owner_symbol"]), cast(str, row["grant_session"])
        )
        for row in cast(Sequence[Mapping[str, object]], raw["epochs"])
    )


def validate_repair_evidence(raw: object) -> tuple[Mapping[str, object], ...]:
    """Validate repair rows while leaving literal bounds to policy.py."""

    result: list[Mapping[str, object]] = []
    for item in _evidence_sequence(raw, label="repair"):
        row = _evidence_mapping(item, label="repair")
        _evidence_fields(
            row,
            {"persisted_damage_level", "target_budget_level", "observations"},
            label="repair",
        )
        _evidence_integer(row["persisted_damage_level"], label="repair damage", minimum=1)
        _evidence_integer(row["target_budget_level"], label="repair target")
        observations = _evidence_sequence(row["observations"], label="repair observations")
        if not observations:
            raise ValueError("absolute generalization repair observations are empty")
        sessions: list[str] = []
        states: list[str] = []
        for value in observations:
            observation = _evidence_mapping(value, label="repair observation")
            _evidence_fields(
                observation,
                {"session", "repair_status", "predicate_results", "state_sha256"},
                label="repair observation",
            )
            sessions.append(_evidence_date(observation["session"], label="repair"))
            status = _evidence_text(observation["repair_status"], label="repair status")
            if status not in _REPAIR_STATES:
                raise ValueError("absolute generalization repair status differs")
            states.append(status)
            state_sha256 = _evidence_sha(
                observation["state_sha256"], label="repair state"
            )
            _predicate_rows(observation["predicate_results"], label="repair health")
            if state_sha256 != canonical_json_sha256(
                {
                    "session": sessions[-1],
                    "repair_status": status,
                    "predicate_results": observation["predicate_results"],
                    "persisted_damage_level": row["persisted_damage_level"],
                    "target_budget_level": row["target_budget_level"],
                }
            ):
                raise ValueError("absolute generalization repair state evidence differs")
        _strict_sessions(sessions, label="repair")
        if states[-1] != "READY" or any(state == "READY" for state in states[:-1]):
            raise ValueError("absolute generalization repair READY transition differs")
        result.append(row)
    return tuple(result)


def repair_healthy_sessions(raw: Mapping[str, object]) -> int:
    return sum(
        all(
            cast(bool, predicate["satisfied"])
            for predicate in cast(
                Sequence[Mapping[str, object]], observation["predicate_results"]
            )
        )
        for observation in cast(
            Sequence[Mapping[str, object]], raw["observations"]
        )
    )


def validate_terminal_evidence(raw: object) -> Mapping[str, object]:
    """Validate a finite sequence of literal observed graph rows."""

    evidence = _evidence_mapping(raw, label="terminal SCC")
    _evidence_fields(evidence, {"transitions"}, label="terminal SCC")
    rows = _evidence_sequence(evidence["transitions"], label="terminal SCC transitions")
    if not rows:
        raise ValueError("absolute generalization terminal SCC evidence is empty")
    sessions: list[str] = []
    for item in rows:
        row = _evidence_mapping(item, label="terminal SCC transition")
        _evidence_fields(
            row,
            {
                "session",
                "phase",
                "edge_kind",
                "state_sha256",
                "predicate_results",
                "positive_strategic_target_weight",
            },
            label="terminal SCC transition",
        )
        sessions.append(_evidence_date(row["session"], label="terminal SCC"))
        if row["phase"] not in _PHASES or row["edge_kind"] != "OBSERVED":
            raise ValueError("absolute generalization terminal SCC transition differs")
        state_sha256 = _evidence_sha(
            row["state_sha256"], label="terminal SCC state"
        )
        _predicate_rows(row["predicate_results"], label="terminal SCC health")
        target = _evidence_number(
            row["positive_strategic_target_weight"], label="terminal SCC target"
        )
        if target < 0.0:
            raise ValueError("absolute generalization terminal SCC target differs")
        if state_sha256 != canonical_json_sha256(
            {
                "phase": row["phase"],
                "predicate_results": row["predicate_results"],
                "positive_strategic_target_weight": target,
            }
        ):
            raise ValueError("absolute generalization terminal SCC state evidence differs")
    _strict_sessions(sessions, label="terminal SCC")
    return evidence


def _evidence_components(
    nodes: set[str], edges: set[tuple[str, str]]
) -> tuple[frozenset[str], ...]:
    graph: dict[str, set[str]] = {node: set() for node in nodes}
    reverse: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        graph[source].add(target)
        reverse[target].add(source)
    visited: set[str] = set()
    order: list[str] = []
    for root in sorted(nodes):
        if root in visited:
            continue
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, closing = stack.pop()
            if closing:
                order.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in sorted(graph[node], reverse=True))
    assigned: set[str] = set()
    components: list[frozenset[str]] = []
    for root in reversed(order):
        if root in assigned:
            continue
        component: set[str] = set()
        reverse_stack = [root]
        assigned.add(root)
        while reverse_stack:
            node = reverse_stack.pop()
            component.add(node)
            for parent in reverse[node]:
                if parent not in assigned:
                    assigned.add(parent)
                    reverse_stack.append(parent)
        components.append(frozenset(component))
    return tuple(components)


def terminal_projection(raw: Mapping[str, object]) -> TerminalProjection:
    """Recompute terminal no-outlet runs and graph counts from observed rows."""

    rows = cast(Sequence[Mapping[str, object]], raw["transitions"])
    states = tuple(cast(str, row["state_sha256"]) for row in rows)
    edges = set(pairwise(states))
    components = _evidence_components(set(states), edges)
    terminal_states = set().union(
        *(
            component
            for component in components
            if not any(
                source in component and target not in component
                for source, target in edges
            )
        )
    )
    durations: list[int] = []
    current = 0
    for row in rows:
        healthy = all(
            cast(bool, predicate["satisfied"])
            for predicate in cast(
                Sequence[Mapping[str, object]], row["predicate_results"]
            )
        )
        terminal = cast(str, row["state_sha256"]) in terminal_states
        no_target = float(cast(float, row["positive_strategic_target_weight"])) <= 0.0
        if healthy and terminal and no_target:
            current += 1
        elif current:
            durations.append(current)
            current = 0
    if current:
        durations.append(current)
    return TerminalProjection(
        durations=tuple(durations),
        state_count=len(set(states)),
        edge_count=len(edges),
        transition_sha256=canonical_json_sha256(_evidence_json_value(rows)),
    )


__all__ = (
    "TerminalProjection",
    "crown_industries",
    "healthy_retry_sessions",
    "repair_healthy_sessions",
    "terminal_projection",
    "validate_champion_evidence",
    "validate_crowning_evidence",
    "validate_failed_grant_evidence",
    "validate_repair_evidence",
    "validate_terminal_evidence",
)
