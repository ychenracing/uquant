from __future__ import annotations

import json
import pickle
import shutil
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from uquant.config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from uquant.engine import ProductionEngine
from uquant.provenance.fingerprints import source_surface_fingerprint
from uquant.provenance.surfaces import load_source_surface_registry

ROOT = Path(__file__).resolve().parents[1]


def test_fixed_policy_rejects_constructor_override_and_instance_assignment() -> None:
    public = {field.name for field in fields(SystemConfig)}
    assert len(public) == 13
    for name, value in DEFAULT_CONFIG.to_dict().items():
        if name in public:
            continue
        with pytest.raises(TypeError):
            SystemConfig(**{name: value})
        with pytest.raises(TypeError):
            DEFAULT_CONFIG.override(**{name: value})
        with pytest.raises((AttributeError, TypeError)):
            setattr(DEFAULT_CONFIG, name, value)


@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf"), -float("inf")])
def test_every_public_numeric_setting_rejects_invalid_values(bad: object) -> None:
    for field in fields(SystemConfig):
        if field.name == "risk_sentinel_mode":
            continue
        with pytest.raises(ValueError, match=field.name):
            SystemConfig(**{field.name: bad})


def test_production_engine_rejects_subclass_and_duck_policy(tmp_path: Path) -> None:
    class Tuned(SystemConfig):
        leader_tenure_days = 99

    for cfg in (Tuned(), SimpleNamespace(**DEFAULT_CONFIG.to_dict())):
        with pytest.raises(TypeError, match="exact SystemConfig"):
            ProductionEngine(tmp_path, cfg)
    engine = ProductionEngine(tmp_path)
    with pytest.raises(AttributeError):
        engine.cfg = Tuned()
    with pytest.raises(TypeError, match="exact SystemConfig"):
        ProductionEngine.for_validation(tmp_path, "p2_lower", Tuned())


def test_closed_native_profiles_match_the_existing_frozen_contract(tmp_path: Path) -> None:
    profiles = json.loads((ROOT / "benchmarks/cross_ai_core_strategy_contract.json").read_text())["profiles"]
    for name in ("p2_lower", "p2_upper", "p7_lower", "p7_upper", "p8_lower", "p8_upper",
                 "confirmation_lower", "confirmation_upper"):
        engine = ProductionEngine.for_validation(tmp_path, name)
        assert engine.cfg.to_dict() == DEFAULT_CONFIG.to_dict() | profiles[name]
        assert config_fingerprint(engine.cfg) != config_fingerprint()
    for unknown in ("", "leader_tenure_days=99", "p4_lower", "custom"):
        with pytest.raises(ValueError, match="unknown frozen policy profile"):
            ProductionEngine.for_validation(tmp_path, unknown)


@pytest.mark.parametrize("relative", [
    "uquant/config/policies.py",
    "uquant/contracts/resources/config_policy_governance.json",
    "uquant/validation/parameter_policy.py",
])
def test_fixed_policy_sources_participate_in_economic_fingerprint(tmp_path: Path, relative: str) -> None:
    registry = load_source_surface_registry(ROOT)
    surface = registry.surface("economic_decision_v1")
    assert relative in surface.paths
    for member in (*surface.paths, "benchmarks/source_surface_registry.json"):
        destination = tmp_path / member
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / member, destination)
    before = source_surface_fingerprint(tmp_path, "economic_decision_v1")
    path = tmp_path / relative
    path.write_bytes(path.read_bytes() + b"\n")
    assert source_surface_fingerprint(tmp_path, "economic_decision_v1") != before


def test_config_pickle_is_current_shape_and_validated() -> None:
    assert pickle.loads(pickle.dumps(DEFAULT_CONFIG)) == DEFAULT_CONFIG
    for values in (list(DEFAULT_CONFIG.to_dict().values()), [None] * 13):
        config = SystemConfig.__new__(SystemConfig)
        with pytest.raises(ValueError):
            config.__setstate__(values)
