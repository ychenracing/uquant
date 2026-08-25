from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
DIAGNOSTICS = ROOT / "artifacts" / "phase1" / "diagnostics"
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
METRICS = {
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "annual_turnover",
    "gross_turnover",
}
RUNNER_COMMIT = "bef641dba3ebc7de011b7e8d621d2be95b66643c"
RUNNER_SOURCE_SHA256 = (
    "d7a27a8d476b8b125b7e144bf222b3c6d5639482e549a9257ce1e9e7b53a106b"
)
HISTORY_BUNDLE = DIAGNOSTICS / "phase1-history.bundle"
HISTORY_BUNDLE_SHA256 = (
    "24c1d66676cc9c35361ff51e5293f252136e545073d7b4cd63bbf97034b063c1"
)


def _load(name: str) -> dict[str, Any]:
    payload = json.loads((DIAGNOSTICS / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _assert_common_contract(payload: dict[str, Any]) -> None:
    assert payload["schema_version"] == 2
    assert payload["diagnostic_only"] is True
    assert payload["current_production_evidence"] is False
    assert payload["economic_start_policy"] == "2023-01-01"
    generated_at = datetime.fromisoformat(payload["generated_at"])
    assert generated_at.tzinfo is not None
    assert generated_at >= datetime(2026, 8, 13, 22, 15, tzinfo=UTC)
    provenance = payload["provenance"]
    assert provenance["data"] == {
        "snapshot_id": "20260809T094222Z-causal-tech-index-rebase",
        "manifest_sha256": hashlib.sha256(
            (ROOT / "data" / "frozen" / "DATA_MANIFEST.json").read_bytes()
        ).hexdigest(),
        "checksums_sha256": hashlib.sha256(
            (ROOT / "data" / "frozen" / "SHA256SUMS").read_bytes()
        ).hexdigest(),
    }
    runtime = provenance["diagnostic_runtime"]
    assert runtime["python_full_version"].startswith("3.12.")
    assert runtime["numpy_version"] == "2.5.1"
    assert runtime["pandas_version"] == "3.0.5"
    assert runtime["uv_version"] == "0.11.33"
    assert runtime["uv_lock_sha256"] == hashlib.sha256(
        (ROOT / "uv.lock").read_bytes()
    ).hexdigest()
    method = provenance["method"]
    assert method["runner"] == "scripts/run_phase1_diagnostic.py"
    assert method["runner_commit"] == RUNNER_COMMIT
    assert method["runner_source_sha256"] == RUNNER_SOURCE_SHA256
    assert method["history_bundle"] == str(HISTORY_BUNDLE.relative_to(ROOT))
    assert method["history_bundle_sha256"] == HISTORY_BUNDLE_SHA256
    assert method["replay_exit_code"] == 0


def _assert_metrics(metrics: dict[str, Any]) -> None:
    assert set(metrics) == METRICS
    assert all(math.isfinite(float(value)) for value in metrics.values())


def _assert_replay_closure(commands: list[str], *, trace_count: int) -> None:
    parsed = [shlex.split(command) for command in commands]
    trace_commands = [parts for parts in parsed if "trace" in parts]
    compare_commands = [parts for parts in parsed if "compare" in parts]
    assert len(trace_commands) == trace_count
    assert len(compare_commands) == 1
    assert parsed[0] == [
        "git",
        "fetch",
        str(HISTORY_BUNDLE.relative_to(ROOT)),
        "HEAD:refs/remotes/phase1-evidence/head",
    ]
    assert any(
        parts[:5]
        == [
            "git",
            "worktree",
            "add",
            "--detach",
            "/tmp/uquant-diagnostic-runner",
        ]
        and parts[-1] == RUNNER_COMMIT
        for parts in parsed
    )
    assert all(
        "/tmp/uquant-diagnostic-runner/scripts/run_phase1_diagnostic.py" in parts
        for parts in (*trace_commands, *compare_commands)
    )
    assert [
        "uv",
        "sync",
        "--project",
        "/tmp/uquant-diagnostic-runner",
        "--frozen",
        "--extra",
        "dev",
    ] in parsed
    assert all(
        "/tmp/uquant-diagnostic-runner/.venv/bin/python" in parts
        for parts in (*trace_commands, *compare_commands)
    )
    outputs = {
        parts[parts.index("--output") + 1]
        for parts in trace_commands
        if "--output" in parts
    }
    compare = compare_commands[0]
    assert compare[compare.index("--left") + 1] in outputs
    assert compare[compare.index("--right") + 1] in outputs


def test_performance_history_bundle_supplies_every_local_evidence_commit(
    tmp_path: Path,
) -> None:
    assert hashlib.sha256(HISTORY_BUNDLE.read_bytes()).hexdigest() == (
        HISTORY_BUNDLE_SHA256
    )
    repository = tmp_path / "replay"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "fetch",
            str(ROOT),
            "685c600d0af5d85af87fb6553df81d4e4b10c358:refs/heads/main",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "fetch",
            str(HISTORY_BUNDLE),
            "HEAD:refs/remotes/phase1-evidence/head",
        ],
        check=True,
        capture_output=True,
    )
    for commit in (
        "9bb58420365b471ee11b4cdfe31793008233ad50",
        "27521a8170aa4a7620d9727110f6d9abb3770d76",
        "4391d189ddc74f6dd9ee92dd108582a9a071ff9f",
        RUNNER_COMMIT,
        "852a66fe8275227ef18e0732dec85f17c45338bf",
    ):
        subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-e", f"{commit}^{{commit}}"],
            check=True,
        )


def test_first_divergence_evidence_is_complete_and_replayable() -> None:
    payload = _load("first-divergence.json")
    _assert_common_contract(payload)
    assert payload["reference_anchor"] == {
        "commit": "ea4fb1cef59256f76ef9f810440c87ef53108aa2",
        "role": "frozen economic threshold and promotion reference",
        "daily_trace_status": "unavailable_at_source_commit",
        "reason": (
            "The anchor predates research/first_divergence.py. Commit-boundary "
            "traces use the earliest source-compatible adapters, while ea4fb1 "
            "remains the immutable economic reference."
        ),
    }
    assert payload["causal_stage_order"] == [
        "reference_context",
        "leaders",
        "risk",
        "opportunity",
        "targets",
        "orders",
        "fills",
    ]
    assert set(payload["comparisons"]) == {"mixed_2023", "choppy_2024", "bull_2025_2026"}
    for comparison in payload["comparisons"].values():
        assert "commands" not in comparison
        assert date.fromisoformat(comparison["interval"]["start"]) >= date(2023, 1, 1)
        assert comparison["replay"]["left_exit_code"] == 0
        assert comparison["replay"]["right_exit_code"] == 0
        assert comparison["replay"]["compare_exit_code"] == 0
        assert comparison["replay"]["commands"]
        _assert_replay_closure(comparison["replay"]["commands"], trace_count=2)
        for side in ("left", "right"):
            source = comparison[side]
            assert COMMIT.fullmatch(source["commit"])
            assert SHA256.fullmatch(source["source_sha256"])
            assert SHA256.fullmatch(source["trace_adapter_sha256"])
            assert SHA256.fullmatch(source["effective_config_sha256"])
            assert SHA256.fullmatch(source["uv_lock_sha256"])
            assert SHA256.fullmatch(source["trace_sha256"])
            _assert_metrics(source["metrics"])
        assert comparison["first_divergence"]["first_stage"] in payload[
            "causal_stage_order"
        ]
        assert comparison["first_executable_divergence"]["first_stage"] in {
            "targets",
            "orders",
            "fills",
        }


def test_ablation_evidence_closes_each_reported_regression() -> None:
    payload = _load("ablation.json")
    _assert_common_contract(payload)
    cases = payload["ablations"]
    assert set(cases) == {
        "mixed_2023_crisis_severity",
        "choppy_2024_damage_turnover",
        "choppy_2024_repair_wealth",
        "bull_level1_multi_family_cap",
        "bull_risk_off_sensitivity",
    }
    for case in cases.values():
        assert date.fromisoformat(case["interval"]["start"]) >= date(2023, 1, 1)
        assert case["replay"]["candidate_exit_code"] == 0
        assert case["replay"]["counterfactual_exit_code"] == 0
        assert case["replay"]["compare_exit_code"] == 0
        assert case["replay"]["commands"]
        _assert_replay_closure(case["replay"]["commands"], trace_count=2)
        for side in ("candidate", "counterfactual"):
            assert SHA256.fullmatch(case[side]["source_sha256"])
            assert SHA256.fullmatch(case[side]["trace_adapter_sha256"])
            assert SHA256.fullmatch(case[side]["effective_config_sha256"])
            assert SHA256.fullmatch(case[side]["trace_sha256"])
            _assert_metrics(case[side]["metrics"])
        expected_delta = {
            name: case["candidate"]["metrics"][name]
            - case["counterfactual"]["metrics"][name]
            for name in METRICS
        }
        assert case["candidate_minus_counterfactual"] == pytest.approx(expected_delta)
        carrier = case["mechanism_carrier"]
        if carrier["type"] == "patch":
            patch = ROOT / carrier["path"]
            assert patch.is_file()
            assert hashlib.sha256(patch.read_bytes()).hexdigest() == carrier["sha256"]
            counterfactual_command = next(
                command
                for command in case["replay"]["commands"]
                if "trace" in command and "counterfactual" in command
            )
            assert (
                f"--expected-patch-sha256 {carrier['sha256']}"
                in counterfactual_command
            )
            compare_command = next(
                command
                for command in case["replay"]["commands"]
                if "compare" in command
            )
            assert "--require-same-config" in compare_command
            assert any(
                command.startswith("git -C /tmp/uquant-ablation apply ")
                and carrier["path"] in command
                for command in case["replay"]["commands"]
            )
        else:
            assert carrier["type"] == "config"
            counterfactual_command = next(
                command
                for command in case["replay"]["commands"]
                if "trace" in command and "counterfactual" in command
            )
            for name, values in carrier["changes"].items():
                assert f"--set {name}={values['counterfactual']}" in counterfactual_command

    assert cases["mixed_2023_crisis_severity"]["candidate_minus_counterfactual"][
        "max_drawdown"
    ] <= -0.06
    assert cases["choppy_2024_damage_turnover"]["candidate_minus_counterfactual"][
        "annual_turnover"
    ] <= -0.45
    wealth_case = cases["choppy_2024_repair_wealth"]
    assert wealth_case["candidate"]["metrics"]["final_wealth"] >= 1.7314
    assert wealth_case["counterfactual"]["metrics"]["final_wealth"] < 1.7314
    assert wealth_case["candidate_minus_counterfactual"]["final_wealth"] >= 0.03
    bull = cases["bull_level1_multi_family_cap"]
    assert bull["candidate_minus_counterfactual"]["final_wealth"] >= 1.0
    assert bull["candidate_minus_counterfactual"]["account_orders"] <= -5
    assert 0.50 <= bull["historical_wealth_recovery_fraction_explained"] <= 0.52
    assert cases["bull_risk_off_sensitivity"]["classification"] == "local_sensitivity_only"
    sensitivity = cases["bull_risk_off_sensitivity"]
    assert sensitivity["counterfactual"]["trace_sha256"] == (
        "ff61970f060b9fad82028cd2f5f09da3a5f164dd93b7c19e6f50932505c5aa54"
    )
    assert sensitivity["counterfactual"]["metrics"]["gross_turnover"] == pytest.approx(
        16.12911600065
    )
    executable = sensitivity["first_executable_divergence"]
    assert executable["date"] == "2025-10-13"
    assert executable["first_stage"] == "orders"
    assert executable["left"]["orders"] == [
        {
            "exit_kind": "crisis",
            "reason_code": "crisis",
            "side": "SELL",
            "symbol": "sz300308",
            "target_weight": 0.364304772328,
        }
    ]
    assert executable["right"]["orders"] == [
        {
            "exit_kind": "crisis",
            "reason_code": "crisis",
            "side": "SELL",
            "symbol": "sz300308",
            "target_weight": 0.344304772328,
        }
    ]


def test_performance_workflow_tracks_every_production_identity_input() -> None:
    workflow = (ROOT / ".github" / "workflows" / "strategy-performance.yml").read_text(
        encoding="utf-8"
    )
    assert "paths:" not in workflow
    assert "--output benchmarks/ai_era_performance.json" in workflow
    assert "benchmarks/ai_era_performance.json" in workflow
    assert "Verify exact HEAD and full provenance" in workflow
    assert "-m uquant.validation.ci_artifacts phase1" in workflow
    assert "UPSTREAM_RESULT: ${{ steps.phase1-gate.outcome }}" in workflow
    assert "--upstream-result \"$UPSTREAM_RESULT\"" in workflow
    assert "artifacts/phase1/ci/phase1-validation.json" in workflow
    assert "ai-era-performance-${{ github.run_id }}-attempt-${{ github.run_attempt }}" in workflow
    assert "/benchmarks/ai_era_performance.json" in (ROOT / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_accepted_candidate_matrix_is_retained() -> None:
    candidate = json.loads(
        (DIAGNOSTICS.parent / "candidates" / "ai-era-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    assert candidate["passed"] is True
    assert candidate["failures"] == []
    assert len(candidate["cells"]) == 30
    assert len(candidate["protected"]) == 15
    binding = candidate["provenance"]["binding"]
    assert binding["production_commit"] == (
        "27521a8170aa4a7620d9727110f6d9abb3770d76"
    )
    assert binding["effective_config_sha256"] == (
        "023d709731196a325d9cd03e95ece92e4baf63d2c5c66bb9f7d0e7a190e7bf20"
    )
    assert binding["data_manifest_sha256"] == hashlib.sha256(
        (ROOT / "data" / "frozen" / "DATA_MANIFEST.json").read_bytes()
    ).hexdigest()
