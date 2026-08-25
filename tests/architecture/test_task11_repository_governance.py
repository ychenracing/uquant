from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from typing import cast

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_sha256

from ._analysis import ROOT

_INVENTORY = ROOT / "artifacts/architecture_refactor/cleanup_inventory.json"
_CLASSIFICATIONS = {
    "KEEP_AUTHORITATIVE",
    "KEEP_REFERENCE",
    "CONSOLIDATE_THEN_DELETE",
    "DELETE_REDUNDANT",
    "EXTERNALIZE_RAW",
    "UNRESOLVED_KEEP",
}
_HIGH_RISK_ANCHORS = {
    "artifacts/architecture_refactor/baseline_inventory.json",
    "artifacts/architecture_refactor/task10_governance_inventory.json",
    "artifacts/phase2/champion-generalization-matrix.json",
    "artifacts/sentinel/risk_differential/closure.json",
    "benchmarks/future_holdout_lane_registry.json",
    "benchmarks/source_surface_registry.json",
    "data/frozen/DATA_MANIFEST.json",
    "pyproject.toml",
    "requirements.txt",
}
_CANONICAL_DOCS = (
    ROOT / "README.md",
    ROOT / "docs/ARCHITECTURE.md",
    ROOT / "docs/CONFIGURATION.md",
    ROOT / "docs/DEVELOPMENT.md",
    ROOT / "docs/OPERATIONS.md",
    ROOT / "docs/PERFORMANCE.md",
    ROOT / "docs/QUALITY.md",
    ROOT / "docs/RISK_SENTINEL.md",
    ROOT / "docs/STRATEGY.md",
    ROOT / "docs/decisions/0001-economic-authority-and-causal-execution.md",
    ROOT / "docs/decisions/0002-source-identity-and-holdout-epochs.md",
)
_LOCAL_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _candidate_paths() -> set[str]:
    return {
        *(
            path.relative_to(ROOT).as_posix()
            for directory in (
                ROOT / "docs/superpowers/plans",
                ROOT / "docs/superpowers/specs",
                ROOT / "docs/reviews",
                ROOT / "artifacts/current_heads/diagnostics",
            )
            for path in directory.glob("*")
            if path.is_file()
        ),
        "artifacts/phase1/diagnostics/phase1-history.bundle",
        "research/__init__.py",
        *_HIGH_RISK_ANCHORS,
    }


def _inventory() -> dict[str, object]:
    return cast(dict[str, object], json.loads(_INVENTORY.read_text(encoding="utf-8")))


def test_task11_cleanup_inventory_covers_only_bounded_candidates_and_high_risk_evidence() -> None:
    payload = _inventory()
    assert payload["schema_version"] == 1
    assert payload["contract"] == "uquant-task11-bounded-cleanup-inventory-v1"
    assert payload["candidate_policy"] == (
        "Only delete/move/externalize/authority-change candidates and high-risk evidence; "
        "one inconclusive search means UNRESOLVED_KEEP."
    )
    entries = cast(list[dict[str, object]], payload["entries"])
    by_path = {str(entry["path"]): entry for entry in entries}
    assert set(by_path) == _candidate_paths()
    assert len(by_path) == 39
    assert set(by_path) >= _HIGH_RISK_ANCHORS
    assert payload["deleted_paths"] == []
    assert payload["externalized_paths"] == []

    unsealed = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    assert payload["canonical_sha256"] == canonical_json_sha256(unsealed)
    for relative, entry in by_path.items():
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink()
        content = path.read_bytes()
        assert entry["size_bytes"] == len(content)
        assert entry["content_sha256"] == hashlib.sha256(content).hexdigest()
        assert entry["classification"] in _CLASSIFICATIONS
        references = cast(list[str], entry["live_references"])
        assert references == sorted(set(references))
        assert isinstance(entry["authority_reason"], str) and entry["authority_reason"]
        assert entry["replacement_path"] is None or (ROOT / str(entry["replacement_path"])).exists()
        recovery = cast(dict[str, object], entry["recovery"])
        assert recovery["status"] in {"GIT_OBJECT", "IN_REPOSITORY", "NOT_EXTERNALIZED"}
        assert isinstance(recovery["command"], str) and recovery["command"]

    assert by_path["requirements.txt"]["classification"] == "KEEP_AUTHORITATIVE"
    assert by_path["research/__init__.py"]["classification"] == "KEEP_REFERENCE"
    assert (
        by_path["artifacts/phase1/diagnostics/phase1-history.bundle"]["classification"]
        == "UNRESOLVED_KEEP"
    )
    assert (
        by_path["benchmarks/source_surface_registry.json"]["classification"]
        == "KEEP_AUTHORITATIVE"
    )


def test_task11_distribution_declares_only_the_production_namespace() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    discovery = project["tool"]["setuptools"]["packages"]["find"]
    assert discovery == {
        "include": ["uquant*"],
        "exclude": ["research*", "scripts*", "tests*"],
    }
    assert project["project"]["version"] == "1.1.0"
    assert DEFAULT_CONFIG.risk_sentinel_mode == "FREEZE_ONLY"


def test_task11_console_pytest_collects_non_distribution_research_tests() -> None:
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "pytest",
            "--collect-only",
            "-q",
            "tests/test_committed_economic_equivalence.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "tests/test_committed_economic_equivalence.py: 3" in output


def test_task11_repository_only_cli_examples_use_module_execution() -> None:
    governed_examples = (
        ROOT / ".github/workflows/ci.yml",
        ROOT / "README.md",
        ROOT / "docs/OPERATIONS.md",
    )
    for path in governed_examples:
        assert "uv run python scripts/" not in path.read_text(encoding="utf-8"), path

    for module in ("scripts.future_holdout", "scripts.production_observation"):
        completed = subprocess.run(
            ["uv", "run", "--no-sync", "python", "-m", module, "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


def test_task11_canonical_docs_have_resolved_internal_links_and_current_authority_terms() -> None:
    assert all(path.is_file() for path in _CANONICAL_DOCS)
    corpus: list[str] = []
    failures: list[str] = []
    for path in _CANONICAL_DOCS:
        text = path.read_text(encoding="utf-8")
        corpus.append(text)
        for raw_target in _LOCAL_LINK.findall(text):
            target = raw_target.strip().split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists() or ROOT.resolve() not in (resolved, *resolved.parents):
                failures.append(f"{path.relative_to(ROOT)} -> {raw_target}")
    assert failures == []
    joined = "\n".join(corpus)
    for required in (
        "Base Risk",
        "PortfolioAllocator",
        "Decision → Order → Fill → AccountState",
        "FREEZE_ONLY",
        "Future Holdout",
        "no-backfill",
        "KEEP_AUTHORITATIVE",
        "UNRESOLVED_KEEP",
        "requirements.txt",
        "source epoch",
        "uquant*",
    ):
        assert required in joined
    for relative in (
        "artifacts/architecture_refactor/baseline_inventory.json",
        "benchmarks/source_surface_registry.json",
        "data/frozen/DATA_MANIFEST.json",
    ):
        assert relative in joined
        assert (ROOT / relative).is_file()


def test_task11_inventory_paths_are_tracked_and_reference_evidence_is_reproducible() -> None:
    entries = cast(list[dict[str, object]], _inventory()["entries"])
    for entry in entries:
        relative = str(entry["path"])
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert tracked.returncode == 0, relative
