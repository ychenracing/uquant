"""Strict immutable cell artifacts for absolute generalization evidence."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final, cast

from uquant.contracts.strict_json import canonical_json_sha256

from ._metrics_reconciliation import derive_complete_cell_metrics_impl
from ._replay_codec import replay_from_raw, replay_to_raw
from .contract import AbsoluteGeneralizationContract, load_absolute_generalization_contract
from .metrics import (
    CellMetrics,
    EventEvidence,
    cell_metrics_from_raw,
)
from .replay import AbsoluteGeneralizationReplay
from .scenarios import AbsoluteGeneralizationScenario

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_EVENT_NAMES: Final = (
    "first_divergence",
    "qualification_to_grant",
    "grant_to_target",
    "target_to_order",
    "order_to_fill",
    "fill_to_active_epoch",
    "failed_grant_retry",
    "terminal_zero_strategic_target_state",
)
ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256: Final = canonical_json_sha256(
    {
        "decision": "daily_close_t",
        "engine": "uquant.engine.ProductionEngine",
        "execution": "next_tradable_open",
        "intraday_exit": False,
        "prelisting": "invisible_until_first_observable_row",
    }
)


def _artifact_git_object(root: Path, revision: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve current checkout identity")
    try:
        result = subprocess.run(
            [executable, "-C", str(root), "rev-parse", revision],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot resolve current checkout identity") from exc
    value = result.stdout.strip()
    if not _COMMIT.fullmatch(value):
        raise RuntimeError("cannot resolve current checkout identity")
    return value


@dataclass(frozen=True, slots=True)
class EventFact:
    """One explicit event observation, including a non-applicable reason."""

    applicable: bool
    observed: bool
    healthy_sessions: int
    reason: str

    def __post_init__(self) -> None:
        if (
            type(self.applicable) is not bool
            or type(self.observed) is not bool
            or type(self.healthy_sessions) is not int
            or self.healthy_sessions < 0
            or not isinstance(self.reason, str)
            or not self.reason
        ):
            raise ValueError("absolute generalization event fact is malformed")
        if not self.applicable and (self.observed or self.healthy_sessions != 0):
            raise ValueError("non-applicable event fact must be unobserved at zero sessions")


@dataclass(frozen=True, slots=True)
class IdentityEnvelope:
    """All identities required to authenticate one cell."""

    head: str
    tree: str
    scenario_contract_sha256: str
    production_source_sha256: str
    effective_config_sha256: str
    uv_lock_sha256: str
    frozen_data_manifest_sha256: str
    universe_sha256: str
    industry_mapping_sha256: str
    tradable_role_identity: str
    qualification_reference_role_identity: str
    risk_reference_role_identity: str
    execution_contract_identity: str


@dataclass(frozen=True, slots=True)
class CellArtifact:
    """Sealed immutable facts for one complete or failed replay cell."""

    schema_version: int
    cell_id: str
    removed_symbol: str
    window_start: str
    window_end: str
    status: str
    replay_error: str
    intervention_provenance: tuple[str, ...]
    accounting_reconciled: bool
    target_order_fill_identity_reconciled: bool
    duplicate_grant_count: int
    duplicate_order_count: int
    duplicate_epoch_count: int
    identities: IdentityEnvelope
    metrics: CellMetrics | None
    event_facts: tuple[tuple[str, EventFact], ...]
    replay_evidence: AbsoluteGeneralizationReplay | None
    canonical_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return a fresh strict-JSON mapping without exposing mutable state."""

        raw = asdict(self)
        raw["intervention_provenance"] = list(self.intervention_provenance)
        raw["event_facts"] = {name: asdict(fact) for name, fact in self.event_facts}
        if self.metrics is not None:
            raw["metrics"] = self.metrics.to_dict()
        raw["replay_evidence"] = (
            None if self.replay_evidence is None else replay_to_raw(self.replay_evidence)
        )
        return cast(dict[str, object], raw)


def _unsealed(artifact: CellArtifact) -> dict[str, object]:
    raw = artifact.to_dict()
    raw.pop("canonical_sha256")
    return raw


def seal_cell_artifact(artifact: CellArtifact) -> CellArtifact:
    """Bind every literal fact to one canonical SHA-256."""

    if artifact.canonical_sha256:
        raise ValueError("absolute generalization artifact is already sealed")
    return replace(artifact, canonical_sha256=canonical_json_sha256(_unsealed(artifact)))


def artifact_exact_fields(raw: Mapping[str, object], expected: set[str], *, label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"absolute generalization {label} fields differ")


def artifact_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"absolute generalization {label} is malformed")
    return cast(Mapping[str, object], value)


def artifact_sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"absolute generalization {label} is malformed")
    return cast(Sequence[object], value)


def _is_production_predicate_fact(value: Mapping[object, object]) -> bool:
    """Recognize the strict production predicate DTO, whose fact is named passed."""

    return (
        set(value)
        == {
            "authoritative_state",
            "code",
            "economic_authority",
            "orphan_residue",
            "passed",
        }
        and isinstance(value.get("authoritative_state"), Mapping)
        and isinstance(value.get("code"), str)
        and bool(value.get("code"))
        and type(value.get("economic_authority")) is bool
        and type(value.get("orphan_residue")) is bool
        and type(value.get("passed")) is bool
    )


def _is_production_predicate_path(path: tuple[str | int, ...]) -> bool:
    owners = {"flat_book_capital_repair", "strategic_cash_rearm"}
    for index, component in enumerate(path):
        if component != "replay_evidence":
            continue
        prefix = path[:index]
        if prefix and not (
            len(prefix) == 2
            and prefix[0] == "cells"
            and isinstance(prefix[1], int)
        ):
            continue
        tail = path[index:]
        if (
            len(tail) == 6
            and tail[:3] == ("replay_evidence", "final_account_payload", "value")
            and tail[3] in owners
            and tail[4] == "predicate_results"
            and isinstance(tail[5], int)
        ):
            return True
        if len(tail) != 9 or tail[:2] != ("replay_evidence", "observations"):
            continue
        if not isinstance(tail[2], int) or tail[6] not in owners:
            continue
        if tail[7] != "predicate_results" or not isinstance(tail[8], int):
            continue
        if tail[3:6] == ("decision_payload", "value", "risk_summary") or (
            tail[3] in {"post_open_account", "post_decision_account"}
            and tail[4:6] == ("account_payload", "value")
        ):
            return True
    return False


def reject_self_assertion_claims(
    value: object,
    *,
    label: str = "artifact",
    path: tuple[str | int, ...] = (),
) -> None:
    if isinstance(value, Mapping):
        forbidden = {
            key
            for key in value
            if isinstance(key, str)
            and (
                key in {"passed", "runner_success", "capability_pass"}
                or key.endswith("_passed")
            )
        }
        if forbidden and not (
            forbidden == {"passed"} and _is_production_predicate_fact(value)
            and _is_production_predicate_path(path)
        ):
            raise ValueError(
                f"absolute generalization {label} contains a self-asserted pass"
            )
        for key, item in value.items():
            reject_self_assertion_claims(item, label=label, path=(*path, key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            reject_self_assertion_claims(item, label=label, path=(*path, index))


def artifact_finite_json(value: object) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("absolute generalization artifact contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for item in value.values():
            artifact_finite_json(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            artifact_finite_json(item)
        return
    raise ValueError("absolute generalization artifact contains a non-JSON value")


def validate_identity_envelope(
    identities: IdentityEnvelope,
    contract: AbsoluteGeneralizationContract,
) -> None:
    """Fail closed on stale contract-controlled or malformed runtime identities."""

    if type(identities) is not IdentityEnvelope:
        raise ValueError("absolute generalization identity envelope type differs")
    if not _COMMIT.fullmatch(identities.head) or not _COMMIT.fullmatch(identities.tree):
        raise ValueError("absolute generalization HEAD/tree identity is malformed")
    sha_fields = (
        identities.scenario_contract_sha256,
        identities.production_source_sha256,
        identities.effective_config_sha256,
        identities.uv_lock_sha256,
        identities.frozen_data_manifest_sha256,
        identities.universe_sha256,
        identities.industry_mapping_sha256,
        identities.tradable_role_identity,
        identities.qualification_reference_role_identity,
        identities.risk_reference_role_identity,
        identities.execution_contract_identity,
    )
    if any(not _SHA256.fullmatch(value) for value in sha_fields):
        raise ValueError("absolute generalization identity envelope contains malformed SHA-256")
    expected = (
        (identities.scenario_contract_sha256, contract.canonical_sha256, "scenario contract"),
        (
            identities.production_source_sha256,
            contract.candidate.production_source_sha256,
            "production source",
        ),
        (
            identities.effective_config_sha256,
            contract.inputs.effective_config_sha256,
            "effective config",
        ),
        (identities.uv_lock_sha256, contract.inputs.uv_lock_sha256, "uv.lock"),
        (
            identities.frozen_data_manifest_sha256,
            contract.inputs.frozen_data.manifest_sha256,
            "frozen data manifest",
        ),
        (identities.universe_sha256, contract.inputs.ai_universe_sha256, "universe"),
        (
            identities.execution_contract_identity,
            ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
            "execution contract",
        ),
    )
    for observed, trusted, label in expected:
        if observed != trusted:
            raise ValueError(f"absolute generalization {label} identity differs")


def _validate_derived_role_identities(
    replay: AbsoluteGeneralizationReplay,
    identities: IdentityEnvelope,
) -> None:
    """Bind the cell envelope to the role identities actually replayed."""

    if not replay.observations:
        raise ValueError("absolute generalization complete replay has no observations")
    roles = replay.observations[-1].roles
    expected = (
        (identities.industry_mapping_sha256, roles.point_in_time_industry_identity, "industry mapping"),
        (identities.tradable_role_identity, roles.tradable_identity, "tradable role"),
        (
            identities.qualification_reference_role_identity,
            roles.qualification_reference_identity,
            "qualification reference role",
        ),
        (identities.risk_reference_role_identity, roles.risk_reference_identity, "risk reference role"),
    )
    for observed, actual, label in expected:
        if observed != actual:
            raise ValueError(f"absolute generalization {label} identity differs")


def artifact_trusted_scenario(
    replay: AbsoluteGeneralizationReplay,
    scenario: AbsoluteGeneralizationScenario,
    contract: AbsoluteGeneralizationContract,
) -> None:
    if type(replay) is not AbsoluteGeneralizationReplay:
        raise ValueError("absolute generalization replay type differs")
    if type(scenario) is not AbsoluteGeneralizationScenario:
        raise ValueError("absolute generalization scenario type differs")
    if replay.scenario != scenario:
        raise ValueError("absolute generalization replay scenario differs")
    if (
        scenario.contract_sha256 != contract.canonical_sha256
        or scenario.cell_id != f"remove-{scenario.removed_symbol}"
        or scenario.removed_symbol not in contract.canonical_universe
        or scenario.window_start != contract.window_start
        or scenario.window_end != contract.window_end
    ):
        raise ValueError("absolute generalization scenario differs from contract")


def _event_facts_from_evidence(
    evidence: tuple[EventEvidence, ...],
) -> tuple[tuple[str, EventFact], ...]:
    if tuple(item.name for item in evidence) != _EVENT_NAMES:
        raise ValueError("absolute generalization event evidence names differ")
    return tuple(
        (
            item.name,
            EventFact(
                applicable=item.applicable,
                observed=item.observed,
                healthy_sessions=item.healthy_sessions,
                reason=item.reason,
            ),
        )
        for item in evidence
    )


def derive_cell_metrics(
    replay: AbsoluteGeneralizationReplay,
    scenario: AbsoluteGeneralizationScenario,
    identities: IdentityEnvelope,
) -> CellArtifact:
    """Derive one sealed artifact from raw replay evidence, never pass claims."""

    contract = load_absolute_generalization_contract()
    artifact_trusted_scenario(replay, scenario, contract)
    validate_identity_envelope(identities, contract)
    if replay.status == "REPLAY_ERROR":
        if not isinstance(replay.replay_error, str) or not replay.replay_error:
            raise ValueError("absolute generalization replay error is malformed")
        return seal_cell_artifact(
            CellArtifact(
                schema_version=1,
                cell_id=scenario.cell_id,
                removed_symbol=scenario.removed_symbol,
                window_start=scenario.window_start.isoformat(),
                window_end=scenario.window_end.isoformat(),
                status="REPLAY_ERROR",
                replay_error=replay.replay_error,
                intervention_provenance=(),
                accounting_reconciled=False,
                target_order_fill_identity_reconciled=False,
                duplicate_grant_count=0,
                duplicate_order_count=0,
                duplicate_epoch_count=0,
                identities=identities,
                metrics=None,
                event_facts=replay_error_event_facts(),
                replay_evidence=None,
                canonical_sha256="",
            )
        )
    if replay.status != "COMPLETE":
        raise ValueError("absolute generalization replay status differs")
    _validate_derived_role_identities(replay, identities)
    metrics, evidence = derive_complete_cell_metrics_impl(replay)
    return seal_cell_artifact(
        CellArtifact(
            schema_version=1,
            cell_id=scenario.cell_id,
            removed_symbol=scenario.removed_symbol,
            window_start=scenario.window_start.isoformat(),
            window_end=scenario.window_end.isoformat(),
            status="COMPLETE",
            replay_error="",
            intervention_provenance=(),
            accounting_reconciled=True,
            target_order_fill_identity_reconciled=True,
            duplicate_grant_count=0,
            duplicate_order_count=0,
            duplicate_epoch_count=0,
            identities=identities,
            metrics=metrics,
            event_facts=_event_facts_from_evidence(evidence),
            replay_evidence=replay,
            canonical_sha256="",
        )
    )


def _identity_from_raw(raw: Mapping[str, object]) -> IdentityEnvelope:
    expected = {field for field in IdentityEnvelope.__dataclass_fields__}
    artifact_exact_fields(raw, expected, label="identity envelope")
    if any(not isinstance(raw[name], str) for name in expected):
        raise ValueError("absolute generalization identity envelope is malformed")
    return IdentityEnvelope(**cast(dict[str, str], dict(raw)))


def _events_from_raw(value: object) -> tuple[tuple[str, EventFact], ...]:
    raw = artifact_mapping(value, label="event facts")
    if tuple(sorted(raw)) != tuple(sorted(_EVENT_NAMES)):
        raise ValueError("absolute generalization event fact names differ")
    facts: list[tuple[str, EventFact]] = []
    expected = {"applicable", "observed", "healthy_sessions", "reason"}
    for name in _EVENT_NAMES:
        fact_raw = artifact_mapping(raw[name], label=f"event fact {name}")
        artifact_exact_fields(fact_raw, expected, label=f"event fact {name}")
        fact = EventFact(
            applicable=cast(bool, fact_raw["applicable"]),
            observed=cast(bool, fact_raw["observed"]),
            healthy_sessions=cast(int, fact_raw["healthy_sessions"]),
            reason=cast(str, fact_raw["reason"]),
        )
        facts.append((name, fact))
    return tuple(facts)


def _validate_metric_identity(metrics: CellMetrics) -> None:
    tolerance = 1e-8
    if not math.isclose(
        metrics.final_wealth,
        metrics.final_equity / metrics.initial_cash,
        rel_tol=1e-12,
        abs_tol=tolerance,
    ) or not math.isclose(
        metrics.total_return,
        metrics.final_wealth - 1.0,
        rel_tol=1e-12,
        abs_tol=tolerance,
    ):
        raise ValueError("absolute generalization metric identity differs")
    if not math.isclose(
        metrics.realized_pnl + metrics.open_pnl,
        metrics.final_equity - metrics.initial_cash,
        rel_tol=1e-12,
        abs_tol=tolerance,
    ):
        raise ValueError("absolute generalization metric accounting identity differs")
    if (
        metrics.actual_strategic_epoch_count != len(metrics.epochs)
        or metrics.repair_episode_count != len(metrics.repairs)
        or metrics.distinct_owner_count != len(metrics.owner_symbols)
        or tuple(sorted(set(metrics.owner_symbols))) != metrics.owner_symbols
        or tuple(sorted({fact.owner_symbol for fact in metrics.epochs})) != metrics.owner_symbols
    ):
        raise ValueError("absolute generalization metric identity differs")


@dataclass(frozen=True, slots=True)
class _ArtifactHeader:
    document: Mapping[str, object]
    schema_version: int
    cell_id: str
    removed_symbol: str
    window_start: str
    window_end: str
    status: str
    replay_error: str
    accounting_reconciled: bool
    target_order_fill_identity_reconciled: bool
    identities: IdentityEnvelope
    canonical_sha256: str


def _absolute_generalization_artifact_header(
    raw: Mapping[str, object], contract: AbsoluteGeneralizationContract
) -> _ArtifactHeader:
    document = artifact_mapping(raw, label="cell artifact")
    reject_self_assertion_claims(document)
    artifact_finite_json(document)
    expected_fields = {
        "schema_version",
        "cell_id",
        "removed_symbol",
        "window_start",
        "window_end",
        "status",
        "replay_error",
        "intervention_provenance",
        "accounting_reconciled",
        "target_order_fill_identity_reconciled",
        "duplicate_grant_count",
        "duplicate_order_count",
        "duplicate_epoch_count",
        "identities",
        "metrics",
        "event_facts",
        "replay_evidence",
        "canonical_sha256",
    }
    artifact_exact_fields(document, expected_fields, label="cell artifact")
    if document["status"] == "REPLAY_ERROR" and document["metrics"] is not None:
        raise ValueError("replay-error artifacts must remain explicit and metric-free")
    schema_version = document["schema_version"]
    cell_id = document["cell_id"]
    window_start = document["window_start"]
    window_end = document["window_end"]
    status = document["status"]
    replay_error = document["replay_error"]
    accounting_reconciled = document["accounting_reconciled"]
    target_order_fill_identity_reconciled = document["target_order_fill_identity_reconciled"]
    seal = document["canonical_sha256"]
    if (
        type(schema_version) is not int
        or not isinstance(cell_id, str)
        or not isinstance(window_start, str)
        or not isinstance(window_end, str)
        or not isinstance(status, str)
        or not isinstance(replay_error, str)
        or type(accounting_reconciled) is not bool
        or type(target_order_fill_identity_reconciled) is not bool
    ):
        raise ValueError("absolute generalization cell artifact scalar fields are malformed")
    unsealed = {key: value for key, value in document.items() if key != "canonical_sha256"}
    if not isinstance(seal, str) or canonical_json_sha256(unsealed) != seal:
        raise ValueError("absolute generalization cell artifact seal is invalid")
    identities = _identity_from_raw(artifact_mapping(document["identities"], label="identities"))
    validate_identity_envelope(identities, contract)
    removed = document["removed_symbol"]
    if not isinstance(removed, str) or removed not in contract.canonical_universe:
        raise ValueError("absolute generalization artifact removed symbol differs")
    if cell_id != f"remove-{removed}":
        raise ValueError("absolute generalization artifact cell identity differs")
    if window_start != contract.window_start.isoformat() or window_end != contract.window_end.isoformat():
        raise ValueError("absolute generalization artifact window differs")
    return _ArtifactHeader(
        document=document,
        schema_version=schema_version,
        cell_id=cell_id,
        removed_symbol=removed,
        window_start=window_start,
        window_end=window_end,
        status=status,
        replay_error=replay_error,
        accounting_reconciled=accounting_reconciled,
        target_order_fill_identity_reconciled=target_order_fill_identity_reconciled,
        identities=identities,
        canonical_sha256=seal,
    )


def _artifact_metrics_and_events(
    header: _ArtifactHeader,
    contract: AbsoluteGeneralizationContract,
) -> tuple[
    CellMetrics | None,
    tuple[tuple[str, EventFact], ...],
    AbsoluteGeneralizationReplay | None,
]:
    metrics_raw = header.document["metrics"]
    facts = _events_from_raw(header.document["event_facts"])
    replay_raw = header.document["replay_evidence"]
    if header.status == "REPLAY_ERROR":
        if not header.replay_error or metrics_raw is not None or replay_raw is not None:
            raise ValueError("replay-error artifacts must remain explicit and metric-free")
        if any(fact != EventFact(False, False, 0, "REPLAY_ERROR") for _, fact in facts):
            raise ValueError("replay-error artifacts require non-applicable event facts")
        return None, facts, None
    if header.status == "COMPLETE":
        if header.replay_error != "" or metrics_raw is None or replay_raw is None:
            raise ValueError("complete artifacts require literal metrics, replay evidence, and no error")
        if any(fact.reason == "REPLAY_ERROR" for _, fact in facts):
            raise ValueError("complete artifacts cannot contain replay-error event facts")
        metrics = cell_metrics_from_raw(artifact_mapping(metrics_raw, label="cell metrics"))
        _validate_metric_identity(metrics)
        replay = replay_from_raw(replay_raw)
        artifact_trusted_scenario(replay, replay.scenario, contract)
        if (
            replay.scenario.cell_id != header.cell_id
            or replay.scenario.removed_symbol != header.removed_symbol
            or replay.scenario.window_start.isoformat() != header.window_start
            or replay.scenario.window_end.isoformat() != header.window_end
        ):
            raise ValueError("absolute generalization replay evidence scenario differs")
        _validate_derived_role_identities(replay, header.identities)
        derived_metrics, evidence = derive_complete_cell_metrics_impl(replay)
        if metrics != derived_metrics:
            raise ValueError("absolute generalization derived metrics differ from replay evidence")
        derived_facts = _event_facts_from_evidence(evidence)
        if facts != derived_facts:
            raise ValueError("absolute generalization derived event facts differ from replay evidence")
        return metrics, facts, replay
    raise ValueError("absolute generalization artifact status differs")


def _artifact_reconciliation_values(
    header: _ArtifactHeader,
) -> tuple[tuple[str, ...], tuple[int, int, int]]:
    provenance_values = artifact_sequence(
        header.document["intervention_provenance"], label="intervention provenance"
    )
    if not all(isinstance(item, str) for item in provenance_values):
        raise ValueError("absolute generalization intervention provenance is malformed")
    provenance = tuple(cast(str, item) for item in provenance_values)
    if any(not item for item in provenance):
        raise ValueError("absolute generalization intervention provenance is malformed")
    counts = tuple(
        header.document[name]
        for name in ("duplicate_grant_count", "duplicate_order_count", "duplicate_epoch_count")
    )
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("absolute generalization duplicate count is malformed")
    duplicate_counts = cast(tuple[int, int, int], counts)
    if header.status == "REPLAY_ERROR" and (
        header.accounting_reconciled is not False
        or header.target_order_fill_identity_reconciled is not False
        or provenance
        or duplicate_counts != (0, 0, 0)
    ):
        raise ValueError("replay-error artifacts must remain entirely non-applicable")
    if header.status == "COMPLETE" and (
        header.accounting_reconciled is not True
        or header.target_order_fill_identity_reconciled is not True
        or provenance
        or duplicate_counts != (0, 0, 0)
    ):
        raise ValueError("absolute generalization complete artifact reconciliation differs")
    return provenance, duplicate_counts


def validate_cell_artifact(
    raw: Mapping[str, object],
    contract: AbsoluteGeneralizationContract,
) -> CellArtifact:
    """Strictly parse and independently validate one sealed cell artifact."""

    if type(contract) is not AbsoluteGeneralizationContract:
        raise ValueError("absolute generalization artifact requires a validated contract")
    header = _absolute_generalization_artifact_header(raw, contract)
    metrics, facts, replay = _artifact_metrics_and_events(header, contract)
    provenance, counts = _artifact_reconciliation_values(header)
    artifact = CellArtifact(
        schema_version=header.schema_version,
        cell_id=header.cell_id,
        removed_symbol=header.removed_symbol,
        window_start=header.window_start,
        window_end=header.window_end,
        status=header.status,
        replay_error=header.replay_error,
        intervention_provenance=provenance,
        accounting_reconciled=header.accounting_reconciled,
        target_order_fill_identity_reconciled=header.target_order_fill_identity_reconciled,
        duplicate_grant_count=counts[0],
        duplicate_order_count=counts[1],
        duplicate_epoch_count=counts[2],
        identities=header.identities,
        metrics=metrics,
        event_facts=facts,
        replay_evidence=replay,
        canonical_sha256=header.canonical_sha256,
    )
    if artifact.schema_version != 1:
        raise ValueError("absolute generalization artifact schema differs")
    return artifact


def derive_runtime_cell_artifact(
    replay: AbsoluteGeneralizationReplay,
    contract: AbsoluteGeneralizationContract,
    *,
    root: str | Path | None = None,
) -> CellArtifact:
    """Derive one sealed cell from raw production replay observations."""

    if not replay.observations:
        raise ValueError("absolute runtime replay has no observed role identity")
    repository = (
        Path(__file__).resolve().parents[3]
        if root is None
        else Path(root).resolve()
    )
    roles = replay.observations[-1].roles
    identities = IdentityEnvelope(
        head=_artifact_git_object(repository, "HEAD"),
        tree=_artifact_git_object(repository, "HEAD^{tree}"),
        scenario_contract_sha256=contract.canonical_sha256,
        production_source_sha256=contract.candidate.production_source_sha256,
        effective_config_sha256=contract.inputs.effective_config_sha256,
        uv_lock_sha256=contract.inputs.uv_lock_sha256,
        frozen_data_manifest_sha256=contract.inputs.frozen_data.manifest_sha256,
        universe_sha256=contract.inputs.ai_universe_sha256,
        industry_mapping_sha256=roles.point_in_time_industry_identity,
        tradable_role_identity=roles.tradable_identity,
        qualification_reference_role_identity=roles.qualification_reference_identity,
        risk_reference_role_identity=roles.risk_reference_identity,
        execution_contract_identity=ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    )
    artifact = derive_cell_metrics(replay, replay.scenario, identities)
    return validate_cell_artifact(artifact.to_dict(), contract)


def replay_error_event_facts() -> tuple[tuple[str, EventFact], ...]:
    """Return the one canonical non-applicable error fact set."""

    return tuple((name, EventFact(False, False, 0, "REPLAY_ERROR")) for name in _EVENT_NAMES)


__all__ = (
    "ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256",
    "CellArtifact",
    "EventFact",
    "IdentityEnvelope",
    "derive_cell_metrics",
    "derive_runtime_cell_artifact",
    "replay_error_event_facts",
    "seal_cell_artifact",
    "validate_cell_artifact",
    "validate_identity_envelope",
)
