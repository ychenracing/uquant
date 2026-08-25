from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import json
import typing
from pathlib import Path

import pytest

from uquant.config import DEFAULT_CONFIG, SystemConfig

ROOT = Path(__file__).parents[1]
CONFIG_MODULES = (
    "uquant.config.model",
    "uquant.config.views",
    "uquant.config.validation",
    "uquant.config.validation.execution",
    "uquant.config.validation.market",
    "uquant.config.validation.portfolio",
    "uquant.config.validation.recovery",
    "uquant.config.validation.risk",
    "uquant.config.validation.sentinel",
    "uquant.config.validation.strategic",
)
MODEL_MODULES = (
    "uquant.models",
    "uquant.models.account",
    "uquant.models.decision",
    "uquant.models.enums",
    "uquant.models.trading",
)


def _find_spec(module: str) -> object | None:
    try:
        return importlib.util.find_spec(module)
    except ModuleNotFoundError:
        return None


def _governed_owner_fields(owner: str) -> list[str]:
    payload = json.loads(
        (ROOT / "benchmarks" / "config_parameter_governance.json").read_text(encoding="utf-8")
    )
    owned = {
        field
        for groups in payload["categories"].values()
        for group in groups
        if group["owner"] == owner
        for field in group["fields"]
    }
    return [field.name for field in dataclasses.fields(SystemConfig) if field.name in owned]


def test_config_and_model_implementation_packages_are_importable() -> None:
    missing = [module for module in (*CONFIG_MODULES, *MODEL_MODULES) if _find_spec(module) is None]

    assert missing == []
    config_spec = importlib.util.find_spec("uquant.config")
    assert config_spec is not None and config_spec.origin is not None
    assert Path(config_spec.origin).relative_to(ROOT).as_posix() == "uquant/config/__init__.py"
    assert not (ROOT / "uquant" / "config.py").exists()


@pytest.mark.parametrize(
    ("view_name", "owner"),
    (
        ("ExecutionConfigView", "EXECUTION"),
        ("PortfolioConfigView", "PORTFOLIO"),
        ("RiskConfigView", "RISK"),
    ),
)
def test_config_views_are_minimal_immutable_owner_derived_snapshots(
    view_name: str,
    owner: str,
) -> None:
    views = importlib.import_module("uquant.config.views")
    view_type = getattr(views, view_name)
    view = view_type.from_config(DEFAULT_CONFIG)
    fields = dataclasses.fields(view_type)
    expected_names = _governed_owner_fields(owner)

    assert [field.name for field in fields] == expected_names
    assert expected_names
    assert all(field.default is dataclasses.MISSING for field in fields)
    assert all(field.default_factory is dataclasses.MISSING for field in fields)
    assert dataclasses.asdict(view) == {name: getattr(DEFAULT_CONFIG, name) for name in expected_names}
    assert not hasattr(view, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(view, expected_names[0], object())


def test_compatibility_facades_export_the_same_config_and_model_objects() -> None:
    config_model = importlib.import_module("uquant.config.model")
    models = importlib.import_module("uquant.models")
    compatibility = importlib.import_module("uquant.types")

    assert config_model.SystemConfig is SystemConfig
    assert config_model.DEFAULT_CONFIG is DEFAULT_CONFIG
    assert SystemConfig.__module__ == "uquant.config"
    for name in compatibility.__all__:
        exported = getattr(compatibility, name)
        if isinstance(exported, type) or callable(exported):
            assert exported is getattr(models, name)
    for name in (
        "AccountState",
        "Decision",
        "Fill",
        "PendingOrder",
        "RiskAssessment",
        "Target",
    ):
        assert getattr(compatibility, name).__module__ == "uquant.types"


def test_compatibility_facades_resolve_relocated_class_type_hints() -> None:
    compatibility = importlib.import_module("uquant.types")

    assert typing.get_type_hints(SystemConfig)["risk_sentinel_mode"] == typing.Literal[
        "SHADOW", "FREEZE_ONLY"
    ]
    for name in compatibility.__all__:
        exported = getattr(compatibility, name)
        if isinstance(exported, type) and (
            dataclasses.is_dataclass(exported) or typing.is_typeddict(exported)
        ):
            typing.get_type_hints(exported)
