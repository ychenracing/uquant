from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from contextlib import redirect_stderr
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from uquant.contracts.strict_json import canonical_json_sha256
from uquant.risk_sentinel import cli as sentinel_cli
from uquant.risk_sentinel.validation import validate_contracts
from uquant.validation import generalization as generalization
from uquant.validation import generalization_reference as policy_module
from uquant.validation import holdout as holdout
from uquant.validation import holdout_runtime as runtime
from uquant.validation.execution_journal import read_execution_journal
from uquant.validation.holdout import HoldoutBinding
from uquant.validation.holdout_lanes import (
    build_lane_validation_report,
    lane_binding_payload,
    load_lane_registry,
    validate_lane_registry_transition,
)

VALIDATION_REFERENCE_COMMIT = "719288f6067686b3199d305899ddc09adf098a0d"
VALIDATION_REFERENCE_TREE = "459d592cb24c6cfed2082bfd2f7519a9badee67d"
LEGACY_PATHS = (
    "uquant/validation/generalization.py",
    "uquant/validation/generalization_reference.py",
    "uquant/validation/holdout.py",
    "uquant/validation/holdout_runtime.py",
    "uquant/validation/holdout_lanes.py",
    "uquant/risk_sentinel/cli.py",
    "uquant/risk_sentinel/validation.py",
)
_V1_JOURNAL_BYTES = (
    b'{"actual_price":null,"actual_shares":null,"actual_time":null,'
    b'"manual_skip":null,"next_open":null,"plan_id":"frozen-plan-1",'
    b'"planned_price":947.74,"planned_shares":100,'
    b'"previous_sha256":"0000000000000000000000000000000000000000000000000000000000000000",'
    b'"record_sha256":"625f4800c03588a453b1c137a49bf6f8ecc1f9480eb1e094049e1135ae8a5b40",'
    b'"recorded_at":"2026-08-05T15:01:00+08:00","schema_version":1,'
    b'"sequence":1,"side":"BUY","slippage_bps":null,'
    b'"slippage_per_share":null,"slippage_value":null,"status":"PLANNED",'
    b'"symbol":"sz300308"}\n'
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _artifact(value: object) -> dict[str, object]:
    encoded = _canonical_bytes(value)
    return {
        "payload": value,
        "canonical_size_bytes": len(encoded),
        "canonical_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _normalize_message(message: str, roots: tuple[Path, ...]) -> str:
    normalized = message
    for root in sorted(roots, key=lambda item: len(str(item)), reverse=True):
        normalized = normalized.replace(str(root), "<TMP>")
    return re.sub(r"(\.(?:rollback|claimed)-)[A-Za-z0-9_-]+", r"\1<TOKEN>", normalized)


def _failure(
    label: str,
    operation: Callable[[], object],
    *,
    roots: tuple[Path, ...] = (),
) -> dict[str, object]:
    try:
        operation()
    except BaseException as exc:
        return {
            "label": label,
            "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "message": _normalize_message(str(exc), roots),
            "notes": [
                _normalize_message(str(note), roots)
                for note in getattr(exc, "__notes__", ())
            ],
        }
    raise AssertionError(f"expected failure did not occur: {label}")


def _source_identity(root: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    files: list[dict[str, object]] = []
    for relative in LEGACY_PATHS:
        content = (root / relative).read_bytes()
        encoded = relative.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        files.append(
            {
                "path": relative,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    requirements = (root / "requirements.txt").read_bytes()
    return {
        "legacy_files": files,
        "legacy_frame_sha256": digest.hexdigest(),
        "requirements_size_bytes": len(requirements),
        "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
        "holdout_source_sha256": holdout.holdout_source_sha256(root),
        "sentinel_source_sha256": sentinel_cli.sentinel_source_fingerprint(root),
    }


def _generalization_success(root: Path) -> dict[str, object]:
    baseline = policy_module.load_generalization_baseline()
    policy = policy_module.load_generalization_policy()
    champion_path = root / "artifacts/phase2/champion-generalization-matrix.json"
    champion = json.loads(champion_path.read_text(encoding="utf-8"))
    evaluation = policy_module.evaluate_generalization_policy_artifact(
        champion,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
        data_dir=root / "data/frozen",
    )
    reference = {
        "final_wealth": 2.0,
        "max_drawdown": 0.10,
        "account_orders": 10,
        "gross_turnover": 1.0,
        "annual_turnover": 2.0,
    }
    regressed = {
        "final_wealth": 1.8,
        "max_drawdown": 0.13,
        "account_orders": 13,
        "gross_turnover": 1.2,
        "annual_turnover": 2.3,
    }
    tracked: dict[str, dict[str, object]] = {}
    for relative in (
        "benchmarks/ai_era_generalization_baseline.json",
        "benchmarks/ai_era_generalization_policy.json",
        "artifacts/phase2/champion-generalization-matrix.json",
    ):
        content = (root / relative).read_bytes()
        tracked[relative] = {
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return {
        "tracked_inputs": tracked,
        "baseline": {
            "sha256": baseline.sha256,
            "runner_head": baseline.runner_head,
            "runner_source_sha256": baseline.runner_source_sha256,
            "artifact_sha256": baseline.artifact_sha256,
            "artifact_size_bytes": baseline.artifact_size_bytes,
            "artifact_equality_sha256": baseline.artifact_equality_sha256,
            "attribution_neutral_equality_sha256": (
                baseline.attribution_neutral_equality_sha256
            ),
            "cells": len(baseline.cells),
            "economic_cells": sum(cell.economic for cell in baseline.cells.values()),
            "replay_error_cells": sum(
                cell.replay_error is not None for cell in baseline.cells.values()
            ),
        },
        "policy": {
            "schema_version": policy.schema_version,
            "policy_id": policy.policy_id,
            "sha256": policy.sha256,
            "baseline_sha256": policy.baseline_sha256,
            "random_seed_indexes": list(policy.random_seed_indexes),
            "random_pool_sizes": list(policy.random_pool_sizes),
            "windows": [list(item) for item in policy.windows],
        },
        "cell_gate_exact": list(
            policy_module.evaluate_cell_non_regression(
                reference, reference, policy=policy
            )
        ),
        "cell_gate_regressed": list(
            policy_module.evaluate_cell_non_regression(
                regressed, reference, policy=policy
            )
        ),
        "concentration": generalization.symbol_pnl_concentration(
            {"a": 10.0, "b": -2.0, "c": 5.0}
        ),
        "champion_evaluation": _artifact(evaluation),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, allow_nan=False, sort_keys=True), encoding="utf-8")


def _reseal_policy(value: dict[str, Any]) -> None:
    unsigned = {key: item for key, item in value.items() if key != "canonical_sha256"}
    value["canonical_sha256"] = canonical_json_sha256(unsigned)


def _generalization_failures(root: Path, temporary: Path) -> list[dict[str, object]]:
    policy_path = root / "benchmarks/ai_era_generalization_policy.json"
    policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
    failures: list[dict[str, object]] = []

    missing = copy.deepcopy(policy_payload)
    missing.pop("relative_per_cell")
    missing_path = temporary / "policy-missing.json"
    _write_json(missing_path, missing)
    failures.append(
        _failure(
            "generalization_policy_missing_field",
            lambda: policy_module.load_generalization_policy(missing_path),
            roots=(temporary,),
        )
    )

    unknown = copy.deepcopy(policy_payload)
    unknown["unreviewed"] = True
    unknown_path = temporary / "policy-unknown.json"
    _write_json(unknown_path, unknown)
    failures.append(
        _failure(
            "generalization_policy_unknown_field",
            lambda: policy_module.load_generalization_policy(unknown_path),
            roots=(temporary,),
        )
    )

    mismatched = copy.deepcopy(policy_payload)
    mismatched["canonical_sha256"] = "0" * 64
    mismatch_path = temporary / "policy-seal.json"
    _write_json(mismatch_path, mismatched)
    failures.append(
        _failure(
            "generalization_policy_seal_mismatch",
            lambda: policy_module.load_generalization_policy(mismatch_path),
            roots=(temporary,),
        )
    )

    resealed = copy.deepcopy(policy_payload)
    resealed["relative_per_cell"]["wealth_ratio_min"] = 0.94
    _reseal_policy(resealed)
    resealed_path = temporary / "policy-resealed.json"
    _write_json(resealed_path, resealed)
    failures.append(
        _failure(
            "generalization_policy_resealed_threshold",
            lambda: policy_module.load_generalization_policy(resealed_path),
            roots=(temporary,),
        )
    )

    nonfinite_path = temporary / "policy-nonfinite.json"
    nonfinite_path.write_text(
        policy_path.read_text(encoding="utf-8").replace("0.95", "NaN", 1),
        encoding="utf-8",
    )
    failures.append(
        _failure(
            "generalization_policy_nonfinite_number",
            lambda: policy_module.load_generalization_policy(nonfinite_path),
            roots=(temporary,),
        )
    )
    failures.append(
        _failure(
            "generalization_metric_nonfinite",
            lambda: generalization.symbol_pnl_concentration({"a": float("inf")}),
        )
    )
    return failures


def _binding(root: Path) -> HoldoutBinding:
    contract = holdout.load_future_holdout_contract()
    return HoldoutBinding(
        production_commit="1" * 40,
        production_source_sha256=holdout.holdout_source_sha256(root),
        strategy_source_sha256=contract.strategy_source_sha256,
        strategy_cli_sha256=contract.strategy_cli_sha256,
        effective_config_sha256=contract.strategy_config_sha256,
        universe_sha256="4" * 64,
        industry_sha256="5" * 64,
        python_full_version="3.12.13",
        numpy_version="2.5.1",
        pandas_version="3.0.5",
        uv_version="0.11.33",
        uv_lock_sha256="6" * 64,
    )


def _account() -> dict[str, object]:
    return {
        "last_successful_run": holdout.LAST_IN_SAMPLE_DATE,
        "data_hash_as_of": holdout.LAST_IN_SAMPLE_DATE,
        "cash": 1_000_000.0,
        "positions": {
            "sz300308": {
                "shares": 100,
                "tranches": [
                    {"shares": 100, "sellable_date": holdout.HOLDOUT_START}
                ],
            }
        },
        "pending_orders": [
            {
                "signal_date": holdout.LAST_IN_SAMPLE_DATE,
                "symbol": "sz300308",
                "side": "SELL",
            }
        ],
        "risk": "NORMAL",
        "risk_streaks": {"caution": 0},
    }


def _decision_record(session: str) -> tuple[str, dict[str, object]]:
    payload: dict[str, object] = {
        "schema": "uquant.decision-control-plane.v2",
        "date": session,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return digest, {
        "date": session,
        "decision_digest": digest,
        "payload": payload,
    }


def _valid_replay(root: Path, sessions: tuple[str, ...]) -> dict[str, Any]:
    contract = holdout.load_future_holdout_contract()
    decisions = [_decision_record(session) for session in sessions]
    observed_metrics = {
        "final_wealth": 1.0,
        "max_drawdown": 0.0,
        "account_orders": 0,
        "gross_turnover": 0.0,
        "top1_concentration": 0.0,
        "top3_concentration": 0.0,
        "pnl_hhi": 0.0,
    }
    reached = [
        value for value in contract.review_milestones if len(sessions) >= value
    ]
    next_milestone = next(
        (value for value in contract.review_milestones if value > len(sessions)),
        None,
    )
    replay: dict[str, Any] = {
        "schema_version": 2,
        "replay_id": "phase2-future-holdout-replay-v2",
        "contract_sha256": contract.sha256,
        "production_source_sha256": holdout.holdout_source_sha256(root),
        "holdout_data_sha256": "a" * 64,
        "prior_close_account_sha256": contract.prior_close_account_sha256,
        "sessions": list(sessions),
        "lane_binding": lane_binding_payload(
            load_lane_registry(root / "benchmarks/future_holdout_lane_registry.json")[0]
        ),
        "decision_digests": [digest for digest, _ in decisions],
        "decisions": [decision for _, decision in decisions],
        "journal_checkpoint": {
            "schema_version": 1,
            "sequence": 0,
            "record_sha256": "0" * 64,
        },
        "milestones": {
            "fixed": list(contract.review_milestones),
            "reached": reached,
            "next": next_milestone,
            "review_action": "REPORT_ONLY",
        },
        "score_status": (
            f"MILESTONE_{reached[-1]}_REVIEWABLE" if reached else "NON_REVIEWABLE"
        ),
        "observed_metrics": observed_metrics,
        "scores": {
            field: (observed_metrics[field] if reached else None)
            for field in contract.score_fields
        },
        "final_account_sha256": "c" * 64,
    }
    replay["canonical_sha256"] = holdout._canonical_sha256(replay)
    return replay


def _snapshot_fixture() -> runtime._HoldoutDataSnapshot:
    sessions = ("2026-08-06", "2026-08-07")
    files: list[tuple[str, bytes]] = []
    for session in sessions:
        for name, close in (("a.csv", 10), ("b.csv", 20)):
            content = (
                "date,open,high,low,close,volume\n"
                f"{session},{close},{close + 1},{close - 1},{close},100\n"
            ).encode()
            files.append((f"{session}/{name}", content))
    frozen = tuple(files)
    return runtime._HoldoutDataSnapshot(
        sessions=sessions,
        sha256=runtime._snapshot_files_sha256(frozen),
        files=frozen,
    )


def _transaction_success(temporary: Path) -> dict[str, object]:
    root = temporary / "transaction"
    root.mkdir()
    carrier = root / "carrier.json"
    original = b"original\n"
    owned = b"owned-generation\n"
    successor = b"successor\n"

    carrier.write_bytes(owned)
    runtime._restore_owned_artifact(carrier, original, owned)
    restored = carrier.read_bytes()

    carrier.write_bytes(successor)
    runtime._restore_owned_artifact(carrier, original, owned)
    preserved = carrier.read_bytes()

    linked = root / "linked.json"
    first_link = runtime._link_bytes_if_absent(linked, owned)
    second_link = runtime._link_bytes_if_absent(linked, successor)
    return {
        "restored_sha256": hashlib.sha256(restored).hexdigest(),
        "successor_preserved_sha256": hashlib.sha256(preserved).hexdigest(),
        "link_first": first_link,
        "link_second": second_link,
        "linked_sha256": hashlib.sha256(linked.read_bytes()).hexdigest(),
    }


def _holdout_success(root: Path, temporary: Path) -> dict[str, object]:
    contract = holdout.load_future_holdout_contract()
    lanes = load_lane_registry(root / "benchmarks/future_holdout_lane_registry.json")
    sessions = contract.review_sessions[:9]
    manifest = holdout._assemble_future_holdout_manifest(
        contract=contract,
        binding=_binding(root),
        account_payload=_account(),
        holdout_sessions=sessions,
        holdout_data_sha256="a" * 64,
        metrics_sha256="b" * 64,
    )
    holdout._validate_future_holdout_manifest_payload(manifest, expected=manifest)
    lane_report = build_lane_validation_report(
        lanes=lanes,
        contract=contract,
        observed_sessions=sessions,
        holdout_data_sha256="d" * 64,
    )
    snapshot = _snapshot_fixture()
    prefix_sha256 = runtime._validated_snapshot_prefix_sha256(
        snapshot,
        prefix_sessions=snapshot.sessions[:1],
    )
    replay = _valid_replay(root, contract.review_sessions[:1])
    replay_path = temporary / "replay.json"
    replay_bytes = _canonical_bytes(replay)
    replay_path.write_bytes(replay_bytes)
    assert runtime.read_future_holdout_replay(
        replay_path,
        contract=contract,
        sessions=contract.review_sessions[:1],
        holdout_data_sha256="a" * 64,
    ) == replay
    decision = runtime._daily_decision_payload(replay)
    decision_bytes = _canonical_bytes(decision)
    checkpoint = runtime._checkpoint_payload(
        replay,
        replay_output_path="/uquant-task9/replay.json",
        replay_output_bytes=replay_bytes,
        decision_output_path="/uquant-task9/decision.json",
        decision_output_bytes=decision_bytes,
    )
    runtime._validate_daily_replay_continuity(
        replay,
        prior_checkpoint=None,
        contract=contract,
    )
    legacy_path = temporary / "journal-v1.jsonl"
    legacy_path.write_bytes(_V1_JOURNAL_BYTES)
    legacy = read_execution_journal(legacy_path)
    tracked: dict[str, dict[str, object]] = {}
    for relative in (
        "benchmarks/future_holdout_contract.json",
        "benchmarks/future_holdout_lane_registry.json",
    ):
        content = (root / relative).read_bytes()
        tracked[relative] = {
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    contract_payload = {
        field.name: getattr(contract, field.name) for field in fields(contract)
    }
    contract_payload["phase1_windows"] = dict(
        contract_payload.pop("performance_windows")
    )
    return {
        "tracked_inputs": tracked,
        "contract": contract_payload,
        "lanes": [asdict(lane) for lane in lanes],
        "manifest": _artifact(manifest),
        "lane_report": _artifact(lane_report),
        "snapshot": {
            "sessions": list(snapshot.sessions),
            "files": [
                {
                    "relative": relative,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
                for relative, content in snapshot.files
            ],
            "sha256": snapshot.sha256,
            "prefix_sha256": prefix_sha256,
        },
        "replay": _artifact(replay),
        "daily_decision": _artifact(decision),
        "checkpoint": _artifact(checkpoint),
        "transaction": _transaction_success(temporary),
        "journal_v1": {
            "input_sha256": hashlib.sha256(_V1_JOURNAL_BYTES).hexdigest(),
            "records": [asdict(record) for record in legacy],
        },
    }


def _reseal_holdout(value: dict[str, Any]) -> None:
    unsigned = {key: item for key, item in value.items() if key != "canonical_sha256"}
    value["canonical_sha256"] = holdout._canonical_sha256(unsigned)


def _write_replay(path: Path, value: dict[str, Any]) -> None:
    _reseal_holdout(value)
    _write_json(path, value)


def _transaction_failure(temporary: Path) -> None:
    root = temporary / "transaction-failure"
    root.mkdir()
    carrier = root / "carrier.json"
    original = b"original\n"
    owned = b"owned-generation\n"
    carrier.write_bytes(owned)
    def unavailable_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("forced recovery link failure")

    with patch.object(os, "link", unavailable_link):
        runtime._restore_owned_artifact(carrier, original, owned)


def _holdout_failures(root: Path, temporary: Path) -> list[dict[str, object]]:
    contract = holdout.load_future_holdout_contract()
    registry = root / "benchmarks/future_holdout_lane_registry.json"
    lanes = load_lane_registry(registry)
    failures: list[dict[str, object]] = []

    manifest = holdout._assemble_future_holdout_manifest(
        contract=contract,
        binding=_binding(root),
        account_payload=_account(),
        holdout_sessions=contract.review_sessions[:1],
        holdout_data_sha256="a" * 64,
        metrics_sha256="b" * 64,
    )
    manifest_unknown = copy.deepcopy(manifest)
    manifest_unknown["unknown"] = True
    _reseal_holdout(manifest_unknown)
    failures.append(
        _failure(
            "holdout_manifest_unknown_field",
            lambda: holdout._validate_future_holdout_manifest_payload(
                manifest_unknown, expected=manifest
            ),
        )
    )
    manifest_parameter = copy.deepcopy(manifest)
    manifest_parameter["observation"]["parameter_changes_from_observation"] = True
    _reseal_holdout(manifest_parameter)
    failures.append(
        _failure(
            "holdout_manifest_parameter_change_first",
            lambda: holdout._validate_future_holdout_manifest_payload(
                manifest_parameter, expected=manifest
            ),
        )
    )
    manifest_seal = copy.deepcopy(manifest)
    manifest_seal["canonical_sha256"] = "0" * 64
    failures.append(
        _failure(
            "holdout_manifest_seal_mismatch",
            lambda: holdout._validate_future_holdout_manifest_payload(
                manifest_seal, expected=manifest
            ),
        )
    )
    stale_manifest = copy.deepcopy(manifest)
    stale_manifest["production"]["commit"] = "2" * 40
    _reseal_holdout(stale_manifest)
    failures.append(
        _failure(
            "holdout_manifest_stale_identity",
            lambda: holdout._validate_future_holdout_manifest_payload(
                stale_manifest, expected=manifest
            ),
        )
    )
    bad_binding = replace(_binding(root), strategy_cli_sha256="0" * 64)
    failures.append(
        _failure(
            "holdout_manifest_cli_identity_mismatch",
            lambda: holdout._assemble_future_holdout_manifest(
                contract=contract,
                binding=bad_binding,
                account_payload=_account(),
                holdout_sessions=(),
                holdout_data_sha256="a" * 64,
            ),
        )
    )
    bad_account = _account()
    bad_account["last_successful_run"] = "2026-08-04"
    failures.append(
        _failure(
            "holdout_manifest_account_identity_mismatch",
            lambda: holdout._assemble_future_holdout_manifest(
                contract=contract,
                binding=_binding(root),
                account_payload=bad_account,
                holdout_sessions=(),
                holdout_data_sha256="a" * 64,
            ),
        )
    )

    failures.append(
        _failure(
            "holdout_lane_deleted",
            lambda: validate_lane_registry_transition(lanes, lanes[:-1], contract),
        )
    )
    changed_lane = replace(lanes[0], source_commit="0" * 40)
    failures.append(
        _failure(
            "holdout_lane_identity_changed",
            lambda: validate_lane_registry_transition(
                lanes, (changed_lane, *lanes[1:]), contract
            ),
        )
    )
    old_lane = replace(lanes[0], status="MILESTONE_20")
    failures.append(
        _failure(
            "holdout_lane_status_regressed",
            lambda: validate_lane_registry_transition(
                (old_lane, *lanes[1:]), lanes, contract
            ),
        )
    )
    observed = contract.review_sessions[:9]
    appended = replace(
        lanes[-1],
        lane_id="post_observation_candidate",
        activation_session=observed[-1],
        parent_lane=lanes[-1].lane_id,
    )
    failures.append(
        _failure(
            "holdout_lane_backfill",
            lambda: validate_lane_registry_transition(
                lanes,
                (*lanes, appended),
                contract,
                observed_sessions=observed,
            ),
        )
    )
    failures.append(
        _failure(
            "holdout_observed_sessions_regressive",
            lambda: validate_lane_registry_transition(
                lanes,
                lanes,
                contract,
                observed_sessions=(
                    contract.review_sessions[1],
                    contract.review_sessions[0],
                ),
            ),
        )
    )

    replay = _valid_replay(root, contract.review_sessions[:1])
    replay_path = temporary / "failure-replay.json"

    source_mismatch = copy.deepcopy(replay)
    source_mismatch["production_source_sha256"] = "0" * 64
    _write_replay(replay_path, source_mismatch)
    failures.append(
        _failure(
            "holdout_replay_source_identity_mismatch",
            lambda: runtime.read_future_holdout_replay(
                replay_path,
                contract=contract,
                sessions=contract.review_sessions[:1],
                holdout_data_sha256="a" * 64,
            ),
            roots=(temporary,),
        )
    )

    data_mismatch = copy.deepcopy(replay)
    _write_replay(replay_path, data_mismatch)
    failures.append(
        _failure(
            "holdout_replay_data_identity_mismatch",
            lambda: runtime.read_future_holdout_replay(
                replay_path,
                contract=contract,
                sessions=contract.review_sessions[:1],
                holdout_data_sha256="f" * 64,
            ),
            roots=(temporary,),
        )
    )

    lane_mismatch = copy.deepcopy(replay)
    lane_mismatch["lane_binding"]["lane_id"] = "unknown_lane"
    _write_replay(replay_path, lane_mismatch)
    failures.append(
        _failure(
            "holdout_replay_lane_binding_mismatch",
            lambda: runtime.read_future_holdout_replay(
                replay_path,
                contract=contract,
                sessions=contract.review_sessions[:1],
                holdout_data_sha256="a" * 64,
            ),
            roots=(temporary,),
        )
    )

    journal_mismatch = copy.deepcopy(replay)
    journal_mismatch["journal_checkpoint"]["record_sha256"] = "short"
    _write_replay(replay_path, journal_mismatch)
    failures.append(
        _failure(
            "holdout_replay_journal_mismatch",
            lambda: runtime.read_future_holdout_replay(
                replay_path,
                contract=contract,
                sessions=contract.review_sessions[:1],
                holdout_data_sha256="a" * 64,
            ),
            roots=(temporary,),
        )
    )

    duplicate = copy.deepcopy(replay)
    duplicate["sessions"] = [
        contract.review_sessions[0],
        contract.review_sessions[0],
    ]
    duplicate["decision_digests"] *= 2
    duplicate["decisions"] *= 2
    failures.append(
        _failure(
            "holdout_replay_duplicate_sessions",
            lambda: runtime._validate_daily_replay_continuity(
                duplicate, prior_checkpoint=None, contract=contract
            ),
        )
    )
    skipped = _valid_replay(root, contract.review_sessions[:3])
    prior_checkpoint = {
        "sessions": [contract.review_sessions[0]],
        "decision_digests": replay["decision_digests"],
        "holdout_data_sha256": replay["holdout_data_sha256"],
    }
    failures.append(
        _failure(
            "holdout_checkpoint_discontinuity",
            lambda: runtime._validate_daily_replay_continuity(
                skipped,
                prior_checkpoint=prior_checkpoint,
                contract=contract,
            ),
        )
    )
    protected = temporary / "protected-data"
    protected.mkdir()
    failures.append(
        _failure(
            "holdout_unsafe_output_boundary",
            lambda: runtime._reject_output_in_protected_data(
                protected / "artifact.json",
                protected_directories=(protected,),
            ),
            roots=(temporary,),
        )
    )
    failures.append(
        _failure(
            "holdout_transaction_recovery_failure",
            lambda: _transaction_failure(temporary),
            roots=(temporary,),
        )
    )
    return failures


def _sentinel_cli_failure() -> object:
    previous = sys.argv[0]
    sys.argv[0] = "uquant-risk-sentinel"
    stderr = io.StringIO()
    try:
        with redirect_stderr(stderr):
            try:
                return sentinel_cli.main(
                    ["--validate-contracts", "--date", "2026-08-05"]
                )
            except BaseException as exc:
                exc.add_note(f"stderr: {stderr.getvalue()}")
                raise
    finally:
        sys.argv[0] = previous


def _sentinel_success(root: Path) -> dict[str, object]:
    previous = sys.argv[0]
    sys.argv[0] = "uquant-risk-sentinel"
    try:
        help_text = sentinel_cli._parser().format_help()
    finally:
        sys.argv[0] = previous
    return {
        "source_fingerprint": sentinel_cli.sentinel_source_fingerprint(root),
        "validation": validate_contracts(root),
        "cli_help": {
            "text": help_text,
            "size_bytes": len(help_text.encode()),
            "sha256": hashlib.sha256(help_text.encode()).hexdigest(),
        },
    }


def _sentinel_failures(temporary: Path) -> list[dict[str, object]]:
    missing = temporary / "sentinel-missing"
    missing.mkdir()
    isolated = temporary / "sentinel-isolation"
    package = isolated / "uquant/risk_sentinel"
    package.mkdir(parents=True)
    (package / "evil.py").write_text("import uquant.engine\n", encoding="utf-8")
    return [
        _failure(
            "sentinel_source_package_missing",
            lambda: sentinel_cli.sentinel_source_fingerprint(missing),
            roots=(temporary,),
        ),
        _failure(
            "sentinel_import_isolation_violation",
            lambda: validate_contracts(isolated),
            roots=(temporary,),
        ),
        _failure(
            "sentinel_cli_validation_argument_conflict",
            _sentinel_cli_failure,
        ),
    ]


def build_validation_oracle(root: Path) -> dict[str, object]:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix="uquant-validation-oracle-") as raw_temporary:
        temporary = Path(raw_temporary)
        generalization_success = _generalization_success(root)
        holdout_success = _holdout_success(root, temporary)
        sentinel_success = _sentinel_success(root)
        failures = [
            *_generalization_failures(root, temporary),
            *_holdout_failures(root, temporary),
            *_sentinel_failures(temporary),
        ]
    payload: dict[str, object] = {
        "baseline_commit": VALIDATION_REFERENCE_COMMIT,
        "baseline_tree": VALIDATION_REFERENCE_TREE,
        "contract": "uquant-task9-validation-contract-oracle-v1",
        "source_identity": _source_identity(root),
        "success": {
            "generalization": generalization_success,
            "holdout": holdout_success,
            "sentinel": sentinel_success,
        },
        "failure_order": failures,
        "coverage": {
            "success_bundles": [
                "baseline",
                "policy",
                "champion_generalization_artifact",
                "manifest",
                "lane_report",
                "daily_snapshot",
                "replay",
                "daily_decision",
                "checkpoint",
                "artifact_transaction",
                "journal_v1",
                "sentinel_validation",
                "sentinel_cli_help",
            ],
            "failure_labels": [str(row["label"]) for row in failures],
        },
    }
    payload = cast(
        dict[str, object],
        json.loads(json.dumps(payload, allow_nan=False, sort_keys=True)),
    )
    payload["success_sha256"] = canonical_json_sha256(payload["success"])
    payload["failure_order_sha256"] = canonical_json_sha256(payload["failure_order"])
    payload["payload_sha256"] = canonical_json_sha256(payload)
    return payload


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: _validation_oracle.py REPOSITORY_ROOT")
    payload = build_validation_oracle(Path(sys.argv[1]))
    print(json.dumps(payload, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "LEGACY_PATHS",
    "VALIDATION_REFERENCE_COMMIT",
    "VALIDATION_REFERENCE_TREE",
    "build_validation_oracle",
)
