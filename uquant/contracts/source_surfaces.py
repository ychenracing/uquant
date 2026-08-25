"""Schema-v2 registry contract for explicitly reviewed source surfaces."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, cast

from .strict_json import canonical_json_sha256, strict_json_loads

SOURCE_SURFACE_IDS: Final = (
    "economic_decision_v1",
    "execution_account_v1",
    "sentinel_v1",
    "validation_runner_v1",
    "full_package_v1",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REGISTRY_FIELDS = frozenset({"registry_version", "surfaces", "canonical_sha256"})
_SURFACE_FIELDS = frozenset({"id", "source_paths", "resource_paths"})


@dataclass(frozen=True, slots=True)
class SourceSurface:
    """One exact, ordered set of source and non-source resource paths."""

    identifier: str
    source_paths: tuple[str, ...]
    resource_paths: tuple[str, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return (*self.source_paths, *self.resource_paths)


@dataclass(frozen=True, slots=True)
class SourceSurfaceRegistry:
    """Validated registry generation and its sealed surfaces."""

    registry_version: int
    surfaces: tuple[SourceSurface, ...]
    canonical_sha256: str

    def surface(self, identifier: str) -> SourceSurface:
        for surface in self.surfaces:
            if surface.identifier == identifier:
                return surface
        raise KeyError(f"unknown source surface: {identifier}")


def _explicit_paths(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
        raise ValueError(f"source surface {label} paths must be a JSON string list")
    paths = cast(list[str], value)
    if paths != sorted(set(paths)):
        raise ValueError(f"source surface {label} paths must be sorted and unique")
    for path in paths:
        normalized = PurePosixPath(path)
        if (
            not path
            or normalized.is_absolute()
            or normalized.as_posix() != path
            or any(part in {"", ".", ".."} for part in normalized.parts)
            or any(character in path for character in "*?[\\")
        ):
            raise ValueError(
                f"source surface {label} member must be an explicit relative path: {path}"
            )
    return tuple(paths)


def _parse_surface(value: object) -> SourceSurface:
    if not isinstance(value, Mapping) or set(value) != _SURFACE_FIELDS:
        raise ValueError("source surface entry schema is invalid")
    identifier = value["id"]
    if not isinstance(identifier, str):
        raise ValueError("source surface identifier is invalid")
    source_paths = _explicit_paths(value["source_paths"], label=identifier)
    resource_paths = _explicit_paths(value["resource_paths"], label=identifier)
    if not source_paths or any(not path.endswith(".py") for path in source_paths):
        raise ValueError(f"source surface {identifier} source paths are invalid")
    overlap = set(source_paths) & set(resource_paths)
    if overlap:
        raise ValueError(
            f"source surface {identifier} source and resource paths overlap: {sorted(overlap)}"
        )
    return SourceSurface(
        identifier=identifier,
        source_paths=source_paths,
        resource_paths=resource_paths,
    )


def parse_source_surface_registry(document: str | bytes | bytearray) -> SourceSurfaceRegistry:
    """Parse and seal-check one strict source-surface registry document."""

    decoded = strict_json_loads(document)
    if not isinstance(decoded, Mapping) or set(decoded) != _REGISTRY_FIELDS:
        raise ValueError("source surface registry schema is invalid")
    raw = cast(Mapping[str, object], decoded)
    if raw["registry_version"] != 2:
        raise ValueError("source surface registry version is invalid")
    seal = raw["canonical_sha256"]
    if not isinstance(seal, str) or not _SHA256.fullmatch(seal):
        raise ValueError("source surface registry seal is invalid")
    unsealed = {key: raw[key] for key in raw if key != "canonical_sha256"}
    if canonical_json_sha256(unsealed) != seal:
        raise ValueError("source surface registry seal is invalid")
    surface_values = raw["surfaces"]
    if not isinstance(surface_values, list):
        raise ValueError("source surface registry surfaces are invalid")
    surfaces = tuple(_parse_surface(value) for value in surface_values)
    if tuple(surface.identifier for surface in surfaces) != SOURCE_SURFACE_IDS:
        raise ValueError("source surface IDs are invalid")
    return SourceSurfaceRegistry(
        registry_version=2,
        surfaces=surfaces,
        canonical_sha256=seal,
    )
