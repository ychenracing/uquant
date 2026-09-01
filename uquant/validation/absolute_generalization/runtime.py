"""Production-backed fact producers for Absolute Generalization Acceptance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import cast

from uquant.account import account_from_dict
from uquant.contracts.strict_json import canonical_json_sha256, strict_json_loads
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.validation.generalization_reference import (
    load_generalization_baseline,
    load_generalization_policy,
)

from ._champion_runtime_reconciliation import (
    derive_champion_runtime_claims,
    derive_report_runtime_claims,
    project_champion_account,
    project_champion_baseline_views,
)
from ._physical_identity import physical_fill_identity_sha256
from .artifacts import (
    CellArtifact,
    derive_runtime_cell_artifact,
)
from .contract import AbsoluteGeneralizationContract
from .recovery_runtime import run_recovery_runtime_payload
from .replay import run_absolute_generalization_replay
from .scenarios import AbsoluteGeneralizationScenario

_FORBIDDEN_CLAIMS = frozenset({"passed", "runner_success", "capability_pass", "status", "retry_sessions"})


def _runtime_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"absolute runtime {label} is malformed")
    return cast(Mapping[str, object], value)


def _runtime_rows(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"absolute runtime {label} is malformed")
    return cast(Sequence[object], value)


_project_baseline_views = project_champion_baseline_views


def _freeze_runtime(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_runtime(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_runtime(item) for item in value)
    return value


def _thaw_runtime(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_runtime(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_thaw_runtime(item) for item in value]
    return value


def _raw_pairs(value: object, *, label: str) -> Mapping[str, object]:
    if type(value) is not tuple:
        raise ValueError(f"absolute {label} requires raw production evidence")
    pairs = cast(tuple[object, ...], value)
    if any(
        type(item) is not tuple
        or len(cast(tuple[object, ...], item)) != 2
        or type(cast(tuple[object, ...], item)[0]) is not str
        for item in pairs
    ):
        raise ValueError(f"absolute {label} requires raw production evidence")
    raw = {cast(str, cast(tuple[object, ...], item)[0]): cast(tuple[object, ...], item)[1] for item in pairs}
    if len(raw) != len(pairs) or _FORBIDDEN_CLAIMS.intersection(raw):
        raise ValueError(f"absolute {label} requires raw production evidence")
    return raw


@dataclass(frozen=True, slots=True)
class ChampionRuntimeEvidence:
    """Deeply immutable champion payload derived only by the production runner."""

    payload: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        raw = _raw_pairs(self.payload, label="champion runtime")
        object.__setattr__(
            self,
            "payload",
            tuple((key, _freeze_runtime(item)) for key, item in raw.items()),
        )

    def to_manifest_payload(self) -> dict[str, object]:
        return cast(dict[str, object], _thaw_runtime(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class RecoveryReachabilityRuntimeEvidence:
    """Deeply immutable raw special-shard payload without policy conclusions."""

    failed_grant_recovery: tuple[tuple[str, object], ...]
    historical_crowning: tuple[tuple[str, object], ...]
    terminal_scc: tuple[tuple[str, object], ...]
    repair_bounds: tuple[tuple[tuple[str, object], ...], ...]
    cross_industry_crowning: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        for name in (
            "failed_grant_recovery",
            "historical_crowning",
            "terminal_scc",
            "cross_industry_crowning",
        ):
            raw = _raw_pairs(getattr(self, name), label=name.replace("_", " "))
            object.__setattr__(
                self,
                name,
                tuple((key, _freeze_runtime(item)) for key, item in raw.items()),
            )
        frozen_repairs = []
        for row in self.repair_bounds:
            raw = _raw_pairs(row, label="repair runtime")
            frozen_repairs.append(tuple((key, _freeze_runtime(item)) for key, item in raw.items()))
        object.__setattr__(self, "repair_bounds", tuple(frozen_repairs))

    def to_manifest_payload(self) -> dict[str, object]:
        return {
            "failed_grant_recovery": _thaw_runtime(dict(self.failed_grant_recovery)),
            "historical_crowning": _thaw_runtime(dict(self.historical_crowning)),
            "terminal_scc": _thaw_runtime(dict(self.terminal_scc)),
            "repair_bounds": [_thaw_runtime(dict(item)) for item in self.repair_bounds],
            "cross_industry_crowning": _thaw_runtime(dict(self.cross_industry_crowning)),
        }


def _physical_json(path: Path, *, label: str) -> Mapping[str, object]:
    validate = path.absolute()
    while True:
        if validate.is_symlink():
            raise ValueError(f"absolute runtime {label} path contains a symlink")
        if validate == validate.parent:
            break
        validate = validate.parent
    if not path.is_file():
        raise ValueError(f"absolute runtime {label} is missing")
    raw = strict_json_loads(path.read_bytes())
    return _runtime_mapping(raw, label=label)


def _validated_runtime_paths(
    *, root: str | Path, data_dir: str | Path, cache_dir: str | Path
) -> tuple[Path, Path, Path]:
    repository = Path(root).resolve()
    data = Path(data_dir)
    cache = Path(cache_dir)
    if not repository.is_dir() or data.is_symlink() or not data.is_dir():
        raise ValueError("absolute runtime repository or data directory is unsafe")
    if cache.is_symlink():
        raise ValueError("absolute runtime cache directory is unsafe")
    cache.mkdir(parents=True, exist_ok=True)
    return repository, data, cache


def _baseline_evidence(
    result: Mapping[str, object], grant_contract: Mapping[str, object]
) -> dict[str, object]:
    baseline = _runtime_mapping(grant_contract.get("baseline"), label="grant baseline")
    ignored = frozenset(
        str(item)
        for item in _runtime_rows(
            grant_contract.get("ignored_non_economic_fields"),
            label="grant ignored fields",
        )
    )
    views = _project_baseline_views(result, ignored)
    paths = {name: canonical_json_sha256(item) for name, item in views.items()}
    metrics_expected = _runtime_mapping(baseline.get("expected_metrics"), label="grant metrics")
    metrics = {name: result.get(name) for name in metrics_expected}
    trace = _runtime_rows(result.get("decision_trace"), label="champion trace")
    first_positive = next(
        str(row["date"])
        for item in trace
        for row in (_runtime_mapping(item, label="champion decision"),)
        if float(cast(float, row["target_gross"])) > 0.0
    )
    expected = {
        "first_positive_target_session": baseline.get("expected_first_positive_target_session"),
        "metrics": dict(metrics_expected),
        "sha256": dict(_runtime_mapping(baseline.get("expected_sha256"), label="grant paths")),
    }
    actual: dict[str, object] = {
        "first_positive_target_session": first_positive,
        "metrics": metrics,
        "sha256": paths,
    }
    if actual != expected:
        raise RuntimeError("absolute champion baseline differs from frozen evidence")
    return actual


def _unique_nonempty(values: Sequence[object], *, label: str) -> list[str]:
    result = [str(item) for item in values if str(item)]
    if not result:
        raise RuntimeError(f"absolute champion {label} identity differs")
    return sorted(set(result))


def _ownership_champion(result: Mapping[str, object], *, scenario_id: str) -> dict[str, object]:
    account = account_from_dict(_runtime_mapping(result.get("final_account"), label="champion account"))
    trace = tuple(
        _runtime_mapping(item, label="champion decision")
        for item in _runtime_rows(result.get("decision_trace"), label="champion trace")
    )
    epochs = tuple(item for item in account.strategic_epochs if item.first_fill_session)
    targets = tuple(
        target
        for row in trace
        for raw in _runtime_rows(row.get("targets"), label="champion targets")
        for target in (_runtime_mapping(raw, label="champion target"),)
        if target.get("origin_subsystem") == "STRATEGIC"
        and float(cast(float, target.get("weight", 0.0))) > 0.0
    )
    fill_identity_sha256s = sorted(physical_fill_identity_sha256(item) for item in account.fills)
    if len(fill_identity_sha256s) != len(set(fill_identity_sha256s)):
        raise ValueError("absolute champion physical fills are duplicated")
    return {
        "scenario_id": scenario_id,
        "owner_symbols": _unique_nonempty([item.owner_symbol for item in epochs], label="owners"),
        "grant_ids": _unique_nonempty([item.grant_id for item in epochs], label="grants"),
        "epoch_ids": _unique_nonempty([item.epoch_id for item in epochs], label="epochs"),
        "target_event_ids": _unique_nonempty(
            [target.get("event_id", "") for target in targets], label="targets"
        ),
        "order_ids": _unique_nonempty([item.order_id for item in account.order_ledger], label="orders"),
        "fill_identity_sha256s": fill_identity_sha256s,
        "final_account": project_champion_account(_runtime_mapping(result["final_account"], label="account")),
        "decision_trace": [dict(item) for item in trace],
        "order_ledger": list(_runtime_rows(result.get("order_ledger"), label="champion order ledger")),
        "equity_curve": list(_runtime_rows(result.get("equity_curve"), label="champion equity curve")),
        "daily_replay_evidence": list(
            _runtime_rows(result.get("daily_replay_evidence"), label="champion replay evidence")
        ),
        "trace_sha256": canonical_json_sha256(list(trace)),
    }


def _report_13(
    result: Mapping[str, object],
    *,
    contract: AbsoluteGeneralizationContract,
    allowed_symbols: Sequence[str],
) -> tuple[dict[str, object], dict[str, object]]:
    report, derived_completion = derive_report_runtime_claims(result, allowed_symbols)
    trace = [
        _runtime_mapping(item, label="report decision")
        for item in _runtime_rows(result.get("decision_trace"), label="report trace")
    ]
    completion = {
        "scenario_id": "report-13",
        "window_start": contract.window_start.isoformat(),
        "window_end": contract.window_end.isoformat(),
        **derived_completion,
        "final_account": project_champion_account(
            _runtime_mapping(result["final_account"], label="report account")
        ),
        "decision_trace": [dict(item) for item in trace],
        "order_ledger": list(_runtime_rows(result.get("order_ledger"), label="report order ledger")),
        "equity_curve": list(_runtime_rows(result.get("equity_curve"), label="report equity curve")),
        "daily_replay_evidence": list(
            _runtime_rows(result.get("daily_replay_evidence"), label="report replay evidence")
        ),
    }
    return report, completion


def _champion_runtime_payload(
    *, repository: Path, data: Path, contract: AbsoluteGeneralizationContract
) -> dict[str, object]:
    grant_contract = _physical_json(
        repository / "benchmarks/strategic_grant_acceptance_contract.json",
        label="strategic grant contract",
    )
    ownership = _physical_json(
        repository / "benchmarks/strategic_ownership_acceptance_contract.json",
        label="strategic ownership contract",
    )
    baseline = _runtime_mapping(grant_contract["baseline"], label="grant baseline")
    champion = ProductionEngine(data).backtest(
        symbols=tuple(str(item) for item in _runtime_rows(baseline["symbols"], label="champion symbols")),
        start=str(baseline["start"]),
        end=str(baseline["end"]),
    )
    report_symbols = tuple(
        str(item) for item in _runtime_rows(ownership["report_universe_13"], label="report universe")
    )
    report_result = ProductionEngine(data).backtest(
        symbols=report_symbols,
        start=contract.window_start.isoformat(),
        end=contract.window_end.isoformat(),
    )
    baseline_evidence = _baseline_evidence(champion, grant_contract)
    champion_claims = derive_champion_runtime_claims(
        champion,
        frozenset(
            str(item)
            for item in _runtime_rows(
                grant_contract["ignored_non_economic_fields"],
                label="grant ignored fields",
            )
        ),
    )
    report, report_completion = _report_13(
        report_result,
        contract=contract,
        allowed_symbols=report_symbols,
    )
    relative_baseline = load_generalization_baseline()
    relative_policy = load_generalization_policy()
    if relative_policy.baseline_sha256 != relative_baseline.sha256:
        raise RuntimeError("absolute relative policy reference identity differs")
    payload: dict[str, object] = {
        **champion_claims,
        "report_13": report,
        "strategic_grant_acceptance": {
            "baseline": baseline_evidence,
        },
        "strategic_ownership_acceptance": {
            "contract_sha256": canonical_json_sha256(ownership),
            "production_source_identity": code_fingerprint(),
            "champion": _ownership_champion(champion, scenario_id="champion-5"),
            "report_13": report_completion,
        },
        "relative_policy_reference": {
            "baseline_canonical_sha256": relative_baseline.sha256,
            "policy_canonical_sha256": relative_policy.sha256,
            "frozen_artifact_sha256": relative_baseline.artifact_sha256,
            "frozen_artifact_size_bytes": relative_baseline.artifact_size_bytes,
        },
    }
    payload["evidence_sha256"] = canonical_json_sha256(payload)
    return payload


def run_runtime_cell_artifact(
    scenario: AbsoluteGeneralizationScenario,
    contract: AbsoluteGeneralizationContract,
    *,
    root: str | Path,
    data_dir: str | Path,
    cache_dir: str | Path,
) -> CellArtifact:
    """Run the production replay owner and derive one strict raw cell."""

    replay = run_absolute_generalization_replay(
        scenario,
        root=root,
        data_dir=data_dir,
        cache_dir=cache_dir,
    )
    return derive_runtime_cell_artifact(replay, contract, root=root)


def run_champion_runtime_evidence(
    *,
    root: str | Path,
    data_dir: str | Path,
    cache_dir: str | Path,
    contract: AbsoluteGeneralizationContract,
) -> ChampionRuntimeEvidence:
    """Run raw champion and report evidence through production authorities."""

    repository, data, _cache = _validated_runtime_paths(root=root, data_dir=data_dir, cache_dir=cache_dir)
    payload = _champion_runtime_payload(
        repository=repository,
        data=data,
        contract=contract,
    )
    return ChampionRuntimeEvidence(payload=tuple(payload.items()))


def run_recovery_and_reachability_runtime_evidence(
    *,
    root: str | Path,
    data_dir: str | Path,
    cache_dir: str | Path,
    contract: AbsoluteGeneralizationContract,
) -> RecoveryReachabilityRuntimeEvidence:
    """Run raw recovery/reachability facts through production authorities."""

    repository, data, cache = _validated_runtime_paths(root=root, data_dir=data_dir, cache_dir=cache_dir)
    payload = run_recovery_runtime_payload(
        root=repository,
        data_dir=data,
        cache_dir=cache,
        contract=contract,
    )
    return RecoveryReachabilityRuntimeEvidence(
        failed_grant_recovery=tuple(cast(Mapping[str, object], payload["failed_grant_recovery"]).items()),
        historical_crowning=tuple(cast(Mapping[str, object], payload["historical_crowning"]).items()),
        terminal_scc=tuple(cast(Mapping[str, object], payload["terminal_scc"]).items()),
        repair_bounds=tuple(
            tuple(cast(Mapping[str, object], item).items())
            for item in cast(Sequence[object], payload["repair_bounds"])
        ),
        cross_industry_crowning=tuple(cast(Mapping[str, object], payload["cross_industry_crowning"]).items()),
    )


__all__ = (
    "ChampionRuntimeEvidence",
    "RecoveryReachabilityRuntimeEvidence",
    "derive_runtime_cell_artifact",
    "run_champion_runtime_evidence",
    "run_recovery_and_reachability_runtime_evidence",
    "run_runtime_cell_artifact",
)
