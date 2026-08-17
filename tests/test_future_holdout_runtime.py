from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

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
    replay: dict[str, object] = {
        "schema_version": 1,
        "replay_id": "phase2-future-holdout-replay-v1",
        "contract_sha256": contract.sha256,
        "production_source_sha256": holdout_source_sha256(Path(__file__).parents[1]),
        "holdout_data_sha256": "a" * 64,
        "prior_close_account_sha256": contract.prior_close_account_sha256,
        "sessions": [session],
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
        "scores": {
            "final_wealth": 1.0,
            "max_drawdown": 0.0,
            "account_orders": 0,
            "gross_turnover": 0.0,
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        },
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
    contract = load_future_holdout_contract()
    replay = {
        "schema_version": 1,
        "replay_id": "phase2-future-holdout-replay-v1",
        "contract_sha256": contract.sha256,
        "holdout_data_sha256": "a" * 64,
        "prior_close_account_sha256": contract.prior_close_account_sha256,
        "sessions": [HOLDOUT_START],
        "decision_digests": ["b" * 64],
        "decisions": [
            {
                "date": HOLDOUT_START,
                "decision_digest": "b" * 64,
                "payload": {"date": HOLDOUT_START},
            }
        ],
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
        "scores": {
            "final_wealth": 1.0,
            "max_drawdown": 0.0,
            "account_orders": 0,
            "gross_turnover": 0.0,
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        },
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
    assert len(git_metadata) >= 2

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
        journal_path=journal,
    )
    assert first["journal_checkpoint"]["sequence"] == 1
    assert (tmp_path / "artifacts/future_holdout_checkpoint.json").is_file()
    journal.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="behind the trusted checkpoint"):
        generate_future_holdout_replay(
            repository_root=tmp_path,
            account_path=account_path,
            output_path=tmp_path / "reports/renamed-replay.json",
            journal_path=journal,
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
    decision_digest, decision = _decision_record(HOLDOUT_START)
    source_sha256 = holdout_source_sha256(Path(__file__).parents[1])
    replay = {
        "schema_version": 1,
        "replay_id": "phase2-future-holdout-replay-v1",
        "contract_sha256": contract.sha256,
        "production_source_sha256": source_sha256,
        "holdout_data_sha256": "a" * 64,
        "prior_close_account_sha256": contract.prior_close_account_sha256,
        "sessions": [HOLDOUT_START],
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
        "scores": {
            "final_wealth": 1.0,
            "max_drawdown": 0.0,
            "account_orders": 0,
            "gross_turnover": 0.0,
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        },
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
    decision_digest, decision = _decision_record(HOLDOUT_START)
    replay = {
        "schema_version": 1,
        "replay_id": "phase2-future-holdout-replay-v1",
        "contract_sha256": contract.sha256,
        "production_source_sha256": holdout_source_sha256(Path(__file__).parents[1]),
        "holdout_data_sha256": "a" * 64,
        "prior_close_account_sha256": contract.prior_close_account_sha256,
        "sessions": [HOLDOUT_START],
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
        "scores": {
            "final_wealth": 1.0,
            "max_drawdown": 0.0,
            "account_orders": 0,
            "gross_turnover": 0.0,
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        },
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
    assert first["sessions"] == [HOLDOUT_START]
    assert first["scores"]["final_wealth"] == pytest.approx(1.0)
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

    assert replay["scores"]["account_orders"] == 1
    assert replay["scores"]["gross_turnover"] > 0


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
