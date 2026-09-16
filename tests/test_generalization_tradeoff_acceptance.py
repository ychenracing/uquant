"""Evidence-bound tests for the generalization tradeoff evaluator."""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import run_generalization_tradeoff_acceptance as evaluator


def _write_gzip(path: Path, value: object) -> None:
    path.write_bytes(gzip.compress(json.dumps(value).encode(), mtime=0))


def _original() -> dict[str, object]:
    return {
        "commit": "c" * 40,
        "completed": True,
        "symbols": ["AAA"],
        "interval": {"start": "2023-01-03", "end": "2023-01-04"},
        "config": {"initial_cash": 100.0},
        "environment": {"python": "3.12"},
        "runner_sha256": "r" * 64,
        "adapter_sha256": "a" * 64,
        "source_files": {"uquant/a.py": "d" * 64},
        "metrics": {
            "final_wealth": 1.1,
            "max_drawdown": 0.0,
            "account_orders": 1,
            "annual_turnover": 1.0,
            "gross_turnover": 1.0,
        },
        "trace": [
            {"date": "2023-01-03", "equity": 100.0, "new_fills": []},
            {
                "date": "2023-01-04",
                "equity": 110.0,
                "new_fills": [
                    {"commission": 1.0, "stamp_duty": 2.0, "transfer_fee": 3.0, "slippage_cost": 4.0}
                ],
            },
        ],
    }


def _reuse(tmp_path: Path) -> tuple[dict[str, object], str]:
    original_path = tmp_path / "source.json.gz"
    original = _original()
    _write_gzip(original_path, original)
    digest = hashlib.sha256(original_path.read_bytes()).hexdigest()
    metrics = original["metrics"]
    assert isinstance(metrics, dict)
    payload: dict[str, object] = {
        "schema_version": 1,
        "method": "native_public_trace_reuse",
        "case": "case",
        "source_head": "c" * 40,
        "source_tree": "t" * 40,
        "inputs": {"uquant/a.py": "d" * 64},
        "runtime": {"python": "3.12"},
        "config": {"initial_cash": 100.0},
        "contract_sha256": "h" * 64,
        "runner_sha256": "r" * 64,
        "adapter_sha256": "a" * 64,
        "symbols": ["AAA"],
        "start": "2023-01-03",
        "end": "2023-01-04",
        "cost_multiplier": 1.0,
        "completed": True,
        "sessions": 2,
        "reused_from": {"path": str(original_path), "sha256": digest},
        "result": {
            **metrics,
            "equity_curve": [
                {"date": "2023-01-03", "equity": 100.0},
                {"date": "2023-01-04", "equity": 110.0},
            ],
            "fees": 6.0,
            "slippage_cost": 4.0,
        },
    }
    return payload, digest


def test_trace_reuse_is_read_back_and_reconciled(tmp_path: Path) -> None:
    payload, _ = _reuse(tmp_path)
    metrics = evaluator._validate_cell(
        payload,
        (["AAA"], "2023-01-03", "2023-01-04", 1.0),
        case="case",
        contract_hashes={"h" * 64},
        expected_dates=["2023-01-03", "2023-01-04"],
    )
    assert {name: metrics[name] for name in ("wealth", "drawdown", "orders")} == {
        "wealth": 1.1,
        "drawdown": 0.0,
        "orders": 1,
    }
    assert metrics["raw_metrics"]["fees"] == 6.0  # type: ignore[index]


@pytest.mark.parametrize("mutation", ["digest", "curve", "fees", "flag"])
def test_trace_reuse_rejects_unverifiable_projection(tmp_path: Path, mutation: str) -> None:
    payload, _ = _reuse(tmp_path)
    if mutation == "digest":
        payload["reused_from"] = {"path": payload["reused_from"]["path"], "sha256": "0" * 64}  # type: ignore[index]
    elif mutation == "curve":
        payload["result"]["equity_curve"][1]["equity"] = 111.0  # type: ignore[index]
    elif mutation == "fees":
        payload["result"]["fees"] = 0.0  # type: ignore[index]
    else:
        payload["passed"] = True
        payload["completed"] = False
    with pytest.raises(ValueError):
        evaluator._validate_cell(
            payload,
            (["AAA"], "2023-01-03", "2023-01-04", 1.0),
            case="case",
            contract_hashes={"h" * 64},
            expected_dates=["2023-01-03", "2023-01-04"],
        )


def test_required_names_derive_from_contract() -> None:
    contract = json.loads((Path(__file__).parents[1] / evaluator.CONTRACT).read_text())
    expected = evaluator._expected(contract)
    performance = {
        f"{pool}-{window}" for pool in contract["pools"] for window in contract["performance_windows"]
    }
    assert len(performance) == 45
    assert performance <= expected["candidate"].keys() & expected["historical"].keys()
    assert {f"loo-{symbol}" for symbol in contract["universe"]} <= expected["historical"].keys()


def test_native_summary_cannot_self_attest_account_integrity(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    summary = tmp_path / "native.json"
    summary.write_text(json.dumps({"status": "COMPLETE", "reconciled": True}))
    report = evaluator.evaluate(root, tmp_path / "runs", summary)
    assert report["status"] == "INCOMPLETE"
    assert report["native_absolute"]["status"] == "INCOMPLETE"  # type: ignore[index]
    assert report["native_absolute"]["untrusted_summary"]["status"] == "BLOCKED"  # type: ignore[index]
    assert report["passed"] is False


@pytest.mark.parametrize(
    "dates",
    [
        ["2023-01-03"],
        ["2023-01-04"],
        ["2023-01-03", "2023-01-03-fabricated"],
    ],
)
def test_cell_requires_every_exact_frozen_session(tmp_path: Path, dates: list[str]) -> None:
    payload, _ = _reuse(tmp_path)
    payload["method"] = "native_public_backtest"
    payload.pop("reused_from")
    result = payload["result"]
    assert isinstance(result, dict)
    result["equity_curve"] = [
        {"date": date, "equity": 100.0 if index == 0 else 110.0} for index, date in enumerate(dates)
    ]
    payload["sessions"] = len(dates)
    with pytest.raises(ValueError, match=r"equity curve length|frozen sessions"):
        evaluator._validate_cell(
            payload,
            (["AAA"], "2023-01-03", "2023-01-04", 1.0),
            case="case",
            contract_hashes={"h" * 64},
            expected_dates=["2023-01-03", "2023-01-04"],
        )


@pytest.mark.parametrize("bad_fills", ["not-a-list", [3]])
def test_trace_reuse_rejects_malformed_fills(tmp_path: Path, bad_fills: object) -> None:
    payload, _ = _reuse(tmp_path)
    source = Path(payload["reused_from"]["path"])  # type: ignore[index]
    original = _original()
    original["trace"][0]["new_fills"] = bad_fills  # type: ignore[index]
    _write_gzip(source, original)
    payload["reused_from"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()  # type: ignore[index]
    with pytest.raises(ValueError, match="new_fills"):
        evaluator._validate_reuse(payload)


def test_stress_start_must_equal_the_frozen_offset_session(tmp_path: Path) -> None:
    payload, _ = _reuse(tmp_path)
    payload["method"] = "native_public_backtest"
    payload.pop("reused_from")
    payload["start"] = "2023-01-03"
    with pytest.raises(ValueError, match="stress offset"):
        evaluator._validate_cell(
            payload,
            (["AAA"], "@offset5", "2023-01-04", 1.0),
            case="case",
            contract_hashes={"h" * 64},
            expected_dates=["2023-01-04"],
        )


def test_partial_pair_ratios_keep_their_real_labels() -> None:
    cells = {
        "candidate": {"second": {"wealth": 6.0}, "fourth": {"wealth": 10.0}},
        "historical": {"second": {"wealth": 3.0}, "fourth": {"wealth": 2.0}},
    }
    assert evaluator._paired_wealth_ratios(["first", "second", "third", "fourth"], cells) == {
        "second": 2.0,
        "fourth": 5.0,
    }


def test_complete_manifest_and_tree_are_required() -> None:
    root = Path(__file__).parents[1]
    source = (
        __import__("subprocess")
        .check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True)
        .strip()
    )
    tree = (
        __import__("subprocess")
        .check_output(["git", "-C", str(root), "rev-parse", f"{source}:uquant"], text=True)
        .strip()
    )
    inputs = evaluator._committed_manifest(root, source)
    evaluator._validate_input_digests(root, {"inputs": inputs, "source_head": source, "source_tree": tree})
    omitted = dict(inputs)
    omitted.pop(next(iter(omitted)))
    with pytest.raises(ValueError, match="complete"):
        evaluator._validate_input_digests(
            root, {"inputs": omitted, "source_head": source, "source_tree": tree}
        )
    with pytest.raises(ValueError, match="source_tree"):
        evaluator._validate_input_digests(
            root, {"inputs": inputs, "source_head": source, "source_tree": "forged"}
        )


def test_legacy_native_config_projection_is_narrowly_normalized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recovered = {
        "full": {"initial_cash": 100.0, "policy_rule": 7},
        "public": {"initial_cash": 100.0},
        "fingerprint": "f" * 64,
    }
    monkeypatch.setattr(evaluator, "_source_config", lambda *_: recovered)
    payload = {
        "method": "native_public_backtest",
        "runner_sha256": evaluator.LEGACY_NATIVE_RUNNER_SHA256,
        "source_head": "c" * 40,
        "cost_multiplier": 1.0,
        "config": recovered["public"],
        "result": {"effective_config_sha256": "f" * 64},
    }
    identity = evaluator._normalize_config(tmp_path, payload)
    assert identity["raw_representation"] == "legacy_dataclass_projection"
    assert identity["full_config"] == recovered["full"]
    payload["runner_sha256"] = "unknown"
    with pytest.raises(ValueError, match="audited identity"):
        evaluator._normalize_config(tmp_path, payload)
    payload["config"] = recovered["full"]
    with pytest.raises(ValueError, match="audited identity"):
        evaluator._normalize_config(tmp_path, payload)


def test_native_effective_config_hash_cannot_self_attest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recovered = {
        "full": {"initial_cash": 100.0, "policy_rule": 7},
        "public": {"initial_cash": 100.0},
        "fingerprint": "f" * 64,
    }
    monkeypatch.setattr(evaluator, "_source_config", lambda *_: recovered)
    payload = {
        "method": "native_public_backtest",
        "runner_sha256": evaluator.FULL_CONFIG_NATIVE_RUNNER_SHA256,
        "source_head": "c" * 40,
        "cost_multiplier": 1.0,
        "config": recovered["full"],
        "result": {"effective_config_sha256": "wrong"},
    }
    with pytest.raises(ValueError, match="effective_config_sha256"):
        evaluator._normalize_config(tmp_path, payload)


def test_native_shards_require_paired_candidate_source_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: "candidate-tree\n",
    )
    assert evaluator._native_evidence(tmp_path, None, {"candidate-tree"}, {})["status"] == "INCOMPLETE"
    result = evaluator._native_evidence(tmp_path, tmp_path / "shards", {"wrong-tree"}, {})
    assert result["status"] == "INCOMPLETE"
    assert "differs" in result["failures"][0]


def test_complete_native_shards_enter_shared_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: "candidate-tree\n",
    )
    monkeypatch.setattr(
        evaluator,
        "evaluate_native",
        lambda *args: {"status": "PASS", "passed": True, "metrics": {"positive_fraction": 1.0}},
    )
    result = evaluator._native_evidence(tmp_path, tmp_path / "shards", {"candidate-tree"}, {})
    assert result["status"] == "PASS"
    assert result["passed"] is True


def test_frozen_sessions_are_the_two_index_intersection(tmp_path: Path) -> None:
    frozen = tmp_path / "data" / "frozen"
    frozen.mkdir(parents=True)
    (frozen / "sh000300.csv").write_text(
        "date,close\n2023-01-03,1\n2023-01-04,1\n2023-01-05,1\n"
    )
    (frozen / "sh000682.csv").write_text(
        "date,close\n2023-01-03,1\n2023-01-05,1\n2023-01-06,1\n"
    )
    assert evaluator._common_sessions(tmp_path) == ["2023-01-03", "2023-01-05"]


def test_only_two_audited_native_runners_are_economically_equivalent() -> None:
    cells = [
        {"method": "native_public_backtest", "runner_sha256": digest}
        for digest in (
            evaluator.LEGACY_NATIVE_RUNNER_SHA256,
            evaluator.FULL_CONFIG_NATIVE_RUNNER_SHA256,
        )
    ]
    result = evaluator._runner_compatibility(cells)
    assert result["native_equivalent_migration"] is True
    native = result["native"]
    assert isinstance(native, dict)
    assert set(native) == {
        evaluator.LEGACY_NATIVE_RUNNER_SHA256,
        evaluator.FULL_CONFIG_NATIVE_RUNNER_SHA256,
    }
    with pytest.raises(ValueError, match="unknown native runner"):
        evaluator._runner_compatibility(
            [*cells, {"method": "native_public_backtest", "runner_sha256": "unknown"}]
        )


def test_trace_reuse_runner_and_adapter_must_remain_uniform() -> None:
    cells = [
        {
            "method": "native_public_trace_reuse",
            "runner_sha256": "runner",
            "adapter_sha256": adapter,
        }
        for adapter in ("first", "second")
    ]
    with pytest.raises(ValueError, match="trace-reuse"):
        evaluator._runner_compatibility(cells)
