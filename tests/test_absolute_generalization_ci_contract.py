from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/absolute-generalization-acceptance.yml"
SHARDS = [
    "champion",
    "loo-a",
    "loo-b",
    "loo-c",
    "loo-d",
    "loo-e",
    "loo-f",
    "recovery-and-reachability",
]
PINS = {
    "actions/cache": "5a3ec84eff668545956fd18022155c47e93e2684",
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
}


def _workflow() -> dict[str, Any]:
    raw = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(raw, dict)
    return raw


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    value = job["steps"]
    assert isinstance(value, list)
    return value


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in _steps(job) if item.get("name") == name)


def test_absolute_workflow_is_unconditional_read_only_and_locked() -> None:
    workflow = _workflow()
    assert workflow["name"] == "Absolute Generalization Acceptance"
    assert workflow["on"] == {
        "pull_request": "",
        "push": {"branches": ["main"]},
        "workflow_dispatch": {},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"] == {"UV_VERSION": "0.11.33"}
    assert "concurrency" not in workflow
    for job in workflow["jobs"].values():
        for step in _steps(job):
            uses = step.get("uses")
            if uses:
                repository, pin = uses.split("@", maxsplit=1)
                assert pin == PINS[repository]
            if str(uses).startswith("actions/setup-python@"):
                assert step["with"]["python-version"] == "3.12.13"
        assert "continue-on-error" not in str(job)
        assert "|| true" not in str(job)


def test_absolute_workflow_has_exact_eight_shards_and_identity_bound_cache() -> None:
    workflow = _workflow()
    shard = workflow["jobs"]["absolute-shard"]
    assert shard["strategy"] == {
        "fail-fast": "false",
        "matrix": {"shard": SHARDS},
    }
    command = _step(shard, "Run absolute shard")["run"]
    for value in (
        "scripts/run_absolute_generalization_acceptance.py",
        '${{ matrix.shard }}',
        '${{ github.run_id }}',
        '${{ github.run_attempt }}',
    ):
        assert value in command
    cache = _step(shard, "Restore raw cell cache")
    key = cache["with"]["key"]
    for value in (
        "uv.lock",
        "data/frozen/SHA256SUMS",
        "benchmarks/absolute_generalization_acceptance_contract.json",
        "benchmarks/source_surface_registry.json",
        "uquant/config",
        "uquant/contracts/universe",
        "uquant/validation/absolute_generalization",
        "scripts/run_absolute_generalization_acceptance.py",
        '${{ matrix.shard }}',
    ):
        assert value in key
    upload = _step(shard, "Upload sealed shard manifest")
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["name"] == (
        "absolute-generalization-${{ github.run_id }}-attempt-"
        "${{ github.run_attempt }}-${{ matrix.shard }}"
    )


def test_absolute_final_job_always_aggregates_and_blocks_on_one_conjunction() -> None:
    workflow = _workflow()
    final = workflow["jobs"]["generalization-acceptance"]
    assert final["name"] == "Generalization Acceptance"
    assert final["needs"] == "absolute-shard"
    assert final["if"] == "${{ always() }}"
    download = _step(final, "Download every sealed shard manifest")
    assert download["if"] == "${{ always() }}"
    assert download["with"]["merge-multiple"] == "false"
    aggregate = _step(final, "Aggregate exact eight shard manifests")
    assert aggregate["if"] == "${{ always() }}"
    assert aggregate["env"] == {
        "SHARD_JOB_RESULT": "${{ needs.absolute-shard.result }}"
    }
    run = aggregate["run"]
    assert "scripts/run_absolute_generalization_acceptance.py" in run
    assert '--shard final' in run
    assert '--upstream-result "$SHARD_JOB_RESULT"' in run
    assert "absolute-generalization" in run
    upload = _step(final, "Upload sealed final report")
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["if-no-files-found"] == "error"


def test_extended_workflows_remain_manual_and_terminal_names_do_not_collide() -> None:
    paths = {
        "performance": ROOT / ".github/workflows/strategy-performance.yml",
        "economic": ROOT / ".github/workflows/strategy-generalization.yml",
    }
    loaded = {
        name: yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for name, path in paths.items()
    }
    assert loaded["performance"]["on"] == {"workflow_dispatch": {}}
    assert loaded["economic"]["on"] == {"workflow_dispatch": {}}
    assert loaded["economic"]["jobs"]["generalization-acceptance"]["name"] == (
        "Extended Economic Matrix Diagnostics"
    )
