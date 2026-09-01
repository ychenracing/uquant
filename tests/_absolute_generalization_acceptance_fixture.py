from __future__ import annotations

import copy
import gzip
import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import cast

from _absolute_generalization_metrics_fixture import (
    OWNER,
    complete_replay,
    completed_crowning_replay,
)
from _absolute_generalization_metrics_fixture import replay_error as fixture_replay_error
from _absolute_generalization_metrics_fixture import scenario as fixture_scenario
from _absolute_generalization_reachability_fixture import (
    _account_with_successor_chain,
    failed_recovery_trace,
    failed_successor_chain,
)
from test_absolute_generalization_reachability import (
    _cash_account,
    _filled_chain,
    _reachability_state,
    _rearm_evidence,
)
from test_absolute_generalization_reachability import (
    _roles as _reachability_roles,
)

from uquant.account import account_from_dict, economic_state_sha256
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.strict_json import (
    canonical_json_bytes,
    canonical_json_sha256,
    strict_json_loads,
)
from uquant.market import ReplayUniverse
from uquant.models.strategic_rearm import (
    FlatBookCapitalRepairState,
    derive_flat_book_capital_repair_episode_id,
    derive_strategic_cash_rearm_authorization_id,
)
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.models.trading import derive_attribution_event_id
from uquant.validation.absolute_generalization import (
    ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    IdentityEnvelope,
    build_leave_one_out_scenarios,
    derive_cell_metrics,
    load_absolute_generalization_contract,
)
from uquant.validation.absolute_generalization._champion_runtime_reconciliation import (
    decode_champion_account,
    project_champion_account,
)
from uquant.validation.absolute_generalization._physical_identity import (
    physical_fill_identity_sha256,
)
from uquant.validation.absolute_generalization._reachability_codec import (
    reachability_state_to_raw,
)
from uquant.validation.absolute_generalization._replay_codec import (
    replay_from_raw,
    replay_to_raw,
)
from uquant.validation.absolute_generalization.replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayRoleSnapshot,
)
from uquant.validation.absolute_generalization.scenarios import (
    AbsoluteGeneralizationScenario,
)

ROOT = Path(__file__).resolve().parents[1]
ALTERNATE_OWNER = "sh601869"
_CHAMPION_RAW_SHA256 = "1f48879fa365c8a0688665e177fcaa722f899e1381b00f645a1c357413934aa2"


def checkout_identity() -> tuple[str, str]:
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return head, tree


def _mapped(value: object, replacements: dict[str, str]) -> object:
    if type(value) is str:
        return replacements.get(cast(str, value), value)
    if type(value) is list:
        return [_mapped(item, replacements) for item in cast(list[object], value)]
    if type(value) is dict:
        raw = cast(dict[str, object], value)
        mapped = {replacements.get(key, key): _mapped(item, replacements) for key, item in raw.items()}
        if set(mapped) == {"sha256", "value"}:
            mapped["sha256"] = hashlib.sha256(canonical_json_bytes(mapped["value"])).hexdigest()
        return mapped
    return value


def _roles(owner: str, session: str) -> AbsoluteGeneralizationReplayRoleSnapshot:
    roles = build_strategic_universe_roles(
        as_of=session,
        tradable_symbols=(owner,),
        qualification_reference_symbols=(owner,),
        risk_reference_symbols=(owner, "sh000300", "sh000682"),
        available_symbols=(owner, "sh000300", "sh000682"),
        industries={owner: "fixture-industry"},
    )
    return AbsoluteGeneralizationReplayRoleSnapshot(
        as_of=roles.as_of,
        tradable_symbols=roles.tradable_symbols,
        qualification_reference_symbols=roles.qualification_reference_symbols,
        risk_reference_symbols=roles.risk_reference_symbols,
        available_symbols=roles.available_symbols,
        unavailable_reference_symbols=roles.unavailable_reference_symbols,
        point_in_time_industries=roles.point_in_time_industries,
        tradable_identity=roles.tradable_identity,
        qualification_reference_identity=roles.qualification_reference_identity,
        risk_reference_identity=roles.risk_reference_identity,
        point_in_time_industry_identity=roles.point_in_time_industry_identity,
    )


def _alternate_owner_replay() -> AbsoluteGeneralizationReplay:
    raw = cast(dict[str, object], _mapped(replay_to_raw(complete_replay()), {OWNER: ALTERNATE_OWNER}))
    observations = cast(list[dict[str, object]], raw["observations"])
    for observation in observations:
        session = cast(str, observation["session"])
        observation["roles"] = asdict(_roles(ALTERNATE_OWNER, session))
        observation["replay_universe_identity"] = ReplayUniverse.from_symbols(
            tradable_symbols=(ALTERNATE_OWNER,),
            reference_symbols=(ALTERNATE_OWNER,),
            index_symbols=("sh000300", "sh000682"),
        ).identity_sha256
        data_manifest = cast(dict[str, object], observation["data_manifest"])
        files = cast(list[list[str]], data_manifest["files"])
        data_manifest["digest"] = hashlib.sha256(
            json.dumps(dict(files), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    return replay_from_raw(raw)


@lru_cache(maxsize=1)
def _historical_replay() -> AbsoluteGeneralizationReplay:
    first = _filled_chain(
        candidate="sz300308",
        qualification_signature="qualification:optical",
        created_session="2026-01-05",
        fill_session="2026-01-06",
    )
    first[2].realized_status = "CLOSED"
    first[2].closed_session = "2026-01-07"
    first[2].close_reason = "strategic rotation"
    first[4].commission = 0.0
    first[4].fill_id = ""
    second = _filled_chain(
        candidate=ALTERNATE_OWNER,
        qualification_signature="qualification:optical:successor",
        previous_grant_id=first[1].grant_id,
        previous_epoch_id=first[2].epoch_id,
        authorization_id="rearm_" + "e" * 64,
        created_session="2026-01-09",
        fill_session="2026-01-10",
    )
    second[3].order_id = "O000000003"
    second[4].order_id = second[3].order_id
    second[1].submitted_order_ids = [second[3].order_id]
    second[1].acknowledged_order_ids = [second[3].order_id]
    second[2].realized_status = "CLOSED"
    second[2].closed_session = "2026-01-11"
    second[2].close_reason = "strategic rotation"
    second[4].commission = 0.0
    second[4].fill_id = ""
    return completed_crowning_replay(
        first=first,
        second=second,
        removed_symbol="sz300502",
    )


def _replay_for_scenario(
    scenario: AbsoluteGeneralizationScenario,
) -> AbsoluteGeneralizationReplay:
    if scenario.removed_symbol == OWNER:
        replay = _alternate_owner_replay()
    elif scenario.removed_symbol == "sz300502":
        replay = _historical_replay()
    else:
        replay = complete_replay()
    observations = tuple(
        replace(
            observation,
            intentional_role_absent_symbols=(scenario.removed_symbol,),
        )
        for observation in replay.observations
    )
    return replace(replay, scenario=scenario, observations=observations)


def _identities(replay: AbsoluteGeneralizationReplay) -> IdentityEnvelope:
    contract = load_absolute_generalization_contract()
    head, tree = checkout_identity()
    roles = replay.observations[-1].roles
    return IdentityEnvelope(
        head=head,
        tree=tree,
        scenario_contract_sha256=contract.canonical_sha256,
        production_source_sha256=contract.candidate.production_source_sha256,
        effective_config_sha256=contract.inputs.effective_config_sha256,
        uv_lock_sha256=contract.inputs.uv_lock_sha256,
        frozen_data_manifest_sha256=contract.inputs.frozen_data.manifest_sha256,
        universe_sha256=contract.inputs.ai_universe_sha256,
        industry_mapping_sha256=roles.point_in_time_industry_identity,
        tradable_role_identity=roles.tradable_identity,
        qualification_reference_role_identity=roles.qualification_reference_identity,
        risk_reference_role_identity=roles.risk_reference_identity,
        execution_contract_identity=ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    )


def _cell_raw(scenario: AbsoluteGeneralizationScenario) -> dict[str, object]:
    replay = _replay_for_scenario(scenario)
    return derive_cell_metrics(replay, scenario, _identities(replay)).to_dict()


def _summary(cells: list[dict[str, object]]) -> dict[str, object]:
    return {
        "cell_count": len(cells),
        "complete_cell_count": sum(cell["status"] == "COMPLETE" for cell in cells),
        "replay_error_cell_count": sum(cell["status"] == "REPLAY_ERROR" for cell in cells),
    }


def _seal_manifest(raw: dict[str, object]) -> dict[str, object]:
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )
    return raw


def reseal_manifest(raw: dict[str, object]) -> None:
    _seal_manifest(raw)


def reseal_cell(raw: dict[str, object]) -> None:
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )


def _envelope(shard: str, cells: list[dict[str, object]]) -> dict[str, object]:
    contract = load_absolute_generalization_contract()
    head, tree = checkout_identity()
    return {
        "schema_version": 1,
        "shard": shard,
        "mode": "canonical",
        "status": "COMPLETE",
        "upstream_success": True,
        "error": "",
        "run_id": "task7-fixture-run",
        "run_attempt": 1,
        "head": head,
        "tree": tree,
        "scenario_contract_sha256": contract.canonical_sha256,
        "production_source_sha256": contract.candidate.production_source_sha256,
        "effective_config_sha256": contract.inputs.effective_config_sha256,
        "uv_lock_sha256": contract.inputs.uv_lock_sha256,
        "frozen_data_manifest_sha256": contract.inputs.frozen_data.manifest_sha256,
        "universe_sha256": contract.inputs.ai_universe_sha256,
        "cells": cells,
        "champion": None,
        "failed_grant_recovery": None,
        "historical_crowning": None,
        "terminal_scc": None,
        "repair_bounds": [],
        "cross_industry_crowning": None,
        "summary": _summary(cells),
        "canonical_sha256": "",
    }


def _champion() -> dict[str, object]:
    grant_contract = json.loads(
        (ROOT / "benchmarks/strategic_grant_acceptance_contract.json").read_text(encoding="utf-8")
    )
    encoded = gzip.decompress((ROOT / "tests/fixtures/absolute_champion_runtime_raw.json.gz").read_bytes())
    if hashlib.sha256(encoded).hexdigest() != _CHAMPION_RAW_SHA256:
        raise ValueError("champion raw fixture identity differs")
    runtime_raw = cast(dict[str, object], strict_json_loads(encoded))
    final_account = project_champion_account(cast(dict[str, object], runtime_raw["final_account"]))
    decision_trace = cast(list[dict[str, object]], runtime_raw["decision_trace"])
    report_trace = copy.deepcopy(decision_trace)
    champion_order_ledger = cast(list[dict[str, object]], runtime_raw["order_ledger"])
    champion_equity_curve = cast(list[dict[str, object]], runtime_raw["equity_curve"])
    daily_replay_evidence = cast(list[dict[str, object]], runtime_raw["daily_replay_evidence"])
    epochs = [item for item in final_account["strategic_epochs"] if item["first_fill_session"]]
    targets = [
        target
        for row in decision_trace
        for target in row["targets"]
        if target["origin_subsystem"] == "STRATEGIC" and target["weight"] > 0.0
    ]
    champion: dict[str, object] = {
        "metrics": {
            "account_orders": 12,
            "final_equity": 49_019_323.60580173,
            "final_wealth": 24.509661802900865,
            "max_drawdown": 0.27146973146234554,
            "total_return": 23.509661802900865,
        },
        "path_sha256": {
            "equity": "654142a4a217d243c53104ac6636a1778314c2e04497cfd0456a6385ea3aab39",
            "fills": "e4927cfbce9202e488dfc3c0cbadf412c527a68314b499eab4e9d916d5037fd1",
            "orders": "24befbce7f2a2eb46b82d2dcd9ef1351d628616ba848a167deff4dc36c857a00",
            "positions": "8819f3e2c32e9076bf6007040510c93ae02cbef8d6c41159bf12ffccec9782d0",
            "targets": "7f33eca7246df9af6895865b526e7e754f9a3a78ffc5dd9b7a293d78cd8c0f95",
        },
        "duplicate_grant_count": 0,
        "duplicate_order_count": 0,
        "duplicate_epoch_count": 0,
        "incumbent_epoch_count": 1,
        "successor_capital_before_incumbent_exit_count": 0,
        "report_13": {
            "initial_cash": final_account["initial_cash"],
            "cash": final_account["cash"],
            "position_market_value": 0.0,
            "realized_pnl": 47_019_323.60580174,
            "open_pnl": 0.0,
            "final_equity": champion_equity_curve[-1]["equity"],
            "maximum_target_gross": max(cast(float, row["target_gross"]) for row in report_trace),
            "minimum_risk_target_gross_cap": min(
                cast(float, cast(dict[str, object], row["risk"])["target_gross_cap"]) for row in report_trace
            ),
            "owner_symbols": sorted(item["owner_symbol"] for item in epochs),
            "unexpected_owner_symbols": [],
        },
        "strategic_grant_acceptance": {
            "baseline": {
                "first_positive_target_session": grant_contract["baseline"][
                    "expected_first_positive_target_session"
                ],
                "metrics": grant_contract["baseline"]["expected_metrics"],
                "sha256": grant_contract["baseline"]["expected_sha256"],
            },
        },
        "strategic_ownership_acceptance": {
            "contract_sha256": "72e6b510c3bcf44ac77d2c13613f4d72a14ae8dab0d60a19e5947055ae7cbf08",
            "production_source_identity": load_absolute_generalization_contract().candidate.production_source_sha256,
            "champion": {
                "scenario_id": "champion-5",
                "owner_symbols": sorted(item["owner_symbol"] for item in epochs),
                "grant_ids": sorted(item["grant_id"] for item in epochs),
                "epoch_ids": sorted(item["epoch_id"] for item in epochs),
                "target_event_ids": sorted({item["event_id"] for item in targets}),
                "order_ids": sorted(item["order_id"] for item in final_account["order_ledger"]),
                "fill_identity_sha256s": sorted(
                    physical_fill_identity_sha256(item) for item in final_account["fills"]
                ),
                "final_account": final_account,
                "decision_trace": decision_trace,
                "order_ledger": champion_order_ledger,
                "equity_curve": champion_equity_curve,
                "daily_replay_evidence": daily_replay_evidence,
                "trace_sha256": canonical_json_sha256(decision_trace),
            },
            "report_13": {
                "scenario_id": "report-13",
                "window_start": "2023-01-03",
                "window_end": "2026-08-05",
                "observed_sessions": len(report_trace),
                "account_orders": 12,
                "final_equity": champion_equity_curve[-1]["equity"],
                "final_account_sha256": economic_state_sha256(
                    account_from_dict(decode_champion_account(final_account), require_hashes=False)
                ),
                "trace_sha256": canonical_json_sha256(report_trace),
                "final_account": final_account,
                "decision_trace": report_trace,
                "order_ledger": champion_order_ledger,
                "equity_curve": champion_equity_curve,
                "daily_replay_evidence": daily_replay_evidence,
            },
        },
        "relative_policy_reference": {
            "baseline_canonical_sha256": "8603c4572fbf15a3de4f89737ab078d7e61d76f9e197f210a24704b8a4aabd79",
            "policy_canonical_sha256": "46cf95d26d04186824f181266da68e5a2d98814b65371c0b358c7cacfa8ef8fc",
            "frozen_artifact_sha256": "926ea8419ab8aad7a05577eee56aeefa90c33cc7faa4e1ee1d2bbbaac77439cc",
            "frozen_artifact_size_bytes": 16_196_017,
        },
    }
    champion["evidence_sha256"] = canonical_json_sha256(champion)
    return champion


def _failed_recovery() -> dict[str, object]:
    first, first_epoch, target, second, second_epoch, order, fill = failed_successor_chain(retry_sessions=20)
    fill.fill_id = ""
    trace = failed_recovery_trace(20, (target, second, second_epoch, order, fill))
    return {
        "first_grant": asdict(first),
        "first_epoch": asdict(first_epoch),
        "second_grant": asdict(second),
        "second_epoch": asdict(second_epoch),
        "target": asdict(target),
        "order": asdict(order),
        "fill": asdict(fill),
        "fill_identity_sha256": physical_fill_identity_sha256(fill),
        "transitions": [
            {
                "session": row["session"],
                "phase": row["phase"],
                "edge_kind": row["edge_kind"],
                "runtime_state": reachability_state_to_raw(row["state"]),
            }
            for row in trace
        ],
    }


def _crowning(*, cross: bool) -> dict[str, object]:
    target1, grant1, epoch1, order1, fill1 = _filled_chain()
    epoch1.realized_status = "CLOSED"
    epoch1.closed_session = "2026-01-07"
    epoch1.close_reason = "strategic rotation"
    candidate = "sh688019" if cross else "sz300502"
    qualification_signature = "qualification:materials" if cross else "qualification:optical:successor"
    rearm = _rearm_evidence(candidate, "2026-01-09")
    rearm["qualification_signature"] = qualification_signature
    rearm["authorization_id"] = derive_strategic_cash_rearm_authorization_id(
        account_identity=str(rearm["account_identity"]),
        repair_episode_id=str(rearm["repair_episode_id"]),
        candidate_symbol=str(rearm["candidate_symbol"]),
        qualification_signature=qualification_signature,
        qualification_route=str(rearm["qualification_route"]),
        qualification_quorum=str(rearm["qualification_quorum"]),
        qualification_evidence_sha256=str(rearm["qualification_evidence_sha256"]),
        capital_budget_level=int(rearm["capital_budget_level"]),
        tradable_universe_identity=str(rearm["tradable_universe_identity"]),
        qualification_reference_universe_identity=str(rearm["qualification_reference_universe_identity"]),
        risk_reference_universe_identity=str(rearm["risk_reference_universe_identity"]),
        point_in_time_industry_identity=str(rearm["point_in_time_industry_identity"]),
        authorized_session=str(rearm["authorized_session"]),
    )
    target2, grant2, epoch2, order2, fill2 = _filled_chain(
        candidate=candidate,
        qualification_signature=qualification_signature,
        previous_grant_id=grant1.grant_id,
        previous_epoch_id=epoch1.epoch_id,
        authorization_id=str(rearm["authorization_id"]),
        created_session="2026-01-09",
        fill_session="2026-01-10",
    )
    order2.order_id = "O000000002"
    if cross:
        order2.industry_at_entry = "materials"
        fill2.industry_at_entry = "materials"
        event_id = derive_attribution_event_id(
            signal_date=order2.signal_date,
            symbol=order2.symbol,
            target_weight=order2.target_weight,
            lifecycle=order2.lifecycle,
            origin_lifecycle=order2.origin_lifecycle,
            origin_subsystem=order2.origin_subsystem,
            mechanism=order2.mechanism,
            replaces_symbol=order2.replaces_symbol,
            industry_at_entry=order2.industry_at_entry,
            industry_manifest_sha256=order2.industry_manifest_sha256,
            reduction_policy=order2.reduction_policy,
            reason_code=order2.reason_code,
            exit_kind=order2.exit_kind,
        )
        target2 = replace(target2, event_id=event_id)
        order2.event_id = event_id
        fill2.event_id = event_id
    fill2.order_id = order2.order_id
    grant2.submitted_order_ids = [order2.order_id]
    grant2.acknowledged_order_ids = [order2.order_id]
    epoch2.realized_status = "CLOSED"
    epoch2.closed_session = "2026-01-11"
    epoch2.close_reason = "strategic rotation"
    fill1.fill_id = ""
    fill2.fill_id = ""
    account = _account_with_successor_chain((target2, grant2, epoch2, order2, fill2), filled=True)
    account.strategic_epochs = [epoch1, epoch2]
    account.active_strategic_epoch_id = ""
    account.strategic_grant = None
    account.order_ledger = [order1, order2]
    account.fills = [fill1, fill2]
    account.next_order_sequence = 3
    chains = [
        {
            "qualification_session": grant.created_session,
            "target_session": target.event_id and grant.created_session,
            "order_session": order.signal_date,
            "authorization_session": grant.created_session,
            "exit_session": epoch.closed_session,
            "target": asdict(target),
            "grant": asdict(grant),
            "epoch": asdict(epoch),
            "order": asdict(order),
            "fill": asdict(fill),
            "fill_identity_sha256": physical_fill_identity_sha256(fill),
        }
        for target, grant, epoch, order, fill in (
            (target1, grant1, epoch1, order1, fill1),
            (target2, grant2, epoch2, order2, fill2),
        )
    ]
    key = "source_scenario_id" if cross else "source_cell_id"
    return {
        key: "cross-industry-production-semantic-v1" if cross else "remove-sz300502",
        "final_account": account.to_dict(),
        "chains": chains,
    }


def _historical_crowning() -> dict[str, object]:
    replay = _historical_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    if not isinstance(account, dict):
        raise AssertionError("historical fixture account differs")
    epochs = {
        cast(str, row["epoch_id"]): cast(dict[str, object], row)
        for row in cast(list[dict[str, object]], account["strategic_epochs"])
    }
    fills = cast(list[dict[str, object]], account["fills"])
    chains: list[dict[str, object]] = []
    for observation in replay.observations:
        decision = strict_json_loads(observation.decision_payload.canonical_json)
        if not isinstance(decision, dict):
            raise AssertionError("historical fixture decision differs")
        targets = cast(list[dict[str, object]], decision["targets"])
        orders = cast(list[dict[str, object]], decision["pending_orders"])
        risk = cast(dict[str, object], decision["risk_summary"])
        if not targets:
            continue
        if len(targets) != 1 or len(orders) != 1:
            raise AssertionError("historical fixture execution differs")
        target = targets[0]
        order = orders[0]
        epoch = epochs[cast(str, target["epoch_id"])]
        matching_fills = [
            fill
            for fill in fills
            if fill["order_id"] == order["order_id"] and fill["epoch_id"] == epoch["epoch_id"]
        ]
        if len(matching_fills) != 1:
            raise AssertionError("historical fixture fill differs")
        grant = cast(dict[str, object], risk["strategic_grant"])
        rearm = cast(dict[str, object], risk["strategic_cash_rearm"])
        chains.append(
            {
                "qualification_session": observation.session,
                "target_session": observation.session,
                "order_session": observation.session,
                "authorization_session": rearm["authorized_session"],
                "exit_session": epoch["closed_session"],
                "target": target,
                "grant": grant,
                "epoch": epoch,
                "order": order,
                "fill": matching_fills[0],
                "fill_identity_sha256": physical_fill_identity_sha256(matching_fills[0]),
            }
        )
    return {
        "source_cell_id": "remove-sz300502",
        "final_account": account,
        "chains": chains,
    }


def _terminal_scc() -> dict[str, object]:
    return {"transitions": _failed_recovery()["transitions"]}


def _repair_bounds() -> list[dict[str, object]]:
    result = []
    for persisted, target, required in (
        (1, 0, 20),
        (2, 1, 40),
        (3, 2, 60),
        (4, 3, 60),
    ):
        observations: list[dict[str, object]] = []
        start = date(2023, persisted * 2, 1)
        risk_identity = _reachability_roles().risk_reference_identity
        config_identity = config_fingerprint(DEFAULT_CONFIG)
        episode_id = derive_flat_book_capital_repair_episode_id(
            account_identity="account:reachability",
            capital_budget_level=persisted,
            first_observed_session=start.isoformat(),
            risk_reference_universe_identity=risk_identity,
            config_identity=config_identity,
        )
        for offset in range(required):
            session = (start + timedelta(days=offset)).isoformat()
            status = "READY" if offset == required - 1 else "ACCUMULATING"
            account = _cash_account(budget_level=persisted)
            account.flat_book_capital_repair = FlatBookCapitalRepairState(
                repair_episode_id=episode_id,
                account_identity=account.account_identity,
                capital_budget_level=persisted,
                repair_target_level=target,
                first_observed_session=start.isoformat(),
                last_observed_session=session,
                last_counted_session=session,
                healthy_session_count=offset + 1,
                required_healthy_sessions=required,
                status=status,
                risk_reference_universe_identity=risk_identity,
                config_identity=config_identity,
                last_ready_session=session if status == "READY" else "",
            )
            observations.append(
                {
                    "session": session,
                    "phase": "POST_DECISION",
                    "edge_kind": "OBSERVED",
                    "runtime_state": reachability_state_to_raw(_reachability_state(account=account)),
                }
            )
        result.append(
            {
                "persisted_damage_level": persisted,
                "target_budget_level": target,
                "observations": observations,
            }
        )
    return result


def _cross_industry() -> dict[str, object]:
    return _crowning(cross=True)


@lru_cache(maxsize=1)
def _cached_manifests() -> tuple[dict[str, object], ...]:
    contract = load_absolute_generalization_contract()
    scenarios = build_leave_one_out_scenarios(contract)
    by_shard = {name: [] for name, _ in contract.shards}
    for scenario in scenarios:
        by_shard[scenario.shard].append(_cell_raw(scenario))
    manifests: list[dict[str, object]] = []
    champion = _envelope("champion", [])
    champion["champion"] = _champion()
    manifests.append(_seal_manifest(champion))
    for shard, _symbols in contract.shards:
        manifests.append(_seal_manifest(_envelope(shard, by_shard[shard])))
    recovery = _envelope("recovery-and-reachability", [])
    recovery["failed_grant_recovery"] = _failed_recovery()
    recovery["historical_crowning"] = _historical_crowning()
    recovery["terminal_scc"] = _terminal_scc()
    recovery["repair_bounds"] = _repair_bounds()
    recovery["cross_industry_crowning"] = _cross_industry()
    manifests.append(_seal_manifest(recovery))
    return tuple(manifests)


def successful_manifests() -> list[dict[str, object]]:
    return copy.deepcopy(list(_cached_manifests()))


def manifest(manifests: list[dict[str, object]], shard: str) -> dict[str, object]:
    return next(item for item in manifests if item["shard"] == shard)


def cell(manifests: list[dict[str, object]], removed_symbol: str) -> dict[str, object]:
    for shard in manifests:
        for item in cast(list[dict[str, object]], shard["cells"]):
            if item["removed_symbol"] == removed_symbol:
                return item
    raise AssertionError(f"missing fixture cell {removed_symbol}")


def replay_error_cell() -> dict[str, object]:
    replay = fixture_replay_error()
    identity_replay = complete_replay()
    return derive_cell_metrics(
        replace(replay, scenario=fixture_scenario()),
        fixture_scenario(),
        _identities(identity_replay),
    ).to_dict()
