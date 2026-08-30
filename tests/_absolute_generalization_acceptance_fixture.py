from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import cast

from _absolute_generalization_metrics_fixture import OWNER, complete_replay
from _absolute_generalization_metrics_fixture import replay_error as fixture_replay_error
from _absolute_generalization_metrics_fixture import scenario as fixture_scenario

from uquant.contracts.strict_json import canonical_json_bytes, canonical_json_sha256
from uquant.market import ReplayUniverse
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.validation.absolute_generalization import (
    ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    IdentityEnvelope,
    build_leave_one_out_scenarios,
    derive_cell_metrics,
    load_absolute_generalization_contract,
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
        mapped = {
            replacements.get(key, key): _mapped(item, replacements)
            for key, item in raw.items()
        }
        if set(mapped) == {"sha256", "value"}:
            mapped["sha256"] = hashlib.sha256(
                canonical_json_bytes(mapped["value"])
            ).hexdigest()
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


def _replay_for_scenario(
    scenario: AbsoluteGeneralizationScenario,
) -> AbsoluteGeneralizationReplay:
    replay = _alternate_owner_replay() if scenario.removed_symbol == OWNER else complete_replay()
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
        (ROOT / "benchmarks/strategic_grant_acceptance_contract.json").read_text(
            encoding="utf-8"
        )
    )
    relative = json.loads(
        (ROOT / "artifacts/phase2/champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    relative.pop("passed")
    relative.pop("failures")
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
            "orders": "85f9a3cabd7964a1c8a1315fa7732ce5ddd593480f34619d925c92c5b4c2fa75",
            "positions": "8819f3e2c32e9076bf6007040510c93ae02cbef8d6c41159bf12ffccec9782d0",
            "targets": "7f33eca7246df9af6895865b526e7e754f9a3a78ffc5dd9b7a293d78cd8c0f95",
        },
        "duplicate_grant_count": 0,
        "duplicate_order_count": 0,
        "duplicate_epoch_count": 0,
        "incumbent_epoch_count": 1,
        "successor_capital_before_incumbent_exit_count": 0,
        "report_13": {
            "initial_cash": 2_000_000.0,
            "cash": 1_000_000.0,
            "position_market_value": 1_250_000.0,
            "realized_pnl": 100_000.0,
            "open_pnl": 150_000.0,
            "final_equity": 2_250_000.0,
            "maximum_target_gross": 0.8,
            "minimum_risk_target_gross_cap": 0.8,
            "owner_symbols": ["sz300308"],
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
            "native_eligibility": [
                {
                    "owner": item["owner"],
                    "date": item["date"],
                    "final_account_sha256": "1" * 64,
                    "trace_sha256": "2" * 64,
                }
                for item in grant_contract["native_eligibility"]
            ],
        },
        "strategic_ownership_acceptance": {
            "contract_sha256": "72e6b510c3bcf44ac77d2c13613f4d72a14ae8dab0d60a19e5947055ae7cbf08",
            "production_source_identity": load_absolute_generalization_contract().candidate.production_source_sha256,
            "champion": {
                "scenario_id": "champion-5",
                "owner_symbols": ["sz300308"],
                "grant_ids": ["grant_" + "a" * 64],
                "epoch_ids": ["epoch_" + "b" * 64],
                "target_ids": ["target_" + "c" * 64],
                "order_ids": ["order_" + "d" * 64],
                "fill_ids": ["fill_" + "e" * 64],
                "trace_sha256": "f" * 64,
            },
            "report_13": {
                "scenario_id": "report-13",
                "window_start": "2023-01-03",
                "window_end": "2026-08-05",
                "observed_sessions": 870,
                "account_orders": 12,
                "final_equity": 2_250_000.0,
                "final_account_sha256": "a" * 64,
                "trace_sha256": "b" * 64,
            },
        },
        "relative_generalization": relative,
    }
    champion["evidence_sha256"] = canonical_json_sha256(champion)
    return champion


def _failed_recovery() -> dict[str, object]:
    observations = []
    for day in range(11, 31):
        session = f"2023-01-{day:02d}"
        predicates = [
            {"code": "FLAT_ALL_CASH", "satisfied": True},
            {"code": "REFERENCE_AVAILABLE", "satisfied": True},
            {"code": "QUALIFICATION_OPPORTUNITY", "satisfied": True},
        ]
        observations.append(
            {
                "session": session,
                "phase": "POST_DECISION",
                "edge_kind": "OBSERVED",
                "state_sha256": canonical_json_sha256(
                    {
                        "session": session,
                        "phase": "POST_DECISION",
                        "predicate_results": predicates,
                    }
                ),
                "predicate_results": predicates,
            }
        )
    return {
        "first_grant": {
            "grant_id": "grant_" + "1" * 64,
            "candidate_symbol": "sh600487",
            "status": "EXPIRED",
            "filled_shares": 0,
            "expiry_reason": "broker_rejection",
            "authorization_id": "rearm_" + "1" * 64,
        },
        "first_epoch": {
            "epoch_id": "epoch_" + "1" * 64,
            "grant_id": "grant_" + "1" * 64,
            "owner_symbol": "sh600487",
            "realized_status": "EXPIRED",
            "first_fill_session": "",
            "active_session": "",
            "closed_session": "2023-01-10",
            "close_reason": "broker_rejection",
        },
        "second_grant": {
            "grant_id": "grant_" + "2" * 64,
            "candidate_symbol": "sh601869",
            "previous_grant_id": "grant_" + "1" * 64,
            "authorization_id": "rearm_" + "2" * 64,
        },
        "second_epoch": {
            "epoch_id": "epoch_" + "2" * 64,
            "grant_id": "grant_" + "2" * 64,
            "owner_symbol": "sh601869",
            "previous_epoch_id": "epoch_" + "1" * 64,
            "first_fill_session": "2023-02-01",
            "active_session": "2023-02-01",
            "realized_status": "ACTIVE",
        },
        "target": {
            "target_id": "target_" + "3" * 64,
            "symbol": "sh601869",
            "weight": 0.2,
            "origin_subsystem": "STRATEGIC",
            "grant_id": "grant_" + "2" * 64,
            "epoch_id": "epoch_" + "2" * 64,
        },
        "order": {
            "order_id": "order_" + "4" * 64,
            "symbol": "sh601869",
            "side": "BUY",
            "target_weight": 0.2,
            "origin_subsystem": "STRATEGIC",
            "grant_id": "grant_" + "2" * 64,
            "epoch_id": "epoch_" + "2" * 64,
            "submitted_date": "2023-01-31",
        },
        "fill": {
            "fill_id": "fill_" + "5" * 64,
            "order_id": "order_" + "4" * 64,
            "symbol": "sh601869",
            "side": "BUY",
            "shares": 100,
            "origin_subsystem": "STRATEGIC",
            "grant_id": "grant_" + "2" * 64,
            "epoch_id": "epoch_" + "2" * 64,
            "fill_date": "2023-02-01",
        },
        "observations": observations,
    }


def _historical_crowning() -> dict[str, object]:
    return {
        "source_cell_id": "remove-sz300502",
        "epochs": [
            {
                "owner_symbol": "sh600487",
                "epoch_id": "epoch_" + "6" * 64,
                "grant_id": "grant_" + "6" * 64,
                "previous_epoch_id": "",
                "previous_grant_id": "",
                "qualification_signature": "qualification-1",
                "qualification_session": "2024-01-02",
                "grant_session": "2024-01-03",
                "fill_id": "fill_" + "6" * 64,
                "order_id": "order_" + "6" * 64,
                "fill_session": "2024-01-04",
                "fill_shares": 100,
                "exit_session": "2024-01-10",
            },
            {
                "owner_symbol": "sh601869",
                "epoch_id": "epoch_" + "7" * 64,
                "grant_id": "grant_" + "7" * 64,
                "previous_epoch_id": "epoch_" + "6" * 64,
                "previous_grant_id": "grant_" + "6" * 64,
                "qualification_signature": "qualification-2",
                "qualification_session": "2024-01-11",
                "grant_session": "2024-01-11",
                "fill_id": "fill_" + "7" * 64,
                "order_id": "order_" + "7" * 64,
                "fill_session": "2024-01-12",
                "fill_shares": 100,
                "exit_session": "2024-02-01",
            },
        ],
    }


def _terminal_scc() -> dict[str, object]:
    rows = []
    for offset in range(60):
        session = (date(2023, 3, 1) + timedelta(days=offset)).isoformat()
        predicates = [
            {"code": "QUALIFICATION_OPPORTUNITY", "satisfied": True},
            {"code": "REFERENCE_AVAILABLE", "satisfied": True},
        ]
        state = {
            "phase": "POST_DECISION",
            "predicate_results": predicates,
            "positive_strategic_target_weight": 0.0,
        }
        rows.append(
            {
                "session": session,
                "phase": "POST_DECISION",
                "edge_kind": "OBSERVED",
                "state_sha256": canonical_json_sha256(state),
                "predicate_results": predicates,
                "positive_strategic_target_weight": 0.0,
            }
        )
    return {"transitions": rows}


def _repair_bounds() -> list[dict[str, object]]:
    result = []
    for persisted, target, required in (
        (1, 0, 20),
        (2, 1, 40),
        (3, 2, 60),
        (4, 3, 60),
    ):
        observations = []
        start = date(2023, persisted * 2, 1)
        for offset in range(required):
            session = (start + timedelta(days=offset)).isoformat()
            predicates = [
                {"code": "FLAT_ALL_CASH", "satisfied": True},
                {"code": "NO_PENDING_EXECUTION", "satisfied": True},
            ]
            status = "READY" if offset == required - 1 else "ACCUMULATING"
            observations.append(
                {
                    "session": session,
                    "repair_status": status,
                    "predicate_results": predicates,
                    "state_sha256": canonical_json_sha256(
                        {
                            "session": session,
                            "repair_status": status,
                            "predicate_results": predicates,
                            "persisted_damage_level": persisted,
                            "target_budget_level": target,
                        }
                    ),
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
    return {
        "source_scenario_id": "cross-industry-production-semantic",
        "epochs": [
            {
                "owner_symbol": "sh600487",
                "epoch_id": "epoch_" + "8" * 64,
                "grant_id": "grant_" + "8" * 64,
                "previous_epoch_id": "",
                "previous_grant_id": "",
                "qualification_signature": "qualification-8",
                "qualification_session": "2024-03-01",
                "grant_session": "2024-03-01",
                "fill_id": "fill_" + "8" * 64,
                "order_id": "order_" + "8" * 64,
                "fill_session": "2024-03-02",
                "fill_shares": 100,
                "exit_session": "2024-03-10",
            },
            {
                "owner_symbol": "sh603688",
                "epoch_id": "epoch_" + "9" * 64,
                "grant_id": "grant_" + "9" * 64,
                "previous_epoch_id": "epoch_" + "8" * 64,
                "previous_grant_id": "grant_" + "8" * 64,
                "qualification_signature": "qualification-9",
                "qualification_session": "2024-03-11",
                "grant_session": "2024-03-11",
                "fill_id": "fill_" + "9" * 64,
                "order_id": "order_" + "9" * 64,
                "fill_session": "2024-03-12",
                "fill_shares": 100,
                "exit_session": "2024-03-20",
            },
        ],
    }


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
