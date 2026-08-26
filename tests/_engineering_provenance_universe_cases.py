from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from uquant.validation import universe as universe_module


@pytest.mark.parametrize(
    "mutation",
    (
        "schema",
        "production_shape",
        "production_identity",
        "data_shape",
        "environment_shape",
        "snapshot",
        "file_count",
        "environment_value",
        "artifact",
        "source_sha",
    ),
)
def test_signed_frozen_champion_rejects_semantic_provenance_changes(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(json.loads(universe_module.frozen_champion_bytes()))
    if mutation == "schema":
        payload["schema_version"] = 2
    elif mutation == "production_shape":
        payload["production"] = {}
    elif mutation == "production_identity":
        payload["production"]["repository"] = "other/repository"
    elif mutation == "data_shape":
        payload["data"] = {}
    elif mutation == "environment_shape":
        payload["environment"] = {}
    elif mutation == "snapshot":
        payload["data"]["snapshot_id"] = ""
    elif mutation == "file_count":
        payload["data"]["files_verified"] = True
    elif mutation == "environment_value":
        payload["environment"]["python_full_version"] = ""
    elif mutation == "artifact":
        payload["github_phase1_artifact_sha256"] = "0" * 64
    else:
        payload["production"]["source_sha256"] = "bad"
    monkeypatch.setattr(
        universe_module,
        "REQUIRED_FROZEN_CHAMPION_SHA256",
        universe_module.canonical_sha256(payload),
    )
    path = tmp_path / f"champion-{mutation}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        universe_module.load_performance_frozen_champion(path)

@pytest.mark.parametrize(
    "mutation",
    (
        "schema",
        "identity",
        "members",
        "member_shape",
        "symbol",
        "industry",
        "domain",
        "tradable",
        "evidence",
        "interval",
        "count",
        "order",
    ),
)
def test_signed_ai_universe_rejects_semantic_membership_changes(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(json.loads(universe_module.ai_universe_manifest_bytes()))
    if mutation == "schema":
        payload.pop("members")
    elif mutation == "identity":
        payload["manifest_id"] = "changed"
    elif mutation == "members":
        payload["members"] = []
    elif mutation == "member_shape":
        payload["members"][0].pop("evidence")
    elif mutation == "symbol":
        payload["members"][0]["symbol"] = "bad"
    elif mutation == "industry":
        payload["members"][0]["industry"] = "unknown"
    elif mutation == "domain":
        payload["members"][0]["ai_domain"] = ""
    elif mutation == "tradable":
        payload["members"][0]["tradable"] = False
    elif mutation == "evidence":
        payload["members"][0]["evidence"] = ""
    elif mutation == "interval":
        payload["members"][0]["effective_to"] = payload["members"][0]["effective_from"]
    elif mutation == "count":
        payload["members"].pop()
    else:
        payload["members"][0], payload["members"][1] = (
            payload["members"][1],
            payload["members"][0],
        )
    payload["canonical_sha256"] = universe_module.canonical_sha256(payload)
    monkeypatch.setattr(
        universe_module,
        "REQUIRED_AI_UNIVERSE_SHA256",
        payload["canonical_sha256"],
    )
    path = tmp_path / f"universe-{mutation}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        universe_module.load_ai_universe(path)
