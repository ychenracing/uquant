from __future__ import annotations

import hashlib
import json

import pytest

from uquant.contracts.json import (
    canonical_json_bytes,
    canonical_json_sha256,
    strict_json_loads,
)
from uquant.contracts.source_surfaces import (
    SOURCE_SURFACE_IDS,
    parse_source_surface_registry,
)

_MINIMAL_REGISTRY_SEAL = "93debfcb71ff35b020a9f9156f744a03772fdd9083fab690c0bff90bdd8919ee"


def _minimal_registry() -> dict[str, object]:
    return {
        "registry_version": 2,
        "surfaces": [
            {
                "id": "economic_decision_v1",
                "source_paths": ["uquant/engine.py"],
                "resource_paths": ["benchmarks/config_parameter_governance.json"],
            },
            {
                "id": "execution_account_v1",
                "source_paths": ["uquant/account.py"],
                "resource_paths": [],
            },
            {
                "id": "sentinel_v1",
                "source_paths": ["uquant/risk_sentinel/service.py"],
                "resource_paths": [],
            },
            {
                "id": "validation_runner_v1",
                "source_paths": ["uquant/validation/cli.py"],
                "resource_paths": ["uv.lock"],
            },
            {
                "id": "full_package_v1",
                "source_paths": ["uquant/__init__.py"],
                "resource_paths": ["pyproject.toml", "requirements.txt", "uv.lock"],
            },
        ],
        "canonical_sha256": _MINIMAL_REGISTRY_SEAL,
    }


def test_canonical_json_bytes_are_stable_utf8_and_reject_nonfinite_values() -> None:
    expected = b'{"a":[3,"\xe9\x9b\xaa"],"z":1}'

    assert canonical_json_bytes({"z": 1, "a": [3, "\u96ea"]}) == expected
    assert canonical_json_sha256({"z": 1, "a": [3, "\u96ea"]}) == hashlib.sha256(
        expected
    ).hexdigest()

    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_json_bytes({"not_finite": float("nan")})


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ('{"surface":"first","surface":"second"}', "duplicate JSON key: surface"),
        ('{"value":NaN}', "nonstandard JSON constant: NaN"),
        ('{"value":Infinity}', "nonstandard JSON constant: Infinity"),
        ('{"value":-Infinity}', "nonstandard JSON constant: -Infinity"),
    ),
)
def test_strict_json_rejects_duplicate_keys_and_nonstandard_constants(
    payload: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        strict_json_loads(payload)


def test_strict_json_accepts_utf8_bytes_without_normalizing_values() -> None:
    assert strict_json_loads(b'{"surface":"economic_decision_v1","enabled":true}') == {
        "surface": "economic_decision_v1",
        "enabled": True,
    }


def test_source_surface_registry_requires_the_five_versioned_identities() -> None:
    registry = parse_source_surface_registry(
        json.dumps(_minimal_registry(), ensure_ascii=False).encode("utf-8")
    )

    assert registry.registry_version == 2
    assert tuple(surface.identifier for surface in registry.surfaces) == SOURCE_SURFACE_IDS
    assert registry.surface("economic_decision_v1").source_paths == (
        "uquant/engine.py",
    )
    assert registry.surface("full_package_v1").resource_paths == (
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
    )
    with pytest.raises(KeyError, match="unknown source surface: absent_v1"):
        registry.surface("absent_v1")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("seal", "source surface registry seal is invalid"),
        ("missing_id", "source surface IDs are invalid"),
        ("duplicate_id", "source surface IDs are invalid"),
        ("glob", "must be an explicit relative path"),
        ("unsorted", "paths must be sorted and unique"),
        ("overlap", "source and resource paths overlap"),
    ),
)
def test_source_surface_registry_fails_closed_on_ambiguous_membership(
    mutation: str,
    message: str,
) -> None:
    payload = _minimal_registry()
    surfaces = payload["surfaces"]
    assert isinstance(surfaces, list)
    first = surfaces[0]
    assert isinstance(first, dict)
    if mutation == "seal":
        payload["canonical_sha256"] = "0" * 64
    elif mutation == "missing_id":
        surfaces.pop()
    elif mutation == "duplicate_id":
        final = surfaces[-1]
        assert isinstance(final, dict)
        final["id"] = "economic_decision_v1"
    elif mutation == "glob":
        first["source_paths"] = ["uquant/*.py"]
    elif mutation == "unsorted":
        first["source_paths"] = ["uquant/engine.py", "uquant/account.py"]
    else:
        first["resource_paths"] = ["uquant/engine.py"]
    if mutation != "seal":
        unsealed = {key: value for key, value in payload.items() if key != "canonical_sha256"}
        payload["canonical_sha256"] = canonical_json_sha256(unsealed)

    with pytest.raises(ValueError, match=message):
        parse_source_surface_registry(json.dumps(payload).encode("utf-8"))
