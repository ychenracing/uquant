"""Strict raw-evidence reconciliation for the final acceptance owners."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence, Set
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from uquant.account import account_from_dict
from uquant.config import SystemConfig
from uquant.contracts.strict_json import canonical_json_sha256, strict_json_loads
from uquant.contracts.universe import default_ai_universe
from uquant.models.decision import RiskAssessment, Target
from uquant.models.strategic_epoch import StrategicEpoch
from uquant.models.strategic_grant import StrategicGrantIntent
from uquant.models.strategic_universe import StrategicUniverseRoles
from uquant.models.trading import AccountOrder, Fill
from uquant.validation.generalization_reference import (
    load_generalization_baseline,
    load_generalization_policy,
)

from ._champion_runtime_reconciliation import (
    decode_champion_account,
    derive_champion_runtime_claims,
    derive_report_runtime_claims,
)
from ._physical_identity import physical_fill_identity_sha256
from ._reachability_codec import reachability_state_from_raw
from .contract import AbsoluteGeneralizationContract
from .reachability import (
    analyze_failed_grant_recovery,
    analyze_terminal_scc,
    is_positive_strategic_outlet,
    normalize_epoch_only_cohort_attribution,
    project_flat_book_repair_health,
)
from .replay import AbsoluteGeneralizationReplayPayload

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
        "relative_policy_reference",
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
    if is_dataclass(value) and not isinstance(value, type):
        return _evidence_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _evidence_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_evidence_json_value(item) for item in value]
    return value


def _evidence_sequence(value: object, *, label: str) -> Sequence[object]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"absolute generalization {label} evidence is malformed")
    return cast(Sequence[object], value)


def _evidence_fields(raw: Mapping[str, object], expected: Set[str], *, label: str) -> None:
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
        (_ROOT / "benchmarks/strategic_grant_acceptance_contract.json").read_text(encoding="utf-8")
    )
    return _evidence_mapping(raw, label="strategic grant contract")


@lru_cache(maxsize=1)
def _ownership_contract() -> Mapping[str, object]:
    raw = json.loads(
        (_ROOT / "benchmarks/strategic_ownership_acceptance_contract.json").read_text(encoding="utf-8")
    )
    return _evidence_mapping(raw, label="strategic ownership contract")


def _validate_grant_acceptance(raw: object) -> None:
    evidence = _evidence_mapping(raw, label="strategic grant acceptance")
    _evidence_fields(evidence, {"baseline"}, label="strategic grant acceptance")
    contract = _grant_contract()
    baseline_contract = _evidence_mapping(contract["baseline"], label="grant baseline")
    expected_baseline = {
        "first_positive_target_session": baseline_contract["expected_first_positive_target_session"],
        "metrics": baseline_contract["expected_metrics"],
        "sha256": baseline_contract["expected_sha256"],
    }
    if evidence["baseline"] != expected_baseline:
        raise ValueError("absolute generalization strategic grant baseline evidence differs")


def _validate_ownership_identity(
    evidence: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> None:
    ownership = _ownership_contract()
    if (
        evidence["contract_sha256"] != canonical_json_sha256(ownership)
        or evidence["contract_sha256"] != contract.frozen_baseline.strategic_ownership_contract_sha256
        or evidence["production_source_identity"] != contract.candidate.production_source_sha256
    ):
        raise ValueError("absolute generalization strategic ownership identity differs")


def _ownership_champion_run(raw: object) -> Mapping[str, object]:
    champion_run = _evidence_mapping(raw, label="ownership champion")
    _evidence_fields(
        champion_run,
        {
            "scenario_id",
            "owner_symbols",
            "grant_ids",
            "epoch_ids",
            "target_event_ids",
            "order_ids",
            "fill_identity_sha256s",
            "final_account",
            "decision_trace",
            "order_ledger",
            "equity_curve",
            "daily_replay_evidence",
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
        "target_event_ids",
        "order_ids",
        "fill_identity_sha256s",
    ):
        values = tuple(
            _evidence_text(item, label=f"ownership champion {name}")
            for item in _evidence_sequence(champion_run[name], label=name)
        )
        if not values or len(values) != len(set(values)):
            raise ValueError("absolute generalization ownership champion lifecycle differs")
    _evidence_sha(champion_run["trace_sha256"], label="ownership champion trace")
    return champion_run


def _validate_champion_runtime(champion: Mapping[str, object]) -> None:
    ownership = _evidence_mapping(
        champion["strategic_ownership_acceptance"], label="strategic ownership acceptance"
    )
    run = _evidence_mapping(ownership["champion"], label="ownership champion")
    grant = _evidence_mapping(champion["strategic_grant_acceptance"], label="strategic grant acceptance")
    grant_contract = _grant_contract()
    expected = derive_champion_runtime_claims(
        run,
        frozenset(
            str(item)
            for item in _evidence_sequence(
                grant_contract["ignored_non_economic_fields"],
                label="grant ignored fields",
            )
        ),
    )
    if (
        any(champion[name] != value for name, value in expected.items())
        or champion["path_sha256"] != _evidence_mapping(grant["baseline"], label="grant baseline")["sha256"]
    ):
        raise ValueError("absolute generalization champion runtime evidence differs")


def _ownership_expected_lifecycle(
    champion_run: Mapping[str, object],
) -> dict[str, list[str]]:
    account_raw = _evidence_mapping(champion_run["final_account"], label="ownership champion account")
    account = account_from_dict(decode_champion_account(account_raw), require_hashes=False)
    trace = tuple(
        _evidence_mapping(item, label="ownership champion decision")
        for item in _evidence_sequence(champion_run["decision_trace"], label="ownership champion trace")
    )
    if canonical_json_sha256(list(trace)) != champion_run["trace_sha256"]:
        raise ValueError("absolute generalization ownership champion trace differs")
    target_event_ids = sorted(
        {
            _evidence_text(target["event_id"], label="ownership target event")
            for row in trace
            for raw_target in _evidence_sequence(row.get("targets"), label="ownership targets")
            for target in (_evidence_mapping(raw_target, label="ownership target"),)
            if target.get("origin_subsystem") == "STRATEGIC"
            and _evidence_number(target.get("weight"), label="ownership target weight") > 0.0
        }
    )
    return {
        "owner_symbols": sorted(
            {epoch.owner_symbol for epoch in account.strategic_epochs if epoch.first_fill_session}
        ),
        "grant_ids": sorted(
            {epoch.grant_id for epoch in account.strategic_epochs if epoch.first_fill_session}
        ),
        "epoch_ids": sorted(
            {epoch.epoch_id for epoch in account.strategic_epochs if epoch.first_fill_session}
        ),
        "target_event_ids": target_event_ids,
        "order_ids": sorted({order.order_id for order in account.order_ledger}),
        "fill_identity_sha256s": sorted({physical_fill_identity_sha256(fill) for fill in account.fills}),
    }


def _validate_ownership_lifecycle(champion_run: Mapping[str, object]) -> None:
    for name, expected_values in _ownership_expected_lifecycle(champion_run).items():
        if list(_evidence_sequence(champion_run[name], label=name)) != expected_values:
            raise ValueError("absolute generalization ownership champion lifecycle differs")


def _validate_ownership_report(
    raw: object,
    contract: AbsoluteGeneralizationContract,
    champion: Mapping[str, object],
) -> None:
    report = _evidence_mapping(raw, label="ownership report-13")
    champion_report = _evidence_mapping(champion["report_13"], label="champion report-13")
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
            "final_account",
            "decision_trace",
            "order_ledger",
            "equity_curve",
            "daily_replay_evidence",
        },
        label="ownership report-13",
    )
    ownership = _ownership_contract()
    report_symbols = tuple(
        _evidence_text(item, label="report universe symbol")
        for item in _evidence_sequence(
            ownership["report_universe_13"], label="report universe"
        )
    )
    expected_report, expected_completion = derive_report_runtime_claims(
        report, report_symbols
    )
    if (
        report["scenario_id"] != "report-13"
        or _evidence_date(report["window_start"], label="report-13 start")
        != contract.window_start.isoformat()
        or _evidence_date(report["window_end"], label="report-13 end") != contract.window_end.isoformat()
        or dict(champion_report) != expected_report
        or any(report[name] != value for name, value in expected_completion.items())
    ):
        raise ValueError("absolute generalization report-13 runtime evidence differs")


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
    _validate_ownership_identity(evidence, contract)
    champion_run = _ownership_champion_run(evidence["champion"])
    _validate_ownership_lifecycle(champion_run)
    _validate_champion_runtime(champion)
    _validate_ownership_report(evidence["report_13"], contract, champion)


@lru_cache(maxsize=1)
def _compile_anchored_relative_policy_reference() -> Mapping[str, object]:
    baseline = load_generalization_baseline()
    policy = load_generalization_policy()
    if policy.baseline_sha256 != baseline.sha256:
        raise ValueError("absolute generalization relative policy reference differs")
    return {
        "baseline_canonical_sha256": baseline.sha256,
        "policy_canonical_sha256": policy.sha256,
        "frozen_artifact_sha256": baseline.artifact_sha256,
        "frozen_artifact_size_bytes": baseline.artifact_size_bytes,
    }


def _validate_relative_policy_reference(raw: object) -> None:
    reference = _evidence_mapping(raw, label="relative policy reference")
    _evidence_fields(
        reference,
        {
            "baseline_canonical_sha256",
            "policy_canonical_sha256",
            "frozen_artifact_sha256",
            "frozen_artifact_size_bytes",
        },
        label="relative policy reference",
    )
    for name in (
        "baseline_canonical_sha256",
        "policy_canonical_sha256",
        "frozen_artifact_sha256",
    ):
        _evidence_sha(reference[name], label="relative policy reference")
    _evidence_integer(
        reference["frozen_artifact_size_bytes"],
        label="relative policy reference size",
        minimum=1,
    )
    if dict(reference) != _compile_anchored_relative_policy_reference():
        raise ValueError("absolute generalization relative policy reference differs")


def validate_champion_evidence(raw: object, contract: AbsoluteGeneralizationContract) -> Mapping[str, object]:
    """Validate raw champion adjuncts through their existing public authorities."""

    champion = _evidence_mapping(raw, label="champion")
    _evidence_fields(champion, _CHAMPION_FIELDS, label="champion")
    _validate_grant_acceptance(champion["strategic_grant_acceptance"])
    _validate_ownership_acceptance(champion["strategic_ownership_acceptance"], contract, champion)
    _validate_relative_policy_reference(champion["relative_policy_reference"])
    evidence_sha256 = _evidence_sha(champion["evidence_sha256"], label="champion evidence")
    if evidence_sha256 != canonical_json_sha256(
        {key: value for key, value in champion.items() if key != "evidence_sha256"}
    ):
        raise ValueError("absolute generalization champion evidence identity differs")
    return champion


def _runtime_transitions(raw: object, *, label: str) -> tuple[dict[str, object], ...]:
    rows = _evidence_sequence(raw, label=label)
    if not rows or len(rows) > 20_000:
        raise ValueError(f"absolute generalization {label} evidence is unbounded")
    result: list[dict[str, object]] = []
    for item in rows:
        row = _evidence_mapping(item, label=label)
        _evidence_fields(row, {"session", "phase", "edge_kind", "runtime_state"}, label=label)
        session = _evidence_date(row["session"], label=label)
        phase = _evidence_text(row["phase"], label=label)
        edge_kind = _evidence_text(row["edge_kind"], label=label)
        if phase not in _PHASES or edge_kind != "OBSERVED":
            raise ValueError(f"absolute generalization {label} transition differs")
        result.append(
            {
                "session": session,
                "phase": phase,
                "edge_kind": edge_kind,
                "state": reachability_state_from_raw(row["runtime_state"]),
            }
        )
    return tuple(result)


def validate_failed_grant_evidence(raw: object) -> Mapping[str, object]:
    """Rebuild Task 6 inputs and validate one realized successor chain."""

    evidence = _evidence_mapping(raw, label="failed-grant")
    expected = {
        "first_grant",
        "first_epoch",
        "second_grant",
        "second_epoch",
        "target",
        "order",
        "fill",
        "fill_identity_sha256",
        "transitions",
    }
    _evidence_fields(evidence, expected, label="failed-grant")
    transitions = _runtime_transitions(evidence["transitions"], label="failed-grant transitions")
    first_grant = StrategicGrantIntent(
        **cast(
            dict[str, Any],
            dict(_evidence_mapping(evidence["first_grant"], label="first grant")),
        )
    )
    first_epoch = StrategicEpoch(
        **cast(
            dict[str, Any],
            dict(_evidence_mapping(evidence["first_epoch"], label="first epoch")),
        )
    )
    result = analyze_failed_grant_recovery(
        first_grant=first_grant,
        first_epoch=first_epoch,
        transitions=transitions,
    )
    if not result.passed:
        raise ValueError("absolute generalization failed-grant recovery exceeds bound")
    final_state = cast(Mapping[str, object], transitions[-1]["state"])
    outlet = cast(Mapping[str, object], final_state["outlet_evidence"])
    for name in ("target", "grant", "epoch"):
        raw_name = {"grant": "second_grant", "epoch": "second_epoch"}.get(name, name)
        if _evidence_json_value(evidence[raw_name]) != _evidence_json_value(outlet[name]):
            raise ValueError("absolute generalization failed-grant successor differs")
    fill_raw = _evidence_mapping(evidence["fill"], label="successor fill")
    digest = _evidence_sha(evidence["fill_identity_sha256"], label="successor physical fill")
    if digest != physical_fill_identity_sha256(fill_raw):
        raise ValueError("absolute generalization successor physical fill differs")
    orders = cast(Sequence[object], outlet["orders"])
    fills = cast(Sequence[object], outlet["fills"])
    if not any(_evidence_json_value(evidence["order"]) == _evidence_json_value(item) for item in orders):
        raise ValueError("absolute generalization failed-grant order differs")
    if not any(_evidence_json_value(fill_raw) == _evidence_json_value(item) for item in fills):
        raise ValueError("absolute generalization failed-grant fill differs")
    return evidence


def healthy_retry_sessions(raw: Mapping[str, object]) -> int:
    """Recompute the number of distinct all-predicate healthy sessions."""
    transitions = _runtime_transitions(raw["transitions"], label="failed-grant transitions")
    first_grant = StrategicGrantIntent(
        **cast(dict[str, Any], dict(cast(Mapping[str, object], raw["first_grant"])))
    )
    first_epoch = StrategicEpoch(**cast(dict[str, Any], dict(cast(Mapping[str, object], raw["first_epoch"]))))
    return analyze_failed_grant_recovery(
        first_grant=first_grant, first_epoch=first_epoch, transitions=transitions
    ).healthy_retry_sessions


@dataclass(frozen=True, slots=True)
class _CrowningChain:
    raw: Mapping[str, object]
    target: Target
    grant: StrategicGrantIntent
    epoch: StrategicEpoch
    order: AccountOrder
    fill: Fill
    fill_identity_sha256: str


def _crowning_chain(raw: object) -> _CrowningChain:
    row = _evidence_mapping(raw, label="crowning chain")
    _evidence_fields(
        row,
        {
            "qualification_session",
            "target_session",
            "order_session",
            "authorization_session",
            "exit_session",
            "target",
            "grant",
            "epoch",
            "order",
            "fill",
            "fill_identity_sha256",
        },
        label="crowning chain",
    )
    target = Target(
        **cast(
            dict[str, Any],
            dict(_evidence_mapping(row["target"], label="crowning target")),
        )
    )
    grant = StrategicGrantIntent(
        **cast(
            dict[str, Any],
            dict(_evidence_mapping(row["grant"], label="crowning grant")),
        )
    )
    epoch = StrategicEpoch(
        **cast(
            dict[str, Any],
            dict(_evidence_mapping(row["epoch"], label="crowning epoch")),
        )
    )
    order = AccountOrder(
        **cast(
            dict[str, Any],
            dict(_evidence_mapping(row["order"], label="crowning order")),
        )
    )
    fill_raw = _evidence_mapping(row["fill"], label="crowning fill")
    return _CrowningChain(
        raw=row,
        target=target,
        grant=grant,
        epoch=epoch,
        order=order,
        fill=Fill(**cast(dict[str, Any], dict(fill_raw))),
        fill_identity_sha256=_evidence_sha(row["fill_identity_sha256"], label="crowning physical fill"),
    )


def _crowning_account_indexes(
    raw: object,
) -> tuple[
    dict[str, StrategicEpoch],
    dict[str, AccountOrder],
    dict[str, Fill],
]:
    account_raw = deepcopy(dict(_evidence_mapping(raw, label="crowning account")))
    normalize_epoch_only_cohort_attribution(account_raw)
    account = account_from_dict(account_raw, require_hashes=False)
    account_fills = {physical_fill_identity_sha256(item): item for item in account.fills}
    if len(account_fills) != len(account.fills):
        raise ValueError("absolute generalization crowning physical fill is duplicated")
    return (
        {item.epoch_id: item for item in account.strategic_epochs},
        {item.order_id: item for item in account.order_ledger},
        account_fills,
    )


def _evidence_crowning_authorization_session(chain: _CrowningChain) -> str:
    raw = chain.raw["authorization_session"]
    if not chain.grant.authorization_id:
        if raw != "":
            raise ValueError("absolute generalization crowning authorization differs")
        return ""
    return _evidence_date(raw, label="crowning authorization")


def _crowning_execution_matches(
    chain: _CrowningChain,
    *,
    qualification: str,
    target_session: str,
    order_session: str,
    authorization_session: str,
    exited: str,
    account_epochs: Mapping[str, StrategicEpoch],
    account_orders: Mapping[str, AccountOrder],
    account_fills: Mapping[str, Fill],
) -> bool:
    target, grant, epoch = chain.target, chain.grant, chain.epoch
    order, fill, digest = chain.order, chain.fill, chain.fill_identity_sha256
    authorized = (not grant.authorization_id and authorization_session == "") or (
        bool(grant.authorization_id)
        and qualification <= authorization_session <= grant.created_session
    )
    return (
        qualification <= grant.created_session
        and authorized
        and grant.created_session
        == epoch.opened_session
        <= target_session
        == order_session
        == order.signal_date
        < fill.fill_date
        <= exited
        == epoch.closed_session
        and fill.shares >= 1
        and target.event_id == order.event_id
        and order.event_id == fill.event_id
        and target.grant_id == grant.grant_id
        and target.epoch_id == epoch.epoch_id
        and order.order_id == fill.order_id
        and physical_fill_identity_sha256(fill) == digest
        and account_epochs.get(epoch.epoch_id) == epoch
        and account_orders.get(order.order_id) == order
        and account_fills.get(digest) == fill
        and is_positive_strategic_outlet(
            target=target,
            grant=grant,
            epoch=epoch,
            orders=(order,),
            fills=(fill,),
        )
        and epoch.first_fill_session == fill.fill_date
        and epoch.active_session == fill.fill_date
    )


def _validate_crowning_execution(
    chain: _CrowningChain,
    *,
    contract: AbsoluteGeneralizationContract,
    account_epochs: Mapping[str, StrategicEpoch],
    account_orders: Mapping[str, AccountOrder],
    account_fills: Mapping[str, Fill],
) -> str:
    if chain.epoch.owner_symbol not in contract.canonical_universe:
        raise ValueError("absolute generalization crowning owner differs")
    qualification = _evidence_date(chain.raw["qualification_session"], label="crowning qualification")
    target_session = _evidence_date(chain.raw["target_session"], label="crowning target")
    order_session = _evidence_date(chain.raw["order_session"], label="crowning order")
    authorization_session = _evidence_crowning_authorization_session(chain)
    exited = _evidence_date(chain.raw["exit_session"], label="crowning exit")
    if not _crowning_execution_matches(
        chain,
        qualification=qualification,
        target_session=target_session,
        order_session=order_session,
        authorization_session=authorization_session,
        exited=exited,
        account_epochs=account_epochs,
        account_orders=account_orders,
        account_fills=account_fills,
    ):
        raise ValueError("absolute generalization crowning chain differs")
    return exited


def _validate_crowning_predecessor(
    chain: _CrowningChain,
    *,
    previous_epoch: StrategicEpoch | None,
    previous_grant: StrategicGrantIntent | None,
    previous_exit: str,
) -> None:
    if previous_epoch is None:
        if chain.epoch.previous_epoch_id or chain.grant.previous_grant_id:
            raise ValueError("absolute generalization first crowning predecessor differs")
        return
    if (
        chain.epoch.previous_epoch_id != previous_epoch.epoch_id
        or previous_grant is None
        or chain.grant.previous_grant_id != previous_grant.grant_id
        or previous_exit >= chain.grant.created_session
    ):
        raise ValueError("absolute generalization crowning identity chain differs")


def validate_crowning_evidence(
    raw: object,
    *,
    cross: bool,
    contract: AbsoluteGeneralizationContract,
) -> Mapping[str, object]:
    """Validate Fill-gated epoch rows and their predecessor identity chain."""

    evidence = _evidence_mapping(raw, label="crowning")
    expected = (
        {"source_scenario_id", "final_account", "chains"}
        if cross
        else {"source_cell_id", "final_account", "chains"}
    )
    _evidence_fields(evidence, expected, label="crowning")
    source_name = "source_scenario_id" if cross else "source_cell_id"
    source = _evidence_text(evidence[source_name], label="crowning source")
    if not cross and source not in {f"remove-{symbol}" for symbol in contract.canonical_universe}:
        raise ValueError("absolute generalization crowning source cell differs")
    chains = _evidence_sequence(evidence["chains"], label="crowning chains")
    if len(chains) < 2:
        raise ValueError("absolute generalization crowning evidence is incomplete")
    account_epochs, account_orders, account_fills = _crowning_account_indexes(evidence["final_account"])
    previous_epoch: StrategicEpoch | None = None
    previous_grant: StrategicGrantIntent | None = None
    previous_exit = ""
    universe = default_ai_universe()
    for item in chains:
        chain = _crowning_chain(item)
        exited = _validate_crowning_execution(
            chain,
            contract=contract,
            account_epochs=account_epochs,
            account_orders=account_orders,
            account_fills=account_fills,
        )
        universe.industry_of(chain.epoch.owner_symbol, chain.grant.created_session)
        _validate_crowning_predecessor(
            chain,
            previous_epoch=previous_epoch,
            previous_grant=previous_grant,
            previous_exit=previous_exit,
        )
        previous_epoch = chain.epoch
        previous_grant = chain.grant
        previous_exit = exited
    return evidence


def crown_industries(raw: Mapping[str, object]) -> tuple[str, ...]:
    universe = default_ai_universe()
    return tuple(
        universe.industry_of(
            cast(str, cast(Mapping[str, object], row["epoch"])["owner_symbol"]),
            cast(str, cast(Mapping[str, object], row["grant"])["created_session"]),
        )
        for row in cast(Sequence[Mapping[str, object]], raw["chains"])
    )


def validate_repair_evidence(raw: object) -> tuple[Mapping[str, object], ...]:
    """Rebuild exact production repair predicates for every observed state."""

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
        transitions = _runtime_transitions(observations, label="repair observations")
        sessions: list[str] = []
        statuses: list[str] = []
        episode_id = ""
        first_session = ""
        counts: list[int] = []
        required = 0
        for transition in transitions:
            state = cast(Mapping[str, object], transition["state"])
            payload = cast(AbsoluteGeneralizationReplayPayload, state["account_payload"])
            account = account_from_dict(
                cast(Mapping[str, object], strict_json_loads(payload.canonical_json)),
                require_hashes=False,
            )
            projection = project_flat_book_repair_health(
                account=account,
                risk=cast(RiskAssessment, state["risk"]),
                universe=cast(StrategicUniverseRoles, state["universe"]),
                cfg=cast(SystemConfig, state["cfg"]),
            )
            if (
                not projection.healthy
                or projection.persisted_damage_level != row["persisted_damage_level"]
                or projection.repair_target_level != row["target_budget_level"]
            ):
                raise ValueError("absolute generalization repair projection differs")
            sessions.append(cast(str, transition["session"]))
            repair = account.flat_book_capital_repair
            statuses.append(repair.status)
            counts.append(repair.healthy_session_count)
            required = repair.required_healthy_sessions
            if not episode_id:
                episode_id = repair.repair_episode_id
                first_session = repair.first_observed_session
            if (
                repair.repair_episode_id != episode_id
                or repair.first_observed_session != first_session
                or repair.last_observed_session != transition["session"]
                or repair.last_counted_session != transition["session"]
            ):
                raise ValueError("absolute generalization repair episode differs")
        if len(set(sessions)) != len(sessions):
            raise ValueError("absolute generalization repair sessions are duplicated")
        if (
            counts != list(range(1, required + 1))
            or len(sessions) != required
            or statuses[-1] != "READY"
            or any(state != "ACCUMULATING" for state in statuses[:-1])
        ):
            raise ValueError("absolute generalization repair READY transition differs")
        result.append(row)
    return tuple(result)


def repair_healthy_sessions(raw: Mapping[str, object]) -> int:
    return len(
        {cast(str, row["session"]) for row in cast(Sequence[Mapping[str, object]], raw["observations"])}
    )


def validate_terminal_evidence(raw: object) -> Mapping[str, object]:
    """Rebuild states and invoke the Task 6 SCC analyzer."""

    evidence = _evidence_mapping(raw, label="terminal SCC")
    _evidence_fields(evidence, {"transitions"}, label="terminal SCC")
    transitions = _runtime_transitions(evidence["transitions"], label="terminal SCC transitions")
    analyze_terminal_scc(transitions)
    return evidence


def _evidence_components(nodes: set[str], edges: set[tuple[str, str]]) -> tuple[frozenset[str], ...]:
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
    """Return the Task 6 projection from strictly rebuilt runtime states."""

    transitions = _runtime_transitions(raw["transitions"], label="terminal SCC transitions")
    result = analyze_terminal_scc(transitions)
    return TerminalProjection(
        durations=(result.maximum_terminal_zero_strategic_target_scc_sessions,),
        state_count=result.state_count,
        edge_count=result.edge_count,
        transition_sha256=result.state_transition_digest,
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
