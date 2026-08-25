# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from uquant.account import save_account
from uquant.data import DataStore
from uquant.engine import INDEX_SYMBOLS, code_fingerprint
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
from uquant.validation.execution_journal import append_filled, append_planned
from uquant.validation.holdout import (
    HOLDOUT_DATA_DIRECTORY,
    HOLDOUT_START,
    LAST_IN_SAMPLE_DATE,
    holdout_source_sha256,
    load_future_holdout_contract,
)
from uquant.validation.holdout_lanes import lane_binding_payload, load_lane_registry
from uquant.validation.holdout_runtime import (
    append_holdout_snapshot,
    generate_future_holdout_replay,
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
    append_filled(
        journal,
        plan_id="holdout-1",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=10.1,
        actual_time="2026-08-06T09:31:00+08:00",
        actual_price=10.2,
        actual_shares=100,
        broker_order_id="manual-fill-1",
    )
    after_manual_fill = generate_future_holdout_replay(
        repository_root=tmp_path,
        account_path=account_path,
        output_path=output,
        decision_output_path=tmp_path / "artifacts/decision.json",
        journal_path=journal,
    )
    assert after_manual_fill["journal_checkpoint"]["sequence"] == 2
    assert after_manual_fill["decision_digests"] == first["decision_digests"]
    assert after_manual_fill["decisions"] == first["decisions"]
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



from _future_holdout_transaction_recovery_cases import (
    test_holdout_bundle_does_not_remove_a_rejected_output_symlink,
    test_holdout_rollback_restores_the_prior_carrier_mode,
    test_holdout_snapshot_mode_and_publish_edges_fail_closed,
    test_holdout_restore_preserves_owned_bytes_when_claim_inspection_fails,
    test_holdout_rollback_does_not_overwrite_a_foreign_carrier_generation,
    test_holdout_canonicalizes_carrier_paths_before_snapshot_and_rollback,
    test_holdout_uses_one_canonical_carrier_identity_after_lock_acquisition,
    test_holdout_rollback_does_not_overwrite_a_foreign_toctou_replacement,
    test_holdout_cleanup_preserves_primary_failure_and_continues_recovery,
)

from _future_holdout_carrier_identity_cases import (
    test_holdout_post_claim_link_failure_preserves_every_carrier_generation,
    test_holdout_lock_cleanup_preserves_primary_and_closes_every_descriptor,
    test_holdout_lock_identity_follows_shared_carriers_across_repositories,
    test_holdout_outputs_cannot_replace_the_transaction_lock,
    test_holdout_checkpoint_prevents_output_carrier_switching,
    test_holdout_checkpoint_rejects_mutation_of_the_prior_data_prefix,
)

from _future_holdout_replay_binding_cases import (
    test_daily_decision_is_fully_bound_and_semantically_read_back,
    test_holdout_replay_readback_binds_data_sessions_scores_decisions_and_journal,
    test_manifest_scores_accept_only_the_exact_reexecuted_replay,
    test_holdout_replay_uses_the_production_next_open_then_decision_path,
    test_holdout_replay_counts_prior_close_orders_filled_on_the_boundary,
    test_holdout_replay_uses_the_same_future_bytes_for_identity_and_execution,
)
