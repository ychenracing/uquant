from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from uquant.account import save_account
from uquant.data import DataStore
from uquant.engine import INDEX_SYMBOLS, code_fingerprint
from uquant.execution_journal import append_planned
from uquant.leader import REFERENCE_UNIVERSE
from uquant.types import (
    AccountOrder,
    AccountState,
    AttributionMechanism,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    derive_attribution_event_id,
)
from uquant.validation import holdout_runtime as holdout_runtime_module
from uquant.validation.holdout import (
    HOLDOUT_DATA_DIRECTORY,
    HOLDOUT_START,
    LAST_IN_SAMPLE_DATE,
    _observation_metrics,
    holdout_source_sha256,
    load_future_holdout_contract,
)
from uquant.validation.holdout_lanes import lane_binding_payload, load_lane_registry
from uquant.validation.holdout_runtime import (
    append_holdout_snapshot,
    generate_future_holdout_replay,
    read_future_holdout_replay,
    replay_future_holdout,
)
from uquant.validation.universe import load_ai_universe


def _csv(
    path: Path,
    date: str,
    close: float = 10.0,
    *,
    volume: float = 100.0,
) -> bytes:
    content = (
        "date,open,high,low,close,volume,amount\n"
        f"{date},{close},{close + 1},{close - 1},{close},{volume},{close * volume}\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


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


def _install_holdout_contract(root: Path) -> None:
    destination = root / "benchmarks/future_holdout_contract.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(
        (Path(__file__).parents[1] / "benchmarks/future_holdout_contract.json").read_bytes()
    )


def _valid_replay(session: str = HOLDOUT_START) -> dict[str, object]:
    contract = load_future_holdout_contract()
    decision_digest, decision = _decision_record(session)
    observed_metrics = {
        "final_wealth": 1.0,
        "max_drawdown": 0.0,
        "account_orders": 0,
        "gross_turnover": 0.0,
        "top1_concentration": 0.0,
        "top3_concentration": 0.0,
        "pnl_hhi": 0.0,
    }
    replay: dict[str, object] = {
        "schema_version": 2,
        "replay_id": "phase2-future-holdout-replay-v2",
        "contract_sha256": contract.sha256,
        "production_source_sha256": holdout_source_sha256(Path(__file__).parents[1]),
        "holdout_data_sha256": "a" * 64,
        "prior_close_account_sha256": contract.prior_close_account_sha256,
        "sessions": [session],
        "lane_binding": lane_binding_payload(
            load_lane_registry(
                Path(__file__).parents[1]
                / "benchmarks/future_holdout_lane_registry.json"
            )[0]
        ),
        "decision_digests": [decision_digest],
        "decisions": [decision],
        "journal_checkpoint": {
            "schema_version": 1,
            "sequence": 0,
            "record_sha256": "0" * 64,
        },
        "milestones": {
            "fixed": [20, 40, 60],
            "reached": [],
            "next": 20,
            "review_action": "REPORT_ONLY",
        },
        "score_status": "NON_REVIEWABLE",
        "observed_metrics": observed_metrics,
        "scores": {field: None for field in observed_metrics},
        "final_account_sha256": "c" * 64,
    }
    replay["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            replay,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return replay


def _holdout_market_fixture(
    root: Path,
    *,
    future_volume: float = 100.0,
) -> tuple[Path, Path, tuple[str, ...]]:
    frozen = root / "data/frozen"
    snapshot = root / "snapshot"
    sessions = pd.bdate_range(end=LAST_IN_SAMPLE_DATE, periods=280)
    required = tuple(
        sorted(
            set(load_ai_universe().symbols)
            | set(REFERENCE_UNIVERSE)
            | set(INDEX_SYMBOLS)
        )
    )
    for offset, symbol in enumerate(required):
        close = 10.0 + offset / 100.0
        rows = ["date,open,high,low,close,volume,amount"]
        rows.extend(
            f"{session.date().isoformat()},{close},{close + 0.1},{close - 0.1},"
            f"{close},1000000,{close * 1000000}"
            for session in sessions
        )
        path = frozen / f"{symbol}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        _csv(
            snapshot / f"{symbol}.csv",
            HOLDOUT_START,
            close,
            volume=future_volume,
        )
    return frozen, snapshot, required


def _save_boundary_order_account(
    root: Path,
    *,
    frozen: Path,
    required: tuple[str, ...],
) -> Path:
    universe = load_ai_universe()
    symbol = "sz300308"
    target_weight = 0.10
    lifecycle = "CORE"
    origin = OriginSubsystem.LEADER.value
    mechanism = AttributionMechanism.LEADER_SELECTION.value
    industry = universe.industry_of(symbol, LAST_IN_SAMPLE_DATE)
    event_id = derive_attribution_event_id(
        signal_date=LAST_IN_SAMPLE_DATE,
        symbol=symbol,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_lifecycle=lifecycle,
        origin_subsystem=origin,
        mechanism=mechanism,
        replaces_symbol=None,
        industry_at_entry=industry,
        industry_manifest_sha256=universe.sha256,
        reduction_policy="FIFO",
        reason_code="strategy_target",
        exit_kind="strategy",
    )
    identity = {
        "event_id": event_id,
        "origin_subsystem": origin,
        "mechanism": mechanism,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": industry,
        "industry_manifest_sha256": universe.sha256,
    }
    pending = PendingOrder(
        signal_date=LAST_IN_SAMPLE_DATE,
        symbol=symbol,
        side="BUY",
        target_weight=target_weight,
        reason="confirmed mature leader core",
        lifecycle=lifecycle,
        order_id="O000000001",
        **identity,
    )
    ledger = AccountOrder(
        order_id=pending.order_id,
        signal_date=pending.signal_date,
        submitted_date=pending.signal_date,
        symbol=symbol,
        side=pending.side,
        target_weight=target_weight,
        reason=pending.reason,
        lifecycle=lifecycle,
        status=OrderStatus.OPEN.value,
        **identity,
    )
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=1_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )
    account.last_successful_run = LAST_IN_SAMPLE_DATE
    account.data_hash_as_of = LAST_IN_SAMPLE_DATE
    account.data_hash_symbols = list(required)
    account.data_hash = DataStore(frozen).manifest(
        required,
        as_of=LAST_IN_SAMPLE_DATE,
    ).digest
    account.code_hash = code_fingerprint()
    account_path = root / "account.json"
    save_account(account, account_path)
    return account_path


def test_daily_holdout_append_is_atomic_idempotent_and_never_edits_frozen_prefix(
    tmp_path: Path,
) -> None:
    frozen = tmp_path / "data/frozen"
    frozen_bytes = {
        name: _csv(frozen / name, LAST_IN_SAMPLE_DATE)
        for name in ("sh000300.csv", "sh000682.csv", "sz300308.csv")
    }
    snapshot = tmp_path / "snapshot"
    snapshot_bytes = {
        name: _csv(snapshot / name, HOLDOUT_START, 11.0)
        for name in frozen_bytes
    }

    result = append_holdout_snapshot(
        repository_root=tmp_path,
        snapshot_dir=snapshot,
        contract=load_future_holdout_contract(),
    )

    destination = tmp_path / HOLDOUT_DATA_DIRECTORY / HOLDOUT_START
    assert result == {
        "session": HOLDOUT_START,
        "files": 3,
        "idempotent": False,
    }
    assert {
        path.name: path.read_bytes() for path in destination.iterdir()
    } == snapshot_bytes
    assert {
        path.name: path.read_bytes() for path in frozen.iterdir()
    } == frozen_bytes

    repeated = append_holdout_snapshot(
        repository_root=tmp_path,
        snapshot_dir=snapshot,
        contract=load_future_holdout_contract(),
    )
    assert repeated["idempotent"] is True

    _csv(snapshot / "sz300308.csv", HOLDOUT_START, 12.0)
    with pytest.raises(ValueError, match="conflicts with the immutable daily append"):
        append_holdout_snapshot(
            repository_root=tmp_path,
            snapshot_dir=snapshot,
            contract=load_future_holdout_contract(),
        )
    assert {
        path.name: path.read_bytes() for path in frozen.iterdir()
    } == frozen_bytes


def test_daily_holdout_append_requires_one_complete_increasing_session(
    tmp_path: Path,
) -> None:
    frozen = tmp_path / "data/frozen"
    for name in ("sh000300.csv", "sh000682.csv", "sz300308.csv"):
        _csv(frozen / name, LAST_IN_SAMPLE_DATE)
    snapshot = tmp_path / "snapshot"
    for name in ("sh000300.csv", "sh000682.csv"):
        _csv(snapshot / name, HOLDOUT_START)

    with pytest.raises(ValueError, match="exactly match the frozen CSV inventory"):
        append_holdout_snapshot(
            repository_root=tmp_path,
            snapshot_dir=snapshot,
            contract=load_future_holdout_contract(),
        )

    _csv(snapshot / "sz300308.csv", "2026-08-07")
    with pytest.raises(ValueError, match="one common session"):
        append_holdout_snapshot(
            repository_root=tmp_path,
            snapshot_dir=snapshot,
            contract=load_future_holdout_contract(),
        )


def test_daily_holdout_append_requires_the_contracted_exchange_session_prefix(
    tmp_path: Path,
) -> None:
    contract = load_future_holdout_contract()
    frozen = tmp_path / "data/frozen"
    snapshot = tmp_path / "snapshot"
    _csv(frozen / "sh000300.csv", LAST_IN_SAMPLE_DATE)
    _csv(snapshot / "sh000300.csv", contract.review_sessions[1])

    with pytest.raises(ValueError, match="next contracted exchange session"):
        append_holdout_snapshot(
            repository_root=tmp_path,
            snapshot_dir=snapshot,
            contract=contract,
        )

    _csv(snapshot / "sh000300.csv", contract.review_sessions[0])
    append_holdout_snapshot(
        repository_root=tmp_path,
        snapshot_dir=snapshot,
        contract=contract,
    )
    _csv(snapshot / "sh000300.csv", contract.review_sessions[2])

    with pytest.raises(ValueError, match="next contracted exchange session"):
        append_holdout_snapshot(
            repository_root=tmp_path,
            snapshot_dir=snapshot,
            contract=contract,
        )
    assert not (
        tmp_path / HOLDOUT_DATA_DIRECTORY / contract.review_sessions[2]
    ).exists()


def test_daily_holdout_append_cannot_skip_the_prior_daily_replay(
    tmp_path: Path,
) -> None:
    contract = load_future_holdout_contract()
    frozen = tmp_path / "data/frozen"
    snapshot = tmp_path / "snapshot"
    _csv(frozen / "sh000300.csv", LAST_IN_SAMPLE_DATE)
    _csv(snapshot / "sh000300.csv", contract.review_sessions[0])
    append_holdout_snapshot(
        repository_root=tmp_path,
        snapshot_dir=snapshot,
        contract=contract,
    )

    _csv(snapshot / "sh000300.csv", contract.review_sessions[1])
    with pytest.raises(ValueError, match="prior daily replay checkpoint"):
        append_holdout_snapshot(
            repository_root=tmp_path,
            snapshot_dir=snapshot,
            contract=contract,
        )
    assert not (
        tmp_path / HOLDOUT_DATA_DIRECTORY / contract.review_sessions[1]
    ).exists()


@pytest.mark.parametrize(("column", "value"), (("open", "inf"), ("amount", "-inf")))
def test_daily_holdout_append_rejects_nonfinite_market_values(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    frozen = tmp_path / "data/frozen"
    _csv(frozen / "sh000300.csv", LAST_IN_SAMPLE_DATE)
    snapshot_path = tmp_path / "snapshot/sh000300.csv"
    _csv(snapshot_path, HOLDOUT_START)
    rows = list(csv.reader(snapshot_path.read_text(encoding="utf-8").splitlines()))
    rows[1][rows[0].index(column)] = value
    snapshot_path.write_text(
        "\n".join(",".join(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="finite OHLCV and amount"):
        append_holdout_snapshot(
            repository_root=tmp_path,
            snapshot_dir=snapshot_path.parent,
            contract=load_future_holdout_contract(),
        )


def test_holdout_replay_outputs_cannot_descend_into_protected_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replay = _valid_replay()
    contract_path = tmp_path / "benchmarks/future_holdout_contract.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(
        (Path(__file__).parents[1] / "benchmarks/future_holdout_contract.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: replay,
    )

    protected = tmp_path / "data/frozen"
    protected.mkdir(parents=True)
    with pytest.raises(ValueError, match="protected data directory"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=protected / "nested/replay.json",
        )
    assert not (protected / "nested/replay.json").exists()

    with pytest.raises(ValueError, match="protected data directory"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=tmp_path / "artifacts/replay.json",
            decision_output_path=protected / "nested/decision.json",
        )
    assert not (protected / "nested/decision.json").exists()


def test_holdout_replay_rejects_all_destructive_output_aliases_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_holdout_contract(tmp_path)
    account = tmp_path / "account.json"
    journal = tmp_path / "execution_journal.jsonl"
    account.write_text("account", encoding="utf-8")
    journal.write_text("journal", encoding="utf-8")

    def unexpected_replay(**_kwargs: object) -> dict[str, object]:
        pytest.fail("path validation must finish before deterministic replay")

    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        unexpected_replay,
    )
    contract_path = tmp_path / "benchmarks/future_holdout_contract.json"
    with pytest.raises(ValueError, match="authoritative path"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=account,
            output_path=contract_path,
            journal_path=journal,
        )
    with pytest.raises(ValueError, match="authoritative path"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=account,
            output_path=tmp_path / "artifacts/replay.json",
            decision_output_path=journal,
            journal_path=journal,
        )
    with pytest.raises(ValueError, match="holdout outputs overlap"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=account,
            output_path=tmp_path / "artifacts/replay.json",
            decision_output_path=tmp_path / "artifacts/replay.json/decision.json",
            journal_path=journal,
        )
    with pytest.raises(ValueError, match="authoritative path"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=account,
            output_path=tmp_path / ".git",
            journal_path=journal,
        )
    with pytest.raises(ValueError, match="authoritative path"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=account,
            output_path=tmp_path / "artifacts/replay.json",
            decision_output_path=tmp_path / "artifacts/future_holdout_checkpoint.json",
            journal_path=journal,
        )
    assert contract_path.read_bytes() == (
        Path(__file__).parents[1] / "benchmarks/future_holdout_contract.json"
    ).read_bytes()
    assert journal.read_text(encoding="utf-8") == "journal"


def test_holdout_replay_protects_resolved_git_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).parents[1]
    git_metadata = holdout_runtime_module._git_metadata_paths(repository_root)
    assert git_metadata
    assert (repository_root / ".git") in git_metadata

    def unexpected_replay(**_kwargs: object) -> dict[str, object]:
        pytest.fail("Git metadata validation must finish before replay")

    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        unexpected_replay,
    )
    for protected in git_metadata:
        with pytest.raises(ValueError, match="authoritative path"):
            generate_future_holdout_replay(
                repository_root=repository_root,
                account_path=tmp_path / "account.json",
                output_path=protected,
            )


def test_holdout_replay_reuses_the_prior_journal_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen, snapshot, required = _holdout_market_fixture(tmp_path)
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
    _install_holdout_contract(tmp_path)
    journal = tmp_path / "execution_journal.jsonl"
    append_planned(
        journal,
        plan_id="holdout-1",
        recorded_at="2026-08-06T08:00:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=10.0,
        planned_shares=100,
    )
    output = tmp_path / "artifacts/replay.json"
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.validate_prior_close_account",
        lambda *_args, **_kwargs: None,
    )

    first = generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=account_path,
        output_path=output,
        decision_output_path=tmp_path / "artifacts/decision.json",
        journal_path=journal,
    )
    assert first["journal_checkpoint"]["sequence"] == 1
    assert (tmp_path / "artifacts/future_holdout_checkpoint.json").is_file()
    journal.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="behind the trusted checkpoint"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=account_path,
            output_path=output,
            decision_output_path=tmp_path / "artifacts/decision.json",
            journal_path=journal,
        )


def test_holdout_replay_cannot_batch_skipped_daily_decisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_holdout_contract(tmp_path)
    contract = load_future_holdout_contract()
    replay = _valid_replay()
    second_digest, second_decision = _decision_record(contract.review_sessions[1])
    replay["sessions"] = list(contract.review_sessions[:2])
    replay["decision_digests"] = [
        *replay["decision_digests"],
        second_digest,
    ]
    replay["decisions"] = [*replay["decisions"], second_decision]
    replay["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in replay.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: replay,
    )

    with pytest.raises(ValueError, match="one uncheckpointed daily session"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=tmp_path / "artifacts/replay.json",
            decision_output_path=tmp_path / "artifacts/decision.json",
        )
    assert not (tmp_path / "artifacts/replay.json").exists()


def test_holdout_checkpoint_protects_prior_replay_and_decision_carriers(
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
    checkpoint = json.loads(
        (tmp_path / "artifacts/future_holdout_checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["sessions"] == [HOLDOUT_START]
    assert checkpoint["replay_output_path"] == str(output.resolve())
    assert checkpoint["decision_output_path"] == str(decision_output.resolve())

    decision_output.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prior daily decision artifact"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision_output,
        )


@pytest.mark.parametrize("failed_artifact", ("decision", "checkpoint"))
@pytest.mark.parametrize("failure_type", (OSError, KeyboardInterrupt))
def test_holdout_bundle_rolls_back_when_a_later_write_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failed_artifact: str,
    failure_type: type[BaseException],
) -> None:
    """Catches earlier replacements surviving a later carrier-write failure."""

    _install_holdout_contract(tmp_path)
    contract = load_future_holdout_contract()
    first = _valid_replay()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: first,
    )
    output = tmp_path / "artifacts/replay.json"
    decision_output = tmp_path / "artifacts/decision.json"
    checkpoint = tmp_path / "artifacts/future_holdout_checkpoint.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision_output,
    )
    before = {
        output: output.read_bytes(),
        decision_output: decision_output.read_bytes(),
        checkpoint: checkpoint.read_bytes(),
    }

    second_digest, second_decision = _decision_record(contract.review_sessions[1])
    extended = json.loads(json.dumps(first))
    extended["sessions"].append(contract.review_sessions[1])
    extended["decision_digests"].append(second_digest)
    extended["decisions"].append(second_decision)
    extended["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in extended.items()
                if key != "canonical_sha256"
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: extended,
    )
    original_write = holdout_runtime_module.atomic_write_text
    failed_destination = decision_output if failed_artifact == "decision" else checkpoint

    def fail_later_write(destination: str | Path, text: str, **kwargs: object) -> None:
        if Path(destination) == failed_destination:
            raise failure_type(f"injected {failed_artifact} write failure")
        original_write(destination, text, **kwargs)

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", fail_later_write)

    with pytest.raises(failure_type, match=rf"injected {failed_artifact} write failure"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision_output,
        )

    assert {path: path.read_bytes() for path in before} == before


def test_holdout_bundle_does_not_remove_a_rejected_output_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches rollback treating an unsafe pre-existing carrier as newly created."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    output = tmp_path / "artifacts/replay.json"
    output.parent.mkdir(parents=True)
    output.symlink_to(tmp_path / "missing-target.json")

    with pytest.raises(ValueError, match="symlink"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=tmp_path / "artifacts/decision.json",
        )

    assert output.is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes")
def test_holdout_rollback_restores_the_prior_carrier_mode(tmp_path: Path) -> None:
    """Catches recovery replacing prior evidence with a private-mode inode."""

    path = tmp_path / "replay.json"
    prior = b"prior replay evidence\n"
    owned = b"transaction-owned replay evidence\n"
    path.write_bytes(prior)
    path.chmod(0o640)
    snapshots = holdout_runtime_module._artifact_snapshots((path,))

    path.unlink()
    path.write_bytes(owned)
    path.chmod(0o600)
    failures = holdout_runtime_module._restore_artifact_snapshots(
        snapshots,
        {path: owned},
    )

    assert failures == ()
    assert path.read_bytes() == prior
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_holdout_snapshot_mode_and_publish_edges_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises canonical identity, mode inspection, and no-replace publication."""

    with pytest.raises(ValueError, match="path is not canonical"):
        holdout_runtime_module._artifact_snapshots((Path("relative.json"),))

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    carrier = real_parent / "carrier.json"
    carrier.write_bytes(b"prior")
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="contains a symlink"):
        holdout_runtime_module._artifact_snapshots((alias / "carrier.json",))

    real_stat = Path.stat
    carrier_stats = 0

    def fail_mode_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal carrier_stats
        if self == carrier and kwargs.get("follow_symlinks") is False:
            carrier_stats += 1
            if carrier_stats == 3:
                raise OSError("mode unavailable")
        return real_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", fail_mode_stat)
        with pytest.raises(ValueError, match=r"cannot inspect.*mode"):
            holdout_runtime_module._artifact_snapshots((carrier,))

    carrier_stats = 0

    def unsafe_mode_stat(self: Path, *args: object, **kwargs: object) -> object:
        nonlocal carrier_stats
        if self == carrier and kwargs.get("follow_symlinks") is False:
            carrier_stats += 1
            if carrier_stats == 3:
                return SimpleNamespace(st_mode=stat.S_IFDIR)
        return real_stat(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", unsafe_mode_stat)
        with pytest.raises(ValueError, match="artifact is unsafe"):
            holdout_runtime_module._artifact_snapshots((carrier,))

    with monkeypatch.context() as patch:
        patch.setattr(
            holdout_runtime_module,
            "os",
            SimpleNamespace(
                name="nt",
                O_RDONLY=os.O_RDONLY,
                O_NOFOLLOW=getattr(os, "O_NOFOLLOW", 0),
                open=os.open,
                fstat=os.fstat,
                fdopen=os.fdopen,
                dup=os.dup,
                close=os.close,
            ),
        )
        snapshot = holdout_runtime_module._artifact_snapshots((carrier,))[carrier]
    assert snapshot.payload == b"prior"
    assert snapshot.mode is None

    assert holdout_runtime_module._link_bytes_if_absent(carrier, b"successor") is False
    assert carrier.read_bytes() == b"prior"


def test_holdout_restore_preserves_owned_bytes_when_claim_inspection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises recovery when a claimed carrier cannot be safely inspected."""

    carrier = tmp_path / "carrier.json"
    owned = b"owned generation"
    carrier.write_bytes(owned)
    real_read = holdout_runtime_module._read_protected_artifact

    def fail_claim_read(path: str | Path, *, label: str) -> bytes:
        if label == "future holdout rollback artifact":
            raise ValueError("claim inspection failed")
        return real_read(path, label=label)

    monkeypatch.setattr(
        holdout_runtime_module,
        "_read_protected_artifact",
        fail_claim_read,
    )
    holdout_runtime_module._restore_owned_artifact(
        carrier,
        b"prior generation",
        owned,
    )
    assert carrier.read_bytes() == owned

    with monkeypatch.context() as patch:
        patch.setattr(
            holdout_runtime_module.os,
            "replace",
            lambda *_: (_ for _ in ()).throw(PermissionError("claim denied")),
        )
        with pytest.raises(PermissionError, match="claim denied") as caught:
            holdout_runtime_module._restore_owned_artifact(carrier, None, owned)
    assert not getattr(caught.value, "__notes__", ())
    assert carrier.read_bytes() == owned


def test_holdout_rollback_does_not_overwrite_a_foreign_carrier_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches rollback clobbering bytes no longer owned by its transaction."""

    _install_holdout_contract(tmp_path)
    contract = load_future_holdout_contract()
    first = _valid_replay()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: first,
    )
    output = tmp_path / "artifacts/replay.json"
    decision_output = tmp_path / "artifacts/decision.json"
    checkpoint = tmp_path / "artifacts/future_holdout_checkpoint.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision_output,
    )
    before_decision = decision_output.read_bytes()
    before_checkpoint = checkpoint.read_bytes()

    second_digest, second_decision = _decision_record(contract.review_sessions[1])
    extended = json.loads(json.dumps(first))
    extended["sessions"].append(contract.review_sessions[1])
    extended["decision_digests"].append(second_digest)
    extended["decisions"].append(second_decision)
    extended["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in extended.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: extended,
    )
    original_write = holdout_runtime_module.atomic_write_text
    foreign = b"foreign carrier generation\n"

    def replace_then_fail(destination: str | Path, text: str, **kwargs: object) -> None:
        if Path(destination) == decision_output:
            output.write_bytes(foreign)
            raise OSError("injected foreign replacement")
        original_write(destination, text, **kwargs)

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", replace_then_fail)

    with pytest.raises(OSError, match="foreign replacement"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision_output,
        )

    assert output.read_bytes() == foreign
    assert decision_output.read_bytes() == before_decision
    assert checkpoint.read_bytes() == before_checkpoint


def test_holdout_canonicalizes_carrier_paths_before_snapshot_and_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a missing ``component/..`` making rollback delete existing evidence."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    canonical = tmp_path / "replay.json"
    canonical.write_bytes(b"prior replay evidence\n")
    decision = tmp_path / "decision.json"
    original_write = holdout_runtime_module.atomic_write_text

    def fail_decision(destination: str | Path, text: str, **kwargs: object) -> None:
        if Path(destination) == decision:
            raise OSError("injected decision failure")
        original_write(destination, text, **kwargs)

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", fail_decision)

    with pytest.raises(OSError, match="injected decision failure"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=tmp_path / "missing" / ".." / "replay.json",
            decision_output_path=decision,
        )

    assert canonical.read_bytes() == b"prior replay evidence\n"
    assert not (tmp_path / "missing").exists()


def test_holdout_uses_one_canonical_carrier_identity_after_lock_acquisition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches relative outputs resolving to different lock and write paths."""

    repository = tmp_path / "repository"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    _install_holdout_contract(repository)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    original_lock = holdout_runtime_module._artifact_bundle_lock
    locked: tuple[Path, ...] = ()

    @contextmanager
    def change_directory_after_lock(
        root: Path,
        carriers: tuple[Path, ...],
    ) -> Iterator[None]:
        nonlocal locked
        with original_lock(root, carriers):
            locked = carriers
            monkeypatch.chdir(second_cwd)
            try:
                yield
            finally:
                monkeypatch.chdir(first_cwd)

    monkeypatch.chdir(first_cwd)
    monkeypatch.setattr(
        holdout_runtime_module,
        "_artifact_bundle_lock",
        change_directory_after_lock,
    )

    generate_future_holdout_replay(
        repository_root=repository,
        account_path=repository / "account.json",
        output_path="replay.json",
        decision_output_path="decision.json",
    )

    assert locked[:1] == (first_cwd / "replay.json",)
    assert (first_cwd / "replay.json").is_file()
    assert (first_cwd / "decision.json").is_file()
    assert not (second_cwd / "replay.json").exists()
    assert not (second_cwd / "decision.json").exists()


def test_holdout_rollback_does_not_overwrite_a_foreign_toctou_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches foreign bytes installed after rollback's ownership read."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    output = tmp_path / "replay.json"
    decision = tmp_path / "decision.json"
    original_read = holdout_runtime_module._read_protected_artifact
    original_write = holdout_runtime_module.atomic_write_text
    armed = False
    foreign = b"foreign carrier generation\n"

    def fail_decision(destination: str | Path, text: str, **kwargs: object) -> None:
        nonlocal armed
        if Path(destination) == decision:
            armed = True
            raise OSError("injected decision failure")
        original_write(destination, text, **kwargs)

    def replace_after_ownership_read(path: Path, *, label: str) -> bytes:
        current = original_read(path, label=label)
        if (
            armed
            and label == "future holdout rollback artifact"
            and (path == output or path.name.startswith(f".{output.name}.claimed-"))
        ):
            output.write_bytes(foreign)
        return current

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", fail_decision)
    monkeypatch.setattr(
        holdout_runtime_module,
        "_read_protected_artifact",
        replace_after_ownership_read,
    )

    with pytest.raises(OSError, match="injected decision failure"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision,
        )

    assert output.read_bytes() == foreign


def test_holdout_cleanup_preserves_primary_failure_and_continues_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches one rollback failure masking the write error and stopping later restores."""

    _install_holdout_contract(tmp_path)
    first = _valid_replay()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: first,
    )
    output = tmp_path / "artifacts/replay.json"
    decision = tmp_path / "artifacts/decision.json"
    checkpoint = tmp_path / "artifacts/future_holdout_checkpoint.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision,
    )
    before_decision = decision.read_bytes()
    contract = load_future_holdout_contract()
    digest, record = _decision_record(contract.review_sessions[1])
    second = json.loads(json.dumps(first))
    second["sessions"].append(contract.review_sessions[1])
    second["decision_digests"].append(digest)
    second["decisions"].append(record)
    second["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in second.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: second,
    )
    original_write = holdout_runtime_module.atomic_write_text
    original_restore = holdout_runtime_module._restore_owned_artifact

    def fail_checkpoint(destination: str | Path, text: str, **kwargs: object) -> None:
        if Path(destination) == checkpoint:
            raise OSError("primary checkpoint failure")
        original_write(destination, text, **kwargs)

    def fail_first_restore(
        destination: Path,
        payload: bytes | None,
        expected: bytes,
        *,
        mode: int | None = None,
    ) -> None:
        if destination == output:
            raise OSError("secondary rollback failure")
        original_restore(destination, payload, expected, mode=mode)

    monkeypatch.setattr(holdout_runtime_module, "atomic_write_text", fail_checkpoint)
    monkeypatch.setattr(
        holdout_runtime_module,
        "_restore_owned_artifact",
        fail_first_restore,
    )

    with pytest.raises(OSError, match="primary checkpoint failure"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision,
        )

    assert decision.read_bytes() == before_decision


@pytest.mark.parametrize("generation", ("owned", "foreign"))
def test_holdout_post_claim_link_failure_preserves_every_carrier_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    generation: str,
) -> None:
    """Catches post-claim restoration errors deleting the only evidence copies."""

    path = tmp_path / "replay.json"
    expected = b"transaction-owned replay\n"
    prior = b"prior replay\n"
    foreign = b"foreign replay\n"
    path.write_bytes(expected if generation == "owned" else foreign)

    def fail_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected post-claim link failure")

    monkeypatch.setattr(holdout_runtime_module.os, "link", fail_link)

    with pytest.raises(OSError, match="post-claim link failure"):
        holdout_runtime_module._restore_owned_artifact(
            path,
            prior,
            expected,
        )

    preserved = {item.read_bytes() for item in tmp_path.iterdir() if item.is_file()}
    if generation == "owned":
        assert preserved == {expected, prior}
    else:
        assert preserved == {foreign}


def test_holdout_lock_cleanup_preserves_primary_and_closes_every_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches one close error masking the primary and skipping later descriptors."""

    original_close = holdout_runtime_module.os.close
    closed: list[int] = []

    def close_then_fail_once(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)
        if len(closed) == 1:
            raise OSError("injected close failure")

    monkeypatch.setattr(holdout_runtime_module.os, "close", close_then_fail_once)

    with (
        pytest.raises(RuntimeError, match="primary transaction failure") as raised,
        holdout_runtime_module._artifact_bundle_lock(
            tmp_path,
            (tmp_path / "replay.json", tmp_path / "decision.json"),
        ),
    ):
        raise RuntimeError("primary transaction failure")

    assert len(closed) == 3
    assert len(set(closed)) == 3
    assert any("injected close failure" in note for note in raised.value.__notes__)


def test_holdout_lock_identity_follows_shared_carriers_across_repositories(
    tmp_path: Path,
) -> None:
    """Catches repository-root locks failing to serialize shared external carriers."""

    shared = tmp_path / "shared" / "replay.json"
    left = holdout_runtime_module._artifact_bundle_lock_paths(
        (shared, tmp_path / "left-decision.json")
    )
    right = holdout_runtime_module._artifact_bundle_lock_paths(
        (shared, tmp_path / "right-decision.json")
    )

    assert set(left) & set(right)


@pytest.mark.parametrize("lock_output", ("replay", "decision"))
def test_holdout_outputs_cannot_replace_the_transaction_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lock_output: str,
) -> None:
    """Catches output replacement invalidating the inode that serializes writers."""

    _install_holdout_contract(tmp_path)
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: _valid_replay(),
    )
    lock_path = holdout_runtime_module._artifact_bundle_lock_path(tmp_path.resolve())
    replay_output = lock_path if lock_output == "replay" else tmp_path / "replay.json"
    decision_output = lock_path if lock_output == "decision" else tmp_path / "decision.json"

    with pytest.raises(ValueError, match="authoritative path"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=replay_output,
            decision_output_path=decision_output,
        )


def test_holdout_checkpoint_prevents_output_carrier_switching(
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

    with pytest.raises(ValueError, match="checkpointed output paths"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=tmp_path / "reports/renamed-replay.json",
            decision_output_path=tmp_path / "reports/renamed-decision.json",
        )


def test_holdout_checkpoint_rejects_mutation_of_the_prior_data_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_holdout_contract(tmp_path)
    contract = load_future_holdout_contract()
    first_path = (
        tmp_path
        / HOLDOUT_DATA_DIRECTORY
        / contract.review_sessions[0]
        / "sh000300.csv"
    )
    _csv(first_path, contract.review_sessions[0])
    first_snapshot = holdout_runtime_module._capture_holdout_data(
        tmp_path / HOLDOUT_DATA_DIRECTORY
    )
    first = _valid_replay()
    first["holdout_data_sha256"] = first_snapshot.sha256
    first["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in first.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: first,
    )
    output = tmp_path / "artifacts/replay.json"
    decision_output = tmp_path / "artifacts/decision.json"
    generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=tmp_path / "account.json",
        output_path=output,
        decision_output_path=decision_output,
    )

    _csv(first_path, contract.review_sessions[0], close=11.0)
    _csv(
        tmp_path
        / HOLDOUT_DATA_DIRECTORY
        / contract.review_sessions[1]
        / "sh000300.csv",
        contract.review_sessions[1],
    )
    current = holdout_runtime_module._capture_holdout_data(
        tmp_path / HOLDOUT_DATA_DIRECTORY
    )
    second_digest, second_decision = _decision_record(contract.review_sessions[1])
    extended = json.loads(json.dumps(first))
    extended["holdout_data_sha256"] = current.sha256
    extended["sessions"] = list(contract.review_sessions[:2])
    extended["decision_digests"].append(second_digest)
    extended["decisions"].append(second_decision)
    extended["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in extended.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    monkeypatch.setattr(
        "uquant.validation.holdout_runtime.replay_future_holdout",
        lambda **_kwargs: extended,
    )

    with pytest.raises(ValueError, match="checkpointed data prefix"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=tmp_path / "account.json",
            output_path=output,
            decision_output_path=decision_output,
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
