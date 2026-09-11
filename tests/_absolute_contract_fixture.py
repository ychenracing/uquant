"""Explicit unit-test authorities; these fixtures never certify the current tree.

The checked-in policy and native replay retain their real historical identities.
Only tests importing one of these fixtures use a simulated producer checkout.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

from uquant.contracts.strict_json import canonical_json_bytes, canonical_json_sha256

HISTORICAL_CONTRACT_SOURCE = "44901d9f7229e56837ee666157fa852c87a354d0b94216b06f9ebeb71715b1fe"
NATIVE_CHAMPION_SOURCE = "73969473f9e53731ed2ee5a5ba957f7f63ce7a7e6cf56f8b88ccad7288f44e3b"


def _producer_checkout(monkeypatch: pytest.MonkeyPatch, source: str) -> ModuleType:
    module = importlib.import_module("uquant.validation.absolute_generalization.contract")
    real_fingerprint = module.source_surface_fingerprint

    def fingerprint(root: Path, surface: str) -> str:
        if root == module._ROOT and surface == "economic_decision_v1":
            return source
        return real_fingerprint(root, surface)

    monkeypatch.setattr(module, "source_surface_fingerprint", fingerprint)
    return module


@pytest.fixture(autouse=True)
def historical_contract_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise frozen policy under its declared producer, with other guards intact."""
    _producer_checkout(monkeypatch, HISTORICAL_CONTRACT_SOURCE)


@pytest.fixture(autouse=True)
def native_champion_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Bind a temporary policy copy to the unmodified native champion test replay."""
    module = _producer_checkout(monkeypatch, NATIVE_CHAMPION_SOURCE)
    read_contract = module._read_strict_contract
    raw = read_contract(module._DEFAULT_CONTRACT_PATH)
    raw["candidate"]["production_source_sha256"] = NATIVE_CHAMPION_SOURCE
    raw["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in raw.items() if key != "canonical_sha256"}
    )
    fixture_path = tmp_path / "native-champion-unit-contract.json"
    fixture_path.write_bytes(canonical_json_bytes(raw) + b"\n")

    def read_fixture(path: Path) -> dict[str, object]:
        return read_contract(fixture_path if path == module._DEFAULT_CONTRACT_PATH else path)

    monkeypatch.setattr(module, "_read_strict_contract", read_fixture)
    monkeypatch.setattr(module, "_CANDIDATE_SOURCE", NATIVE_CHAMPION_SOURCE)
    for name in (
        "uquant.validation.absolute_generalization",
        "uquant.validation.absolute_generalization.contract",
        "uquant.validation.absolute_generalization.scenarios",
        "_absolute_generalization_metrics_fixture",
    ):
        target = importlib.import_module(name)
        monkeypatch.setattr(target, "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256", raw["canonical_sha256"])
