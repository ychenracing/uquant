"""Fail-closed validation for a frozen full-cycle benchmark matrix.

The reference is data-only. This module never fetches source code or writes
reference values; it validates reviewed evidence, replays the production engine
under the same execution contract, and evaluates every pool/window cell.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from ..engine import ProductionEngine
from .ai_era import AI_ERA_WINDOWS, require_ai_era_interval

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_METRIC_FIELDS = {"final_wealth", "max_drawdown", "account_orders"}
_POLICY_FIELDS = {
    "wealth_floor_ratio",
    "drawdown_tolerance",
    "absolute_max_drawdown",
    "order_tolerance",
    "order_ceiling_ratio",
}


@dataclass(frozen=True, slots=True)
class MatrixWindow:
    """A scored market interval whose start is the economic-account boundary."""

    name: str
    start: str
    end: str

    def __post_init__(self) -> None:
        try:
            require_ai_era_interval(self.start, self.end)
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
        except ValueError as exc:
            raise ValueError(f"invalid competitor window: {self.name}") from exc
        if not self.name or start >= end:
            raise ValueError(f"invalid competitor window: {self.name}")

    def to_payload(self) -> dict[str, str]:
        """Return the canonical serialized window definition."""

        return {"start": self.start, "end": self.end}


CANONICAL_WINDOWS: Final[tuple[MatrixWindow, ...]] = tuple(
    MatrixWindow(name, start, end) for name, (start, end) in AI_ERA_WINDOWS.items()
)
REQUIRED_WINDOWS: Final[tuple[str, ...]] = tuple(item.name for item in CANONICAL_WINDOWS)
REQUIRED_COMPETITORS: Final[tuple[str, ...]] = ("aquant", "qwenquant", "trade")
REQUIRED_POOLS: Final[tuple[str, ...]] = ("a", "b", "c", "d", "e")


@dataclass(frozen=True, slots=True)
class ExecutionContract:
    """Frozen assumptions shared by every matrix replay."""

    initial_cash: float
    calendar: str
    signal: str
    execution: str
    mark: str
    intraday_exit: bool
    prelisting: str
    commission_rate: float
    minimum_commission: float
    sell_stamp_duty: float
    transfer_fee: float
    one_way_slippage: float
    max_volume_participation: float
    t_plus_one: bool
    board_lot_shares: int
    star_initial_minimum_shares: int
    limit_locked_orders_pending: bool
    suspended_orders_pending: bool
    partial_fills: bool
    account_order_measure: str

    def to_payload(self) -> dict[str, float | int | str | bool]:
        """Return the complete execution contract as primitive values."""

        return {
            "initial_cash": self.initial_cash,
            "calendar": self.calendar,
            "signal": self.signal,
            "execution": self.execution,
            "mark": self.mark,
            "intraday_exit": self.intraday_exit,
            "prelisting": self.prelisting,
            "commission_rate": self.commission_rate,
            "minimum_commission": self.minimum_commission,
            "sell_stamp_duty": self.sell_stamp_duty,
            "transfer_fee": self.transfer_fee,
            "one_way_slippage": self.one_way_slippage,
            "max_volume_participation": self.max_volume_participation,
            "t_plus_one": self.t_plus_one,
            "board_lot_shares": self.board_lot_shares,
            "star_initial_minimum_shares": self.star_initial_minimum_shares,
            "limit_locked_orders_pending": self.limit_locked_orders_pending,
            "suspended_orders_pending": self.suspended_orders_pending,
            "partial_fills": self.partial_fills,
            "account_order_measure": self.account_order_measure,
        }


CANONICAL_EXECUTION_CONTRACT: Final[ExecutionContract] = ExecutionContract(
    initial_cash=2_000_000.0,
    calendar="sh000300_intersection_sh000682",
    signal="close_t",
    execution="next_tradable_open",
    mark="daily_close",
    intraday_exit=False,
    prelisting="invisible until first observable row",
    commission_rate=0.00025,
    minimum_commission=5.0,
    sell_stamp_duty=0.0005,
    transfer_fee=0.00001,
    one_way_slippage=0.001,
    max_volume_participation=0.005,
    t_plus_one=True,
    board_lot_shares=100,
    star_initial_minimum_shares=200,
    limit_locked_orders_pending=True,
    suspended_orders_pending=True,
    partial_fills=True,
    account_order_measure="distinct orders with positive filled shares",
)


@dataclass(frozen=True, slots=True)
class RepositoryProvenance:
    """Immutable source identity for one reviewed benchmark implementation."""

    repository: str
    commit: str
    adapter_source_sha256: str

    def to_payload(self) -> dict[str, str]:
        """Return repository provenance in canonical field order."""

        return {
            "repository": self.repository,
            "commit": self.commit,
            "adapter_source_sha256": self.adapter_source_sha256,
        }


LOCKED_COMPETITOR_PROVENANCE: Final[tuple[tuple[str, RepositoryProvenance], ...]] = (
    (
        "aquant",
        RepositoryProvenance(
            repository="ychenracing/aquant",
            commit="3c38fbbf679a0fb1b4ee8f3d47b6931d3eb8fdbd",
            adapter_source_sha256=("0fdc39c40239e51b5c91024507bef1bed222cd83575e4d9f870b8ada2f73a50a"),
        ),
    ),
    (
        "qwenquant",
        RepositoryProvenance(
            repository="ychenracing/qwenquant",
            commit="0b3681e10b75425ad8600e75835677a6a125ed13",
            adapter_source_sha256=("66fc531989e294990d40dae5f0c0ff867fe4e144ab2bae81863b42e7113c46c0"),
        ),
    ),
    (
        "trade",
        RepositoryProvenance(
            repository="ychenracing/trade",
            commit="cee1620f40af3af8f839e15db188a9e388a78dd0",
            adapter_source_sha256=("03e33e1396ca31d61e724bcd9cf58971ae656134740eb8929313167aa8ed8597"),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class DataProvenance:
    """Hashes that bind the matrix to one exact market-data snapshot."""

    snapshot_id: str
    adjustment: str
    manifest_sha256: str
    checksums_sha256: str
    dataset_sha256: str

    def to_payload(self) -> dict[str, str]:
        """Return the complete data provenance payload."""

        return {
            "snapshot_id": self.snapshot_id,
            "adjustment": self.adjustment,
            "manifest_sha256": self.manifest_sha256,
            "checksums_sha256": self.checksums_sha256,
            "dataset_sha256": self.dataset_sha256,
        }


@dataclass(frozen=True, slots=True)
class GatePolicy:
    """Allowed wealth, drawdown, and order-count tolerances."""

    wealth_floor_ratio: float
    drawdown_tolerance: float
    absolute_max_drawdown: float
    order_tolerance: int
    order_ceiling_ratio: float


@dataclass(frozen=True, slots=True)
class CompetitorMetrics:
    """Validated wealth, drawdown, and account-order metrics for one cell."""

    final_wealth: float
    max_drawdown: float
    account_orders: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.final_wealth) or self.final_wealth <= 0:
            raise ValueError("competitor final_wealth must be finite and positive")
        if not math.isfinite(self.max_drawdown) or not 0 <= self.max_drawdown <= 1:
            raise ValueError("competitor max_drawdown must be in [0, 1]")
        if (
            isinstance(self.account_orders, bool)
            or not isinstance(self.account_orders, int)
            or self.account_orders < 0
        ):
            raise ValueError("competitor account_orders must be a nonnegative integer")

    def to_payload(self) -> dict[str, float | int]:
        """Return metrics as JSON-compatible values."""

        return {
            "final_wealth": self.final_wealth,
            "max_drawdown": self.max_drawdown,
            "account_orders": self.account_orders,
        }


@dataclass(frozen=True, slots=True)
class CompetitorMatrixReference:
    """Fully parsed benchmark matrix and its immutable provenance."""

    source_sha256: str
    frozen_at_utc: str
    policy: GatePolicy
    execution_contract: ExecutionContract
    data_provenance: DataProvenance
    pools: tuple[tuple[str, tuple[str, ...]], ...]
    repositories: tuple[tuple[str, RepositoryProvenance], ...]
    results: tuple[tuple[str, CompetitorMetrics], ...]

    def pool_map(self) -> dict[str, tuple[str, ...]]:
        """Return pool names mapped to their ordered symbol tuples."""

        return dict(self.pools)

    def repository_map(self) -> dict[str, RepositoryProvenance]:
        """Return benchmark names mapped to reviewed source identities."""

        return dict(self.repositories)

    def result_map(self) -> dict[str, CompetitorMetrics]:
        """Return matrix cell keys mapped to validated metrics."""

        return dict(self.results)


type Runner = Callable[[str, tuple[str, ...], MatrixWindow], Mapping[str, Any]]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise RuntimeError(f"competitor matrix contains duplicate key: {key}")
        output[key] = value
    return output


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"competitor matrix {label} must be an object")
    return value


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"competitor matrix {label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError(f"competitor matrix {label} must be finite")
    return result


def _parse_execution_contract(value: Any) -> ExecutionContract:
    payload = _object(value, label="execution_contract")
    expected = CANONICAL_EXECUTION_CONTRACT.to_payload()
    if set(payload) != set(expected):
        raise RuntimeError("competitor execution-contract fields are malformed")
    mismatches: list[str] = []
    for name, expected_value in expected.items():
        observed_value = payload[name]
        if isinstance(expected_value, float):
            valid = (
                not isinstance(observed_value, bool)
                and isinstance(observed_value, (int, float))
                and math.isfinite(float(observed_value))
                and float(observed_value) == expected_value
            )
        else:
            valid = type(observed_value) is type(expected_value) and observed_value == expected_value
        if not valid:
            mismatches.append(name)
    if mismatches:
        raise RuntimeError(
            "competitor execution-contract mismatch: expected close_t / "
            "next_tradable_open / no intraday exit; fields=" + str(mismatches)
        )
    return CANONICAL_EXECUTION_CONTRACT


def _parse_repository(name: str, value: Any) -> RepositoryProvenance:
    payload = _object(value, label=f"repositories.{name}")
    fields = {"repository", "commit", "adapter_source_sha256"}
    if set(payload) != fields or any(not isinstance(payload[field], str) for field in fields):
        raise RuntimeError(f"competitor repository provenance is malformed: {name}")
    observed = RepositoryProvenance(
        repository=payload["repository"],
        commit=payload["commit"],
        adapter_source_sha256=payload["adapter_source_sha256"],
    )
    if not observed.repository or _COMMIT.fullmatch(observed.commit) is None:
        raise RuntimeError(f"competitor repository commit provenance is malformed: {name}")
    if _SHA256.fullmatch(observed.adapter_source_sha256) is None:
        raise RuntimeError(f"competitor adapter provenance is malformed: {name}")
    return observed


def _parse_data_provenance(value: Any) -> DataProvenance:
    payload = _object(value, label="data_provenance")
    fields = {
        "snapshot_id",
        "adjustment",
        "manifest_sha256",
        "checksums_sha256",
        "dataset_sha256",
    }
    if set(payload) != fields or any(not isinstance(payload[field], str) for field in fields):
        raise RuntimeError("competitor data provenance is malformed")
    if not payload["snapshot_id"] or not payload["adjustment"]:
        raise RuntimeError("competitor data provenance is malformed")
    for field in ("manifest_sha256", "checksums_sha256", "dataset_sha256"):
        if _SHA256.fullmatch(payload[field]) is None:
            raise RuntimeError(f"competitor data provenance hash is malformed: {field}")
    return DataProvenance(
        snapshot_id=payload["snapshot_id"],
        adjustment=payload["adjustment"],
        manifest_sha256=payload["manifest_sha256"],
        checksums_sha256=payload["checksums_sha256"],
        dataset_sha256=payload["dataset_sha256"],
    )


def data_provenance_from_directory(data_dir: str | Path) -> DataProvenance:
    """Fingerprint manifest bytes, checksum bytes, and the actual CSV inventory."""
    root = Path(data_dir)
    manifest_path = root / "DATA_MANIFEST.json"
    checksums_path = root / "SHA256SUMS"
    try:
        manifest_bytes = manifest_path.read_bytes()
        checksums_bytes = checksums_path.read_bytes()
        manifest = json.loads(
            manifest_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        csv_paths = sorted(root.glob("*.csv"), key=lambda item: item.name)
        if not csv_paths:
            raise RuntimeError("competitor data provenance has no CSV inventory")
        inventory = hashlib.sha256()
        for path in csv_paths:
            raw = path.read_bytes()
            inventory.update(len(path.name).to_bytes(4, "big"))
            inventory.update(path.name.encode("utf-8"))
            inventory.update(hashlib.sha256(raw).digest())
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"competitor data provenance is unreadable: {root}") from exc
    payload = _object(manifest, label="data manifest")
    snapshot_id = payload.get("snapshot_id")
    adjustment = payload.get("adjustment")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise RuntimeError("competitor data manifest has no snapshot_id")
    if not isinstance(adjustment, str) or not adjustment:
        raise RuntimeError("competitor data manifest has no adjustment policy")
    return DataProvenance(
        snapshot_id=snapshot_id,
        adjustment=adjustment,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        checksums_sha256=hashlib.sha256(checksums_bytes).hexdigest(),
        dataset_sha256=inventory.hexdigest(),
    )


def _parse_policy(value: Any) -> GatePolicy:
    """Parse gate tolerances and reject missing, extra, or unsafe values."""

    payload = _object(value, label="policy")
    if set(payload) != _POLICY_FIELDS:
        raise RuntimeError("competitor gate policy fields are malformed")
    wealth_ratio = _finite(payload["wealth_floor_ratio"], label="policy.wealth_floor_ratio")
    drawdown_tolerance = _finite(payload["drawdown_tolerance"], label="policy.drawdown_tolerance")
    absolute_drawdown = _finite(payload["absolute_max_drawdown"], label="policy.absolute_max_drawdown")
    order_ratio = _finite(payload["order_ceiling_ratio"], label="policy.order_ceiling_ratio")
    raw_order_tolerance = payload["order_tolerance"]
    if isinstance(raw_order_tolerance, bool) or not isinstance(raw_order_tolerance, int):
        raise RuntimeError("competitor policy.order_tolerance must be an integer")
    if not 0 < wealth_ratio <= 1:
        raise RuntimeError("competitor wealth_floor_ratio must be in (0, 1]")
    if drawdown_tolerance < 0 or not 0 <= absolute_drawdown <= 1:
        raise RuntimeError("competitor drawdown policy is invalid")
    if raw_order_tolerance < 0 or order_ratio < 1:
        raise RuntimeError("competitor order policy is invalid")
    return GatePolicy(
        wealth_floor_ratio=wealth_ratio,
        drawdown_tolerance=drawdown_tolerance,
        absolute_max_drawdown=absolute_drawdown,
        order_tolerance=raw_order_tolerance,
        order_ceiling_ratio=order_ratio,
    )


def _parse_metrics(value: Any, *, label: str) -> CompetitorMetrics:
    payload = _object(value, label=label)
    if set(payload) != _METRIC_FIELDS:
        raise RuntimeError(f"competitor result metrics are malformed: {label}")
    wealth = _finite(payload["final_wealth"], label=f"{label}.final_wealth")
    drawdown = _finite(payload["max_drawdown"], label=f"{label}.max_drawdown")
    orders = payload["account_orders"]
    if isinstance(orders, bool) or not isinstance(orders, int):
        raise RuntimeError(f"competitor result order count is malformed: {label}")
    try:
        return CompetitorMetrics(wealth, drawdown, orders)
    except ValueError as exc:
        raise RuntimeError(f"competitor result is invalid: {label}") from exc


def _parse_pools(value: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Parse the complete required pool set with stable symbol ordering."""

    payload = _object(value, label="pools")
    missing = sorted(set(REQUIRED_POOLS) - set(payload))
    unexpected = sorted(set(payload) - set(REQUIRED_POOLS))
    if missing:
        raise RuntimeError(f"competitor matrix is missing required pools: {missing}")
    if unexpected:
        raise RuntimeError(f"competitor matrix has unexpected pools: {unexpected}")
    pools: list[tuple[str, tuple[str, ...]]] = []
    for name, raw_symbols in sorted(payload.items()):
        if not isinstance(name, str) or not name or not isinstance(raw_symbols, list):
            raise RuntimeError("competitor pools must be named symbol lists")
        if not raw_symbols or any(not isinstance(item, str) or not item for item in raw_symbols):
            raise RuntimeError(f"competitor pool contains an invalid symbol: {name}")
        symbols = tuple(raw_symbols)
        if len(symbols) != len(set(symbols)):
            raise RuntimeError(f"competitor pool contains duplicate symbols: {name}")
        pools.append((name, symbols))
    return tuple(pools)


def _validate_windows(value: Any) -> None:
    payload = _object(value, label="windows")
    expected = {window.name: window.to_payload() for window in CANONICAL_WINDOWS}
    missing = sorted(set(expected) - set(payload))
    unexpected = sorted(set(payload) - set(expected))
    if missing:
        raise RuntimeError(f"competitor matrix is missing required windows: {missing}")
    if unexpected:
        raise RuntimeError(f"competitor matrix has unexpected windows: {unexpected}")
    for name, required in expected.items():
        if payload[name] != required:
            raise RuntimeError(f"competitor window definition mismatch: {name}")


def _expected_result_cells(pools: Sequence[str]) -> set[str]:
    return {
        f"{pool}/{window}/{competitor}"
        for pool in pools
        for window in REQUIRED_WINDOWS
        for competitor in REQUIRED_COMPETITORS
    }


def load_competitor_matrix(
    path: str | Path,
    *,
    data_dir: str | Path | None = None,
) -> CompetitorMatrixReference:
    """Strictly load a reviewed full matrix and optionally bind it to local data."""
    source = Path(path)
    try:
        raw = source.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"competitor matrix reference is missing or corrupt: {source}") from exc
    payload = _object(value, label="reference")
    required_sections = {
        "schema_version",
        "frozen_at_utc",
        "policy",
        "execution_contract",
        "data_provenance",
        "repositories",
        "pools",
        "windows",
        "results",
    }
    missing_sections = sorted(required_sections - set(payload))
    unexpected_sections = sorted(set(payload) - required_sections)
    if missing_sections:
        raise RuntimeError(f"competitor matrix is missing sections: {missing_sections}")
    if unexpected_sections:
        raise RuntimeError(f"competitor matrix has unexpected sections: {unexpected_sections}")
    if isinstance(payload["schema_version"], bool) or payload["schema_version"] != 1:
        raise RuntimeError("unsupported competitor matrix schema")
    frozen = payload["frozen_at_utc"]
    if not isinstance(frozen, str):
        raise RuntimeError("competitor frozen_at_utc provenance is malformed")
    try:
        frozen_time = datetime.fromisoformat(frozen.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("competitor frozen_at_utc provenance is malformed") from exc
    if frozen_time.tzinfo is None or frozen_time.utcoffset() != timedelta(0):
        raise RuntimeError("competitor frozen_at_utc provenance must be UTC")

    policy = _parse_policy(payload["policy"])
    execution_contract = _parse_execution_contract(payload["execution_contract"])
    data_provenance = _parse_data_provenance(payload["data_provenance"])
    if data_dir is not None:
        current_data = data_provenance_from_directory(data_dir)
        if current_data != data_provenance:
            raise RuntimeError("competitor data provenance mismatch")

    raw_repositories = _object(payload["repositories"], label="repositories")
    missing_repositories = sorted(set(REQUIRED_COMPETITORS) - set(raw_repositories))
    unexpected_repositories = sorted(set(raw_repositories) - set(REQUIRED_COMPETITORS))
    if missing_repositories:
        raise RuntimeError(f"competitor repository provenance is missing: {missing_repositories}")
    if unexpected_repositories:
        raise RuntimeError(f"competitor repository provenance is unexpected: {unexpected_repositories}")
    repositories = tuple(
        (name, _parse_repository(name, raw_repositories[name])) for name in REQUIRED_COMPETITORS
    )
    locked = dict(LOCKED_COMPETITOR_PROVENANCE)
    for name, observed in repositories:
        if observed != locked[name]:
            raise RuntimeError(f"competitor commit/adapter provenance mismatch: {name}")

    pools = _parse_pools(payload["pools"])
    _validate_windows(payload["windows"])
    raw_results = _object(payload["results"], label="results")
    expected_cells = _expected_result_cells([name for name, _ in pools])
    observed_cells = set(raw_results)
    missing_cells = sorted(expected_cells - observed_cells)
    unexpected_cells = sorted(observed_cells - expected_cells)
    if missing_cells:
        raise RuntimeError(f"competitor matrix is missing result cells: {missing_cells}")
    if unexpected_cells:
        raise RuntimeError(f"competitor matrix has unexpected result cells: {unexpected_cells}")
    results = tuple(
        (name, _parse_metrics(raw_results[name], label=f"results.{name}")) for name in sorted(expected_cells)
    )
    return CompetitorMatrixReference(
        source_sha256=hashlib.sha256(raw).hexdigest(),
        frozen_at_utc=frozen,
        policy=policy,
        execution_contract=execution_contract,
        data_provenance=data_provenance,
        pools=pools,
        repositories=repositories,
        results=results,
    )


def best_of_three(
    values: Mapping[str, CompetitorMetrics],
) -> dict[str, dict[str, float | int | str]]:
    """Return the independently strongest wealth, drawdown, and order benchmark."""
    missing = sorted(set(REQUIRED_COMPETITORS) - set(values))
    unexpected = sorted(set(values) - set(REQUIRED_COMPETITORS))
    if missing or unexpected:
        raise ValueError(
            f"best-of-three requires exactly {list(REQUIRED_COMPETITORS)}; "
            f"missing={missing}, unexpected={unexpected}"
        )
    wealth_name, wealth = max(
        values.items(), key=lambda item: (item[1].final_wealth, -REQUIRED_COMPETITORS.index(item[0]))
    )
    drawdown_name, drawdown = min(values.items(), key=lambda item: (item[1].max_drawdown, item[0]))
    orders_name, orders = min(values.items(), key=lambda item: (item[1].account_orders, item[0]))
    return {
        "final_wealth": {"competitor": wealth_name, "value": wealth.final_wealth},
        "max_drawdown": {"competitor": drawdown_name, "value": drawdown.max_drawdown},
        "account_orders": {"competitor": orders_name, "value": orders.account_orders},
    }


def _coerce_execution_contract(
    value: ExecutionContract | Mapping[str, Any] | None,
) -> ExecutionContract:
    if value is None:
        return CANONICAL_EXECUTION_CONTRACT
    if isinstance(value, ExecutionContract):
        observed = value
    else:
        observed = _parse_execution_contract(dict(value))
    if observed != CANONICAL_EXECUTION_CONTRACT:
        raise RuntimeError("candidate execution-contract mismatch")
    return observed


def evaluate_competitor_gate(
    reference: CompetitorMatrixReference,
    candidate_results: Mapping[str, Mapping[str, Any] | CompetitorMetrics],
    *,
    execution_contract: ExecutionContract | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply wealth/DD/order gates to every pool/window candidate cell."""
    candidate_contract = _coerce_execution_contract(execution_contract)
    if candidate_contract != reference.execution_contract:
        raise RuntimeError("candidate and competitor execution contracts differ")
    pools = [name for name, _ in reference.pools]
    expected = {f"{pool}/{window}" for pool in pools for window in REQUIRED_WINDOWS}
    observed = set(candidate_results)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise RuntimeError(f"candidate competitor gate is missing cells: {missing}")
    if unexpected:
        raise RuntimeError(f"candidate competitor gate has unexpected cells: {unexpected}")

    competitors = reference.result_map()
    policy = reference.policy
    failures: list[str] = []
    cells: dict[str, dict[str, Any]] = {}
    for name in sorted(expected):
        raw_candidate = candidate_results[name]
        candidate = (
            raw_candidate
            if isinstance(raw_candidate, CompetitorMetrics)
            else _parse_metrics(raw_candidate, label=f"candidate.{name}")
        )
        pool, window = name.split("/", maxsplit=1)
        comparison = {
            competitor: competitors[f"{pool}/{window}/{competitor}"] for competitor in REQUIRED_COMPETITORS
        }
        best = best_of_three(comparison)
        wealth_floor = float(best["final_wealth"]["value"]) * policy.wealth_floor_ratio
        drawdown_ceiling = min(
            policy.absolute_max_drawdown,
            float(best["max_drawdown"]["value"]) + policy.drawdown_tolerance,
        )
        best_orders = int(best["account_orders"]["value"])
        order_ceiling = max(
            best_orders + policy.order_tolerance,
            math.ceil(best_orders * policy.order_ceiling_ratio),
        )
        cell_failures: list[str] = []
        if candidate.final_wealth < wealth_floor:
            cell_failures.append(f"{name}: final_wealth below {wealth_floor:.6f}")
        if candidate.max_drawdown > drawdown_ceiling:
            cell_failures.append(f"{name}: max_drawdown above {drawdown_ceiling:.6f}")
        if candidate.account_orders > order_ceiling:
            cell_failures.append(f"{name}: account_orders above {order_ceiling}")
        failures.extend(cell_failures)
        cells[name] = {
            "passed": not cell_failures,
            "candidate": candidate.to_payload(),
            "competitors": {
                competitor: comparison[competitor].to_payload() for competitor in REQUIRED_COMPETITORS
            },
            "best_of_three": best,
            "thresholds": {
                "final_wealth_floor": wealth_floor,
                "max_drawdown_ceiling": drawdown_ceiling,
                "account_orders_ceiling": order_ceiling,
            },
            "failures": cell_failures,
        }
    return {
        "baseline_sha256": reference.source_sha256,
        "passed": not failures,
        "failures": failures,
        "provenance": {
            "frozen_at_utc": reference.frozen_at_utc,
            "data": reference.data_provenance.to_payload(),
            "execution_contract": reference.execution_contract.to_payload(),
            "repositories": {name: provenance.to_payload() for name, provenance in reference.repositories},
        },
        "summary": {
            "cells": len(cells),
            "windows": len(REQUIRED_WINDOWS),
            "pools": len(pools),
            "competitors": len(REQUIRED_COMPETITORS),
        },
        "results": cells,
    }


def _engine_metrics(raw: Mapping[str, Any], window: MatrixWindow) -> CompetitorMetrics:
    try:
        compact = {name: raw[name] for name in _METRIC_FIELDS}
    except KeyError as exc:
        raise RuntimeError(f"candidate replay lacks required metric: {window.name}") from exc
    return _parse_metrics(compact, label=f"candidate.{window.name}")


def run_competitor_gate(
    *,
    data_dir: str | Path,
    reference_path: str | Path,
    runner: Runner | None = None,
    execution_contract: ExecutionContract | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay the complete matrix and fail if reference/data bytes drift."""
    source = Path(reference_path)
    reference = load_competitor_matrix(source, data_dir=data_dir)
    selected_runner = runner
    if selected_runner is None:
        engine = ProductionEngine(data_dir)

        def production_runner(
            _: str,
            symbols: tuple[str, ...],
            window: MatrixWindow,
        ) -> Mapping[str, Any]:
            """Replay one production matrix cell and return normalized metrics."""

            raw = engine.backtest(
                symbols=symbols,
                start=window.start,
                end=window.end,
            )
            return _engine_metrics(raw, window).to_payload()

        selected_runner = production_runner

    candidate: dict[str, Mapping[str, Any]] = {}
    windows = {item.name: item for item in CANONICAL_WINDOWS}
    for pool_name, symbols in reference.pools:
        for window_name in REQUIRED_WINDOWS:
            candidate[f"{pool_name}/{window_name}"] = selected_runner(
                pool_name,
                symbols,
                windows[window_name],
            )
    report = evaluate_competitor_gate(
        reference,
        candidate,
        execution_contract=execution_contract,
    )
    try:
        current_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError("competitor matrix disappeared during validation") from exc
    if current_source_hash != reference.source_sha256:
        raise RuntimeError("competitor matrix changed during validation")
    if data_provenance_from_directory(data_dir) != reference.data_provenance:
        raise RuntimeError("competitor data changed during validation")
    return report
