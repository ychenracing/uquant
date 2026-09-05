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

from ._reviewed_owner_transport import RETIRED_LEADER_METHODS

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
_FRAME_DIAGNOSTIC_SOURCES: dict[str, pd.DataFrame] = {}
_EXPECTED_CODE_FINGERPRINT: str | None = None
_ECONOMIC_GRANT_FILLS: dict[tuple[str, str, str], tuple[int, int]] = {}


def _trace_dataframe_projection(value: pd.DataFrame) -> pd.DataFrame:
    """Canonicalize the host-sensitive rolling-correlation tail for trace hashing."""

    if "trend_r2_120" not in value.columns:
        return value
    return value.assign(trend_r2_120=value["trend_r2_120"].round(10))


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
            encoded = _trace_dataframe_projection(value).to_json(
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
            _FRAME_DIAGNOSTIC_SOURCES[str(digest["sha256"])] = value
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


def _trace_transport_value(value: object) -> object:
    serialized = _jsonable(value)
    if isinstance(serialized, dict):
        return {
            key: _trace_transport_value(item)
            for key, item in serialized.items()
            if key not in {"epoch_id", "grant_id", "reference_coverage"}
        }
    if isinstance(serialized, list):
        return [_trace_transport_value(item) for item in serialized]
    return serialized


def _diagnostic_digest_tree(
    value: object,
    *,
    depth: int,
    field_name: str = "",
) -> dict[str, object]:
    """Return compact child digests for a failed byte-exact trace checkpoint."""

    digest = canonical_json_sha256(value)
    result: dict[str, object] = {"sha256": digest}
    if isinstance(value, dict) and value.get("kind") == "DataFrame":
        source = _FRAME_DIAGNOSTIC_SOURCES.get(str(value.get("sha256", "")))
        if source is not None:
            result["dataframe"] = {
                str(column): {
                    f"precision_{precision}": hashlib.sha256(
                        source[column]
                        .to_json(
                            date_format="iso",
                            date_unit="ns",
                            double_precision=precision,
                        )
                        .encode()
                    ).hexdigest()
                    for precision in (10, 12, 14, 15)
                }
                for column in source.columns
            }
    if depth <= 0 or field_name in {"account", "account_before", "account_after"}:
        return result
    if isinstance(value, dict):
        result["children"] = {
            str(key): _diagnostic_digest_tree(
                item,
                depth=depth - 1,
                field_name=str(key),
            )
            for key, item in value.items()
        }
    elif isinstance(value, list):
        result["children"] = [
            _diagnostic_digest_tree(item, depth=depth - 1)
            for item in value
        ]
    elif isinstance(value, (str, int, float, bool)) or value is None:
        result["value"] = value
    return result


def _account_payload(account: AccountState) -> dict[str, Any]:
    payload = _jsonable(_economic_account_dict(account))
    assert isinstance(payload, dict)
    return payload


def _economic_account_dict(account: AccountState) -> dict[str, Any]:
    global _EXPECTED_CODE_FINGERPRINT
    payload = account.to_dict()
    payload["schema_version"] = 5
    for key in (
        "active_strategic_epoch_id",
        "flat_book_capital_repair",
        "protected_weight_epoch_ids",
        "recovery_owner_epoch_id",
        "strategic_cash_rearm",
        "strategic_epochs",
        "strategic_qualification_universe_identity",
        "strategic_restore_epoch_ids",
        "strategic_risk_universe_identity",
        "strategic_successor_qualification",
        "strategic_tradable_universe_identity",
    ):
        payload.pop(key, None)

    groups: list[list[dict[str, Any]]] = []
    indexes: dict[tuple[str, ...], int] = {}
    for order in payload["order_ledger"]:
        group_key = (
            ("STRATEGIC_GRANT_EVENT", str(order["grant_id"]), str(order["event_id"]))
            if order.get("grant_id") and order.get("event_id")
            else ("PHYSICAL_ORDER", str(order["order_id"]))
        )
        index = indexes.setdefault(group_key, len(groups))
        if index == len(groups):
            groups.append([])
        groups[index].append(order)
    collapsed: list[dict[str, Any]] = []
    event_order_ids: dict[str, str] = {}
    projected_remainders: dict[tuple[str, str, str], int] = {}
    for group in groups:
        first = dict(group[0])
        last = group[-1]
        filled_shares = sum(int(order["filled_shares"]) for order in group)
        grant_key = (
            (str(first["grant_id"]), str(first["event_id"]), str(first["symbol"]))
            if first.get("grant_id") and first.get("event_id")
            else None
        )
        economic_fill = (
            _ECONOMIC_GRANT_FILLS.get(grant_key) if grant_key is not None else None
        )
        terminal_target_fill = (
            last["status"] == "CANCELLED"
            and last["cancel_reason"] == "target already satisfied"
            and last["last_event"] == "FILL"
            and filled_shares > 0
            and int(last["remaining_shares"]) > 0
        )
        if economic_fill is None:
            remaining_shares = (
                0 if terminal_target_fill else int(last["remaining_shares"])
            )
            requested_shares = filled_shares + remaining_shares
        else:
            economic_target_requested, latest_fill_shares = economic_fill
            remaining_shares = max(0, economic_target_requested - latest_fill_shares)
            requested_shares = (
                filled_shares - latest_fill_shares + economic_target_requested
            )
            assert grant_key is not None
            projected_remainders[grant_key] = remaining_shares
        attempts = max(int(order["attempts"]) for order in group)
        first.update(
            status="FILLED" if terminal_target_fill else last["status"],
            requested_shares=requested_shares,
            filled_shares=filled_shares,
            remaining_shares=remaining_shares,
            attempts=attempts - 1 if terminal_target_fill else attempts,
            last_update_date=last["last_update_date"],
            last_event=last["last_event"],
            replaced_by=last["replaced_by"],
            cancel_reason="" if terminal_target_fill else last["cancel_reason"],
        )
        if first["last_event"] == "PARTIAL_REMAINDER_RELEASED":
            first.update(
                status="PARTIALLY_FILLED",
                last_event="FILL",
                cancel_reason="",
            )
        collapsed.append(first)
        for order in group:
            event_id = str(order.get("event_id", ""))
            if event_id:
                event_order_ids[event_id] = str(first["order_id"])
    payload["order_ledger"] = collapsed
    payload["next_order_sequence"] = len(collapsed) + 1
    for collection in (payload["fills"], payload["pending_orders"]):
        for item in collection:
            event_id = str(item.get("event_id", ""))
            if event_id in event_order_ids:
                item["order_id"] = event_order_ids[event_id]
            grant_key = (
                (str(item["grant_id"]), event_id, str(item["symbol"]))
                if item.get("grant_id") and event_id
                else None
            )
            if collection is payload["pending_orders"] and grant_key in projected_remainders:
                item["remaining_shares"] = projected_remainders[grant_key]
    if payload["strategic_epoch"] == 0 and payload["strategic_cohort_targets"]:
        # The formal epoch now waits for a matching Fill.  The historical
        # counter advanced when the target cohort was opened, so retain that
        # administrative timing only inside the frozen economic trace.
        payload["strategic_epoch"] = 1

    def strip_strategic_identity(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: strip_strategic_identity(item)
                for key, item in value.items()
                if key
                not in {
                    "account_identity",
                    "epoch_id",
                    "grant_id",
                    "remainder_release_session",
                    "remainder_release_shares",
                    "strategic_grant",
                    "strategic_qualification",
                }
            }
        if isinstance(value, list):
            return [strip_strategic_identity(item) for item in value]
        return value

    projected = strip_strategic_identity(payload)
    if not isinstance(projected, dict):
        raise AssertionError("economic account projection must remain a mapping")
    payload = projected
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
        # Grant identity now publishes the current source before the first
        # allocation.  The frozen economic trace published it only after that
        # decision, so project the first-session administrative timing back to
        # its historical state while still validating the observed identity.
        payload["_trace_code_hash_status"] = (
            "matches_current_source" if payload["last_successful_run"] else "unset"
        )
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
    legacy_kwargs = dict(kwargs)
    if name in {
        "_allocate_strategy",
        "_initialize_strategic_cohort",
        "_strategic_cohort_targets",
    }:
        for key in (
            "qualification_leaders",
            "qualification_panel",
            "strategic_universe",
        ):
            legacy_kwargs.pop(key, None)

    return {
        "method": name,
        "args": _trace_transport_value(args),
        "kwargs": _trace_transport_value(legacy_kwargs),
        "account_before": before,
        "result": _trace_transport_value(result),
        "account_after": after,
    }


def _legacy_economic_event_visible(name: str, kwargs: dict[str, object]) -> bool:
    """Exclude read-only observation added after the frozen target trace."""

    if name not in {"_initialize_strategic_cohort", "_strategic_cohort_targets"}:
        return True
    account = kwargs.get("account")
    risk = kwargs.get("risk")
    if not isinstance(account, AccountState) or not isinstance(risk, RiskAssessment):
        raise AssertionError("strategic target trace requires account and risk")
    strategic_live = account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    freeze_active = bool(
        risk.freeze_new_risk
        or risk.evidence.get("freeze_new_risk", False)
        or risk.state.value in {"RISK_OFF", "CRISIS"}
    )
    observation_open = bool(
        account.opportunity in {"CHOPPY", "WEAK", "TREND", "STRONG_TREND"}
        and risk.state.value == "NORMAL"
        and not freeze_active
    )
    return strategic_live or observation_open


def portfolio_trace_replay(
    *,
    name: str,
    start: str,
    end: str,
    symbols: Sequence[str],
    root: Path,
    expected_records: Sequence[dict[str, Any]] = (),
    diagnostics: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    import uquant.engine as engine_module
    import uquant.execution.open_execution as open_execution_module

    global _EXPECTED_CODE_FINGERPRINT
    _FRAME_DIGESTS.clear()
    _FRAME_DIAGNOSTIC_SOURCES.clear()
    _ECONOMIC_GRANT_FILLS.clear()
    _EXPECTED_CODE_FINGERPRINT = None
    active: dict[str, Any] | None = None
    diagnostic_recorded = False
    expected_by_date = {str(record["date"]): record for record in expected_records}
    originals: list[tuple[type[object], str, object]] = []
    records: list[dict[str, Any]] = []

    for stage, names in _STAGE_METHODS.items():
        for method_name in names:
            if method_name in RETIRED_LEADER_METHODS:
                assert not hasattr(PortfolioAllocator, method_name)
                continue
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
                    if active is not None and _legacy_economic_event_visible(
                        __name, kwargs
                    ):
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
                    if active is not None and _legacy_economic_event_visible(
                        __name, kwargs
                    ):
                        active[__stage].append(
                            _event_payload(__name, args, kwargs, result, before, after)
                        )
                    return result

                setattr(owner, method_name, instance_wrapper)

    allocate_owner = _method_owner("allocate")
    allocate_descriptor = allocate_owner.__dict__["allocate"]
    originals.append((allocate_owner, "allocate", allocate_descriptor))
    original_allocate = cast(Any, allocate_descriptor)
    execution_hooks = cast(Any, open_execution_module)
    original_record_open_fill = execution_hooks._record_open_fill

    def traced_record_open_fill(*args: object, **kwargs: object) -> object:
        request = cast(Any, kwargs["request"])
        order = request.order
        if order.grant_id and order.event_id:
            _ECONOMIC_GRANT_FILLS[
                (str(order.grant_id), str(order.event_id), str(order.symbol))
            ] = (int(request.economic_target_requested), int(request.shares))
        return original_record_open_fill(*args, **kwargs)

    def traced_allocate(self: object, *args: object, **kwargs: object) -> object:
        nonlocal active, diagnostic_recorded
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
            "risk": _trace_transport_value(risk),
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
            {"name": _CHECKPOINT_NAMES[7], "payload": _trace_transport_value(result)},
            {"name": _CHECKPOINT_NAMES[8], "payload": after},
        ]
        assert tuple(checkpoint["name"] for checkpoint in checkpoints) == _CHECKPOINT_NAMES
        date = kwargs["date"]
        if not isinstance(date, pd.Timestamp):
            raise AssertionError("portfolio trace requires a Timestamp date")
        session = str(date.date())
        checkpoint_sha256 = [
            {
                "name": checkpoint["name"],
                "sha256": canonical_json_sha256(checkpoint["payload"]),
            }
            for checkpoint in checkpoints
        ]
        if diagnostics is not None and not diagnostic_recorded and session in expected_by_date:
            expected = {
                str(checkpoint["name"]): str(checkpoint["sha256"])
                for checkpoint in expected_by_date[session]["checkpoint_sha256"]
            }
            mismatches = [
                {
                    "name": checkpoint["name"],
                    "expected_sha256": expected[str(checkpoint["name"])],
                    "observed": _diagnostic_digest_tree(
                        checkpoint["payload"],
                        depth=4,
                    ),
                }
                for checkpoint, observed in zip(checkpoints, checkpoint_sha256, strict=True)
                if expected[str(checkpoint["name"])] != observed["sha256"]
            ]
            if mismatches:
                diagnostics.append({"scenario": name, "date": session, "mismatches": mismatches})
                diagnostic_recorded = True
        records.append(
            {
                "date": session,
                "checkpoint_sha256": checkpoint_sha256,
                "ordered_checkpoint_sha256": canonical_json_sha256(checkpoints),
            }
        )
        return result

    execution_hooks._record_open_fill = traced_record_open_fill
    type.__setattr__(allocate_owner, "allocate", traced_allocate)
    try:
        engine_module.ProductionEngine(root / "data" / "frozen").backtest(
            symbols=symbols,
            start=start,
            end=end,
        )
    finally:
        execution_hooks._record_open_fill = original_record_open_fill
        _ECONOMIC_GRANT_FILLS.clear()
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
