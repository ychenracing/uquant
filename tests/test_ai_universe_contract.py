from __future__ import annotations

import json
from pathlib import Path

import pytest

from uquant.leader import INDUSTRY, REFERENCE_UNIVERSE
from uquant.validation.universe import (
    CANONICAL_INDUSTRIES,
    FROZEN_CHAMPION_COMMIT,
    GITHUB_PHASE1_ARTIFACT_SHA256,
    canonical_sha256,
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
    manifest = ROOT / "benchmarks" / "ai_universe_manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert canonical_sha256(payload) == load_ai_universe().sha256

    payload["members"][0]["symbol"] = "sh688205"
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=r"canonical SHA-256|production reference"):
        load_ai_universe(stale)
