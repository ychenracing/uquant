"""Production replay to Task 6 recovery/reachability evidence projection."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from uquant.account import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import strict_json_loads
from uquant.models.decision import LeaderScore, RiskAssessment, Target
from uquant.models.strategic_epoch import StrategicEpoch
from uquant.models.strategic_grant import StrategicGrantIntent, StrategicQualificationObservation
from uquant.models.strategic_universe import StrategicUniverseRoles
from uquant.models.trading import AccountOrder, Fill
from uquant.types import AccountState
from uquant.validation.universe import default_ai_universe

from ._physical_identity import physical_fill_identity_sha256
from ._reachability_codec import (
    decision_runtime_inputs_from_raw,
    reachability_state_to_raw,
)
from ._recovery_runtime_fixtures import (
    run_cross_industry_fixture,
    run_failed_grant_fixture,
    run_repair_fixture,
    run_terminal_fixture,
)
from .artifacts import derive_runtime_cell_artifact
from .contract import AbsoluteGeneralizationContract
from .reachability import (
    analyze_failed_grant_recovery,
    analyze_terminal_scc,
    is_positive_strategic_outlet,
    project_flat_book_repair_health,
    project_observed_reachability_state,
)
from .replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayAccountSnapshot,
    AbsoluteGeneralizationReplayObservation,
    AbsoluteGeneralizationReplayPayload,
    run_absolute_generalization_replay,
)
from .scenarios import build_leave_one_out_scenarios

_TRANSITION_LIMIT = 20_000


def _recovery_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"absolute recovery {label} is malformed")
    return cast(Mapping[str, object], value)


def _payload_mapping(
    payload: AbsoluteGeneralizationReplayPayload, *, label: str
) -> Mapping[str, object]:
    if hashlib.sha256(payload.canonical_json).hexdigest() != payload.sha256:
        raise ValueError(f"absolute recovery {label} identity differs")
    return _recovery_mapping(strict_json_loads(payload.canonical_json), label=label)


def _account(snapshot: AbsoluteGeneralizationReplayAccountSnapshot) -> AccountState:
    return account_from_dict(
        _payload_mapping(snapshot.account_payload, label="account"),
        require_hashes=False,
    )


def _targets(payload: AbsoluteGeneralizationReplayPayload) -> tuple[Target, ...]:
    raw = _payload_mapping(payload, label="decision")
    rows = raw.get("targets")
    if type(rows) is not list:
        raise ValueError("absolute recovery decision targets are malformed")
    return tuple(
        Target(**cast(dict[str, Any], dict(_recovery_mapping(item, label="target"))))
        for item in rows
    )


def _outlet(
    *, account: AccountState, decision_payload: AbsoluteGeneralizationReplayPayload
) -> object:
    grant = account.strategic_grant
    if grant is None:
        return None
    epochs = {item.epoch_id: item for item in account.strategic_epochs}
    for target in _targets(decision_payload):
        epoch = epochs.get(target.epoch_id)
        orders = tuple(
            item
            for item in account.order_ledger
            if item.grant_id == target.grant_id and item.epoch_id == target.epoch_id
        )
        fills = tuple(
            item
            for item in account.fills
            if item.grant_id == target.grant_id and item.epoch_id == target.epoch_id
        )
        if is_positive_strategic_outlet(
            target=target,
            grant=grant,
            epoch=epoch,
            orders=orders,
            fills=fills,
        ):
            return {
                "target": target,
                "grant": grant,
                "epoch": epoch,
                "orders": orders,
                "fills": fills,
            }
    return None


def _activation_order_and_fill(
    outlet: Mapping[str, object],
) -> tuple[AccountOrder, Fill]:
    target = cast(Target, outlet["target"])
    grant = cast(StrategicGrantIntent, outlet["grant"])
    epoch = cast(StrategicEpoch, outlet["epoch"])
    orders = cast(tuple[AccountOrder, ...], outlet["orders"])
    fills = tuple(
        fill
        for fill in cast(tuple[Fill, ...], outlet["fills"])
        if fill.shares > 0
        and fill.side == "BUY"
        and fill.symbol == target.symbol == grant.candidate_symbol == epoch.owner_symbol
        and fill.event_id == target.event_id
        and fill.grant_id == target.grant_id == grant.grant_id == epoch.grant_id
        and fill.epoch_id == target.epoch_id == grant.epoch_id == epoch.epoch_id
        and fill.origin_subsystem == "STRATEGIC"
        and fill.fill_date == epoch.first_fill_session
    )
    if not fills:
        raise ValueError("absolute recovery activation fill is absent")
    fill = min(
        fills,
        key=lambda item: (item.fill_date, physical_fill_identity_sha256(item)),
    )
    matching = tuple(order for order in orders if order.order_id == fill.order_id)
    if len(matching) != 1:
        raise ValueError("absolute recovery activation order differs")
    order = matching[0]
    if (
        order.event_id != target.event_id
        or order.symbol != target.symbol
        or order.grant_id != target.grant_id
        or order.epoch_id != target.epoch_id
        or order.origin_subsystem != "STRATEGIC"
        or order.side != "BUY"
    ):
        raise ValueError("absolute recovery activation order chain differs")
    return order, fill


def _state(
    *,
    account_snapshot: AbsoluteGeneralizationReplayAccountSnapshot,
    market_payload: AbsoluteGeneralizationReplayPayload,
    decision_payload: AbsoluteGeneralizationReplayPayload,
) -> dict[str, object]:
    account = _account(account_snapshot)
    market = decision_runtime_inputs_from_raw(market_payload)
    if (
        asdict(account.strategic_qualification)
        != asdict(
            cast(
                StrategicQualificationObservation,
                market["strategic_qualification"],
            )
        )
        or asdict(account.strategic_successor_qualification)
        != asdict(
            cast(
                StrategicQualificationObservation,
                market["strategic_successor_qualification"],
            )
        )
    ):
        raise ValueError("absolute recovery qualification snapshot differs")
    return project_observed_reachability_state(
        account_payload=account_snapshot.account_payload,
        cfg=DEFAULT_CONFIG,
        risk=cast(RiskAssessment, market["risk"]),
        universe=cast(StrategicUniverseRoles, market["universe"]),
        snapshots=cast(dict[str, dict[str, float]], market["snapshots"]),
        leaders=cast(dict[str, LeaderScore], market["leaders"]),
        outlet_evidence=_outlet(account=account, decision_payload=decision_payload),
    )


def replay_reachability_transitions(
    replay: AbsoluteGeneralizationReplay,
) -> tuple[dict[str, object], ...]:
    """Build prior-close POST_OPEN and same-close POST_DECISION runtime facts."""

    rows: list[dict[str, object]] = []
    previous: AbsoluteGeneralizationReplayObservation | None = None
    for observation in replay.observations:
        current_market = observation.decision_runtime_payload
        if current_market is None:
            raise ValueError("absolute recovery decision runtime evidence is missing")
        if previous is not None:
            previous_market = previous.decision_runtime_payload
            if previous_market is None:
                raise ValueError("absolute recovery prior-close evidence is missing")
            rows.append(
                {
                    "session": observation.session,
                    "phase": "POST_OPEN",
                    "edge_kind": "OBSERVED",
                    "state": _state(
                        account_snapshot=observation.post_open_account,
                        market_payload=previous_market,
                        decision_payload=previous.decision_payload,
                    ),
                }
            )
        rows.append(
            {
                "session": observation.session,
                "phase": "POST_DECISION",
                "edge_kind": "OBSERVED",
                "state": _state(
                    account_snapshot=observation.post_decision_account,
                    market_payload=current_market,
                    decision_payload=observation.decision_payload,
                ),
            }
        )
        previous = observation
    if not rows or len(rows) > _TRANSITION_LIMIT:
        raise ValueError("absolute recovery transition count differs")
    analyze_terminal_scc(rows)
    return tuple(rows)


def _raw_transitions(
    transitions: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            "session": row["session"],
            "phase": row["phase"],
            "edge_kind": row["edge_kind"],
            "runtime_state": reachability_state_to_raw(row["state"]),
        }
        for row in transitions
    ]


def _terminal_payload(
    transitions: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    analyze_terminal_scc(transitions)
    return {"transitions": _raw_transitions(transitions)}


def _matching_crowning_target(
    raw: Mapping[str, object],
    *,
    target: Target,
    grant: StrategicGrantIntent,
    epoch: StrategicEpoch,
) -> bool:
    weight = raw.get("weight")
    return (
        raw.get("epoch_id") == epoch.epoch_id
        and raw.get("grant_id") == grant.grant_id
        and raw.get("symbol") == target.symbol
        and raw.get("event_id") == target.event_id
        and raw.get("origin_subsystem") == "STRATEGIC"
        and isinstance(weight, (int, float))
        and not isinstance(weight, bool)
        and float(weight) > 0.0
    )


def _matching_crowning_order(
    raw: Mapping[str, object],
    *,
    target: Target,
    grant: StrategicGrantIntent,
    epoch: StrategicEpoch,
    order: AccountOrder,
) -> bool:
    return (
        raw.get("order_id") == order.order_id
        and raw.get("epoch_id") == epoch.epoch_id
        and raw.get("grant_id") == grant.grant_id
        and raw.get("symbol") == target.symbol
        and raw.get("event_id") == target.event_id
        and raw.get("origin_subsystem") == "STRATEGIC"
        and raw.get("side") == "BUY"
    )


def _crowning_authorization_session(
    risk: Mapping[str, object], grant: StrategicGrantIntent
) -> str | None:
    raw_grant = risk.get("strategic_grant")
    if not isinstance(raw_grant, Mapping):
        return None
    observed_grant = _recovery_mapping(raw_grant, label="crowning grant")
    if observed_grant.get("grant_id") != grant.grant_id:
        return None
    raw_rearm = _recovery_mapping(
        risk.get("strategic_cash_rearm"), label="crowning authorization"
    )
    if raw_rearm.get("authorization_id") != grant.authorization_id:
        raise ValueError("absolute crowning authorization identity differs")
    authorized = raw_rearm.get("authorized_session")
    if type(authorized) is not str or not authorized:
        raise ValueError("absolute crowning authorization session differs")
    return authorized


def _crowning_decision_sessions(
    replay: AbsoluteGeneralizationReplay,
    *,
    target: Target,
    grant: StrategicGrantIntent,
    epoch: StrategicEpoch,
    order: AccountOrder,
) -> tuple[str, str, str]:
    target_sessions: set[str] = set()
    order_sessions: set[str] = set()
    authorization_sessions: set[str] = set()
    for observation in replay.observations:
        decision = _payload_mapping(
            observation.decision_payload, label="crowning decision"
        )
        targets = decision.get("targets")
        orders = decision.get("pending_orders")
        if type(targets) is not list or type(orders) is not list:
            raise ValueError("absolute crowning decision execution is malformed")
        for item in cast(list[object], targets):
            raw = _recovery_mapping(item, label="crowning target")
            if _matching_crowning_target(
                raw, target=target, grant=grant, epoch=epoch
            ):
                target_sessions.add(observation.session)
        for item in cast(list[object], orders):
            raw = _recovery_mapping(item, label="crowning order")
            if _matching_crowning_order(
                raw,
                target=target,
                grant=grant,
                epoch=epoch,
                order=order,
            ):
                order_sessions.add(observation.session)
        risk = _recovery_mapping(
            decision.get("risk_summary"), label="crowning decision risk"
        )
        authorized = _crowning_authorization_session(risk, grant)
        if authorized is not None:
            authorization_sessions.add(authorized)
    if (
        not target_sessions
        or len(order_sessions) != 1
        or len(authorization_sessions) != 1
    ):
        raise RuntimeError("absolute crowning decision chronology differs")
    target_session = min(target_sessions)
    order_session = next(iter(order_sessions))
    authorization_session = next(iter(authorization_sessions))
    if order.signal_date != order_session:
        raise RuntimeError("absolute crowning order session differs")
    return target_session, order_session, authorization_session


def _failed_recovery_payload(
    transitions: Sequence[Mapping[str, object]],
    contract: AbsoluteGeneralizationContract,
) -> dict[str, object]:
    first_grant: StrategicGrantIntent | None = None
    first_epoch: StrategicEpoch | None = None
    first_index = -1
    for index, row in enumerate(transitions):
        state = cast(Mapping[str, object], row["state"])
        account = account_from_dict(
            _payload_mapping(
                cast(AbsoluteGeneralizationReplayPayload, state["account_payload"]),
                label="failed account",
            ),
            require_hashes=False,
        )
        grant = account.strategic_grant
        if grant is None or not grant.terminal or grant.filled_shares != 0:
            continue
        epoch = next(
            (item for item in account.strategic_epochs if item.epoch_id == grant.epoch_id),
            None,
        )
        if epoch is not None and epoch.terminal:
            first_grant, first_epoch, first_index = grant, epoch, index
            break
    if first_grant is None or first_epoch is None:
        raise RuntimeError("absolute recovery has no terminal unfilled predecessor")
    maximum = contract.thresholds.maximum_failed_grant_retry_healthy_sessions
    for end in range(first_index + 1, len(transitions)):
        state = cast(Mapping[str, object], transitions[end]["state"])
        outlet = state["outlet_evidence"]
        if not isinstance(outlet, Mapping):
            continue
        candidate = tuple(transitions[first_index : end + 1])
        analysis = analyze_failed_grant_recovery(
            first_grant=first_grant,
            first_epoch=first_epoch,
            transitions=candidate,
            maximum_healthy_sessions=maximum,
        )
        if not analysis.passed:
            continue
        grant = cast(StrategicGrantIntent, outlet["grant"])
        epoch = cast(StrategicEpoch, outlet["epoch"])
        target = cast(Target, outlet["target"])
        order, fill = _activation_order_and_fill(outlet)
        return {
            "first_grant": asdict(first_grant),
            "first_epoch": asdict(first_epoch),
            "second_grant": asdict(grant),
            "second_epoch": asdict(epoch),
            "target": asdict(target),
            "order": asdict(order),
            "fill": asdict(fill),
            "fill_identity_sha256": physical_fill_identity_sha256(fill),
            "transitions": _raw_transitions(candidate),
        }
    raise RuntimeError("absolute recovery has no bounded realized successor")


def _crowning_payload(
    replay: AbsoluteGeneralizationReplay,
    transitions: Sequence[Mapping[str, object]],
    *,
    source_name: str,
    cross: bool,
) -> dict[str, object]:
    final_account = account_from_dict(
        _payload_mapping(replay.final_account_payload, label="crowning account"),
        require_hashes=False,
    )
    final_epochs = {item.epoch_id: item for item in final_account.strategic_epochs}
    qualifications: dict[tuple[str, str], str] = {}
    for observation in replay.observations:
        payload = observation.decision_runtime_payload
        if payload is None:
            raise ValueError("absolute crowning qualification evidence is absent")
        runtime = _payload_mapping(payload, label="crowning qualification")
        for name in ("strategic_qualification", "strategic_successor_qualification"):
            raw = _recovery_mapping(runtime[name], label="crowning qualification")
            if raw.get("qualification_ready") is True:
                key = (cast(str, raw["candidate_symbol"]), cast(str, raw["qualification_signature"]))
                qualifications.setdefault(key, observation.session)
    chains: dict[str, dict[str, object]] = {}
    for row in transitions:
        state = cast(Mapping[str, object], row["state"])
        outlet = state["outlet_evidence"]
        if not isinstance(outlet, Mapping):
            continue
        epoch = cast(StrategicEpoch, outlet["epoch"])
        closed = final_epochs.get(epoch.epoch_id)
        if closed is None or not closed.closed_session:
            continue
        order, fill = _activation_order_and_fill(outlet)
        grant = cast(StrategicGrantIntent, outlet["grant"])
        target = cast(Target, outlet["target"])
        target_session, order_session, authorization_session = (
            _crowning_decision_sessions(
                replay,
                target=target,
                grant=grant,
                epoch=closed,
                order=order,
            )
        )
        qualification_session = qualifications.get(
            (grant.candidate_symbol, grant.qualification_signature)
        )
        if qualification_session is None or qualification_session > grant.created_session:
            raise RuntimeError("absolute crowning qualification chronology differs")
        chains.setdefault(
            epoch.epoch_id,
            {
                "qualification_session": qualification_session,
                "target_session": target_session,
                "order_session": order_session,
                "authorization_session": authorization_session,
                "exit_session": closed.closed_session,
                "target": asdict(target),
                "grant": asdict(grant),
                "epoch": asdict(closed),
                "order": asdict(order),
                "fill": asdict(fill),
                "fill_identity_sha256": physical_fill_identity_sha256(fill),
            },
        )
    ordered = sorted(chains.values(), key=lambda item: cast(str, item["exit_session"]))
    if len(ordered) < 2:
        raise RuntimeError("absolute recovery repeated crowning evidence is absent")
    if cross:
        universe = default_ai_universe()
        industries = {
            universe.industry_of(
                cast(str, cast(Mapping[str, object], item["epoch"])["owner_symbol"]),
                cast(str, item["qualification_session"]),
            )
            for item in ordered
        }
        if len(industries) < 2:
            raise RuntimeError("absolute recovery cross-industry crowning is absent")
    source_key = "source_scenario_id" if cross else "source_cell_id"
    return {
        source_key: source_name,
        "final_account": dict(
            _payload_mapping(replay.final_account_payload, label="crowning account")
        ),
        "chains": ordered,
    }


def _repair_payloads(
    transitions: Sequence[Mapping[str, object]],
    contract: AbsoluteGeneralizationContract,
    *,
    persisted_level: int | None = None,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for bound in contract.thresholds.repair_bounds:
        if persisted_level is not None and bound.persisted_damage_level != persisted_level:
            continue
        observed: list[dict[str, object]] = []
        for row in transitions:
            if row["phase"] != "POST_DECISION":
                continue
            state = cast(Mapping[str, object], row["state"])
            account = account_from_dict(
                _payload_mapping(
                    cast(AbsoluteGeneralizationReplayPayload, state["account_payload"]),
                    label="repair account",
                ),
                require_hashes=False,
            )
            projection = project_flat_book_repair_health(
                account=account,
                risk=cast(RiskAssessment, state["risk"]),
                universe=cast(StrategicUniverseRoles, state["universe"]),
                cfg=DEFAULT_CONFIG,
            )
            if (
                projection.persisted_damage_level != bound.persisted_damage_level
                or projection.repair_target_level != bound.target_budget_level
                or not projection.healthy
            ):
                continue
            observed.append(
                {
                    "session": row["session"],
                    "phase": row["phase"],
                    "edge_kind": row["edge_kind"],
                    "runtime_state": reachability_state_to_raw(state),
                }
            )
            if account.flat_book_capital_repair.status == "READY":
                break
        if len(observed) != bound.maximum_healthy_sessions:
            raise RuntimeError("absolute recovery repair bound evidence is absent")
        result.append(
            {
                "persisted_damage_level": bound.persisted_damage_level,
                "target_budget_level": bound.target_budget_level,
                "observations": observed,
            }
        )
    return result


def run_recovery_runtime_payload(
    *,
    root: Path,
    data_dir: Path,
    cache_dir: Path,
    contract: AbsoluteGeneralizationContract,
) -> dict[str, object]:
    """Run isolated preregistered production traces and derive strict raw facts."""

    scenarios = {
        item.removed_symbol: item
        for item in build_leave_one_out_scenarios(contract)
        if item.removed_symbol in set(contract.critical_removals)
    }
    replays = {
        symbol: run_absolute_generalization_replay(
            scenarios[symbol], root=root, data_dir=data_dir, cache_dir=cache_dir
        )
        for symbol in contract.critical_removals
    }
    historical_symbol = "sz300502"
    historical_transitions = replay_reachability_transitions(
        replays[historical_symbol]
    )
    historical_artifact = derive_runtime_cell_artifact(
        replays[historical_symbol], contract, root=root
    )
    if historical_artifact.status != "COMPLETE" or historical_artifact.metrics is None:
        raise RuntimeError("absolute recovery historical source cell is incomplete")
    historical_payload = _crowning_payload(
        replays[historical_symbol],
        historical_transitions,
        source_name="remove-sz300502",
        cross=False,
    )
    facts = {item.epoch_id: item for item in historical_artifact.metrics.epochs}
    for chain in cast(Sequence[Mapping[str, object]], historical_payload["chains"]):
        epoch = _recovery_mapping(chain["epoch"], label="historical epoch")
        grant = _recovery_mapping(chain["grant"], label="historical grant")
        fill = _recovery_mapping(chain["fill"], label="historical fill")
        fact = facts.get(cast(str, epoch["epoch_id"]))
        expected = {
            "epoch_id": epoch["epoch_id"],
            "grant_id": epoch["grant_id"],
            "owner_symbol": epoch["owner_symbol"],
            "qualification_signature": epoch["qualification_signature"],
            "qualification_route": epoch["qualification_route"],
            "qualification_quorum": epoch["qualification_quorum"],
            "qualification_session": chain["qualification_session"],
            "grant_session": grant["created_session"],
            "target_session": chain["target_session"],
            "order_session": chain["order_session"],
            "fill_session": fill["fill_date"],
            "active_session": epoch["active_session"],
            "closed_session": epoch["closed_session"],
            "close_reason": epoch["close_reason"],
            "realized_status": epoch["realized_status"],
            "previous_epoch_id": epoch["previous_epoch_id"],
            "previous_grant_id": grant["previous_grant_id"],
            "authorization_id": grant["authorization_id"],
            "authorization_session": chain["authorization_session"],
        }
        if fact is None or fact.to_dict() != expected:
            raise RuntimeError("absolute recovery historical Task 5 binding differs")
    failed_replay = run_failed_grant_fixture(contract)
    failed_transitions = replay_reachability_transitions(failed_replay)
    cross_replay = run_cross_industry_fixture(contract)
    cross_transitions = replay_reachability_transitions(cross_replay)
    terminal_transitions = replay_reachability_transitions(
        run_terminal_fixture(contract)
    )
    repair_payloads: list[dict[str, object]] = []
    for bound in contract.thresholds.repair_bounds:
        repair_replay = run_repair_fixture(
            contract,
            level=bound.persisted_damage_level,
            sessions=bound.maximum_healthy_sessions,
        )
        projected = _repair_payloads(
            replay_reachability_transitions(repair_replay),
            contract,
            persisted_level=bound.persisted_damage_level,
        )
        matching = [
            item
            for item in projected
            if item["persisted_damage_level"] == bound.persisted_damage_level
        ]
        if len(matching) != 1:
            raise RuntimeError("absolute recovery isolated repair evidence differs")
        repair_payloads.extend(matching)
    cross_payload = _crowning_payload(
        cross_replay,
        cross_transitions,
        source_name="cross-industry-production-semantic-v1",
        cross=True,
    )
    return {
        "failed_grant_recovery": _failed_recovery_payload(
            failed_transitions, contract
        ),
        "historical_crowning": historical_payload,
        "terminal_scc": _terminal_payload(terminal_transitions),
        "repair_bounds": repair_payloads,
        "cross_industry_crowning": cross_payload,
    }


__all__ = ("replay_reachability_transitions", "run_recovery_runtime_payload")
