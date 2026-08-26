from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import sys
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any, cast

import pandas as pd

from uquant.contracts.strict_json import canonical_json_sha256
from uquant.models.account import AccountState
from uquant.models.decision import RiskAssessment
from uquant.portfolio import PortfolioAllocator

_PORTFOLIO_REFERENCE_COMMIT = "4b6bedb03fb7c58914d9d5032a2514c67f41f6ba"
_PORTFOLIO_REFERENCE_TREE = "d3824f7c5d89521b8284b5de08cc1e82e3ab7ebd"

_CHECKPOINT_NAMES = (
    "account_before",
    "settled_invalid_lifecycle_rights",
    "risk_reduction_freeze_draft",
    "leader_admission_lifecycle_target_draft",
    "strategic_discovery_lifecycle_target_draft",
    "recovery_ownership_substitution_admission_target_draft",
    "owner_handoff_gross_cap_sparse_reduction_result",
    "final_ordered_targets_reasons_attribution",
    "account_after",
)

_STAGE_METHODS = {
    "risk": (
        "_risk_attribution_mechanism",
        "_risk_retention_score",
        "_risk_retention_vector",
        "_risk_lifecycle_rank",
        "_subset_retention_vector",
        "_risk_reduction_metadata",
        "_turnover_aware_sector_cap",
        "_commit_frozen_exit_state",
        "_frozen_existing_targets",
    ),
    "leader": (
        "_cap_opportunity_gross",
        "_conviction_shares",
        "_conviction_evidence_qualified",
        "_session_clock",
        "_session_distance",
        "_correlations",
        "_admission_utility",
        "_dynamic_k",
        "_rotation_allowed",
        "_update_leader_cycle_arm",
        "_retention_score",
        "_leader_lifecycle_exit_confirmed",
        "_industry_handoff",
        "_leader_targets",
    ),
    "strategic": (
        "_bounded_strategic_restore_risk_open",
        "_retire_strategic_member",
        "_initialize_strategic_cohort",
        "_strategic_cohort_targets",
    ),
    "recovery": (
        "_confirmed_recovery_gross",
        "_recovery_anchor_substitution",
    ),
    "handoff": (
        "_allocate_strategy",
        "_sparse_risk_reduce",
    ),
}


@dataclasses.dataclass(frozen=True, slots=True)
class _TraceSpec:
    name: str
    start: str
    end: str
    symbols: tuple[str, ...]


_OFFICIAL_TRACE_SPECS = (
    _TraceSpec(
        name="early_ai_entry",
        start="2023-01-03",
        end="2023-01-20",
        symbols=("sz300308", "sz300502", "sz300394"),
    ),
    _TraceSpec(
        name="late_2024_rotation",
        start="2024-08-01",
        end="2024-09-02",
        symbols=("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    ),
    _TraceSpec(
        name="recent_shock",
        start="2026-06-30",
        end="2026-07-30",
        symbols=("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    ),
)

_FRAME_DIGESTS: dict[int, tuple[pd.DataFrame, dict[str, object]]] = {}
_EXPECTED_CODE_FINGERPRINT: str | None = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, AccountState):
        return _jsonable(_economic_account_dict(value))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, pd.DataFrame):
        key = id(value)
        cached = _FRAME_DIGESTS.get(key)
        if cached is None or cached[0] is not value:
            encoded = value.to_json(
                orient="split",
                date_format="iso",
                date_unit="ns",
                double_precision=15,
            ).encode()
            digest: dict[str, object] = {
                "kind": "DataFrame",
                "rows": len(value),
                "columns": [str(column) for column in value.columns],
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
            _FRAME_DIGESTS[key] = (value, digest)
            return digest
        return cached[1]
    if isinstance(value, pd.Series):
        encoded = value.to_json(date_format="iso", date_unit="ns", double_precision=15).encode()
        return {
            "kind": "Series",
            "size": len(value),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if hasattr(value, "item") and type(value).__module__.startswith("numpy"):
        return _jsonable(value.item())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_jsonable(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True)) if isinstance(
            value, (set, frozenset)
        ) else items
    if isinstance(value, Path):
        return str(value)
    return value


def _account_payload(account: AccountState) -> dict[str, Any]:
    payload = _jsonable(_economic_account_dict(account))
    assert isinstance(payload, dict)
    return payload


def _economic_account_dict(account: AccountState) -> dict[str, Any]:
    global _EXPECTED_CODE_FINGERPRINT
    payload = account.to_dict()
    # A package relocation necessarily changes the source-surface fingerprint.
    # It is independently fail-closed by the registry/provenance gates and is
    # not an economic AccountState mutation.
    observed = payload.pop("code_hash", "")
    if observed:
        if _EXPECTED_CODE_FINGERPRINT is None:
            from uquant.engine import code_fingerprint

            _EXPECTED_CODE_FINGERPRINT = code_fingerprint()
        if observed != _EXPECTED_CODE_FINGERPRINT:
            raise AssertionError("account code_hash does not match the replay source surface")
        payload["_trace_code_hash_status"] = "matches_current_source"
    else:
        payload["_trace_code_hash_status"] = "unset"
    return payload


def _lifecycle_rights(account_payload: dict[str, Any]) -> dict[str, Any]:
    keywords = (
        "active",
        "anchor",
        "cohort",
        "epoch",
        "lifecycle",
        "order",
        "protected",
        "tenure",
    )
    return {
        key: value
        for key, value in account_payload.items()
        if key == "positions" or any(keyword in key for keyword in keywords)
    }


def _method_owner(name: str) -> type[object]:
    return next(owner for owner in PortfolioAllocator.__mro__ if name in owner.__dict__)


def _event_payload(
    name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    result: object,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "method": name,
        "args": _jsonable(args),
        "kwargs": _jsonable(kwargs),
        "account_before": before,
        "result": _jsonable(result),
        "account_after": after,
    }


def portfolio_trace_replay(
    *,
    name: str,
    start: str,
    end: str,
    symbols: Sequence[str],
    root: Path,
) -> dict[str, Any]:
    import uquant.engine as engine_module

    global _EXPECTED_CODE_FINGERPRINT
    _FRAME_DIGESTS.clear()
    _EXPECTED_CODE_FINGERPRINT = None
    active: dict[str, Any] | None = None
    originals: list[tuple[type[object], str, object]] = []
    records: list[dict[str, Any]] = []

    for stage, names in _STAGE_METHODS.items():
        for method_name in names:
            owner = _method_owner(method_name)
            descriptor = owner.__dict__[method_name]
            originals.append((owner, method_name, descriptor))
            if isinstance(descriptor, staticmethod):
                original = descriptor.__func__

                def static_wrapper(
                    *args: object,
                    __name: str = method_name,
                    __original: Any = original,
                    __stage: str = stage,
                    **kwargs: object,
                ) -> object:
                    account = next(
                        (
                            value
                            for value in (*args, *kwargs.values())
                            if isinstance(value, AccountState)
                        ),
                        None,
                    )
                    before = _account_payload(account) if account is not None else None
                    result = __original(*args, **kwargs)
                    after = _account_payload(account) if account is not None else None
                    if active is not None:
                        active[__stage].append(
                            _event_payload(__name, args, kwargs, result, before, after)
                        )
                    return result

                setattr(owner, method_name, staticmethod(static_wrapper))
            elif isinstance(descriptor, classmethod):
                raise AssertionError(f"unsupported classmethod trace seam: {method_name}")
            else:
                original = descriptor

                def instance_wrapper(
                    self: object,
                    *args: object,
                    __name: str = method_name,
                    __original: Any = original,
                    __stage: str = stage,
                    **kwargs: object,
                ) -> object:
                    account = next(
                        (
                            value
                            for value in (*args, *kwargs.values())
                            if isinstance(value, AccountState)
                        ),
                        None,
                    )
                    before = _account_payload(account) if account is not None else None
                    result = __original(self, *args, **kwargs)
                    after = _account_payload(account) if account is not None else None
                    if active is not None:
                        active[__stage].append(
                            _event_payload(__name, args, kwargs, result, before, after)
                        )
                    return result

                setattr(owner, method_name, instance_wrapper)

    allocate_owner = _method_owner("allocate")
    allocate_descriptor = allocate_owner.__dict__["allocate"]
    originals.append((allocate_owner, "allocate", allocate_descriptor))
    original_allocate = cast(Any, allocate_descriptor)

    def traced_allocate(self: object, *args: object, **kwargs: object) -> object:
        nonlocal active
        if active is not None:
            raise AssertionError("portfolio allocation trace cannot nest allocate calls")
        account = kwargs["account"]
        if not isinstance(account, AccountState):
            raise AssertionError("portfolio trace requires the real AccountState")
        before = _account_payload(account)
        state: dict[str, list[dict[str, Any]]] = {stage: [] for stage in _STAGE_METHODS}
        active = state
        try:
            result = original_allocate(self, *args, **kwargs)
        finally:
            active = None
        after = _account_payload(account)
        risk = kwargs["risk"]
        if not isinstance(risk, RiskAssessment):
            raise AssertionError("portfolio trace requires the real RiskAssessment")
        input_payload = {
            "date": _jsonable(kwargs["date"]),
            "opportunity": _jsonable(kwargs["opportunity"]),
            "risk": _jsonable(risk),
            "user_panel": _jsonable(kwargs["user_panel"]),
            "leaders": _jsonable(kwargs["leaders"]),
            "prices": _jsonable(kwargs["prices"]),
        }
        checkpoints: list[dict[str, Any]] = [
            {"name": _CHECKPOINT_NAMES[0], "payload": before},
            {
                "name": _CHECKPOINT_NAMES[1],
                "payload": _lifecycle_rights(before),
            },
            {"name": _CHECKPOINT_NAMES[2], "payload": state["risk"]},
            {"name": _CHECKPOINT_NAMES[3], "payload": state["leader"]},
            {"name": _CHECKPOINT_NAMES[4], "payload": state["strategic"]},
            {"name": _CHECKPOINT_NAMES[5], "payload": state["recovery"]},
            {
                "name": _CHECKPOINT_NAMES[6],
                "payload": {
                    "allocation_input": input_payload,
                    "events": state["handoff"],
                    "risk_target_gross_cap": risk.target_gross_cap,
                },
            },
            {"name": _CHECKPOINT_NAMES[7], "payload": _jsonable(result)},
            {"name": _CHECKPOINT_NAMES[8], "payload": after},
        ]
        assert tuple(checkpoint["name"] for checkpoint in checkpoints) == _CHECKPOINT_NAMES
        date = kwargs["date"]
        if not isinstance(date, pd.Timestamp):
            raise AssertionError("portfolio trace requires a Timestamp date")
        records.append(
            {
                "date": str(date.date()),
                "checkpoint_sha256": [
                    {
                        "name": checkpoint["name"],
                        "sha256": canonical_json_sha256(checkpoint["payload"]),
                    }
                    for checkpoint in checkpoints
                ],
                "ordered_checkpoint_sha256": canonical_json_sha256(checkpoints),
            }
        )
        return result

    type.__setattr__(allocate_owner, "allocate", traced_allocate)
    try:
        engine_module.ProductionEngine(root / "data" / "frozen").backtest(
            symbols=symbols,
            start=start,
            end=end,
        )
    finally:
        for owner, method_name, descriptor in reversed(originals):
            setattr(owner, method_name, descriptor)

    return {
        "name": name,
        "requested_start": start,
        "requested_end": end,
        "symbols": list(symbols),
        "record_count": len(records),
        "records": records,
        "records_sha256": canonical_json_sha256(records),
    }


def official_portfolio_trace(root: Path) -> dict[str, Any]:
    scenarios = [
        portfolio_trace_replay(
            name=spec.name,
            start=spec.start,
            end=spec.end,
            symbols=spec.symbols,
            root=root,
        )
        for spec in _OFFICIAL_TRACE_SPECS
    ]
    payload: dict[str, Any] = {
        "baseline_commit": _PORTFOLIO_REFERENCE_COMMIT,
        "baseline_tree": _PORTFOLIO_REFERENCE_TREE,
        "checkpoint_names": list(_CHECKPOINT_NAMES),
        "checkpoint_2_boundary": (
            "AccountState observed at allocate entry after ProductionEngine.execute_open has "
            "settled the session's pending orders; complete position, pending-order and durable "
            "lifecycle-right fields are projected before any allocation-owner mutation."
        ),
        "contract": "uquant-task8-daily-allocation-trace-v2",
        "excluded_structural_account_fields": ["code_hash"],
        "projection": (
            "nine ordered complete allocation/account/lifecycle owner checkpoint payload hashes"
        ),
        "scenarios": scenarios,
        "schema_version": 1,
    }
    payload["payload_sha256"] = canonical_json_sha256(payload)
    return payload


def _assert_snapshot_modules(root: Path) -> None:
    expected = root.resolve()
    for name, module in sys.modules.items():
        if name != "uquant" and not name.startswith("uquant."):
            continue
        source = getattr(module, "__file__", None)
        if source is not None and not Path(source).resolve().is_relative_to(expected):
            raise RuntimeError(f"trace imported uquant outside immutable snapshot: {name}")


def _main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("immutable trace runner requires one snapshot root")
    root = Path(sys.argv[1]).resolve()
    _assert_snapshot_modules(root)
    payload = official_portfolio_trace(root)
    _assert_snapshot_modules(root)
    print(json.dumps(payload, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ("official_portfolio_trace", "portfolio_trace_replay")
