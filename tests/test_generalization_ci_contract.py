from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
OFFICIAL_WINDOWS = (
    "h1_2023",
    "h2_2023",
    "h1_2024",
    "h2_2024",
    "bull_crash_2025_2026",
    "continuous_ai_era",
)
PINNED_ACTIONS = {
    "actions/cache": "5a3ec84eff668545956fd18022155c47e93e2684",
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
}
DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "CONFIGURATION.md",
    ROOT / "docs" / "DEVELOPMENT.md",
    ROOT / "docs" / "OPERATIONS.md",
    ROOT / "docs" / "PERFORMANCE.md",
    ROOT / "docs" / "QUALITY.md",
    ROOT / "docs" / "STRATEGY.md",
)


def _workflow(name: str) -> dict[str, Any]:
    payload = yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _named_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in _steps(job) if step.get("name") == name)


def _assert_unconditional_pr_and_main(workflow: dict[str, Any]) -> None:
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert triggers["push"] == {"branches": ["main"]}
    pull_request = triggers["pull_request"]
    assert pull_request in ("", None) or pull_request == {}
    assert "paths" not in triggers["push"]
    if isinstance(pull_request, dict):
        assert "paths" not in pull_request


def _assert_manual_only(workflow: dict[str, Any]) -> None:
    assert workflow["on"] == {"workflow_dispatch": {}}


def _assert_locked_runtime(workflow: dict[str, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["UV_VERSION"] == "0.11.33"
    assert "concurrency" not in workflow
    for job in workflow["jobs"].values():
        expected_python = (
            "3.12.10" if job["runs-on"] == "windows-latest" else "3.12.13"
        )
        for step in _steps(job):
            uses = step.get("uses")
            if uses is not None:
                repository, ref = uses.split("@", maxsplit=1)
                assert ref == PINNED_ACTIONS[repository]
                assert re.fullmatch(r"[0-9a-f]{40}", ref)
            if uses is not None and uses.startswith("actions/setup-python@"):
                assert step["with"]["python-version"] == expected_python


def _assert_always_blocking_summary(
    workflow: dict[str, Any],
    *,
    job_id: str,
    check_name: str,
    needs: set[str],
) -> None:
    summary = workflow["jobs"][job_id]
    assert summary["name"] == check_name
    assert summary["if"] == "${{ always() }}"
    actual_needs = summary["needs"]
    if isinstance(actual_needs, str):
        actual_needs = [actual_needs]
    assert set(actual_needs) == needs
    run = "\n".join(str(step.get("run", "")) for step in _steps(summary))
    for dependency in needs:
        assert f"needs.{dependency}.result" in run
    assert "exit 1" in run


def test_engineering_summary_catches_quality_or_security_failure_without_skipping() -> None:
    """Catches branch protection depending on separate jobs that skip their final conclusion."""
    workflow = _workflow("ci.yml")
    _assert_unconditional_pr_and_main(workflow)
    _assert_locked_runtime(workflow)
    _assert_always_blocking_summary(
        workflow,
        job_id="engineering",
        check_name="Engineering",
        needs={"quality", "test_matrix", "coverage", "security", "windows_smoke"},
    )
    quality_steps = _steps(workflow["jobs"]["quality"])
    assert "workflow_dispatch" in workflow["on"]
    holdout_step = next(
        step
        for step in quality_steps
        if step.get("name") == "Future holdout contract and lane integrity"
    )
    assert "validate-static-lanes" in holdout_step["run"]
    assert "report-lanes" not in holdout_step["run"]
    journal_step = next(
        step for step in quality_steps if step.get("name") == "Manual execution evidence contract"
    )
    assert "journal verify" in journal_step["run"]
    assert "python -m scripts.production_observation --help" in journal_step["run"]
    assert "git check-ignore" in journal_step["run"]
    shards = workflow["jobs"]["test_matrix"]
    assert shards["strategy"]["fail-fast"] == "false"
    assert shards["strategy"]["matrix"]["shard"] == [
        "architecture-foundation",
        "architecture-portfolio",
        "architecture-remaining",
        "application-left",
        "application-right",
    ]
    pytest_step = _named_step(shards, "Run deterministic test shard")
    assert "--cov-fail-under=0" in pytest_step["run"]
    assert "Unknown test shard" in pytest_step["run"]
    coverage_step = _named_step(workflow["jobs"]["coverage"], "Require combined branch coverage")
    assert "coverage combine" in coverage_step["run"]
    assert "coverage report --show-missing --fail-under=85" in coverage_step["run"]

    path = WORKFLOWS / "release-candidate.yml"
    assert path.is_file(), "manual release-candidate workflow is missing"
    workflow = _workflow(path.name)

    assert workflow["name"] == "Release Candidate Source Equality"
    _assert_manual_only(workflow)
    _assert_locked_runtime(workflow)
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"source_equality"}
    job = workflow["jobs"]["source_equality"]
    assert job["env"] == {"UQUANT_RELEASE_CANDIDATE": "1"}
    checkout = next(
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == "0"
    run = _named_step(job, "Run release candidate source equality and contracts")["run"]
    assert "tests/architecture/test_release_acceptance.py" in run
    assert "tests/test_generalization_ci_contract.py" in run
    assert "tag" not in run.lower()
    assert "release" not in run.lower().replace("release_acceptance", "")
    assert "build_reproducible_wheel" not in run
    for forbidden in (
        "git tag",
        "gh release",
        "uv build",
        "python -m build",
        "twine",
        "pyproject.toml",
        "__version__",
    ):
        assert forbidden not in run


def test_security_gate_blocks_only_findings_added_over_the_event_base() -> None:
    """Catches a candidate-only scan or the wrong Git event base weakening the differential."""

    workflow = _workflow("ci.yml")
    security = workflow["jobs"]["security"]
    checkout = next(
        step for step in _steps(security) if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == "0"

    resolve = _named_step(security, "Resolve Bandit comparison base")
    assert resolve["id"] == "bandit-base"
    assert resolve["env"] == {
        "EVENT_NAME": "${{ github.event_name }}",
        "PR_BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "PUSH_BASE_SHA": "${{ github.event.before }}",
    }
    resolve_run = resolve["run"]
    assert "pull_request" in resolve_run
    assert "PUSH_BASE_SHA" in resolve_run
    assert "HEAD^" in resolve_run
    assert 'echo "sha=$base_sha" >> "$GITHUB_OUTPUT"' in resolve_run

    differential = _named_step(security, "Run differential static security scan")
    assert differential["env"]["BASE_SHA"] == "${{ steps.bandit-base.outputs.sha }}"
    run = differential["run"]
    assert "git worktree add --detach" in run
    assert run.count("bandit -q -f json") == 2
    assert ".github/scripts/bandit_differential.py" in run
    assert "--baseline-root" in run
    assert "--candidate-root" in run

    names = [str(step.get("name", "")) for step in _steps(security)]
    assert names.index("Run differential static security scan") < names.index("Audit production dependencies")


def test_performance_summary_catches_path_skips_and_partial_or_stale_performance_evidence() -> None:
    """Catches path-filter skips, a weakened profile, or incomplete HEAD/provenance readback."""
    workflow = _workflow("strategy-performance.yml")
    assert workflow["name"] == "Extended Performance Matrix"
    _assert_manual_only(workflow)
    _assert_locked_runtime(workflow)
    _assert_always_blocking_summary(
        workflow,
        job_id="performance-acceptance",
        check_name="Performance Acceptance",
        needs={"ai-era-performance"},
    )

    run_job = workflow["jobs"]["ai-era-performance"]
    gate = _named_step(run_job, "Run full AI-Era blocking gate")["run"]
    assert "promotion" in gate
    assert "--profile full" in gate
    assert "--output benchmarks/ai_era_performance.json" in gate

    verify = _named_step(run_job, "Verify exact HEAD and full provenance")
    assert "-m uquant.validation.ci_artifacts performance" in verify["run"]
    assert verify["env"]["UPSTREAM_RESULT"] == "${{ steps.performance-gate.outcome }}"
    upload = _named_step(run_job, "Upload AI-Era performance report")
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["name"] == (
        "ai-era-performance-${{ github.run_id }}-attempt-${{ github.run_attempt }}"
    )


def test_generalization_matrix_catches_missing_window_cancelled_shards_and_failed_artifact_loss() -> None:
    """Catches an incomplete matrix, fail-fast cancellation, or diagnostics lost on failure."""
    workflow = _workflow("strategy-generalization.yml")
    assert workflow["name"] == "Extended Economic Matrix"
    _assert_manual_only(workflow)
    _assert_locked_runtime(workflow)

    shard = workflow["jobs"]["generalization-shard"]
    strategy = shard["strategy"]
    assert strategy["fail-fast"] == "false"
    assert tuple(strategy["matrix"]["window"]) == OFFICIAL_WINDOWS
    assert "${{ matrix.window }}" in shard["name"]
    run = _named_step(shard, "Run exact official-window shard")["run"]
    assert "generalization-matrix" in run
    assert '--window "${{ matrix.window }}"' in run
    upload = _named_step(shard, "Upload window evidence")
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["name"] == (
        "ai-era-generalization-${{ github.run_id }}-attempt-"
        "${{ github.run_attempt }}-${{ matrix.window }}"
    )
    assert "${{ matrix.window }}" in upload["with"]["path"]


def test_generalization_aggregator_catches_incomplete_stale_or_policy_failing_evidence() -> None:
    """Catches missing/extra shards, stale provenance, fabricated cells, or a weakened policy."""
    workflow = _workflow("strategy-generalization.yml")
    aggregate = workflow["jobs"]["generalization-acceptance"]
    assert aggregate["name"] == "Generalization Acceptance"
    assert aggregate["needs"] == "generalization-shard"
    assert aggregate["if"] == "${{ always() }}"

    download = _named_step(aggregate, "Download every shard artifact")
    assert download["if"] == "${{ always() }}"
    assert download["with"]["pattern"] == (
        "ai-era-generalization-${{ github.run_id }}-attempt-"
        "${{ github.run_attempt }}-*"
    )
    assert download["with"]["merge-multiple"] == "false"

    verify = _named_step(aggregate, "Aggregate and validate all evidence")
    assert "-m uquant.validation.ci_artifacts generalization" in verify["run"]
    assert "${{ github.run_id }}" in verify["run"]
    assert "${{ github.run_attempt }}" in verify["run"]
    assert "SHARD_JOB_RESULT" in verify["env"]
    report = _named_step(aggregate, "Upload Generalization diagnostics")
    assert report["if"] == "${{ always() }}"
    assert report["with"]["name"] == (
        "ai-era-generalization-summary-${{ github.run_id }}-attempt-"
        "${{ github.run_attempt }}"
    )


def test_workflows_catch_failure_suppression_and_unpinned_action_regressions() -> None:
    """Catches a failed gate being converted to success or an action floating by branch/tag."""
    for name in (
        "ci.yml",
        "release-candidate.yml",
        "strategic-ownership-acceptance.yml",
        "strategy-performance.yml",
        "strategy-generalization.yml",
    ):
        workflow = _workflow(name)
        rendered_runs: list[str] = []
        for job in workflow["jobs"].values():
            assert job.get("continue-on-error") not in ("true", True)
            for step in _steps(job):
                assert step.get("continue-on-error") not in ("true", True)
                rendered_runs.append(str(step.get("run", "")))
                uses = step.get("uses")
                if uses is not None:
                    repository, ref = uses.split("@", maxsplit=1)
                    assert ref == PINNED_ACTIONS[repository]
                    assert re.fullmatch(r"[0-9a-f]{40}", ref)
        scripts = "\n".join(rendered_runs)
        assert "|| true" not in scripts
        assert "continue-on-error" not in scripts


def test_action_pins_keep_readable_verified_version_comments() -> None:
    """Catches a full SHA losing its human-auditable upstream release identity."""
    for name in (
        "ci.yml",
        "release-candidate.yml",
        "strategic-ownership-acceptance.yml",
        "strategy-performance.yml",
        "strategy-generalization.yml",
    ):
        source = (WORKFLOWS / name).read_text(encoding="utf-8")
        for repository, sha in PINNED_ACTIONS.items():
            if repository not in source:
                continue
            assert re.search(
                rf"uses:\s+{re.escape(repository)}@{sha}\s+#\s+v\d+\.\d+\.\d+",
                source,
            )


def test_artifact_names_bind_each_upload_and_download_to_one_run_attempt() -> None:
    """Catches immutable-v4 collisions or a retry downloading another attempt's shards."""
    engineering = _workflow("ci.yml")
    shard_coverage = _named_step(
        engineering["jobs"]["test_matrix"], "Upload shard coverage"
    )["with"]["name"]
    assert shard_coverage == (
        "engineering-coverage-${{ github.run_id }}-attempt-"
        "${{ github.run_attempt }}-${{ matrix.shard }}"
    )
    download_pattern = _named_step(
        engineering["jobs"]["coverage"], "Download shard coverage"
    )["with"]["pattern"]
    assert download_pattern == (
        "engineering-coverage-${{ github.run_id }}-attempt-"
        "${{ github.run_attempt }}-*"
    )
    coverage = _named_step(engineering["jobs"]["coverage"], "Upload coverage")["with"]["name"]
    assert coverage == "coverage-${{ github.run_id }}-attempt-${{ github.run_attempt }}"

    performance = _workflow("strategy-performance.yml")
    performance = _named_step(
        performance["jobs"]["ai-era-performance"], "Upload AI-Era performance report"
    )["with"]["name"]
    assert performance == (
        "ai-era-performance-${{ github.run_id }}-attempt-${{ github.run_attempt }}"
    )

    generalization = _workflow("strategy-generalization.yml")
    shard_name = _named_step(
        generalization["jobs"]["generalization-shard"], "Upload window evidence"
    )["with"]["name"]
    pattern = _named_step(
        generalization["jobs"]["generalization-acceptance"], "Download every shard artifact"
    )["with"]["pattern"]
    prefix = (
        "ai-era-generalization-${{ github.run_id }}-attempt-${{ github.run_attempt }}"
    )
    assert shard_name == f"{prefix}-${{{{ matrix.window }}}}"
    assert pattern == f"{prefix}-*"


def test_post_checkout_self_binding_artifacts_are_git_ignored() -> None:
    """Catches a generated exact-HEAD artifact becoming eligible for an accidental commit."""
    artifacts = (
        "benchmarks/ai_era_performance.json",
        "benchmarks/ai_era_generalization.json",
        "benchmarks/future_holdout_manifest.json",
    )

    result = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=ROOT,
        input="\n".join(artifacts) + "\n",
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert tuple(result.stdout.splitlines()) == artifacts


def test_real_execution_evidence_is_ignored_and_untracked() -> None:
    """Catches private operator evidence becoming eligible for an accidental commit."""
    evidence = (
        "future_holdout_execution_journal.jsonl",
        "future_holdout_execution_journal.checkpoint.json",
        "future_holdout_lane_report.json",
        "production_observation_backups/example/receipt.json",
        "artifacts/future_holdout_replay.json",
        "artifacts/future_holdout_decision.json",
        "artifacts/future_holdout_checkpoint.json",
    )
    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=ROOT,
        input="\n".join(evidence) + "\n",
        capture_output=True,
        check=False,
        text=True,
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", *evidence],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert ignored.returncode == 0
    assert tuple(ignored.stdout.splitlines()) == evidence
    assert tracked.returncode != 0


@pytest.mark.parametrize("path", DOCS, ids=lambda path: path.name)
def test_each_public_document_catches_non_ai_or_pre_2023_economic_scope_drift(path: Path) -> None:
    """Catches a listed public document widening the economic scope beyond AI-era A shares."""
    text = path.read_text(encoding="utf-8")
    assert "2023" in text
    assert "AI" in text
    assert "warm-up" in text
    assert re.search(r"人工|manual", text, re.IGNORECASE)


def test_public_document_set_catches_incomplete_generalization_contract_or_fake_holdout_claims() -> None:
    """Catches omitted canonical facts or claims that unobserved holdout performance exists."""
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for required in (
        "34",
        "20260810",
        "0..4",
        "5 / 9 / 15 / 20",
        "final_wealth",
        "max_drawdown",
        "account_orders",
        "gross_turnover",
        "annual_turnover",
        "top1_concentration",
        "top3_concentration",
        "pnl_hhi",
        "MARKET_RULE",
        "SAFETY",
        "ECONOMIC",
        "DERIVED",
        "COMPATIBILITY",
        "2026-08-05",
        "2026-08-06",
        "40--60",
        "append-only",
        "broker-independent",
    ):
        assert required in corpus
    assert "realized_pnl + open_pnl = final_equity - initial_cash" in corpus
    assert "cash drag" in corpus
    assert "risk avoidance" in corpus
    assert re.search(r"(null|空值).{0,80}(holdout|留出)", corpus, re.IGNORECASE | re.DOTALL)


def test_configuration_requires_complete_six_window_gate_for_every_default_change() -> None:
    """Catches guidance allowing only selected generalization cells to rerun."""
    configuration = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")

    assert re.search(
        r"任何被接受的默认值变化.{0,80}性能验收.{0,80}完整.{0,30}六窗口泛化",
        configuration,
        re.DOTALL,
    )
    assert "受影响的泛化" not in configuration


def test_public_documents_catch_adjustable_official_contract_and_legacy_smoke_guidance() -> None:
    """Catches obsolete smoke/baseline guidance or instructions to tune the official matrix."""
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    prohibited = (
        r"generalization[_ -]smoke",
        r"generalization_baseline\.json",
        r"random[_ -]seed[_ -]count",
        r"调整.{0,20}(20260810|官方.{0,8}(seed|种子)|官方.{0,8}窗口)",
        r"--start 20(?:1\d|2[0-2])",
        r"(消费|白酒|新能源|非 AI).{0,30}(官方|验证|泛化|证券池)",
        r"(seed|种子).{0,20}(可调整|可以调整|可修改)",
        r"(holdout|留出).{0,30}(final_wealth|收益|回撤)\s*[:=]\s*\d",
    )
    for pattern in prohibited:
        assert re.search(pattern, corpus, re.IGNORECASE) is None
