from __future__ import annotations

import ast
import functools
import hashlib
import json
import re
import subprocess
import tomllib
from typing import cast

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.runtime_identity import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS
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
    "artifacts/architecture_refactor/source_epoch_v2.json",
    "artifacts/architecture_refactor/wheels/uquant-1.1.0-py3-none-any.whl",
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
    ROOT / "docs/HOLDOUT.md",
    ROOT / "docs/OPERATIONS.md",
    ROOT / "docs/PERFORMANCE.md",
    ROOT / "docs/QUALITY.md",
    ROOT / "docs/RISK_SENTINEL.md",
    ROOT / "docs/STRATEGY.md",
    ROOT / "docs/decisions/0001-economic-authority-and-causal-execution.md",
    ROOT / "docs/decisions/0002-source-identity-and-holdout-epochs.md",
)
_REFERENCE_DOCS = {
    "artifacts/current_heads/analysis.md",
    "artifacts/sentinel/risk_differential/analysis.md",
}
_CURRENT_SOURCE_NARRATIVE = (
    "uquant/application/backtest.py",
    "uquant/application/decision.py",
    "uquant/application/metrics.py",
    "uquant/application/risk_timeline_cache.py",
    "uquant/broker.py",
    "uquant/config/model.py",
    "uquant/execution/fees.py",
    "uquant/execution/market_constraints.py",
    "uquant/execution/open_execution.py",
    "uquant/execution/order_planning.py",
    "uquant/execution/pending.py",
    "uquant/execution/reconciliation.py",
    "uquant/execution/tranches.py",
    "uquant/portfolio/allocator.py",
    "uquant/portfolio/freeze.py",
    "uquant/portfolio/leaders/admission.py",
    "uquant/portfolio/leaders/lifecycle.py",
    "uquant/portfolio/leaders/targets.py",
    "uquant/portfolio/risk_reduction.py",
    "uquant/portfolio/strategic/discovery.py",
    "uquant/portfolio/strategic/lifecycle.py",
    "uquant/risk_sentinel/integration.py",
)
_DELETED_DOCS = {
    ".superpowers/sdd/2026-08-14-phase2-ai-era-generalization/task-7-report.md",
    ".superpowers/sdd/2026-08-14-phase2-ai-era-generalization/task-8-report.md",
    "docs/reviews/2026-08-17-balanced-review.md",
    "docs/reviews/2026-08-18-future-holdout-operations.md",
    "docs/reviews/2026-08-18-sentinel-freeze-only.md",
    "docs/reviews/2026-08-19-phase5-gross-cap-rejection-archive.md",
    "docs/reviews/2026-08-20-risk-sentinel-consolidation.md",
    "docs/reviews/phase7_artifact_review.md",
    "docs/reviews/phase7_rejection_summary.md",
    "docs/superpowers/plans/2026-08-13-phase1-ai-era-validation.md",
    "docs/superpowers/plans/2026-08-14-phase2-ai-era-generalization.md",
    "docs/superpowers/plans/2026-08-17-balanced-code-and-documentation-review.md",
    "docs/superpowers/plans/2026-08-18-current-heads-baseline.md",
    "docs/superpowers/plans/2026-08-18-future-holdout-lanes.md",
    "docs/superpowers/plans/2026-08-19-risk-sentinel-causal-timeline.md",
    "docs/superpowers/plans/2026-08-19-risk-sentinel-shadow.md",
    "docs/superpowers/plans/2026-08-20-risk-sentinel-consolidation.md",
    "docs/superpowers/specs/2026-08-14-phase2-ai-era-generalization-design.md",
    "docs/superpowers/specs/2026-08-17-balanced-code-and-documentation-review-design.md",
    "docs/superpowers/specs/2026-08-19-risk-sentinel-causal-timeline-design.md",
    "docs/superpowers/specs/2026-08-19-risk-sentinel-shadow-design.md",
    "docs/superpowers/specs/2026-08-20-risk-sentinel-consolidation-design.md",
}
_RELOCATED_DOCS = {
    "docs/reviews/2026-08-18-current-heads-baseline.md": "artifacts/current_heads/analysis.md",
    "docs/reviews/2026-08-21-risk-differential-closure.md": (
        "artifacts/sentinel/risk_differential/analysis.md"
    ),
}
_LOCAL_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_BASE_TREE = "da2149e94d43e224250699ce033cef664d44ec5d"


@functools.cache
def _tracked_contents() -> dict[str, bytes]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    inventory_relative = _INVENTORY.relative_to(ROOT).as_posix()
    return {
        tracked: (ROOT / tracked).read_bytes()
        for tracked in completed.stdout.splitlines()
        if tracked != inventory_relative
    }


def _exact_path_references(relative: str) -> list[str]:
    needle = relative.encode()
    return sorted(
        tracked
        for tracked, content in _tracked_contents().items()
        if needle in content
    )


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
        *_REFERENCE_DOCS,
        *_HIGH_RISK_ANCHORS,
    }


def _inventory() -> dict[str, object]:
    return cast(dict[str, object], json.loads(_INVENTORY.read_text(encoding="utf-8")))


def test_documentation_cleanup_inventory_records_current_authority_and_history() -> None:
    payload = _inventory()
    assert payload["schema_version"] == 2
    assert payload["contract"] == "uquant-documentation-governance-cleanup-v2"
    assert payload["candidate_policy"] == (
        "Keep canonical documents and sealed evidence; relocate durable analyses beside their "
        "machine evidence; delete completed plans, task reports, and review transcripts after "
        "live links and current authority are replaced."
    )
    entries = cast(list[dict[str, object]], payload["entries"])
    by_path = {str(entry["path"]): entry for entry in entries}
    assert set(by_path) == _candidate_paths()
    assert len(by_path) == 21
    assert set(by_path) >= _HIGH_RISK_ANCHORS
    assert set(cast(list[str], payload["deleted_paths"])) == _DELETED_DOCS
    assert all(not (ROOT / relative).exists() for relative in _DELETED_DOCS)
    relocations = cast(list[dict[str, str]], payload["relocated_paths"])
    assert {row["from"]: row["to"] for row in relocations} == _RELOCATED_DOCS
    assert all(not (ROOT / source).exists() for source in _RELOCATED_DOCS)
    assert all((ROOT / target).is_file() for target in _RELOCATED_DOCS.values())
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
        assert references == _exact_path_references(relative)
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
    assert all(by_path[path]["classification"] == "KEEP_REFERENCE" for path in _REFERENCE_DOCS)

    deletion_recovery = cast(dict[str, str], payload["deletion_recovery"])
    assert deletion_recovery == {
        "base_tree": _BASE_TREE,
        "command_template": f"git show {_BASE_TREE}:{{path}}",
        "status": "GIT_OBJECT",
    }
    base_tree = subprocess.run(
        ["git", "cat-file", "-e", f"{_BASE_TREE}^{{tree}}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert base_tree.returncode == 0, base_tree.stderr.decode()
    for relative in _DELETED_DOCS:
        recovered = subprocess.run(
            ["git", "show", f"{_BASE_TREE}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        assert recovered.returncode == 0, relative


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


def test_current_operator_help_names_authoritative_entry_points_and_permissions() -> None:
    expectations = {
        "scripts.future_holdout": (
            "usage: python -m scripts.future_holdout",
            "validate-static-lanes",
        ),
        "scripts.production_observation": (
            "usage: python -m scripts.production_observation",
            "evidence-only uquant production observation cycle",
        ),
        "uquant.risk_sentinel": (
            "usage: uquant-sentinel",
            "Offline, read-only Risk Sentinel Shadow diagnostic",
        ),
    }
    for module, required in expectations.items():
        completed = subprocess.run(
            ["uv", "run", "--no-sync", "python", "-m", module, "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        output = completed.stdout + completed.stderr
        assert completed.returncode == 0, output
        assert all(fragment in output for fragment in required), output


def test_performance_guide_binds_the_exact_runtime_windows() -> None:
    performance = (ROOT / "docs/PERFORMANCE.md").read_text(encoding="utf-8")
    for windows in (AI_ERA_WINDOWS, AI_ERA_ACUTE_WINDOWS):
        for name, (start, end) in windows.items():
            assert name in performance
            assert start in performance
            assert end in performance


def test_historical_markdown_declares_its_non_authoritative_boundary() -> None:
    index = ROOT / "artifacts/README.md"
    assert index.is_file()
    for path in sorted((ROOT / "artifacts").rglob("*.md")):
        if path == index:
            continue
        text = path.read_text(encoding="utf-8")
        assert "> **权威级别\uff1a历史证据**" in text, path.relative_to(ROOT)


def test_current_production_narrative_avoids_refactor_timeline_labels() -> None:
    stale = re.compile(r"\b(?:Task|Phase) [0-9]+\b")
    for relative in _CURRENT_SOURCE_NARRATIVE:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert stale.search(text) is None, relative


def test_canonical_docs_describe_the_bounded_dominant_incumbent_exception() -> None:
    governed_docs = (
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/CONFIGURATION.md",
        "docs/STRATEGY.md",
    )
    for relative in governed_docs:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "0001-economic-authority-and-causal-execution.md" in text, relative

    adr = (
        ROOT / "docs/decisions/0001-economic-authority-and-causal-execution.md"
    ).read_text(encoding="utf-8")
    allocator = ast.parse((ROOT / "uquant/portfolio/allocator.py").read_text(encoding="utf-8"))
    retention = next(
        node
        for node in allocator.body
        if isinstance(node, ast.FunctionDef) and node.name == "_dominant_level1_retention"
    )
    implementation_contract = ast.unparse(retention)
    for predicate in (
        "live_symbols == {dominant_symbol}",
        "risk.state in {Risk.NORMAL, Risk.CAUTION}",
        "risk.reduction_level <= 1",
        "sector_guard_active",
        "strategic_damage_guard",
        "acute_sector_evacuation",
        "target_gross >= current_gross - 1e-12",
    ):
        assert predicate in implementation_contract
    for documented_boundary in (
        "live_symbols == {dominant_symbol}",
        "NORMAL/CAUTION",
        "reduction_level <= 1",
        "sector_guard_active",
        "strategic_damage_guard",
        "acute_sector_evacuation",
        "target_gross >= current_gross",
        "strategic_dominant_max_weight",
        "不买入补足",
    ):
        assert documented_boundary in adr


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
