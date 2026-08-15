"""Validate and sequentially replay the immutable Phase 2 ablation registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeGuard

_RUNNER_ROOT = Path(__file__).resolve().parents[1]
_CAUSAL_STAGES = (
    "reference_context",
    "leaders",
    "risk",
    "opportunity",
    "targets",
    "orders",
    "fills",
)
_METRIC_FIELDS = {
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "acute_return",
    "gross_turnover",
    "annual_turnover",
    "top1_concentration",
    "top3_concentration",
    "pnl_hhi",
}
_CONCENTRATION_TOLERANCE = 1e-12
_SHA256_LENGTH = 64


def _project_imports() -> tuple[Any, ...]:
    if str(_RUNNER_ROOT) not in sys.path:
        sys.path.insert(0, str(_RUNNER_ROOT))
    from research.ablation_registry import (
        build_contract_schedule,
        isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        verify_carrier_checkout,
    )

    return (
        build_contract_schedule,
        isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        verify_carrier_checkout,
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _is_sha256(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_metrics(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("ablation worker valid metrics are missing")
    if set(value) != _METRIC_FIELDS:
        raise ValueError("ablation worker metric coverage differs")
    numeric = tuple(value[name] for name in _METRIC_FIELDS - {"account_orders", "acute_return"})
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item))
        for item in numeric
    ):
        raise ValueError("ablation worker metrics are malformed")
    orders = value["account_orders"]
    if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
        raise ValueError("ablation worker order count is malformed")
    acute = value["acute_return"]
    if acute is not None and (
        isinstance(acute, bool) or not isinstance(acute, (int, float)) or not math.isfinite(float(acute))
    ):
        raise ValueError("ablation worker acute return is malformed")
    if float(value["final_wealth"]) <= 0:
        raise ValueError("ablation worker final wealth is malformed")
    if not 0 <= float(value["max_drawdown"]) <= 1:
        raise ValueError("ablation worker drawdown is malformed")
    if float(value["gross_turnover"]) < 0 or float(value["annual_turnover"]) < 0:
        raise ValueError("ablation worker turnover is malformed")
    top1 = float(value["top1_concentration"])
    top3 = float(value["top3_concentration"])
    hhi = float(value["pnl_hhi"])
    if (
        any(
            not -_CONCENTRATION_TOLERANCE <= item <= 1 + _CONCENTRATION_TOLERANCE
            for item in (top1, top3, hhi)
        )
        or top1 > top3 + _CONCENTRATION_TOLERANCE
    ):
        raise ValueError("ablation worker concentration is malformed")
    if acute is not None and float(acute) <= -1:
        raise ValueError("ablation worker acute return is malformed")


def _validate_worker_payload(
    payload: Mapping[str, Any],
    *,
    schedule: Sequence[Any],
    binding_sha256: str,
    experiment_id: str,
) -> None:
    """Reject partial, stale, status-rewritten, or trace-free worker evidence."""
    if (
        payload.get("schema_version") != 1
        or payload.get("mode") != "contract-replay"
        or payload.get("binding_sha256") != binding_sha256
        or payload.get("experiment_id") != experiment_id
    ):
        raise ValueError("ablation worker provenance is stale")
    raw_cells = payload.get("cells")
    traces = payload.get("traces")
    provenance = payload.get("provenance")
    if (
        not isinstance(raw_cells, list)
        or not isinstance(traces, Mapping)
        or not isinstance(provenance, Mapping)
    ):
        raise ValueError("ablation worker payload is incomplete")
    expected = tuple((item.contract, item.cell_id, item.status, item.economic) for item in schedule)
    observed: list[tuple[str, str, str, bool]] = []
    expected_trace_keys: set[str] = set()
    for raw, item in zip(raw_cells, schedule, strict=False):
        if not isinstance(raw, Mapping):
            raise ValueError("ablation worker cell is malformed")
        identity = (
            raw.get("contract"),
            raw.get("cell_id"),
            raw.get("status"),
            raw.get("economic"),
        )
        if not (
            isinstance(identity[0], str)
            and isinstance(identity[1], str)
            and isinstance(identity[2], str)
            and isinstance(identity[3], bool)
        ):
            raise ValueError("ablation worker cell identity is malformed")
        observed.append(identity)  # type: ignore[arg-type]
        if identity[:2] != (item.contract, item.cell_id):
            raise ValueError("ablation worker cell coverage differs")
        if identity[2] != item.status or identity[3] != item.economic:
            raise ValueError("ablation worker cell status differs from frozen contract")
        metrics = raw.get("metrics")
        replay_error = raw.get("replay_error")
        result_hash = raw.get("raw_result_sha256")
        key = f"{item.contract}/{item.cell_id}"
        if item.status == "VALID":
            _validate_metrics(metrics)
            if replay_error is not None or not _is_sha256(result_hash):
                raise ValueError("ablation worker valid cell evidence is malformed")
            expected_trace_keys.add(key)
        elif item.status == "REPLAY_ERROR":
            if (
                metrics is not None
                or result_hash is not None
                or not isinstance(replay_error, Mapping)
                or not isinstance(replay_error.get("type"), str)
                or not isinstance(replay_error.get("message"), str)
            ):
                raise ValueError("ablation worker replay error evidence is malformed")
        elif metrics is not None or replay_error is not None or result_hash is not None:
            raise ValueError("ablation insufficient cell contains economic evidence")
    if tuple(observed) != expected:
        raise ValueError("ablation worker cell coverage differs")
    if set(traces) != expected_trace_keys:
        raise ValueError("ablation worker trace coverage differs")
    for key in expected_trace_keys:
        rows = traces[key]
        if not isinstance(rows, list) or not rows:
            raise ValueError("ablation worker decision trace is missing")
        previous = ""
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                raise ValueError("ablation worker decision trace is malformed")
            date = raw_row.get("date")
            stages = raw_row.get("stages")
            if (
                not isinstance(date, str)
                or not date
                or date <= previous
                or not isinstance(stages, Mapping)
                or set(stages) != set(_CAUSAL_STAGES)
                or any(not _is_sha256(stages[name]) for name in _CAUSAL_STAGES)
            ):
                raise ValueError("ablation worker decision trace is malformed")
            previous = date


def _write_checkpoint(path: Path, payload: Mapping[str, Any]) -> str:
    """Atomically write a canonical, content-addressed checkpoint envelope."""
    canonical_payload = dict(payload)
    digest = hashlib.sha256(_canonical_bytes(canonical_payload)).hexdigest()
    envelope = {
        "schema_version": 1,
        "payload_sha256": digest,
        "payload": canonical_payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_bytes(envelope) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return digest


def _read_checkpoint(
    path: Path,
    *,
    binding_sha256: str,
    kind: str,
) -> dict[str, Any]:
    """Read and authenticate one checkpoint against the exact run binding."""
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("ablation checkpoint is unreadable") from exc
    if not isinstance(envelope, Mapping) or set(envelope) != {
        "schema_version",
        "payload_sha256",
        "payload",
    }:
        raise ValueError("ablation checkpoint envelope is malformed")
    payload = envelope.get("payload")
    expected_hash = envelope.get("payload_sha256")
    if (
        envelope.get("schema_version") != 1
        or not isinstance(payload, Mapping)
        or not _is_sha256(expected_hash)
        or hashlib.sha256(_canonical_bytes(payload)).hexdigest() != expected_hash
    ):
        raise ValueError("ablation checkpoint content hash differs")
    if payload.get("binding_sha256") != binding_sha256 or payload.get("kind") != kind:
        raise ValueError("ablation checkpoint is stale")
    return dict(payload)


def _validate_comparison_coverage(
    comparison: Mapping[str, Any],
    *,
    schedule: Sequence[Any],
) -> None:
    rows = comparison.get("cells")
    aggregates = comparison.get("aggregates")
    if not isinstance(rows, list) or not isinstance(aggregates, Mapping):
        raise ValueError("ablation comparison cell coverage is malformed")
    expected = tuple((item.contract, item.cell_id, item.status) for item in schedule)
    observed: list[tuple[str, str, str]] = []
    for row, item in zip(rows, schedule, strict=False):
        if not isinstance(row, Mapping):
            raise ValueError("ablation comparison cell coverage is malformed")
        identity = (
            row.get("contract"),
            row.get("cell_id"),
            row.get("baseline_status"),
        )
        if not all(isinstance(value, str) for value in identity):
            raise ValueError("ablation comparison cell coverage is malformed")
        observed.append((str(identity[0]), str(identity[1]), str(identity[2])))
        if row.get("variant_status") != item.status:
            raise ValueError("ablation comparison status differs from frozen contract")
        delta = row.get("delta")
        if (item.status == "VALID") != isinstance(delta, Mapping):
            raise ValueError("ablation comparison delta coverage differs")
        if isinstance(delta, Mapping):
            if set(delta) != _METRIC_FIELDS:
                raise ValueError("ablation comparison delta dimensions differ")
            for name, value in delta.items():
                if name == "acute_return" and value is None:
                    continue
                if name == "account_orders":
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise ValueError("ablation comparison delta dimensions are malformed")
                elif (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise ValueError("ablation comparison delta dimensions are malformed")
    if tuple(observed) != expected:
        raise ValueError("ablation comparison cell coverage differs")
    expected_contracts = {item.contract for item in schedule if item.status == "VALID"}
    if set(aggregates) != expected_contracts:
        raise ValueError("ablation comparison aggregate coverage differs")
    for contract in expected_contracts:
        aggregate = aggregates[contract]
        if not isinstance(aggregate, Mapping) or set(aggregate) != {
            "baseline",
            "variant",
            "delta",
        }:
            raise ValueError("ablation comparison aggregate coverage differs")
        baseline = aggregate["baseline"]
        variant = aggregate["variant"]
        delta = aggregate["delta"]
        valid_count = sum(item.contract == contract and item.status == "VALID" for item in schedule)
        if (
            not isinstance(baseline, Mapping)
            or not isinstance(variant, Mapping)
            or not isinstance(delta, Mapping)
            or set(baseline) != set(variant)
            or set(delta) != set(baseline)
            or baseline.get("economic_cells") != valid_count
            or variant.get("economic_cells") != valid_count
            or delta.get("economic_cells") != 0
        ):
            raise ValueError("ablation comparison aggregate coverage differs")


def _validate_experiment_checkpoints(
    registry: Any,
    checkpoints: Mapping[str, Mapping[str, Any]],
    *,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> list[dict[str, Any]]:
    """Require a distinct authenticated checkpoint for all 13 experiments."""
    expected_ids = tuple(item.experiment_id for item in registry.experiments)
    if len(expected_ids) != 13 or set(checkpoints) != set(expected_ids):
        raise ValueError("ablation aggregation requires exact 13/13 experiment coverage")
    ordered: list[dict[str, Any]] = []
    worker_hashes: set[str] = set()
    for experiment in registry.experiments:
        raw = checkpoints[experiment.experiment_id]
        worker_hash = _validate_experiment_checkpoint(
            experiment,
            raw,
            binding_sha256=binding_sha256,
            schedule=schedule,
        )
        if worker_hash in worker_hashes:
            raise ValueError("ablation variant worker evidence was reused")
        worker_hashes.add(worker_hash)
        ordered.append(dict(raw))
    return ordered


def _validate_experiment_checkpoint(
    experiment: Any,
    raw: Mapping[str, Any],
    *,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> str:
    comparison = raw.get("comparison")
    divergence = comparison.get("first_divergence") if isinstance(comparison, Mapping) else None
    if (
        raw.get("schema_version") != 1
        or raw.get("kind") != "experiment"
        or raw.get("binding_sha256") != binding_sha256
        or raw.get("experiment_id") != experiment.experiment_id
        or raw.get("subsystem") != experiment.subsystem
    ):
        raise ValueError("ablation experiment checkpoint is stale")
    if raw.get("carrier_sha256") != experiment.carrier.sha256:
        raise ValueError("ablation experiment checkpoint carrier differs")
    worker_hash = raw.get("worker_payload_sha256")
    if not _is_sha256(worker_hash):
        raise ValueError("ablation experiment worker hash is malformed")
    if (
        not isinstance(comparison, Mapping)
        or not isinstance(comparison.get("cells"), list)
        or not isinstance(comparison.get("aggregates"), Mapping)
        or not isinstance(divergence, Mapping)
        or not divergence.get("cell_id")
        or not divergence.get("date")
        or not divergence.get("first_stage")
        or not isinstance(raw.get("replay_command"), list)
    ):
        raise ValueError("ablation experiment checkpoint is incomplete")
    _validate_comparison_coverage(comparison, schedule=schedule)
    return worker_hash


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _combined_sha256(paths: Sequence[Path], *, root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _runtime() -> dict[str, str]:
    import numpy as np
    import pandas as pd

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("cannot resolve uv for ablation runtime provenance")
    try:
        uv_output = subprocess.run(  # nosec B603
            [uv, "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot inspect ablation uv runtime") from exc
    parts = uv_output.split()
    if len(parts) < 2 or parts[0] != "uv":
        raise RuntimeError("ablation uv version is malformed")
    return {
        "python_full_version": platform.python_version(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "uv_version": parts[1],
    }


def _data_provenance(data_dir: Path) -> dict[str, Any]:
    if str(_RUNNER_ROOT) not in sys.path:
        sys.path.insert(0, str(_RUNNER_ROOT))
    from uquant.validation.manifest import verify_data_manifest

    return dict(verify_data_manifest(data_dir))


_PROBE = """
import json
import platform
import sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import pandas as pd
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import ProductionEngine
from uquant.types import AccountState
changes = json.loads(sys.argv[2])
config = DEFAULT_CONFIG.override(**changes)
first = AccountState.empty(config.initial_cash)
second = AccountState.empty(config.initial_cash)
if first is second or first.positions or second.positions or first.pending_orders or second.pending_orders:
    raise RuntimeError("ablation account probe is not fresh")
print(json.dumps({
    "effective_config_sha256": config_fingerprint(config),
    "fresh_account_sha256": __import__("hashlib").sha256(
        json.dumps(first.to_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest(),
    "runtime": {
        "python_full_version": platform.python_version(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    },
    "engine_source": __import__("inspect").getfile(ProductionEngine),
}, allow_nan=False, separators=(",", ":"), sort_keys=True))
"""


def _probe_checkout(root: Path, changes: Mapping[str, bool]) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    try:
        process = subprocess.run(  # nosec B603
            [
                sys.executable,
                "-I",
                "-c",
                _PROBE,
                str(root),
                json.dumps(dict(changes), separators=(",", ":"), sort_keys=True),
            ],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(process.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError("isolated ablation carrier is not importable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("isolated ablation carrier probe is malformed")
    engine_source = Path(str(payload.get("engine_source", ""))).resolve()
    if not engine_source.is_relative_to(root):
        raise RuntimeError("isolated ablation imported production outside its checkout")
    return payload


def _contract_summary(schedule: Sequence[Any]) -> dict[str, Any]:
    names = tuple(dict.fromkeys(item.contract for item in schedule))
    return {
        name: {
            "record_count": len(selected),
            "economic_count": sum(item.economic for item in selected),
            "status_counts": dict(sorted(Counter(item.status for item in selected).items())),
        }
        for name in names
        if (selected := tuple(item for item in schedule if item.contract == name))
    }


def _schedule_rows(schedule: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "contract": item.contract,
            "cell_id": item.cell_id,
            "status": item.status,
            "economic": item.economic,
            "symbols": list(item.symbols),
            "start": item.start,
            "end": item.end,
            "acute_start": item.acute_start,
            "acute_end": item.acute_end,
            "pool_size": item.pool_size,
            "seed_index": item.seed_index,
            "derived_seed": item.derived_seed,
        }
        for item in schedule
    ]


def _git_output(root: Path, *arguments: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve git for ablation provenance")
    try:
        return subprocess.run(  # nosec B603
            [git, "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot inspect ablation git provenance") from exc


def _runner_sha256(registry_path: Path) -> str:
    paths = (
        Path(__file__).resolve(),
        _RUNNER_ROOT / "research" / "ablation.py",
        _RUNNER_ROOT / "research" / "ablation_registry.py",
        registry_path,
    )
    return _combined_sha256(paths, root=_RUNNER_ROOT)


def _baseline_config_sha256() -> str:
    if str(_RUNNER_ROOT) not in sys.path:
        sys.path.insert(0, str(_RUNNER_ROOT))
    from uquant.config import DEFAULT_CONFIG, config_fingerprint

    return str(config_fingerprint(DEFAULT_CONFIG))


def _execution_binding(
    *,
    registry: Any,
    registry_path: Path,
    source_root: Path,
    data_dir: Path,
    schedule: Sequence[Any],
) -> dict[str, Any]:
    runtime = _runtime()
    runtime.update(
        {
            "platform_system": platform.system(),
            "platform_release": platform.release(),
            "platform_machine": platform.machine(),
        }
    )
    return {
        "schema_version": 1,
        "registry_sha256": registry.payload_sha256,
        "source": {
            "base_commit": registry.base_commit,
            "production_source_sha256": registry.source_sha256,
            "orchestrator_head": _git_output(source_root, "rev-parse", "HEAD"),
        },
        "fixed_contracts": [
            {
                "name": item.name,
                "path": item.path,
                "sha256": item.sha256,
                "record_count": item.record_count,
                "economic_count": item.economic_count,
            }
            for item in registry.fixed_contracts
        ],
        "schedule_sha256": hashlib.sha256(_canonical_bytes(_schedule_rows(schedule))).hexdigest(),
        "contracts": _contract_summary(schedule),
        "baseline_config_sha256": _baseline_config_sha256(),
        "runner_sha256": _runner_sha256(registry_path),
        "uv_lock_sha256": _sha256(source_root / "uv.lock"),
        "runtime": runtime,
        "data": _data_provenance(data_dir),
    }


def _replay_command(
    *,
    source_root: Path,
    registry_path: Path,
    data_dir: Path,
    experiment_id: str,
    checkpoint_dir: Path | None = None,
    output: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--source-root",
        str(source_root),
        "--registry",
        str(registry_path),
        "--data-dir",
        str(data_dir),
        "--checkpoint-dir",
        str(
            checkpoint_dir if checkpoint_dir is not None else Path("/tmp/uquant-phase2-ablation-checkpoints")
        ),
        "--output",
        str(output if output is not None else Path("/tmp/uquant-phase2-ablation-progress.json")),
        "--experiment",
        experiment_id,
    ]
    return command


def _first_hashed_divergence(
    baseline: Mapping[str, Sequence[Mapping[str, Any]]],
    variant: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    require: bool = False,
) -> dict[str, Any] | None:
    """Return the first fixed-cell/date causal-stage hash difference."""
    if set(baseline) != set(variant):
        raise ValueError("ablation decision trace coverage differs")
    differences: list[tuple[str, str, dict[str, Any]]] = []
    for cell_id in baseline:
        left_rows = baseline[cell_id]
        right_rows = variant[cell_id]
        left_dates = tuple(str(row.get("date", "")) for row in left_rows)
        right_dates = tuple(str(row.get("date", "")) for row in right_rows)
        if left_dates != right_dates:
            raise ValueError("ablation decision traces require aligned dates")
        for left, right in zip(left_rows, right_rows, strict=True):
            left_stages = left.get("stages")
            right_stages = right.get("stages")
            if not isinstance(left_stages, Mapping) or not isinstance(right_stages, Mapping):
                raise ValueError("ablation decision stage hashes are malformed")
            if set(left_stages) != set(_CAUSAL_STAGES) or set(right_stages) != set(_CAUSAL_STAGES):
                raise ValueError("ablation decision stage hash coverage differs")
            changed = [stage for stage in _CAUSAL_STAGES if left_stages[stage] != right_stages[stage]]
            if changed:
                difference = {
                    "cell_id": cell_id,
                    "date": left["date"],
                    "changed_fields": changed,
                    "first_stage": changed[0],
                    "baseline_stage_sha256": dict(left_stages),
                    "variant_stage_sha256": dict(right_stages),
                }
                differences.append((str(left["date"]), cell_id, difference))
                break
    if differences:
        return min(differences, key=lambda item: (item[0], item[1]))[2]
    if require:
        raise ValueError("ablation experiment has no behavior divergence")
    return None


def _replay_cell(
    engine: Any,
    *,
    symbols: Sequence[str],
    start: str,
    end: str,
    acute_start: str | None = None,
    acute_end: str | None = None,
) -> dict[str, Any]:
    """Replay one real production cell and retain raw dimensions plus stage hashes."""
    import pandas as pd

    from research.first_divergence import _CAUSAL_STAGES as TRACE_STAGES
    from research.first_divergence import _canonical_stages, trace_backtest
    from uquant.validation.generalization import (
        symbol_pnl_concentration,
        symbol_pnl_from_result,
    )
    from uquant.validation.promotion import _compact

    if tuple(TRACE_STAGES) != _CAUSAL_STAGES:
        raise RuntimeError("ablation trace stage contract drifted")
    raw, trace = trace_backtest(
        engine,
        symbols=tuple(symbols),
        start=start,
        end=end,
    )
    compact = _compact(
        raw,
        acute=(acute_start, acute_end) if acute_start is not None and acute_end is not None else None,
    )
    account = raw.get("final_account")
    if not isinstance(account, Mapping):
        raise RuntimeError("ablation replay final account is missing")
    positions = account.get("positions")
    if not isinstance(positions, Mapping):
        raise RuntimeError("ablation replay final positions are malformed")
    final_date = pd.Timestamp(str(raw.get("end", end)))
    final_prices = {
        str(symbol): engine._price(str(symbol), final_date)
        for symbol, position in positions.items()
        if isinstance(position, Mapping) and int(position.get("shares", 0)) > 0
    }
    concentration = symbol_pnl_concentration(symbol_pnl_from_result(raw, final_prices))
    trace_hashes = [
        {
            "date": row["date"],
            "stages": {
                stage: hashlib.sha256(_canonical_bytes(stages[stage])).hexdigest() for stage in _CAUSAL_STAGES
            },
        }
        for row in trace
        if (stages := _canonical_stages(row))
    ]
    return {
        "metrics": {
            "final_wealth": compact["final_wealth"],
            "max_drawdown": compact["max_drawdown"],
            "account_orders": compact["account_orders"],
            "acute_return": compact["acute_return"],
            "gross_turnover": compact["gross_turnover"],
            "annual_turnover": compact["annual_turnover"],
            **concentration,
        },
        "trace": trace_hashes,
        "raw_result_sha256": hashlib.sha256(_canonical_bytes(raw)).hexdigest(),
    }


def _compare_worker_payloads(
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two complete raw runs without making a Task 8 conclusion."""
    from research.ablation import (
        AblationCell,
        AblationMetrics,
        aggregate_dimensions,
        compare_cells,
    )

    raw_baseline_cells = baseline.get("cells")
    raw_variant_cells = variant.get("cells")
    baseline_traces = baseline.get("traces")
    variant_traces = variant.get("traces")
    if (
        not isinstance(raw_baseline_cells, list)
        or not isinstance(raw_variant_cells, list)
        or not isinstance(baseline_traces, Mapping)
        or not isinstance(variant_traces, Mapping)
    ):
        raise ValueError("ablation worker payload is incomplete")

    def by_identity(rows: Sequence[object]) -> dict[tuple[str, str], Mapping[str, Any]]:
        result: dict[tuple[str, str], Mapping[str, Any]] = {}
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ValueError("ablation worker cell is malformed")
            contract = raw.get("contract")
            cell_id = raw.get("cell_id")
            if not isinstance(contract, str) or not contract or not isinstance(cell_id, str) or not cell_id:
                raise ValueError("ablation worker cell identity is malformed")
            identity = (contract, cell_id)
            if identity in result:
                raise ValueError("ablation worker contains duplicate cells")
            result[identity] = raw
        return result

    baseline_by_id = by_identity(raw_baseline_cells)
    variant_by_id = by_identity(raw_variant_cells)
    if set(baseline_by_id) != set(variant_by_id):
        raise ValueError("ablation worker cell coverage differs")

    def typed_cell(raw: Mapping[str, Any]) -> AblationCell:
        status = raw.get("status")
        metrics_raw = raw.get("metrics")
        if not isinstance(status, str):
            raise ValueError("ablation worker cell status is malformed")
        metrics = None
        if metrics_raw is not None:
            if not isinstance(metrics_raw, Mapping):
                raise ValueError("ablation worker metrics are malformed")
            metrics = AblationMetrics(
                final_wealth=float(metrics_raw["final_wealth"]),
                max_drawdown=float(metrics_raw["max_drawdown"]),
                account_orders=int(metrics_raw["account_orders"]),
                acute_return=(
                    None if metrics_raw["acute_return"] is None else float(metrics_raw["acute_return"])
                ),
                gross_turnover=float(metrics_raw["gross_turnover"]),
                annual_turnover=float(metrics_raw["annual_turnover"]),
                top1_concentration=float(metrics_raw["top1_concentration"]),
                top3_concentration=float(metrics_raw["top3_concentration"]),
                pnl_hhi=float(metrics_raw["pnl_hhi"]),
            )
        return AblationCell(str(raw["contract"]), str(raw["cell_id"]), status, metrics)

    compared_cells: list[dict[str, Any]] = []
    baseline_typed: list[Any] = []
    variant_typed: list[Any] = []
    for identity in baseline_by_id:
        left_raw = baseline_by_id[identity]
        right_raw = variant_by_id[identity]
        left = typed_cell(left_raw)
        right = typed_cell(right_raw)
        baseline_typed.append(left)
        variant_typed.append(right)
        delta = compare_cells(left, right).to_dict() if left.status == right.status == "VALID" else None
        compared_cells.append(
            {
                "contract": identity[0],
                "cell_id": identity[1],
                "baseline_status": left.status,
                "variant_status": right.status,
                "baseline_metrics": None if left.metrics is None else left.metrics.to_dict(),
                "variant_metrics": None if right.metrics is None else right.metrics.to_dict(),
                "delta": delta,
                "baseline_raw_result_sha256": left_raw.get("raw_result_sha256"),
                "variant_raw_result_sha256": right_raw.get("raw_result_sha256"),
            }
        )

    contracts = tuple(dict.fromkeys(item.contract for item in baseline_typed))
    aggregates: dict[str, Any] = {}
    for contract in contracts:
        left_valid = tuple(
            item for item in baseline_typed if item.contract == contract and item.status == "VALID"
        )
        right_valid = tuple(
            item for item in variant_typed if item.contract == contract and item.status == "VALID"
        )
        left_aggregate = aggregate_dimensions(left_valid)
        right_aggregate = aggregate_dimensions(right_valid)
        common = set(left_aggregate) & set(right_aggregate)
        aggregates[contract] = {
            "baseline": left_aggregate,
            "variant": right_aggregate,
            "delta": {name: right_aggregate[name] - left_aggregate[name] for name in sorted(common)},
        }
    return {
        "first_divergence": _first_hashed_divergence(
            baseline_traces,
            variant_traces,
            require=True,
        ),
        "cells": compared_cells,
        "aggregates": aggregates,
        "baseline_provenance": baseline.get("provenance"),
        "variant_provenance": variant.get("provenance"),
    }


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} is malformed")
    return dict(payload)


def _worker(args: argparse.Namespace) -> dict[str, Any]:
    """Replay an exact schedule using production imported from one isolated checkout."""
    source_root = Path(args.source_root).resolve()
    data_dir = Path(args.data_dir).resolve()
    schedule_checkpoint = _read_checkpoint(
        Path(args.schedule_checkpoint).resolve(),
        binding_sha256=args.binding_sha256,
        kind="schedule",
    )
    raw_schedule = schedule_checkpoint.get("cells")
    schedule_sha256 = schedule_checkpoint.get("schedule_sha256")
    if (
        not isinstance(raw_schedule, list)
        or not _is_sha256(schedule_sha256)
        or hashlib.sha256(_canonical_bytes(raw_schedule)).hexdigest() != schedule_sha256
    ):
        raise ValueError("ablation worker schedule is malformed")
    try:
        changes_payload = json.loads(args.config_json)
        checkout_payload = json.loads(args.checkout_json)
    except json.JSONDecodeError as exc:
        raise ValueError("ablation worker invocation provenance is malformed") from exc
    if (
        not isinstance(changes_payload, Mapping)
        or any(
            not isinstance(name, str) or not isinstance(value, bool)
            for name, value in changes_payload.items()
        )
        or not isinstance(checkout_payload, Mapping)
    ):
        raise ValueError("ablation worker invocation provenance is malformed")

    resolved_source = str(source_root)
    sys.path = [resolved_source, *[item for item in sys.path if item != resolved_source]]
    from uquant.config import DEFAULT_CONFIG, config_fingerprint
    from uquant.engine import ProductionEngine
    from uquant.types import AccountState
    from uquant.validation.manifest import verify_data_manifest

    engine_source = Path(sys.modules[ProductionEngine.__module__].__file__ or "").resolve()
    if not engine_source.is_relative_to(source_root):
        raise RuntimeError("ablation worker imported production outside its checkout")
    config = DEFAULT_CONFIG.override(**dict(changes_payload))
    first_account = AccountState.empty(config.initial_cash)
    second_account = AccountState.empty(config.initial_cash)
    if (
        first_account is second_account
        or first_account.positions
        or second_account.positions
        or first_account.pending_orders
        or second_account.pending_orders
    ):
        raise RuntimeError("ablation worker account factory is not fresh")
    fresh_account_sha256 = hashlib.sha256(_canonical_bytes(first_account.to_dict())).hexdigest()
    engine = ProductionEngine(data_dir, config)
    cells: list[dict[str, Any]] = []
    traces: dict[str, Any] = {}
    economic_complete = 0
    economic_total = sum(bool(item.get("economic")) for item in raw_schedule if isinstance(item, Mapping))
    for index, raw in enumerate(raw_schedule, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("ablation worker schedule cell is malformed")
        required = {
            "contract",
            "cell_id",
            "status",
            "economic",
            "symbols",
            "start",
            "end",
            "acute_start",
            "acute_end",
            "pool_size",
            "seed_index",
            "derived_seed",
        }
        if set(raw) != required:
            raise ValueError("ablation worker schedule cell fields differ")
        contract = raw["contract"]
        cell_id = raw["cell_id"]
        status = raw["status"]
        economic = raw["economic"]
        symbols = raw["symbols"]
        if (
            not isinstance(contract, str)
            or not isinstance(cell_id, str)
            or status not in {"VALID", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}
            or not isinstance(economic, bool)
            or not isinstance(symbols, list)
            or any(not isinstance(symbol, str) for symbol in symbols)
        ):
            raise ValueError("ablation worker schedule cell is malformed")
        cell_payload: dict[str, Any] = {
            "contract": contract,
            "cell_id": cell_id,
            "status": status,
            "economic": economic,
            "metrics": None,
            "replay_error": None,
            "raw_result_sha256": None,
        }
        if economic:
            economic_complete += 1
            print(
                f"[{args.experiment_id}] economic {economic_complete}/{economic_total} "
                f"record {index}/{len(raw_schedule)} {contract}/{cell_id}",
                file=sys.stderr,
                flush=True,
            )
            try:
                result = _replay_cell(
                    engine,
                    symbols=tuple(symbols),
                    start=str(raw["start"]),
                    end=str(raw["end"]),
                    acute_start=(str(raw["acute_start"]) if raw["acute_start"] is not None else None),
                    acute_end=(str(raw["acute_end"]) if raw["acute_end"] is not None else None),
                )
            except Exception as exc:
                if status != "REPLAY_ERROR":
                    raise RuntimeError(
                        f"unexpected ablation replay error for {contract}/{cell_id}: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                cell_payload["replay_error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            else:
                if status != "VALID":
                    raise RuntimeError(
                        f"frozen replay-error status unexpectedly succeeded: {contract}/{cell_id}"
                    )
                cell_payload["metrics"] = result["metrics"]
                cell_payload["raw_result_sha256"] = result["raw_result_sha256"]
                traces[f"{contract}/{cell_id}"] = result["trace"]
        cells.append(cell_payload)
    return {
        "schema_version": 1,
        "mode": "contract-replay",
        "binding_sha256": args.binding_sha256,
        "experiment_id": args.experiment_id,
        "cells": cells,
        "traces": traces,
        "provenance": {
            "checkout": dict(checkout_payload),
            "production_engine_source": engine_source.relative_to(source_root).as_posix(),
            "effective_config_sha256": config_fingerprint(config),
            "fresh_account_sha256": fresh_account_sha256,
            "account_factory": "uquant.types.AccountState.empty/per-backtest",
            "schedule_sha256": schedule_sha256,
            "data": dict(verify_data_manifest(data_dir)),
            "runtime": _runtime(),
            "uv_lock_sha256": _sha256(source_root / "uv.lock"),
            "process_contract": {
                "isolated_python": True,
                "pythonhashseed": os.environ.get("PYTHONHASHSEED", ""),
                "single_process": True,
                "thread_limits": {
                    name: os.environ.get(name, "")
                    for name in (
                        "OMP_NUM_THREADS",
                        "OPENBLAS_NUM_THREADS",
                        "MKL_NUM_THREADS",
                        "NUMEXPR_NUM_THREADS",
                    )
                },
            },
        },
    }


def _invoke_worker(
    *,
    source_root: Path,
    data_dir: Path,
    schedule_checkpoint: Path,
    binding_sha256: str,
    experiment_id: str,
    config_changes: Mapping[str, bool],
    checkout: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        "worker",
        "--source-root",
        str(source_root),
        "--data-dir",
        str(data_dir),
        "--schedule-checkpoint",
        str(schedule_checkpoint),
        "--binding-sha256",
        binding_sha256,
        "--experiment-id",
        experiment_id,
        "--config-json",
        _canonical_bytes(dict(config_changes)).decode(),
        "--checkout-json",
        _canonical_bytes(dict(checkout)).decode(),
        "--output",
        str(output),
    ]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    try:
        subprocess.run(  # nosec B603
            command,
            cwd=source_root,
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"isolated ablation worker failed: {experiment_id}") from exc
    return _load_json_mapping(output, label="ablation worker output")


def _checkout_payload(checkout: Any) -> dict[str, Any]:
    return {
        "base_commit": checkout.base_commit,
        "experiment_commit": checkout.experiment_commit,
        "production_source_sha256": checkout.source_sha256,
        "tree_sha256": checkout.tree_sha256,
        "carrier_sha256": checkout.carrier_sha256,
        "config_changes": dict(checkout.config_changes),
        "clean": True,
    }


def _validate_worker_provenance(
    payload: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    checkout: Mapping[str, Any],
    effective_config_sha256: str,
    fresh_account_sha256: str,
) -> None:
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("ablation worker provenance is missing")
    runtime = binding.get("runtime")
    expected_runtime = (
        {
            name: runtime[name]
            for name in ("python_full_version", "numpy_version", "pandas_version", "uv_version")
        }
        if isinstance(runtime, Mapping)
        else None
    )
    process_contract = provenance.get("process_contract")
    if (
        provenance.get("checkout") != checkout
        or provenance.get("production_engine_source") != "uquant/engine.py"
        or provenance.get("effective_config_sha256") != effective_config_sha256
        or provenance.get("fresh_account_sha256") != fresh_account_sha256
        or provenance.get("schedule_sha256") != binding.get("schedule_sha256")
        or provenance.get("data") != binding.get("data")
        or provenance.get("runtime") != expected_runtime
        or provenance.get("uv_lock_sha256") != binding.get("uv_lock_sha256")
        or not isinstance(process_contract, Mapping)
        or process_contract.get("isolated_python") is not True
        or process_contract.get("pythonhashseed") != "0"
        or process_contract.get("single_process") is not True
        or process_contract.get("thread_limits")
        != {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    ):
        raise ValueError("ablation worker provenance differs from the exact binding")


def _baseline_replay_command(
    *,
    source_root: Path,
    registry_path: Path,
    data_dir: Path,
    checkpoint_dir: Path,
    output: Path,
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--source-root",
        str(source_root),
        "--registry",
        str(registry_path),
        "--data-dir",
        str(data_dir),
        "--checkpoint-dir",
        str(checkpoint_dir),
        "--output",
        str(output),
        "--baseline-only",
    ]


def _load_baseline_checkpoint(
    path: Path,
    *,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> dict[str, Any]:
    checkpoint = _read_checkpoint(
        path,
        binding_sha256=binding_sha256,
        kind="baseline",
    )
    worker = checkpoint.get("worker")
    worker_hash = checkpoint.get("worker_payload_sha256")
    if (
        checkpoint.get("schema_version") != 1
        or not isinstance(worker, Mapping)
        or not _is_sha256(worker_hash)
        or hashlib.sha256(_canonical_bytes(worker)).hexdigest() != worker_hash
        or not isinstance(checkpoint.get("replay_command"), list)
    ):
        raise ValueError("ablation baseline checkpoint is malformed")
    _validate_worker_payload(
        worker,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
    )
    return checkpoint


def _load_available_experiments(
    registry: Any,
    *,
    checkpoint_dir: Path,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    for experiment in registry.experiments:
        path = checkpoint_dir / f"{experiment.experiment_id}.json"
        if path.exists():
            checkpoint = _read_checkpoint(
                path,
                binding_sha256=binding_sha256,
                kind="experiment",
            )
            _validate_experiment_checkpoint(
                experiment,
                checkpoint,
                binding_sha256=binding_sha256,
                schedule=schedule,
            )
            available[experiment.experiment_id] = checkpoint
    return available


def _progress_payload(
    *,
    registry: Any,
    binding: Mapping[str, Any],
    binding_sha256: str,
    checkpoint_dir: Path,
    baseline_path: Path,
    completed: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    expected = tuple(item.experiment_id for item in registry.experiments)
    completed_ids = tuple(item for item in expected if item in completed)
    return {
        "schema_version": 1,
        "mode": "ablation-checkpoint-progress",
        "complete": False,
        "binding_sha256": binding_sha256,
        "binding": dict(binding),
        "baseline": {
            "checkpoint": str(baseline_path),
            "file_sha256": _sha256(baseline_path),
        },
        "completed_count": len(completed_ids),
        "required_count": len(expected),
        "completed_experiment_ids": list(completed_ids),
        "missing_experiment_ids": [item for item in expected if item not in completed],
        "checkpoint_dir": str(checkpoint_dir),
    }


def _complete_evidence(
    *,
    registry: Any,
    binding: Mapping[str, Any],
    binding_sha256: str,
    baseline_checkpoint: Mapping[str, Any],
    baseline_path: Path,
    checkpoint_dir: Path,
    checkpoints: Mapping[str, Mapping[str, Any]],
    schedule: Sequence[Any],
) -> dict[str, Any]:
    ordered = _validate_experiment_checkpoints(
        registry,
        checkpoints,
        binding_sha256=binding_sha256,
        schedule=schedule,
    )
    experiments: list[dict[str, Any]] = []
    for item in ordered:
        experiment_id = str(item["experiment_id"])
        checkpoint_path = checkpoint_dir / f"{experiment_id}.json"
        experiments.append(
            {
                **item,
                "checkpoint_file": str(checkpoint_path),
                "checkpoint_file_sha256": _sha256(checkpoint_path),
            }
        )
    return {
        "schema_version": 1,
        "mode": "complete-ablation-raw-evidence",
        "complete": True,
        "binding_sha256": binding_sha256,
        "binding": dict(binding),
        "baseline": {
            "checkpoint_file": str(baseline_path),
            "checkpoint_file_sha256": _sha256(baseline_path),
            "worker_payload_sha256": baseline_checkpoint["worker_payload_sha256"],
            "replay_command": baseline_checkpoint["replay_command"],
            "provenance": baseline_checkpoint["worker"]["provenance"],
        },
        "experiments": experiments,
        "exclusions": [
            {
                "subsystem": item.subsystem,
                "reason": item.reason,
                "evidence_field": item.evidence_field,
                "frozen_value": item.frozen_value,
            }
            for item in registry.exclusions
        ],
    }


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    (
        build_contract_schedule,
        _isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        _verify_carrier_checkout,
    ) = _project_imports()
    source_root = Path(args.source_root).resolve()
    registry_path = Path(args.registry).resolve()
    data_dir = Path(args.data_dir).resolve()
    registry = load_ablation_registry(registry_path)
    validate_ablation_registry(registry, source_root=source_root)
    schedule = build_contract_schedule(registry, source_root=source_root)
    experiments: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="uquant-phase2-ablation-") as temporary:
        temporary_root = Path(temporary)
        for index, experiment in enumerate(registry.experiments):
            destination = temporary_root / f"{index:02d}-{experiment.subsystem}"
            with isolated_carrier_checkout(
                registry,
                experiment,
                source_root=source_root,
                destination=destination,
            ) as checkout:
                probe = _probe_checkout(checkout.root, dict(checkout.config_changes))
                parent_runtime = _runtime()
                if probe.get("runtime") != {
                    name: parent_runtime[name]
                    for name in ("python_full_version", "numpy_version", "pandas_version")
                }:
                    raise RuntimeError("isolated ablation carrier runtime differs")
                experiments.append(
                    {
                        "experiment_id": experiment.experiment_id,
                        "subsystem": experiment.subsystem,
                        "carrier": {
                            "type": experiment.carrier.kind,
                            "sha256": experiment.carrier.sha256,
                            "changes": dict(experiment.carrier.changes),
                            "touched_paths": list(experiment.carrier.touched_paths),
                        },
                        "checkout": {
                            "base_commit": checkout.base_commit,
                            "experiment_commit": checkout.experiment_commit,
                            "source_sha256": checkout.source_sha256,
                            "tree_sha256": checkout.tree_sha256,
                            "clean": True,
                        },
                        "effective_config_sha256": probe["effective_config_sha256"],
                        "fresh_account_sha256": probe["fresh_account_sha256"],
                        "replay_command": _replay_command(
                            source_root=source_root,
                            registry_path=registry_path,
                            data_dir=data_dir,
                            experiment_id=experiment.experiment_id,
                        ),
                    }
                )
    return {
        "schema_version": 1,
        "mode": "carrier-validation",
        "passed": True,
        "registry_sha256": registry.payload_sha256,
        "source": {
            "base_commit": registry.base_commit,
            "production_source_sha256": registry.source_sha256,
        },
        "contracts": _contract_summary(schedule),
        "experiments": experiments,
        "exclusions": [
            {
                "subsystem": item.subsystem,
                "reason": item.reason,
                "evidence_field": item.evidence_field,
                "frozen_value": item.frozen_value,
            }
            for item in registry.exclusions
        ],
        "provenance": {
            "runner_sha256": _runner_sha256(registry_path),
            "uv_lock_sha256": _sha256(source_root / "uv.lock"),
            "runtime": _runtime(),
            "data": _data_provenance(data_dir),
        },
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Prepare/reuse baseline and execute at most one independent variant."""
    (
        build_contract_schedule,
        isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        verify_carrier_checkout,
    ) = _project_imports()
    source_root = Path(args.source_root).resolve()
    registry_path = Path(args.registry).resolve()
    data_dir = Path(args.data_dir).resolve()
    output = Path(args.output).resolve()
    checkpoint_dir = Path(args.checkpoint_dir).resolve()
    if _git_output(source_root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("ablation orchestration requires an exact clean source HEAD")
    registry = load_ablation_registry(registry_path)
    validate_ablation_registry(registry, source_root=source_root)
    schedule = build_contract_schedule(registry, source_root=source_root)
    binding = _execution_binding(
        registry=registry,
        registry_path=registry_path,
        source_root=source_root,
        data_dir=data_dir,
        schedule=schedule,
    )
    binding_sha256 = hashlib.sha256(_canonical_bytes(binding)).hexdigest()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = checkpoint_dir / "schedule.json"
    schedule_payload = {
        "schema_version": 1,
        "kind": "schedule",
        "binding_sha256": binding_sha256,
        "schedule_sha256": binding["schedule_sha256"],
        "cells": _schedule_rows(schedule),
    }
    if schedule_path.exists():
        observed_schedule = _read_checkpoint(
            schedule_path,
            binding_sha256=binding_sha256,
            kind="schedule",
        )
        if observed_schedule != schedule_payload:
            raise ValueError("ablation schedule checkpoint is stale")
    else:
        _write_checkpoint(schedule_path, schedule_payload)

    baseline_path = checkpoint_dir / "baseline.json"
    if baseline_path.exists():
        baseline_checkpoint = _load_baseline_checkpoint(
            baseline_path,
            binding_sha256=binding_sha256,
            schedule=schedule,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="uquant-phase2-baseline-") as temporary:
            checkout_destination = Path(temporary) / "checkout"
            worker_output = Path(temporary) / "worker.json"
            with isolated_baseline_checkout(
                registry,
                source_root=source_root,
                destination=checkout_destination,
            ) as checkout:
                checkout_provenance = _checkout_payload(checkout)
                probe = _probe_checkout(checkout.root, {})
                worker = _invoke_worker(
                    source_root=checkout.root,
                    data_dir=data_dir,
                    schedule_checkpoint=schedule_path,
                    binding_sha256=binding_sha256,
                    experiment_id="baseline",
                    config_changes={},
                    checkout=checkout_provenance,
                    output=worker_output,
                )
                if _git_output(checkout.root, "status", "--porcelain", "--untracked-files=all"):
                    raise ValueError("isolated baseline changed during replay")
                _validate_worker_payload(
                    worker,
                    schedule=schedule,
                    binding_sha256=binding_sha256,
                    experiment_id="baseline",
                )
                _validate_worker_provenance(
                    worker,
                    binding=binding,
                    checkout=checkout_provenance,
                    effective_config_sha256=str(probe["effective_config_sha256"]),
                    fresh_account_sha256=str(probe["fresh_account_sha256"]),
                )
        baseline_checkpoint = {
            "schema_version": 1,
            "kind": "baseline",
            "binding_sha256": binding_sha256,
            "worker_payload_sha256": hashlib.sha256(_canonical_bytes(worker)).hexdigest(),
            "replay_command": _baseline_replay_command(
                source_root=source_root,
                registry_path=registry_path,
                data_dir=data_dir,
                checkpoint_dir=checkpoint_dir,
                output=output,
            ),
            "worker": worker,
        }
        _write_checkpoint(baseline_path, baseline_checkpoint)

    selected = args.experiment or []
    if args.baseline_only and selected:
        raise ValueError("ablation baseline-only mode cannot select an experiment")
    if not args.baseline_only and len(selected) != 1:
        raise ValueError("ablation run requires exactly one --experiment per process")
    if selected:
        experiment_id = selected[0]
        matches = tuple(item for item in registry.experiments if item.experiment_id == experiment_id)
        if len(matches) != 1:
            raise ValueError("ablation experiment is not registered")
        experiment = matches[0]
        experiment_path = checkpoint_dir / f"{experiment.experiment_id}.json"
        previous: dict[str, Any] | None = None
        if experiment_path.exists():
            previous = _read_checkpoint(
                experiment_path,
                binding_sha256=binding_sha256,
                kind="experiment",
            )
        if previous is None or args.rerun:
            with tempfile.TemporaryDirectory(
                prefix=f"uquant-phase2-{experiment.experiment_id}-"
            ) as temporary:
                checkout_destination = Path(temporary) / "checkout"
                worker_output = Path(temporary) / "worker.json"
                with isolated_carrier_checkout(
                    registry,
                    experiment,
                    source_root=source_root,
                    destination=checkout_destination,
                ) as checkout:
                    checkout_provenance = _checkout_payload(checkout)
                    changes = dict(checkout.config_changes)
                    probe = _probe_checkout(checkout.root, changes)
                    variant_worker = _invoke_worker(
                        source_root=checkout.root,
                        data_dir=data_dir,
                        schedule_checkpoint=schedule_path,
                        binding_sha256=binding_sha256,
                        experiment_id=experiment.experiment_id,
                        config_changes=changes,
                        checkout=checkout_provenance,
                        output=worker_output,
                    )
                    verify_carrier_checkout(registry, experiment, checkout)
                    _validate_worker_payload(
                        variant_worker,
                        schedule=schedule,
                        binding_sha256=binding_sha256,
                        experiment_id=experiment.experiment_id,
                    )
                    _validate_worker_provenance(
                        variant_worker,
                        binding=binding,
                        checkout=checkout_provenance,
                        effective_config_sha256=str(probe["effective_config_sha256"]),
                        fresh_account_sha256=str(probe["fresh_account_sha256"]),
                    )
            experiment_checkpoint = {
                "schema_version": 1,
                "kind": "experiment",
                "binding_sha256": binding_sha256,
                "experiment_id": experiment.experiment_id,
                "subsystem": experiment.subsystem,
                "carrier_sha256": experiment.carrier.sha256,
                "worker_payload_sha256": hashlib.sha256(_canonical_bytes(variant_worker)).hexdigest(),
                "comparison": _compare_worker_payloads(
                    baseline_checkpoint["worker"],
                    variant_worker,
                ),
                "replay_command": _replay_command(
                    source_root=source_root,
                    registry_path=registry_path,
                    data_dir=data_dir,
                    checkpoint_dir=checkpoint_dir,
                    output=output,
                    experiment_id=experiment.experiment_id,
                ),
            }
            if previous is not None and previous != experiment_checkpoint:
                raise ValueError("ablation deterministic rerun differs from checkpoint")
            _write_checkpoint(experiment_path, experiment_checkpoint)

    available = _load_available_experiments(
        registry,
        checkpoint_dir=checkpoint_dir,
        binding_sha256=binding_sha256,
        schedule=schedule,
    )
    if len(available) != len(registry.experiments):
        return _progress_payload(
            registry=registry,
            binding=binding,
            binding_sha256=binding_sha256,
            checkpoint_dir=checkpoint_dir,
            baseline_path=baseline_path,
            completed=available,
        )
    return _complete_evidence(
        registry=registry,
        binding=binding,
        binding_sha256=binding_sha256,
        baseline_checkpoint=baseline_checkpoint,
        baseline_path=baseline_path,
        checkpoint_dir=checkpoint_dir,
        checkpoints=available,
        schedule=schedule,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_phase2_ablation.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--source-root", required=True)
        command.add_argument("--registry", required=True)
        command.add_argument("--data-dir", default="data/frozen")
        command.add_argument("--output", required=name == "run")
        if name == "run":
            command.add_argument("--experiment", action="append", default=None)
            command.add_argument("--baseline-only", action="store_true")
            command.add_argument("--checkpoint-dir", required=True)
            command.add_argument("--rerun", action="store_true")
    worker = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--source-root", required=True)
    worker.add_argument("--data-dir", required=True)
    worker.add_argument("--schedule-checkpoint", required=True)
    worker.add_argument("--binding-sha256", required=True)
    worker.add_argument("--experiment-id", required=True)
    worker.add_argument("--config-json", required=True)
    worker.add_argument("--checkout-json", required=True)
    worker.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate carriers or execute a sequential full-contract replay."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            payload = _validate(args)
        elif args.command == "worker":
            payload = _worker(args)
        else:
            payload = _run(args)
        encoded = _canonical_bytes(payload).decode()
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded + "\n", encoding="utf-8")
        if args.command != "worker":
            print(encoded)
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"phase2 ablation failed closed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
