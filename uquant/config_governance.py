"""Compile-anchored governance for every production configuration field."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

from .config import SystemConfig, config_fingerprint

GOVERNANCE_PATH: Final = Path("benchmarks") / "config_parameter_governance.json"
GOVERNANCE_BASE_COMMIT: Final = "e71c3f6cf42244f71e59458ec15375b92ed4da1f"
REQUIRED_CONFIG_PARAMETER_GOVERNANCE_SHA256: Final = (
    "2efef1f096766dee6bcc92bd66fb2d772779fbd19736b11d22d7ead8f6fd24a0"
)
FROZEN_CHAMPION_CONFIG_SHA256: Final = (
    "023d709731196a325d9cd03e95ece92e4baf63d2c5c66bb9f7d0e7a190e7bf20"
)
REMOVAL_ORDER: Final = (
    "strategic_cohort_symbols",
    "strategic_partial_universe_max_size",
    "adaptive_broad_universe_min_size",
    "adaptive_broad_universe_compatibility_enabled",
    "strategic_expansive_universe_min_size",
    "strategic_persistent_confirm_days",
    "strategic_reversal_confirm_days",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ParameterCategory(StrEnum):
    """Closed governance category for a production configuration field."""

    MARKET_RULE = "MARKET_RULE"
    SAFETY = "SAFETY"
    ECONOMIC = "ECONOMIC"
    DERIVED = "DERIVED"
    COMPATIBILITY = "COMPATIBILITY"


class SubsystemOwner(StrEnum):
    """Single accountable subsystem for a governed field."""

    ACCOUNTING = "ACCOUNTING"
    CONTROL_PLANE = "CONTROL_PLANE"
    EXECUTION = "EXECUTION"
    FEATURES = "FEATURES"
    LEADER_SELECTION = "LEADER_SELECTION"
    OPPORTUNITY = "OPPORTUNITY"
    PORTFOLIO = "PORTFOLIO"
    RECOVERY = "RECOVERY"
    RISK = "RISK"
    SECTOR_RISK = "SECTOR_RISK"
    STRATEGIC = "STRATEGIC"


@dataclass(frozen=True, slots=True)
class ParameterGovernance:
    """One unambiguous field classification."""

    field: str
    category: ParameterCategory
    owner: SubsystemOwner
    rationale: str


@dataclass(frozen=True, slots=True)
class ConfigGovernance:
    """Validated, anchored configuration-freedom inventory."""

    entries: tuple[ParameterGovernance, ...]
    before_total_fields: int
    before_economic_fields: int
    after_total_fields: int
    after_economic_fields: int
    current_total_fields: int
    current_economic_fields: int
    removed_fields: tuple[str, ...]
    champion_config_sha256: str
    candidate_config_sha256: str
    artifact_sha256: str

    def entry(self, field: str) -> ParameterGovernance:
        """Return the declared entry or fail closed for an unknown field."""

        matches = tuple(item for item in self.entries if item.field == field)
        if len(matches) != 1:
            raise ValueError(f"configuration field is not governed exactly once: {field}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class GovernedConfigMigration:
    """Exact, compile-anchored identity carrier for an authorized deletion prefix."""

    champion_config_sha256: str
    candidate_config_sha256: str
    removed_fields: tuple[str, ...]
    governance_sha256: str
    carrier_sha256: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"configuration governance contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise RuntimeError(f"configuration governance contains non-standard number: {value}")


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {key: value for key, value in payload.items() if key != "artifact_sha256"},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_path() -> Path:
    return Path(__file__).resolve().parents[1] / GOVERNANCE_PATH


def _required_mapping(value: Any, *, label: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RuntimeError(f"configuration governance {label} is malformed")
    return cast(dict[str, Any], value)


def _required_count(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"configuration governance count is malformed: {label}")
    return cast(int, value)


def load_config_governance(path: str | Path | None = None) -> ConfigGovernance:
    """Load the exact reviewed inventory and reject edits, gaps, and duplicates."""

    source = _default_path() if path is None else Path(path)
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("configuration governance artifact is missing or not a regular file")
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("configuration governance artifact is corrupt") from exc
    payload = _required_mapping(
        payload,
        label="artifact",
        keys={
            "schema_version",
            "contract_id",
            "baseline_commit",
            "counts",
            "config_migration",
            "categories",
            "removal_plan",
            "removed_fields",
            "artifact_sha256",
        },
    )
    if payload["schema_version"] != 1 or payload["contract_id"] != "uquant-config-governance-v1":
        raise RuntimeError("unsupported configuration governance contract")
    if payload["baseline_commit"] != GOVERNANCE_BASE_COMMIT:
        raise RuntimeError("configuration governance baseline commit changed")

    artifact_sha256 = payload["artifact_sha256"]
    if not isinstance(artifact_sha256, str) or not _SHA256.fullmatch(artifact_sha256):
        raise RuntimeError("configuration governance artifact SHA-256 is malformed")
    if artifact_sha256 != _canonical_sha256(payload):
        raise RuntimeError("configuration governance self-seal is stale")
    if artifact_sha256 != REQUIRED_CONFIG_PARAMETER_GOVERNANCE_SHA256:
        raise RuntimeError("configuration governance differs from compiled reviewed governance")

    counts = _required_mapping(
        payload["counts"],
        label="counts",
        keys={"before", "after", "current"},
    )
    parsed_counts: dict[str, tuple[int, int]] = {}
    for name in ("before", "after", "current"):
        item = _required_mapping(
            counts[name],
            label=f"counts.{name}",
            keys={"total_fields", "economic_fields"},
        )
        parsed_counts[name] = (
            _required_count(item["total_fields"], label=f"{name}.total_fields"),
            _required_count(item["economic_fields"], label=f"{name}.economic_fields"),
        )
    if parsed_counts["before"][0] != 285 or parsed_counts["after"][0] != 278:
        raise RuntimeError("configuration governance total-field change is not the reviewed 285-to-278")
    if parsed_counts["before"][1] != parsed_counts["after"][1]:
        raise RuntimeError("configuration cleanup must not change ECONOMIC freedom")

    config_migration = _required_mapping(
        payload["config_migration"],
        label="config_migration",
        keys={"champion_config_sha256", "candidate_config_sha256"},
    )
    champion_config_sha256 = config_migration["champion_config_sha256"]
    candidate_config_sha256 = config_migration["candidate_config_sha256"]
    if champion_config_sha256 != FROZEN_CHAMPION_CONFIG_SHA256:
        raise RuntimeError("configuration governance champion identity changed")
    if not isinstance(candidate_config_sha256, str) or not _SHA256.fullmatch(
        candidate_config_sha256
    ):
        raise RuntimeError("configuration governance candidate identity is malformed")

    categories = _required_mapping(
        payload["categories"],
        label="categories",
        keys={category.value for category in ParameterCategory},
    )
    entries: list[ParameterGovernance] = []
    for category in ParameterCategory:
        groups = categories[category.value]
        if not isinstance(groups, list):
            raise RuntimeError(f"configuration governance category is malformed: {category.value}")
        for group in groups:
            group = _required_mapping(
                group,
                label=f"category.{category.value}",
                keys={"owner", "rationale", "fields"},
            )
            try:
                owner = SubsystemOwner(group["owner"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("configuration governance owner is unknown") from exc
            rationale = group["rationale"]
            raw_fields = group["fields"]
            if (
                not isinstance(rationale, str)
                or not rationale.strip()
                or not isinstance(raw_fields, list)
                or any(not isinstance(field, str) or not field for field in raw_fields)
            ):
                raise RuntimeError("configuration governance group is malformed")
            entries.extend(ParameterGovernance(field, category, owner, rationale) for field in raw_fields)

    entry_names = tuple(item.field for item in entries)
    if len(entry_names) != len(set(entry_names)):
        raise RuntimeError("configuration governance classifies a field more than once")
    actual_names = {field.name for field in fields(SystemConfig)}
    if set(entry_names) != actual_names:
        missing = sorted(actual_names - set(entry_names))
        unknown = sorted(set(entry_names) - actual_names)
        raise RuntimeError(
            f"configuration governance does not match SystemConfig: missing={missing}, unknown={unknown}"
        )

    removal_plan = payload["removal_plan"]
    if not isinstance(removal_plan, list) or len(removal_plan) != len(REMOVAL_ORDER):
        raise RuntimeError("configuration governance removal plan is malformed")
    planned_names: list[str] = []
    for raw in removal_plan:
        raw = _required_mapping(
            raw,
            label="removal_plan entry",
            keys={"field", "category", "owner", "rationale"},
        )
        if raw["category"] != ParameterCategory.COMPATIBILITY.value:
            raise RuntimeError("removed configuration fields must be COMPATIBILITY-only")
        try:
            SubsystemOwner(raw["owner"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("configuration governance removal owner is unknown") from exc
        if (
            not isinstance(raw["field"], str)
            or not isinstance(raw["rationale"], str)
            or not raw["rationale"].strip()
        ):
            raise RuntimeError("configuration governance removal entry is malformed")
        planned_names.append(raw["field"])
    if tuple(planned_names) != REMOVAL_ORDER:
        raise RuntimeError("configuration governance removal plan changed")
    removed = payload["removed_fields"]
    if not isinstance(removed, list) or any(not isinstance(field, str) for field in removed):
        raise RuntimeError("configuration governance removed-fields ledger is malformed")
    removed_fields = tuple(removed)
    if removed_fields != REMOVAL_ORDER[: len(removed_fields)]:
        raise RuntimeError("configuration fields were not removed in the reviewed order")
    if actual_names.intersection(removed_fields):
        raise RuntimeError("configuration governance marks a live field as removed")
    if set(REMOVAL_ORDER[len(removed_fields) :]) - actual_names:
        raise RuntimeError("configuration field disappeared without removal-ledger evidence")

    economic_count = sum(item.category is ParameterCategory.ECONOMIC for item in entries)
    if parsed_counts["current"] != (len(entries), economic_count):
        raise RuntimeError("configuration governance current counts are stale")
    if parsed_counts["before"] != (285, economic_count):
        raise RuntimeError("configuration governance before counts are stale")
    if parsed_counts["after"] != (278, economic_count):
        raise RuntimeError("configuration governance after counts are stale")

    return ConfigGovernance(
        entries=tuple(entries),
        before_total_fields=parsed_counts["before"][0],
        before_economic_fields=parsed_counts["before"][1],
        after_total_fields=parsed_counts["after"][0],
        after_economic_fields=parsed_counts["after"][1],
        current_total_fields=parsed_counts["current"][0],
        current_economic_fields=parsed_counts["current"][1],
        removed_fields=removed_fields,
        champion_config_sha256=champion_config_sha256,
        candidate_config_sha256=candidate_config_sha256,
        artifact_sha256=artifact_sha256,
    )


def validate_governed_config_migration(config: SystemConfig) -> GovernedConfigMigration:
    """Prove the trusted candidate is the exact reviewed post-deletion configuration."""

    if not isinstance(config, SystemConfig):
        raise ValueError("governed config migration requires a trusted SystemConfig")
    governance = load_config_governance()
    candidate_config_sha256 = config_fingerprint(config)
    if candidate_config_sha256 != governance.candidate_config_sha256:
        raise ValueError("trusted config differs from reviewed post-removal config")
    if not governance.removed_fields:
        raise ValueError("governed config migration requires an authorized field deletion")
    carrier = {
        "champion_config_sha256": governance.champion_config_sha256,
        "candidate_config_sha256": candidate_config_sha256,
        "removed_fields": governance.removed_fields,
        "governance_sha256": governance.artifact_sha256,
    }
    return GovernedConfigMigration(
        champion_config_sha256=governance.champion_config_sha256,
        candidate_config_sha256=candidate_config_sha256,
        removed_fields=governance.removed_fields,
        governance_sha256=governance.artifact_sha256,
        carrier_sha256=_canonical_sha256(cast(dict[str, Any], carrier)),
    )


def economic_parameter_names() -> frozenset[str]:
    """Return the exact reviewed candidate-search freedom."""

    return frozenset(
        entry.field
        for entry in load_config_governance().entries
        if entry.category is ParameterCategory.ECONOMIC
    )
