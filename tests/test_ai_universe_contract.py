from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uquant.leader import INDUSTRY, REFERENCE_UNIVERSE
from uquant.validation.universe import (
    CANONICAL_INDUSTRIES,
    FROZEN_CHAMPION_COMMIT,
    GITHUB_PHASE1_ARTIFACT_SHA256,
    REQUIRED_FROZEN_CHAMPION_SHA256,
    ai_universe_manifest_bytes,
    canonical_sha256,
    frozen_champion_bytes,
    load_ai_universe,
    load_phase1_frozen_champion,
)

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_champion_preserves_every_reviewed_phase1_identity() -> None:
    """Breaks if a frozen input is replaced by a candidate's own identity."""
    champion = load_phase1_frozen_champion()

    assert champion.production_commit == FROZEN_CHAMPION_COMMIT
    assert champion.production_commit == "cf8fecff76564fd4ed87faa0da336a06d433fd93"
    assert champion.github_artifact_sha256 == GITHUB_PHASE1_ARTIFACT_SHA256
    assert champion.github_artifact_sha256 == (
        "86d894f46a22740cb4bc59a279cb2150927f312947859ad2559e3a17b45f5deb"
    )
    assert champion.production_source_sha256 == "1ba818600877ee07558d14589df8054100368706b88455e05bd33f9adaf05199"
    assert champion.effective_config_sha256 == "023d709731196a325d9cd03e95ece92e4baf63d2c5c66bb9f7d0e7a190e7bf20"
    assert champion.data_snapshot_id == "20260809T094222Z-causal-tech-index-rebase"
    assert champion.uv_lock_sha256 == "4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61"


@pytest.mark.parametrize(
    ("group", "mutate"),
    [
        ("production", lambda payload: payload["production"].update(source_sha256="0" * 64)),
        ("effective_config", lambda payload: payload.update(effective_config_sha256="0" * 64)),
        ("data", lambda payload: payload["data"].update(files_verified=0)),
        ("environment", lambda payload: payload["environment"].update(uv_version="0.0.0")),
    ],
)
def test_frozen_champion_rejects_mutated_and_resealed_nested_provenance(
    tmp_path: Path,
    group: str,
    mutate: object,
) -> None:
    """Breaks if any nested champion identity can be self-signed after mutation."""
    payload = json.loads(frozen_champion_bytes())
    assert canonical_sha256(payload) == REQUIRED_FROZEN_CHAMPION_SHA256
    assert callable(mutate)
    mutate(payload)
    path = tmp_path / f"{group}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="differs from the reviewed Phase 1 contract"):
        load_phase1_frozen_champion(path)


def test_canonical_manifest_owns_exact_current_reference_coverage() -> None:
    """Breaks if a production reference symbol is added, removed, or duplicated."""
    universe = load_ai_universe()

    assert len(universe.members) == 34
    assert universe.symbols == tuple(sorted(REFERENCE_UNIVERSE))
    assert len(universe.symbols) == len(set(universe.symbols))
    assert "sh688205" not in universe.symbols
    assert set(INDUSTRY) == set(universe.symbols)


def test_manifest_exposes_pit_taxonomy_with_legacy_decision_compatibility() -> None:
    """Breaks if canonical taxonomy changes Phase 1's existing bucket decisions."""
    universe = load_ai_universe()

    assert set(universe.industries) >= CANONICAL_INDUSTRIES
    assert {"memory", "equipment", "packaging"}.isdisjoint(universe.industries)
    assert universe.industry_of("sh603986", "2023-01-03") == "storage"
    assert universe.industry_of("sh688120", "2023-01-03") == "semicap"
    assert universe.industry_of("sh688498", "2023-01-03") == "advanced_packaging"
    assert INDUSTRY["sh603986"] == "memory"
    assert INDUSTRY["sh688120"] == "equipment"
    assert INDUSTRY["sh688498"] == "packaging"
    assert {"sh688146", "sh688347", "sh688361"}.isdisjoint(universe.symbols_as_of("2023-01-01"))
    assert "sh688146" in universe.symbols_as_of("2023-04-21")


def test_manifest_hash_is_canonical_and_rejects_stale_nonreference_symbol(tmp_path: Path) -> None:
    """Breaks if a resealed or stale universe can enter the production reference."""
    payload = json.loads(ai_universe_manifest_bytes())
    assert canonical_sha256(payload) == load_ai_universe().sha256

    payload["members"][0]["symbol"] = "sh688205"
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=r"canonical SHA-256|production reference"):
        load_ai_universe(stale)


def test_package_resources_are_the_single_runtime_manifest_source() -> None:
    """Breaks if source-tree benchmark copies drift from packaged runtime bytes."""
    assert not (ROOT / "benchmarks" / "ai_universe_manifest.json").exists()
    assert not (ROOT / "benchmarks" / "phase1_frozen_champion.json").exists()
    assert json.loads(ai_universe_manifest_bytes())["canonical_sha256"] == load_ai_universe().sha256
    assert json.loads(frozen_champion_bytes())["production"]["commit"] == FROZEN_CHAMPION_COMMIT


def test_built_wheel_imports_the_packaged_universe_contract(tmp_path: Path) -> None:
    """Breaks if the installed wheel omits immutable manifest package data."""
    dist = tmp_path / "dist"
    target = tmp_path / "site"
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(dist),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("uquant-*.whl"))
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    env = {**os.environ, "PYTHONPATH": str(target)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from uquant.validation.universe import load_ai_universe; print(load_ai_universe().sha256)",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == load_ai_universe().sha256
