from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from test_future_holdout_runtime import (
    _holdout_market_fixture,
    _install_holdout_contract,
    _save_boundary_order_account,
    _valid_replay,
)

from uquant.account import save_account
from uquant.data import DataStore
from uquant.engine import code_fingerprint
from uquant.types import (
    AccountState,
)
from uquant.validation import holdout_runtime as holdout_runtime_module
from uquant.validation.holdout import (
    HOLDOUT_DATA_DIRECTORY,
    HOLDOUT_START,
    LAST_IN_SAMPLE_DATE,
    load_future_holdout_contract,
)
from uquant.validation.holdout_runtime import (
    _observation_metrics,
    append_holdout_snapshot,
    generate_future_holdout_replay,
    read_future_holdout_replay,
    replay_future_holdout,
)


def test_daily_decision_is_fully_bound_and_semantically_read_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_holdout_contract(tmp_path)
    replay = _valid_replay()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: replay,
    )
    output = tmp_path / "artifacts/replay.json"
    decision_output = tmp_path / "artifacts/decision.json"

    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision_output,
    )
    decision = json.loads(decision_output.read_text(encoding="utf-8"))
    assert decision["contract_sha256"] == replay["contract_sha256"]
    assert decision["production_source_sha256"] == replay["production_source_sha256"]
    assert decision["holdout_data_sha256"] == replay["holdout_data_sha256"]
    assert decision["prior_close_account_sha256"] == replay[
        "prior_close_account_sha256"
    ]
    assert decision["replay_canonical_sha256"] == replay["canonical_sha256"]
    assert holdout_runtime_module.read_future_holdout_decision(
        decision_output,
        replay=replay,
    ) == decision

    decision["holdout_data_sha256"] = "d" * 64
    decision["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in decision.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    decision_output.write_text(json.dumps(decision), encoding="utf-8")
    with pytest.raises(ValueError, match="daily decision binding is stale"):
        holdout_runtime_module.read_future_holdout_decision(
            decision_output,
            replay=replay,
        )

def test_holdout_replay_readback_binds_data_sessions_scores_decisions_and_journal(
    tmp_path: Path,
) -> None:
    contract = load_future_holdout_contract()
    replay = _valid_replay()
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(replay), encoding="utf-8")

    observed = read_future_holdout_replay(
        path,
        contract=contract,
        sessions=(HOLDOUT_START,),
        holdout_data_sha256="a" * 64,
    )
    assert observed == replay

    tampered = dict(replay)
    tampered["scores"] = {**replay["scores"], "final_wealth": 99.0}
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="replay hash is invalid"):
        read_future_holdout_replay(
            path,
            contract=contract,
            sessions=(HOLDOUT_START,),
            holdout_data_sha256="a" * 64,
        )

    semantic_tamper = json.loads(json.dumps(replay))
    semantic_tamper["decisions"][0]["payload"]["date"] = "2026-08-07"
    semantic_tamper["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in semantic_tamper.items()
                if key != "canonical_sha256"
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(semantic_tamper), encoding="utf-8")
    with pytest.raises(ValueError, match="decisions are malformed"):
        read_future_holdout_replay(
            path,
            contract=contract,
            sessions=(HOLDOUT_START,),
            holdout_data_sha256="a" * 64,
        )

    stale_source = json.loads(json.dumps(replay))
    stale_source["production_source_sha256"] = "d" * 64
    stale_source["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in stale_source.items()
                if key != "canonical_sha256"
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(stale_source), encoding="utf-8")
    with pytest.raises(ValueError, match="source binding is stale"):
        read_future_holdout_replay(
            path,
            contract=contract,
            sessions=(HOLDOUT_START,),
            holdout_data_sha256="a" * 64,
        )

def test_manifest_scores_accept_only_the_exact_reexecuted_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract = load_future_holdout_contract()
    replay = _valid_replay()
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(replay), encoding="utf-8")
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: replay,
    )

    scores, identity = _observation_metrics(
        path,
        sessions=(HOLDOUT_START,),
        holdout_data_sha256="a" * 64,
        contract=contract,
        account_path=tmp_path / "account.json",
        repository_root=tmp_path,
    )

    assert scores == replay["scores"]
    assert identity == hashlib.sha256(path.read_bytes()).hexdigest()

    different = {**replay, "final_account_sha256": "d" * 64}
    different["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in different.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: different,
    )
    with pytest.raises(RuntimeError, match="differs from deterministic re-execution"):
        _observation_metrics(
            path,
            sessions=(HOLDOUT_START,),
            holdout_data_sha256="a" * 64,
            contract=contract,
            account_path=tmp_path / "account.json",
            repository_root=tmp_path,
        )

def test_holdout_replay_uses_the_production_next_open_then_decision_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen, snapshot, required = _holdout_market_fixture(tmp_path)

    account = AccountState.empty(500_000.0)
    account.cash = 1_000_000.0
    account.operating_peak = 1_000_000.0
    account.capital_peak = 1_000_000.0
    account.last_successful_run = LAST_IN_SAMPLE_DATE
    account.data_hash_as_of = LAST_IN_SAMPLE_DATE
    account.data_hash_symbols = list(required)
    account.data_hash = DataStore(frozen).manifest(
        required,
        as_of=LAST_IN_SAMPLE_DATE,
    ).digest
    account.code_hash = code_fingerprint()
    account_path = tmp_path / "account.json"
    save_account(account, account_path)
    append_holdout_snapshot(
        repository_root=tmp_path,
        snapshot_dir=snapshot,
        contract=load_future_holdout_contract(),
    )
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.validate_prior_close_account",
        lambda *_args, **_kwargs: None,
    )

    first = replay_future_holdout(
        repository_root=tmp_path,
        account_path=account_path,
        contract=load_future_holdout_contract(),
    )
    second = replay_future_holdout(
        repository_root=tmp_path,
        account_path=account_path,
        contract=load_future_holdout_contract(),
    )

    assert first == second
    assert first["schema_version"] == 2
    assert first["replay_id"] == "phase2-future-holdout-replay-v2"
    assert first["sessions"] == [HOLDOUT_START]
    assert first["score_status"] == "NON_REVIEWABLE"
    assert all(value is None for value in first["scores"].values())
    assert first["observed_metrics"]["final_wealth"] == pytest.approx(1.0)
    assert first["lane_binding"]["lane_id"] == "champion_pre_sentinel"
    assert first["lane_binding"]["activation_session"] == HOLDOUT_START
    assert len(first["decision_digests"]) == 1
    assert first["decisions"][0]["date"] == HOLDOUT_START
    assert first["milestones"]["fixed"] == [20, 40, 60]
    assert first["milestones"]["review_action"] == "REPORT_ONLY"

def test_holdout_replay_counts_prior_close_orders_filled_on_the_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen, snapshot, required = _holdout_market_fixture(
        tmp_path,
        future_volume=1_000_000.0,
    )
    account_path = _save_boundary_order_account(
        tmp_path,
        frozen=frozen,
        required=required,
    )
    append_holdout_snapshot(
        repository_root=tmp_path,
        snapshot_dir=snapshot,
        contract=load_future_holdout_contract(),
    )
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.validate_prior_close_account",
        lambda *_args, **_kwargs: None,
    )

    replay = replay_future_holdout(
        repository_root=tmp_path,
        account_path=account_path,
        contract=load_future_holdout_contract(),
    )

    assert all(value is None for value in replay["scores"].values())
    assert replay["observed_metrics"]["account_orders"] == 1
    assert replay["observed_metrics"]["gross_turnover"] > 0

def test_holdout_replay_uses_the_same_future_bytes_for_identity_and_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    frozen, snapshot, required = _holdout_market_fixture(
        control,
        future_volume=1_000_000.0,
    )
    account_path = _save_boundary_order_account(
        control,
        frozen=frozen,
        required=required,
    )
    append_holdout_snapshot(
        repository_root=control,
        snapshot_dir=snapshot,
        contract=load_future_holdout_contract(),
    )
    mutating = tmp_path / "mutating"
    shutil.copytree(control, mutating)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.validate_prior_close_account",
        lambda *_args, **_kwargs: None,
    )
    expected = replay_future_holdout(
        repository_root=control,
        account_path=account_path,
        contract=load_future_holdout_contract(),
    )

    future_path = (
        mutating
        / HOLDOUT_DATA_DIRECTORY
        / HOLDOUT_START
        / "sz300308.csv"
    )

    def mutate_after_identity(*_args: object, **_kwargs: object) -> None:
        rows = list(csv.reader(future_path.read_text(encoding="utf-8").splitlines()))
        header = rows[0]
        row = rows[1]
        close = float(row[header.index("close")]) * 2.0
        row[header.index("open")] = str(close)
        row[header.index("high")] = str(close + 1.0)
        row[header.index("low")] = str(close - 1.0)
        row[header.index("close")] = str(close)
        volume = float(row[header.index("volume")])
        row[header.index("amount")] = str(close * volume)
        future_path.write_text(
            "\n".join(",".join(item) for item in rows) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.validate_prior_close_account",
        mutate_after_identity,
    )
    observed = replay_future_holdout(
        repository_root=mutating,
        account_path=mutating / "account.json",
        contract=load_future_holdout_contract(),
    )

    assert observed == expected
