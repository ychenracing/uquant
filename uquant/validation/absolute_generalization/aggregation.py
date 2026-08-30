"""Independent fail-closed aggregation of canonical shard evidence."""

from __future__ import annotations

import math
import re
import shutil
import subprocess  # nosec B404 - fixed Git executable and argument vectors
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from uquant.contracts.strict_json import canonical_json_sha256

from ._acceptance_evidence import (
    validate_champion_evidence,
    validate_crowning_evidence,
    validate_failed_grant_evidence,
    validate_repair_evidence,
    validate_terminal_evidence,
)
from .artifacts import CellArtifact, validate_cell_artifact
from .contract import AbsoluteGeneralizationContract
from .policy import ComponentResult, evaluate_literal_components

_ROOT = Path(__file__).resolve().parents[3]
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMPONENTS = (
    "champion_non_regression",
    "absolute_strategic_robustness",
    "failed_grant_recovery",
    "witness_resilience",
    "repeated_crowning",
    "bounded_healthy_cash_vacancy",
    "complete_literal_metrics",
)
_SPECIAL_SHARDS = ("champion", "recovery-and-reachability")
_MANIFEST_FIELDS = frozenset(
    {
    "schema_version",
    "shard",
    "mode",
    "status",
    "upstream_success",
    "error",
    "run_id",
    "run_attempt",
    "head",
    "tree",
    "scenario_contract_sha256",
    "production_source_sha256",
    "effective_config_sha256",
    "uv_lock_sha256",
    "frozen_data_manifest_sha256",
    "universe_sha256",
    "cells",
    "champion",
    "failed_grant_recovery",
    "historical_crowning",
    "terminal_scc",
    "repair_bounds",
    "cross_industry_crowning",
    "summary",
        "canonical_sha256",
    }
)


def _freeze_manifest(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_manifest(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_manifest(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_thaw(item) for item in value]
    return value


def _manifest_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"absolute generalization {label} is malformed")
    return cast(Mapping[str, object], value)


def _manifest_sequence(value: object, *, label: str) -> Sequence[object]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"absolute generalization {label} is malformed")
    return cast(Sequence[object], value)


def _fields(raw: Mapping[str, object], expected: Set[str], *, label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"absolute generalization {label} fields differ")


def _manifest_text(value: object, *, label: str, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value):
        raise ValueError(f"absolute generalization {label} is malformed")
    return value


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"absolute generalization {label} is malformed")
    return value


def _manifest_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"absolute generalization {label} is malformed")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"absolute generalization {label} is malformed")
    return number


def _validate_finite_manifest(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("absolute generalization manifest contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_finite_manifest(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _validate_finite_manifest(item)
        return
    raise ValueError("absolute generalization manifest contains a non-JSON value")


def _reject_pass_claims(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"passed", "runner_success", "capability_pass"} or key.endswith(
                "_passed"
            ):
                raise ValueError("absolute generalization manifest contains a self-asserted pass")
            _reject_pass_claims(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_pass_claims(item)


def _git_output(*arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve current checkout identity")
    try:
        result = subprocess.run(
            [executable, "-C", str(_ROOT), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot resolve current checkout identity") from exc
    value = result.stdout.strip()
    if not _GIT_OBJECT.fullmatch(value):
        raise RuntimeError("cannot resolve current checkout identity")
    return value


def _checkout_identity() -> tuple[str, str]:
    return _git_output("rev-parse", "HEAD"), _git_output("rev-parse", "HEAD^{tree}")


@dataclass(frozen=True, slots=True)
class ShardManifest:
    """One deeply immutable, validated raw shard envelope."""

    document: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "document",
            cast(Mapping[str, object], _freeze_manifest(self.document)),
        )

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], _thaw(self.document))

    @property
    def shard(self) -> str:
        return cast(str, self.document["shard"])

    @property
    def status(self) -> str:
        return cast(str, self.document["status"])


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    """Sealed final report whose green state has exactly one conjunction."""

    schema_version: int
    runner_success: bool
    capability_pass: bool
    passed: bool
    runner_failures: tuple[str, ...]
    components: tuple[ComponentResult, ...]
    expected_cells: int
    valid_cells: int
    replay_error_cells: int
    missing_cells: int
    duplicate_cells: int
    complete_metric_cells: int
    statistics: tuple[tuple[str, float], ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if not self._shape_is_valid() or not self._state_is_valid():
            raise ValueError("absolute generalization acceptance conjunction differs")
        expected_seal = canonical_json_sha256(self._unsealed())
        if self.canonical_sha256 and expected_seal != self.canonical_sha256:
            raise ValueError("absolute generalization acceptance report seal differs")
        if not self.canonical_sha256:
            object.__setattr__(self, "canonical_sha256", expected_seal)

    def _shape_is_valid(self) -> bool:
        return not (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or type(self.runner_success) is not bool
            or type(self.capability_pass) is not bool
            or type(self.passed) is not bool
            or type(self.runner_failures) is not tuple
            or tuple(component.name for component in self.components) != _COMPONENTS
            or any(type(value) is not int or value < 0 for value in self._counts())
            or any(
                type(name) is not str or type(value) is not float
                for name, value in self.statistics
            )
        )

    def _state_is_valid(self) -> bool:
        return not (
            self.capability_pass is not all(item.passed for item in self.components)
            or self.passed is not (self.runner_success and self.capability_pass)
            or (self.runner_success and self.runner_failures)
            or (not self.runner_success and not self.runner_failures)
        )

    def _counts(self) -> tuple[int, ...]:
        return (
            self.expected_cells,
            self.valid_cells,
            self.replay_error_cells,
            self.missing_cells,
            self.duplicate_cells,
            self.complete_metric_cells,
        )

    def _unsealed(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runner_success": self.runner_success,
            "capability_pass": self.capability_pass,
            "passed": self.passed,
            "runner_failures": list(self.runner_failures),
            "components": [
                {
                    "name": item.name,
                    "passed": item.passed,
                    "failures": list(item.failures),
                    "evidence": _thaw(item.evidence),
                    "evidence_sha256": item.evidence_sha256,
                }
                for item in self.components
            ],
            "expected_cells": self.expected_cells,
            "valid_cells": self.valid_cells,
            "replay_error_cells": self.replay_error_cells,
            "missing_cells": self.missing_cells,
            "duplicate_cells": self.duplicate_cells,
            "complete_metric_cells": self.complete_metric_cells,
            "statistics": {name: value for name, value in self.statistics},
        }

    def to_dict(self) -> dict[str, object]:
        raw = self._unsealed()
        raw["canonical_sha256"] = self.canonical_sha256
        return raw


def _validate_summary(raw: object, cells: Sequence[Mapping[str, object]]) -> None:
    summary = _manifest_mapping(raw, label="manifest summary")
    _fields(
        summary,
        {"cell_count", "complete_cell_count", "replay_error_cell_count"},
        label="manifest summary",
    )
    expected = {
        "cell_count": len(cells),
        "complete_cell_count": sum(cell.get("status") == "COMPLETE" for cell in cells),
        "replay_error_cell_count": sum(cell.get("status") == "REPLAY_ERROR" for cell in cells),
    }
    if dict(summary) != expected:
        raise ValueError("absolute generalization manifest summary differs")


def _validate_champion_facts(
    raw: object, contract: AbsoluteGeneralizationContract
) -> Mapping[str, object]:
    value = validate_champion_evidence(raw, contract)
    metrics = _manifest_mapping(value["metrics"], label="champion metrics")
    _fields(metrics, {"account_orders", "final_equity", "final_wealth", "max_drawdown", "total_return"}, label="champion metrics")
    for name in ("final_equity", "final_wealth", "max_drawdown", "total_return"):
        _manifest_number(metrics[name], label=f"champion {name}")
    _integer(metrics["account_orders"], label="champion account orders")
    paths = _manifest_mapping(value["path_sha256"], label="champion paths")
    _fields(paths, {"equity", "fills", "orders", "positions", "targets"}, label="champion paths")
    if any(
        not _SHA256.fullmatch(_manifest_text(item, label="champion path"))
        for item in paths.values()
    ):
        raise ValueError("absolute generalization champion path identity is malformed")
    for name in (
        "duplicate_grant_count", "duplicate_order_count", "duplicate_epoch_count",
        "incumbent_epoch_count", "successor_capital_before_incumbent_exit_count",
    ):
        _integer(value[name], label=f"champion {name}")
    report = _manifest_mapping(value["report_13"], label="champion report-13")
    _fields(
        report,
        {"initial_cash", "cash", "position_market_value", "realized_pnl", "open_pnl", "final_equity", "maximum_target_gross", "minimum_risk_target_gross_cap", "owner_symbols", "unexpected_owner_symbols"},
        label="champion report-13",
    )
    for name in report.keys() - {"owner_symbols", "unexpected_owner_symbols"}:
        _manifest_number(report[name], label=f"report-13 {name}")
    for name in ("owner_symbols", "unexpected_owner_symbols"):
        if any(
            type(item) is not str or not item
            for item in _manifest_sequence(report[name], label=name)
        ):
            raise ValueError("absolute generalization champion report owner is malformed")
    if not _SHA256.fullmatch(
        _manifest_text(value["evidence_sha256"], label="champion evidence")
    ):
        raise ValueError("absolute generalization champion evidence identity is malformed")
    return value


def _validate_recovery(raw: object) -> Mapping[str, object]:
    value = validate_failed_grant_evidence(raw)
    expected = {
        "first_grant", "first_epoch", "second_grant", "second_epoch", "target", "order",
        "fill", "observations",
    }
    _fields(value, expected, label="failed-grant facts")
    nested = {
        "first_grant": {"grant_id", "candidate_symbol", "status", "filled_shares", "expiry_reason", "authorization_id"},
        "first_epoch": {"epoch_id", "grant_id", "owner_symbol", "realized_status", "first_fill_session", "active_session", "closed_session", "close_reason"},
        "second_grant": {"grant_id", "candidate_symbol", "previous_grant_id", "authorization_id"},
        "second_epoch": {"epoch_id", "grant_id", "owner_symbol", "previous_epoch_id", "first_fill_session", "active_session", "realized_status"},
        "target": {"target_id", "symbol", "weight", "origin_subsystem", "grant_id", "epoch_id"},
        "order": {"order_id", "symbol", "side", "target_weight", "origin_subsystem", "grant_id", "epoch_id", "submitted_date"},
        "fill": {"fill_id", "order_id", "symbol", "side", "shares", "origin_subsystem", "grant_id", "epoch_id", "fill_date"},
    }
    rows: dict[str, Mapping[str, object]] = {}
    for name, fields in nested.items():
        rows[name] = _manifest_mapping(value[name], label=name)
        _fields(rows[name], fields, label=name)
    _integer(rows["first_grant"]["filled_shares"], label="first grant filled shares")
    _manifest_number(rows["target"]["weight"], label="successor target weight")
    _manifest_number(rows["order"]["target_weight"], label="successor order weight")
    _integer(rows["fill"]["shares"], label="successor fill shares")
    for row in rows.values():
        for key, item in row.items():
            if key not in {"filled_shares", "weight", "target_weight", "shares"}:
                _manifest_text(
                    item,
                    label=f"failed-grant {key}",
                    empty=key in {"first_fill_session", "active_session"},
                )
    return value


def _validate_crowning(
    raw: object,
    *,
    cross: bool,
    contract: AbsoluteGeneralizationContract,
) -> Mapping[str, object]:
    return validate_crowning_evidence(raw, cross=cross, contract=contract)


def _validate_scc(raw: object) -> Mapping[str, object]:
    return validate_terminal_evidence(raw)


def _validate_repairs(raw: object) -> tuple[Mapping[str, object], ...]:
    return validate_repair_evidence(raw)


def _validate_payloads(
    document: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> None:
    shard = cast(str, document["shard"])
    status = cast(str, document["status"])
    if status == "ERROR":
        if any(document[name] is not None for name in ("champion", "failed_grant_recovery", "historical_crowning", "terminal_scc", "cross_industry_crowning")) or document["repair_bounds"] != [] or document["cells"] != []:
            raise ValueError("absolute generalization ERROR manifest must be evidence-free")
        return
    if shard == "champion":
        _validate_champion_facts(document["champion"], contract)
        forbidden = ("failed_grant_recovery", "historical_crowning", "terminal_scc", "cross_industry_crowning")
        if any(document[name] is not None for name in forbidden) or document["repair_bounds"] != [] or document["cells"] != []:
            raise ValueError("absolute generalization champion manifest payload differs")
    elif shard == "recovery-and-reachability":
        _validate_recovery(document["failed_grant_recovery"])
        _validate_crowning(
            document["historical_crowning"], cross=False, contract=contract
        )
        _validate_scc(document["terminal_scc"])
        _validate_repairs(document["repair_bounds"])
        _validate_crowning(
            document["cross_industry_crowning"], cross=True, contract=contract
        )
        if document["champion"] is not None or document["cells"] != []:
            raise ValueError("absolute generalization recovery manifest payload differs")
    else:
        if any(document[name] is not None for name in ("champion", "failed_grant_recovery", "historical_crowning", "terminal_scc", "cross_industry_crowning")) or document["repair_bounds"] != []:
            raise ValueError("absolute generalization LOO manifest payload differs")


def _manifest_document(
    raw: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> Mapping[str, object]:
    document = _manifest_mapping(raw, label="shard manifest")
    _reject_pass_claims(document)
    _validate_finite_manifest(document)
    _fields(document, _MANIFEST_FIELDS, label="shard manifest")
    if document["schema_version"] != 1:
        raise ValueError("absolute generalization manifest schema differs")
    shard = _manifest_text(document["shard"], label="manifest shard")
    mode = _manifest_text(document["mode"], label="manifest mode")
    status = _manifest_text(document["status"], label="manifest status")
    upstream = document["upstream_success"]
    error = _manifest_text(document["error"], label="manifest error", empty=True)
    if mode not in {"canonical", "targeted"}:
        raise ValueError("absolute generalization manifest mode differs")
    if status not in {"COMPLETE", "ERROR"} or type(upstream) is not bool:
        raise ValueError("absolute generalization manifest status/upstream differs")
    if (status == "COMPLETE" and (upstream is not True or error)) or (
        status == "ERROR" and (upstream is not False or not error)
    ):
        raise ValueError("absolute generalization manifest status/upstream differs")
    _manifest_text(document["run_id"], label="manifest run identity")
    _integer(document["run_attempt"], label="manifest run attempt", minimum=1)
    head = _manifest_text(document["head"], label="manifest HEAD")
    tree = _manifest_text(document["tree"], label="manifest tree")
    if not _GIT_OBJECT.fullmatch(head) or not _GIT_OBJECT.fullmatch(tree):
        raise ValueError("absolute generalization manifest checkout identity is malformed")
    expected = {
        "scenario_contract_sha256": contract.canonical_sha256,
        "production_source_sha256": contract.candidate.production_source_sha256,
        "effective_config_sha256": contract.inputs.effective_config_sha256,
        "uv_lock_sha256": contract.inputs.uv_lock_sha256,
        "frozen_data_manifest_sha256": contract.inputs.frozen_data.manifest_sha256,
        "universe_sha256": contract.inputs.ai_universe_sha256,
    }
    for name, trusted in expected.items():
        if document[name] != trusted:
            raise ValueError(f"absolute generalization manifest {name.replace('_sha256', '').replace('_', ' ')} differs")
    seal = _manifest_text(document["canonical_sha256"], label="manifest seal")
    unsealed = {key: value for key, value in document.items() if key != "canonical_sha256"}
    if not _SHA256.fullmatch(seal) or canonical_json_sha256(unsealed) != seal:
        raise ValueError("absolute generalization manifest seal differs")
    cells = tuple(
        _manifest_mapping(item, label="raw cell")
        for item in _manifest_sequence(document["cells"], label="manifest cells")
    )
    _validate_summary(document["summary"], cells)
    _validate_payloads(document, contract)
    del shard
    return document


def validate_shard_manifest(
    raw: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> ShardManifest:
    """Validate one sealed manifest against the actual checkout and contract."""

    document = _manifest_document(raw, contract)
    if (document["head"], document["tree"]) != _checkout_identity():
        raise ValueError("absolute generalization manifest differs from current checkout")
    for item in cast(Sequence[Mapping[str, object]], document["cells"]):
        artifact = validate_cell_artifact(item, contract)
        if artifact.identities.head != document["head"] or artifact.identities.tree != document["tree"]:
            raise ValueError("absolute generalization cell differs from manifest checkout")
    return ShardManifest(document)


def seal_shard_manifest(
    raw: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> dict[str, object]:
    """Seal and immediately validate one newly built strict manifest."""

    if "canonical_sha256" in raw:
        raise ValueError("absolute generalization manifest is already sealed")
    document = dict(raw)
    document["canonical_sha256"] = canonical_json_sha256(document)
    return validate_shard_manifest(document, contract).to_dict()


def build_error_shard_manifest(
    *,
    shard: str,
    mode: str,
    error: str,
    run_id: object,
    run_attempt: object,
    head: object,
    tree: object,
    contract: AbsoluteGeneralizationContract,
) -> dict[str, object]:
    """Build the sole strict non-self-asserted upstream failure envelope."""

    raw: dict[str, object] = {
        "schema_version": 1,
        "shard": shard,
        "mode": mode,
        "status": "ERROR",
        "upstream_success": False,
        "error": error,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "head": head,
        "tree": tree,
        "scenario_contract_sha256": contract.canonical_sha256,
        "production_source_sha256": contract.candidate.production_source_sha256,
        "effective_config_sha256": contract.inputs.effective_config_sha256,
        "uv_lock_sha256": contract.inputs.uv_lock_sha256,
        "frozen_data_manifest_sha256": contract.inputs.frozen_data.manifest_sha256,
        "universe_sha256": contract.inputs.ai_universe_sha256,
        "cells": [],
        "champion": None,
        "failed_grant_recovery": None,
        "historical_crowning": None,
        "terminal_scc": None,
        "repair_bounds": [],
        "cross_industry_crowning": None,
        "summary": {"cell_count": 0, "complete_cell_count": 0, "replay_error_cell_count": 0},
    }
    return seal_shard_manifest(raw, contract)


def _manifest_set(
    shard_manifests: Sequence[Mapping[str, object]],
    contract: AbsoluteGeneralizationContract,
) -> tuple[ShardManifest, ...]:
    expected = {*_SPECIAL_SHARDS, *(name for name, _symbols in contract.shards)}
    raw_names = [item.get("shard") if isinstance(item, Mapping) else None for item in shard_manifests]
    duplicates = {name for name in raw_names if raw_names.count(name) > 1}
    if duplicates:
        raise ValueError("absolute generalization duplicate shard")
    unexpected = set(raw_names) - expected
    if unexpected:
        raise ValueError("absolute generalization unexpected shard")
    missing = expected - set(raw_names)
    if missing:
        raise ValueError("absolute generalization missing shard")
    manifests = tuple(validate_shard_manifest(raw, contract) for raw in shard_manifests)
    if any(item.document["mode"] != "canonical" for item in manifests):
        raise ValueError("absolute generalization final aggregation requires canonical mode")
    run_identities = {
        (item.document["run_id"], item.document["run_attempt"], item.document["head"], item.document["tree"])
        for item in manifests
    }
    if len(run_identities) != 1:
        raise ValueError("absolute generalization manifest run identity differs")
    return manifests


def _validated_cells(
    manifests: Sequence[ShardManifest], contract: AbsoluteGeneralizationContract
) -> tuple[CellArtifact, ...]:
    expected_by_shard = {name: set(symbols) for name, symbols in contract.shards}
    observed_ids: list[str] = []
    artifacts: list[CellArtifact] = []
    for manifest in manifests:
        if manifest.shard not in expected_by_shard:
            continue
        raw_cells = tuple(
            cast(Mapping[str, object], _thaw(item))
            for item in cast(Sequence[Mapping[str, object]], manifest.document["cells"])
        )
        ids = [cast(str, item.get("cell_id")) for item in raw_cells]
        removed = [cast(str, item.get("removed_symbol")) for item in raw_cells]
        if len(ids) != len(set(ids)) or any(cell_id in observed_ids for cell_id in ids):
            raise ValueError("absolute generalization duplicate cell")
        observed_ids.extend(ids)
        if manifest.status == "COMPLETE" and (
            set(removed) != expected_by_shard[manifest.shard]
            or set(ids) != {f"remove-{symbol}" for symbol in expected_by_shard[manifest.shard]}
        ):
            raise ValueError("absolute generalization canonical cell coverage differs")
        if manifest.status == "ERROR" and raw_cells:
            raise ValueError("absolute generalization ERROR manifest contains cells")
        artifacts.extend(validate_cell_artifact(item, contract) for item in raw_cells)
    if len(set(observed_ids)) != len(observed_ids):
        raise ValueError("absolute generalization duplicate cell")
    expected_ids = {f"remove-{symbol}" for symbol in contract.canonical_universe}
    if set(observed_ids) - expected_ids:
        raise ValueError("absolute generalization canonical cell coverage differs")
    return tuple(artifacts)


def _absolute_payload(
    manifests: Sequence[ShardManifest], shard: str, name: str
) -> object:
    selected = next(item for item in manifests if item.shard == shard)
    return selected.document[name] if selected.status == "COMPLETE" else None


def _statistics(components: Sequence[ComponentResult]) -> tuple[tuple[str, float], ...]:
    by_name = {item.name: item for item in components}
    absolute = by_name["absolute_strategic_robustness"].evidence
    witness = by_name["witness_resilience"].evidence
    metrics = by_name["complete_literal_metrics"].evidence
    return tuple(
        sorted(
            {
                "accounting_reconciliation_fraction": float(cast(int, metrics["accounting_reconciled_cells"])) / 34,
                "actual_strategic_epoch_cells": float(
                    cast(int, absolute["actual_strategic_epoch_cells"])
                ),
                "intervention_free_fraction": float(cast(int, metrics["intervention_free_cells"])) / 34,
                "p10_final_wealth": float(cast(float, absolute["p10_final_wealth"])),
                "p90_healthy_zero_total_target_streak": float(cast(float, absolute["p90_healthy_zero_total_target_streak"])),
                "p90_max_drawdown": float(cast(float, absolute["p90_max_drawdown"])),
                "positive_return_fraction": float(cast(float, absolute["positive_return_fraction"])),
                "positive_strategic_target_cells": float(
                    cast(int, absolute["positive_strategic_target_cells"])
                ),
                "witness_missing_recovery_fraction": float(cast(float, witness["fraction"])),
                "worst_healthy_zero_total_target_streak": float(cast(int, absolute["worst_healthy_zero_total_target_streak"])),
            }.items()
        )
    )


def _upstream_codes(
    upstream_success: object, upstream_failure_codes: object
) -> tuple[str, ...]:
    if type(upstream_success) is not bool or type(upstream_failure_codes) not in {
        list,
        tuple,
    }:
        raise ValueError("absolute generalization upstream result is malformed")
    codes = tuple(cast(Sequence[object], upstream_failure_codes))
    if (
        any(type(item) is not str or not item for item in codes)
        or len(set(codes)) != len(codes)
        or (upstream_success and codes)
        or (not upstream_success and not codes)
    ):
        raise ValueError("absolute generalization upstream result is malformed")
    return cast(tuple[str, ...], codes)


def _runner_failures(
    manifests: Sequence[ShardManifest],
    errors: Sequence[CellArtifact],
    missing: int,
    complete_metrics: int,
    upstream_codes: Sequence[str],
) -> tuple[str, ...]:
    failures = [
        f"{item.shard} upstream failure: {item.document['error']}"
        for item in manifests
        if item.status == "ERROR"
    ]
    failures.extend(f"workflow upstream failure: {code}" for code in upstream_codes)
    if errors:
        failures.append(f"{len(errors)} replay error cell(s)")
    if missing:
        failures.append(f"{missing} missing canonical cell(s)")
    if complete_metrics != 34:
        failures.append(f"only {complete_metrics}/34 cells have complete metrics")
    return tuple(failures)


def aggregate_acceptance(
    shard_manifests: Sequence[Mapping[str, object]],
    contract: AbsoluteGeneralizationContract,
    *,
    upstream_success: bool = True,
    upstream_failure_codes: Sequence[str] = (),
) -> AcceptanceReport:
    """Revalidate all eight manifests/cells and recompute the sole final report."""

    if type(contract) is not AbsoluteGeneralizationContract:
        raise ValueError("absolute generalization aggregation requires a validated contract")
    upstream_codes = _upstream_codes(upstream_success, upstream_failure_codes)
    manifests = _manifest_set(shard_manifests, contract)
    cells = _validated_cells(manifests, contract)
    complete = tuple(cell for cell in cells if cell.status == "COMPLETE")
    errors = tuple(cell for cell in cells if cell.status == "REPLAY_ERROR")
    missing = len(contract.canonical_universe) - len(cells)
    components = evaluate_literal_components(
        cells=cells,
        champion=cast(
            Mapping[str, object] | None,
            _absolute_payload(manifests, "champion", "champion"),
        ),
        failed_grant=cast(
            Mapping[str, object] | None,
            _absolute_payload(
                manifests, "recovery-and-reachability", "failed_grant_recovery"
            ),
        ),
        historical_crowning=cast(
            Mapping[str, object] | None,
            _absolute_payload(
                manifests, "recovery-and-reachability", "historical_crowning"
            ),
        ),
        terminal_scc=cast(
            Mapping[str, object] | None,
            _absolute_payload(manifests, "recovery-and-reachability", "terminal_scc"),
        ),
        repair_bounds=cast(
            Sequence[Mapping[str, object]],
            _absolute_payload(manifests, "recovery-and-reachability", "repair_bounds")
            or (),
        ),
        cross_industry_crowning=cast(
            Mapping[str, object] | None,
            _absolute_payload(
                manifests, "recovery-and-reachability", "cross_industry_crowning"
            ),
        ),
        contract=contract,
    )
    complete_metrics = sum(cell.metrics is not None for cell in cells)
    runner_failures = _runner_failures(
        manifests, errors, missing, complete_metrics, upstream_codes
    )
    runner_success = not runner_failures and len(complete) == 34
    capability = all(item.passed for item in components)
    report = AcceptanceReport(
        schema_version=1,
        runner_success=runner_success,
        capability_pass=capability,
        passed=runner_success and capability,
        runner_failures=tuple(runner_failures),
        components=components,
        expected_cells=34,
        valid_cells=len(complete),
        replay_error_cells=len(errors),
        missing_cells=missing,
        duplicate_cells=0,
        complete_metric_cells=complete_metrics,
        statistics=_statistics(components) if runner_success else (),
        canonical_sha256="",
    )
    return report


__all__ = (
    "AcceptanceReport",
    "ShardManifest",
    "aggregate_acceptance",
    "build_error_shard_manifest",
    "seal_shard_manifest",
    "validate_shard_manifest",
)
