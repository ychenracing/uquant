from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_engineering_gate_edges import (
    ROOT,
)

from uquant.validation import equivalence as equivalence_module
from uquant.validation import holdout as holdout_module
from uquant.validation import universe as universe_module
from uquant.validation.equivalence import Phase1Case, Phase1DecisionTrace


def test_reviewed_holdout_strategy_anchor_rejects_one_byte_mutation(tmp_path: Path) -> None:
    """Catches accepting later strategy drift merely because its path was reviewed."""

    checkout = tmp_path / "reviewed-strategy"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(checkout)],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "checkout",
            "--quiet",
            "--detach",
            holdout_module.STRATEGY_ANCHOR_COMMIT,
        ],
        check=True,
    )
    assert (
        holdout_module._validated_strategy_source_sha256(checkout)
        == holdout_module.STRATEGY_SOURCE_SHA256
    )
    strategy = checkout / "uquant" / "portfolio_strategic.py"
    strategy.write_text(strategy.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="bytes drifted"):
        holdout_module._validated_strategy_source_sha256(checkout)

def test_holdout_observation_metrics_reject_every_detached_input(tmp_path: Path) -> None:
    contract = holdout_module.load_future_holdout_contract()
    data_sha256 = "a" * 64
    with pytest.raises(ValueError, match="must be omitted"):
        holdout_module._observation_metrics(
            tmp_path / "metrics.json",
            sessions=(),
            holdout_data_sha256=data_sha256,
            contract=contract,
        )
    with pytest.raises(RuntimeError, match="deterministic holdout replay"):
        holdout_module._observation_metrics(
            None,
            sessions=(contract.first_holdout_date,),
            holdout_data_sha256=data_sha256,
            contract=contract,
        )
    base: dict[str, object] = {
        "schema_version": 1,
        "holdout_data_sha256": data_sha256,
        "sessions": [contract.first_holdout_date],
        "scores": {
            "final_wealth": 1.0,
            "max_drawdown": 0.0,
            "account_orders": 0,
            "gross_turnover": 0.0,
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        },
    }
    for name, mutation in (
        ("schema", {"schema_version": 2}),
        ("data", {"holdout_data_sha256": "b" * 64}),
        ("sessions-type", {"sessions": "bad"}),
        ("sessions-value", {"sessions": ["2026-08-07"]}),
        ("scores", {"scores": []}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({**base, **mutation}), encoding="utf-8")
        with pytest.raises(RuntimeError, match="deterministic holdout replay"):
            holdout_module._observation_metrics(
                path,
                sessions=(contract.first_holdout_date,),
                holdout_data_sha256=data_sha256,
                contract=contract,
            )

def test_holdout_file_layout_rejects_missing_dates_links_and_stale_state(
    tmp_path: Path,
) -> None:
    contract = holdout_module.load_future_holdout_contract()
    with pytest.raises(RuntimeError, match="lacks date column"):
        holdout_module._csv_dates_from_text("close\n1\n", path=tmp_path / "bad.csv")
    with pytest.raises(RuntimeError, match="invalid date"):
        holdout_module._csv_dates_from_text("date\nbad\n", path=tmp_path / "bad.csv")
    with pytest.raises(RuntimeError, match="no observed market sessions"):
        holdout_module.maximum_observed_market_date(tmp_path)
    assert holdout_module._closed_csv_files(
        tmp_path / "absent", label="fixture", missing_ok=True
    ) == ()
    with pytest.raises(RuntimeError, match="is missing"):
        holdout_module._closed_csv_files(
            tmp_path / "absent", label="fixture", missing_ok=False
        )
    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must be a directory"):
        holdout_module._closed_csv_files(regular, label="fixture", missing_ok=False)
    unsupported = tmp_path / "unsupported"
    unsupported.mkdir()
    (unsupported / "note.txt").write_text("bad", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported file"):
        holdout_module._closed_csv_files(unsupported, label="fixture", missing_ok=False)

    with pytest.raises(ValueError, match="exact prior-close state"):
        holdout_module._state_hashes({}, as_of=contract.last_in_sample_date)
    with pytest.raises(ValueError, match="positions or pending orders"):
        holdout_module._state_hashes(
            {
                "last_successful_run": contract.last_in_sample_date,
                "data_hash_as_of": contract.last_in_sample_date,
                "positions": [],
                "pending_orders": {},
            },
            as_of=contract.last_in_sample_date,
        )
    with pytest.raises(ValueError, match="owning repository root"):
        holdout_module._manifest_repository_root(tmp_path)

    repository_link = tmp_path / "repository-link"
    repository_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="repository root must not be a symlink"):
        holdout_module.validate_holdout_layout(repository_link)

    root = tmp_path / "isolated"
    frozen = root / "data/frozen"
    frozen.mkdir(parents=True)
    (frozen / "a.csv").write_text(
        "date,open,high,low,close,volume\n2026-08-05,1,1,1,1,1\n",
        encoding="utf-8",
    )
    linked = frozen / "linked.csv"
    linked.symlink_to(frozen / "a.csv")
    with pytest.raises(RuntimeError, match="data/frozen contains a symlink"):
        holdout_module.validate_holdout_layout(root)
    linked.unlink()

    holdout_root = root / "data/holdout"
    holdout_root.symlink_to(frozen, target_is_directory=True)
    with pytest.raises(RuntimeError, match="data/holdout must be a physical directory"):
        holdout_module.validate_holdout_layout(root)
    holdout_root.unlink()
    future = root / contract.data_directory
    future.mkdir(parents=True)
    (future / "a.csv").write_text(
        "date,open,high,low,close,volume\n2026-08-05,1,1,1,1,1\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="in-sample market row"):
        holdout_module.validate_holdout_layout(root)

def test_performance_equivalence_rejects_incomplete_matrix_and_trace_contracts(
    tmp_path: Path,
) -> None:
    baseline = json.loads((ROOT / "benchmarks/promotion_baseline.json").read_text(encoding="utf-8"))
    for name, mutation, message in (
        ("root", {"pools": []}, "baseline is malformed"),
        ("pools", {"pools": {}}, "complete replay matrix"),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({**baseline, **mutation}), encoding="utf-8")
        with pytest.raises(RuntimeError, match=message):
            equivalence_module.phase1_cases(path)
    malformed_pool = copy.deepcopy(baseline)
    malformed_pool["pools"]["a"] = [1]
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(json.dumps(malformed_pool), encoding="utf-8")
    with pytest.raises(RuntimeError, match="pool is malformed"):
        equivalence_module.phase1_cases(pool_path)
    malformed_interval = copy.deepcopy(baseline)
    malformed_interval["contract"]["windows"]["h1_2023"] = []
    interval_path = tmp_path / "interval.json"
    interval_path.write_text(json.dumps(malformed_interval), encoding="utf-8")
    with pytest.raises(RuntimeError, match="interval is malformed"):
        equivalence_module.phase1_cases(interval_path)

    required = {
        "decision_payload_sha256": "a" * 64,
        "economic_account_sha256": "b" * 64,
    }
    candidate = Phase1DecisionTrace(production_commit="f" * 40, cases={"case": required})
    with pytest.raises(RuntimeError, match="not bound"):
        equivalence_module.assert_equivalent_phase1_traces(
            Phase1DecisionTrace(production_commit="0" * 40, cases={"case": required}),
            candidate,
        )
    frozen = Phase1DecisionTrace(
        production_commit=equivalence_module.FROZEN_CHAMPION_COMMIT,
        cases={"case": required},
    )
    with pytest.raises(RuntimeError, match="cases differ"):
        equivalence_module.assert_equivalent_phase1_traces(
            frozen, Phase1DecisionTrace(production_commit="f" * 40, cases={})
        )
    with pytest.raises(RuntimeError, match="payload is malformed"):
        equivalence_module.assert_equivalent_phase1_traces(
            frozen,
            Phase1DecisionTrace(
                production_commit="f" * 40,
                cases={"case": {"decision_payload_sha256": "a" * 64}},
            ),
        )

def test_performance_equivalence_subprocess_boundaries_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(equivalence_module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="cannot resolve git"):
        equivalence_module._git_executable()
    monkeypatch.setattr(equivalence_module.shutil, "which", lambda _: "git")
    monkeypatch.setattr(
        equivalence_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, ["git"])
        ),
    )
    with pytest.raises(RuntimeError, match="cannot resolve commit"):
        equivalence_module._git_commit(tmp_path)

    case = Phase1Case("a/h1_2023", ("sh000300",), "2023-01-03", "2023-01-04")
    outputs = (
        ("not-json", "cannot capture"),
        ("[]", "trace is malformed"),
        (json.dumps({"decision_payload_sha256": "a" * 64}), "trace is malformed"),
        (
            json.dumps(
                {
                    "decision_payload_sha256": "short",
                    "economic_account_sha256": "b" * 64,
                }
            ),
            "digest is malformed",
        ),
    )
    for stdout, message in outputs:
        monkeypatch.setattr(
            equivalence_module.subprocess,
            "run",
            lambda *_args, _stdout=stdout, **_kwargs: SimpleNamespace(stdout=_stdout),
        )
        with pytest.raises(RuntimeError, match=message):
            equivalence_module.trace_phase1_case(root=tmp_path, data_dir=tmp_path, case=case)

    monkeypatch.setattr(equivalence_module, "_git_commit", lambda _: "0" * 40)
    with pytest.raises(RuntimeError, match="does not match"):
        equivalence_module.compare_phase1_commits(
            frozen_root=tmp_path,
            candidate_root=tmp_path,
            data_dir=tmp_path,
            cases=(case,),
        )

def test_universe_json_and_scalar_helpers_reject_ambiguous_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        universe_module._reject_duplicate_keys([("a", 1), ("a", 2)])
    with pytest.raises(ValueError, match="non-standard number"):
        universe_module._reject_nonstandard_constant("NaN")
    with pytest.raises(ValueError, match="must be an ISO date"):
        universe_module._parse_date(1, label="date")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be an ISO date"):
        universe_module._parse_date("bad", label="date")
    with pytest.raises(ValueError, match="is corrupt"):
        universe_module._read_json_bytes(b"\xff", label="fixture")
    with pytest.raises(ValueError, match="must be a JSON object"):
        universe_module._read_json_bytes(b"[]", label="fixture")
    with pytest.raises(ValueError, match="missing or not a regular file"):
        universe_module._read_json(tmp_path / "missing.json", label="fixture")
    with pytest.raises(ValueError, match="must be SHA-256"):
        universe_module._sha256("bad", label="fixture")
