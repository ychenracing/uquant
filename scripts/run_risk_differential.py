#!/usr/bin/env python3
"""Preregister and seal the Risk Differential Closure evidence plane."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess  # nosec B404 - fixed git/date commands, never shell execution
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.risk_counterfactual import POLICY_SET
from research.risk_differential import BOOLEAN_AXES, classify_boolean_axis
from research.risk_differential_models import (
    CapabilityRecord,
    canonical_bytes,
    canonical_sha256,
    hash_lock_files,
    hash_python_sources,
    hash_selected_sources,
    validate_capabilities,
    validate_registry_checkout,
)
from research.risk_replay_runtime import (
    ReplayCell,
    build_trade_data_view,
    run_trade_cell,
    run_uquant_cell,
)
from uquant.atomic_io import atomic_write_bytes, atomic_write_text

STARTING_MAIN = "ba314003044a229969270bee6854240dfb7f211e"
TRADE_COMMIT = "2066fbf0f99be94142c5d0cb0b6c99d276c2472d"
TRADE_FULL_SOURCE_SHA256 = "48280acee356ee4bd28fa83b260426f3025e6b3bd93c1cee2f92188486761b90"
TRADE_FULL_LOCK_SHA256 = "182d6bbfc2dba29d568f521ee765de335227e721e783a8a9a9cdfef436db7ba2"
TRADE_RISK_FILES = (
    "quantfusion/config/overlay.py",
    "quantfusion/risk/governance.py",
    "quantfusion/risk/managers.py",
    "quantfusion/risk/overlay/actions.py",
    "quantfusion/risk/overlay/adapter.py",
    "quantfusion/risk/overlay/evidence.py",
    "quantfusion/risk/overlay/policy.py",
    "quantfusion/risk/overlay/policy_base.py",
    "quantfusion/engine/sector_risk.py",
    "quantfusion/engine/universe_risk.py",
    "quantfusion/engine/market_regime.py",
    "quantfusion/engine/ensemble_orchestration.py",
    "quantfusion/application/stress.py",
    "regime_adaptive.py",
)


def _cap(
    identifier: str,
    source: str,
    category: str,
    mapping: str,
    action: str,
    base: tuple[str, ...] = (),
    sentinel: tuple[str, ...] = (),
    *,
    exact: bool = False,
    economic: bool = False,
    rationale: str,
) -> CapabilityRecord:
    return CapabilityRecord(
        capability_id=identifier,
        trade_source=(source,),
        category=category,
        uquant_base_equivalent=base,
        sentinel_equivalent=sentinel,
        mapping_status=mapping,
        action_classification=action,
        exact_transfer_possible=exact,
        economic_counterfactual_supported=economic,
        production_promotion_allowed_this_phase=False,
        rationale=rationale,
    )


def capability_inventory() -> tuple[CapabilityRecord, ...]:
    g = "quantfusion/risk/governance.py"
    p = "quantfusion/risk/overlay/policy_base.py"
    e = "quantfusion/risk/overlay/evidence.py"
    m = "quantfusion/risk/managers.py"
    return validate_capabilities(
        (
            _cap(
                "observation.warmup_health",
                g,
                "OBSERVATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/coverage.py",),
                rationale="both expose READY/DEGRADED/NOT_READY and fail closed",
            ),
            _cap(
                "observation.risk_opinion",
                g,
                "OBSERVATION",
                "PARTIAL_EQUIVALENT",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/risk.py",),
                sentinel=("uquant/risk_sentinel/opinion.py",),
                exact=True,
                rationale="standardized opinion exists but ownership differs",
            ),
            _cap(
                "observation.basket_coverage_confidence",
                g,
                "OBSERVATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/coverage.py",),
                rationale="same 0.45/0.35/0.20 dimensions",
            ),
            _cap(
                "calibration.risk_event_outcomes",
                g,
                "OFFLINE_CALIBRATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/calibration.py",),
                rationale="same 1/3/5/10/20-day offline outcome contract",
            ),
            _cap(
                "calibration.false_positive_opportunity_cost",
                g,
                "OFFLINE_CALIBRATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/calibration.py",),
                rationale="existing Sentinel calibration measures opportunity cost",
            ),
            _cap(
                "calibration.bull_silence",
                g,
                "OFFLINE_CALIBRATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/calibration.py",),
                rationale="existing Sentinel contract includes bull silence",
            ),
            _cap(
                "calibration.missed_shock",
                g,
                "OFFLINE_CALIBRATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/calibration.py",),
                rationale="existing Sentinel contract includes missed-shock depth",
            ),
            _cap(
                "observation.sleeve_agreement",
                g,
                "OBSERVATION",
                "INCREMENTAL_OBSERVATIONAL",
                "NON_TRANSFERABLE",
                rationale="trade sleeve topology has no unambiguous uquant analogue",
            ),
            _cap(
                "risk.market_velocity",
                e,
                "OBSERVATION",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/market_risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                exact=True,
                rationale="same causal index-velocity family",
            ),
            _cap(
                "risk.breadth_structure",
                e,
                "OBSERVATION",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/market_risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                exact=True,
                rationale="same breadth/MA structural family",
            ),
            _cap(
                "risk.ma_structure",
                e,
                "OBSERVATION",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/market_risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                rationale="MA20 structural damage already votes in Base and Sentinel",
            ),
            _cap(
                "risk.covariance_correlation",
                e,
                "OBSERVATION",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/market_risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                exact=True,
                rationale="correlation shock is a canonical family",
            ),
            _cap(
                "risk.volatility_expansion",
                e,
                "OBSERVATION",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/market_risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                rationale="volatility shock already feeds covariance_stress",
            ),
            _cap(
                "risk.leadership_damage",
                e,
                "OBSERVATION",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/market_risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                exact=True,
                rationale="leadership damage is a canonical family",
            ),
            _cap(
                "risk.live_book_damage",
                e,
                "OBSERVATION",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/market_risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                exact=True,
                rationale="live-book damage is present and not backfilled",
            ),
            _cap(
                "risk.capital_damage",
                m,
                "RISK_STATE",
                "ABSORBED_BASE",
                "DIRECTLY_REPLAYABLE",
                base=("uquant/risk.py",),
                sentinel=("uquant/risk_sentinel/evidence.py",),
                exact=True,
                rationale="Base owns capital high-water and Sentinel reads it only",
            ),
            _cap(
                "observation.subindustry_equal_weighting",
                e,
                "OBSERVATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/evidence.py",),
                rationale="Sentinel already aggregates subindustries equally",
            ),
            _cap(
                "observation.weakest_cluster",
                e,
                "OBSERVATION",
                "ABSORBED_SENTINEL",
                "DIRECTLY_REPLAYABLE",
                sentinel=("uquant/risk_sentinel/evidence.py",),
                rationale="Sentinel emits weakest_subindustries",
            ),
            _cap(
                "admission.block_new_entries",
                "quantfusion/risk/overlay/adapter.py",
                "ADMISSION_GATE",
                "PARTIAL_EQUIVALENT",
                "TRANSLATABLE",
                base=("uquant/portfolio.py",),
                sentinel=("uquant/risk_sentinel/integration.py",),
                exact=True,
                economic=True,
                rationale="maps to existing freeze without new authority",
            ),
            _cap(
                "admission.block_pyramids",
                "quantfusion/risk/overlay/adapter.py",
                "ADMISSION_GATE",
                "PARTIAL_EQUIVALENT",
                "TRANSLATABLE",
                base=("uquant/portfolio.py",),
                sentinel=("uquant/risk_sentinel/integration.py",),
                exact=True,
                economic=True,
                rationale="same-symbol additions can be clamped research-only",
            ),
            _cap(
                "exposure.recommended_gross_cap",
                g,
                "EXPOSURE_POLICY",
                "REJECTED_PREVIOUSLY",
                "TRANSLATABLE",
                base=("uquant/risk.py",),
                exact=True,
                economic=True,
                rationale="Base owns caps; Sentinel Phase 5 gross-cap authority was rejected",
            ),
            _cap(
                "exposure.graded_trim",
                p,
                "EXPOSURE_POLICY",
                "INCREMENTAL_EXECUTION_POLICY",
                "NON_TRANSFERABLE",
                rationale="depends on trade sleeve scoring and emits sells",
            ),
            _cap(
                "exposure.transition_trim",
                "quantfusion/engine/market_regime.py",
                "EXPOSURE_POLICY",
                "PARTIAL_EQUIVALENT",
                "HYBRID_DIAGNOSTIC",
                base=("uquant/risk_sector.py",),
                rationale="uquant has transition/sector reductions but no identical ranking",
            ),
            _cap(
                "exposure.concentration_cluster_guard",
                p,
                "EXPOSURE_POLICY",
                "PARTIAL_EQUIVALENT",
                "HYBRID_DIAGNOSTIC",
                base=("uquant/portfolio_core.py",),
                rationale="concentration is guarded but trade ranking is not transferable",
            ),
            _cap(
                "exit.catastrophe_stop",
                e,
                "SYMBOL_EXIT_POLICY",
                "ABSORBED_ARCHITECTURALLY",
                "TRANSLATABLE",
                base=("uquant/risk.py", "uquant/execution.py"),
                exact=True,
                economic=True,
                rationale="uquant has causal risk exits and next-open execution; threshold remains research-only",
            ),
            _cap(
                "exit.cost_absolute_stop",
                e,
                "SYMBOL_EXIT_POLICY",
                "PARTIAL_EQUIVALENT",
                "TRANSLATABLE",
                base=("uquant/portfolio.py",),
                exact=True,
                economic=True,
                rationale="transfer is mechanically defined but not existing Sentinel authority",
            ),
            _cap(
                "exit.atr_chandelier",
                e,
                "SYMBOL_EXIT_POLICY",
                "PARTIAL_EQUIVALENT",
                "TRANSLATABLE",
                base=("uquant/portfolio.py",),
                exact=True,
                economic=True,
                rationale="Wilder ATR line can be reproduced without trade imports",
            ),
            _cap(
                "exit.profit_tier_giveback",
                e,
                "SYMBOL_EXIT_POLICY",
                "ABSORBED_ARCHITECTURALLY",
                "TRANSLATABLE",
                base=("uquant/portfolio.py", "uquant/config.py"),
                exact=True,
                economic=True,
                rationale="uquant already has lifecycle profit protection; exact trade line stays shadow",
            ),
            _cap(
                "cooldown.catastrophe",
                "quantfusion/risk/overlay/adapter.py",
                "COOLDOWN_POLICY",
                "ABSORBED_ARCHITECTURALLY",
                "NON_TRANSFERABLE",
                base=("uquant/types.py", "uquant/risk.py"),
                rationale="uquant already owns cooldown; a second symbol cooldown is forbidden",
            ),
            _cap(
                "risk.re_shock_persistence",
                p,
                "RISK_STATE",
                "ABSORBED_BASE",
                "NON_TRANSFERABLE",
                base=("uquant/risk.py",),
                rationale="Base owns shock/recovery persistence",
            ),
            _cap(
                "risk.recovery",
                m,
                "RISK_STATE",
                "ABSORBED_BASE",
                "NON_TRANSFERABLE",
                base=("uquant/risk.py", "uquant/risk_sector.py"),
                rationale="Base owns the sole recovery state machine",
            ),
            _cap(
                "execution.risk_owner",
                p,
                "EXECUTION_OWNERSHIP",
                "ABSORBED_ARCHITECTURALLY",
                "NON_TRANSFERABLE",
                base=("uquant/risk.py", "uquant/execution.py"),
                rationale="uquant already has one owner; overlay ownership cannot be copied",
            ),
            _cap(
                "exposure.structural_shock_trim",
                p,
                "EXPOSURE_POLICY",
                "REJECTED_PREVIOUSLY",
                "NON_TRANSFERABLE",
                rationale="trade disables legacy shock trim by default and Sentinel cannot sell",
            ),
        )
    )


def _write(path: Path, payload: dict[str, Any], *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=(",", ":") if compact else None,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
    )


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["payload_sha256"] = canonical_sha256(payload)
    return payload


def _adapter_cache_identity(root: Path) -> str:
    """Bind cell caches to every local normalization/contract input."""

    paths = (
        root / "scripts/run_risk_differential.py",
        root / "research/risk_differential.py",
        root / "research/risk_differential_models.py",
        root / "research/risk_replay_runtime.py",
        root / "benchmarks/risk_differential_contract.json",
        root / "benchmarks/risk_differential_source_registry.json",
    )
    return canonical_sha256(
        {"inputs": [
            {"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in paths
        ]}
    )


def _run_replay_cell(
    args: tuple[ReplayCell, str, str, str, dict[str, Any] | None, dict[str, Any] | None]
) -> dict[str, Any]:
    cell, data_dir, trade_root, data_view, cached_uquant, cached_trade = args
    uquant = cached_uquant or run_uquant_cell(cell, Path(data_dir))
    trade = cached_trade or run_trade_cell(cell, Path(trade_root), Path(data_view))
    if uquant["dates"] != trade["dates"]:
        raise RuntimeError(
            f"calendar mismatch for {cell.cell_id}: "
            f"uquant={len(uquant['dates'])}, trade={len(trade['dates'])}"
        )
    return {
        "cell": asdict(cell),
        "uquant": uquant,
        "trade": trade,
        "runtime_identity": {"python_hash_seed": os.environ.get("PYTHONHASHSEED")},
    }


def seal_trade_trace(root: Path, trade_root: Path) -> None:
    """Seal the already executed pinned challenger trace as a deterministic input."""

    registry = json.loads((root / "benchmarks/risk_differential_source_registry.json").read_text())
    validate_registry_checkout(trade_root, registry["trade"])
    cells: dict[str, dict[str, Any]] = {}
    for cache in sorted((root / ".risk_differential_runtime/cells").glob("*.json")):
        payload = json.loads(cache.read_text(encoding="utf-8"))
        cell_id = str(payload["cell"]["cell_id"])
        cells[cell_id] = {
            "cell_id": cell_id,
            "source_cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
            "trace": payload["trade"],
        }
    expected, _ = _replay_cells(root, "all")
    expected_ids = {cell.cell_id for cell in expected}
    if set(cells) != expected_ids:
        raise RuntimeError(
            f"pinned challenger trace coverage mismatch: {len(cells)} != {len(expected_ids)}"
        )
    sealed = _seal(
        {
            "schema_version": 1,
            "trade_commit": registry["trade"]["commit"],
            "trade_source_sha256": registry["trade"]["risk_source_sha256"],
            "source_registry_sha256": registry["payload_sha256"],
            "generation_note": (
                "materialized from the completed pinned-source replay before outcome analysis"
            ),
            "cells": [cells[cell_id] for cell_id in sorted(cells)],
        }
    )
    target = root / "artifacts/sentinel/risk_differential/trade_challenger_trace.json.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target, gzip.compress(canonical_bytes(sealed), compresslevel=9, mtime=0))


def _replay_cells(root: Path, scope: str) -> tuple[list[ReplayCell], list[dict[str, Any]]]:
    matrix = json.loads((root / "benchmarks/current_heads_competitor_matrix.json").read_text())
    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for item in matrix["cells"]:
        if item["system"] not in {"uquant", "trade"}:
            continue
        key = (str(item["axis"]), str(item["window"]), str(item["name"]))
        grouped.setdefault(key, {})[str(item["system"])] = item
    cells: list[ReplayCell] = []
    excluded: list[dict[str, Any]] = []
    for key in sorted(grouped):
        pair = grouped[key]
        uquant = pair.get("uquant")
        trade = pair.get("trade")
        if not uquant or not trade:
            raise RuntimeError(f"comparison pair is incomplete: {key}")
        if scope == "official" and key[0] != "official_pool":
            continue
        if uquant["status"] != "SUCCESS" or trade["status"] != "SUCCESS":
            excluded.append(
                {
                    "cell_id": "/".join(key),
                    "axis": key[0],
                    "window": key[1],
                    "universe": key[2],
                    "status": "INSUFFICIENT_SAMPLE"
                    if "INSUFFICIENT_SAMPLE" in {uquant["status"], trade["status"]}
                    else "SOURCE_REPLAY_ERROR",
                    "source_status": {"uquant": uquant["status"], "trade": trade["status"]},
                }
            )
            continue
        symbols = tuple(str(item) for item in uquant["effective_symbols"])
        cells.append(
            ReplayCell(
                cell_id="/".join(key),
                axis=key[0],
                window=key[1],
                universe=key[2],
                family=str(uquant["family"]),
                symbols=symbols,
                start=str(uquant["start"]),
                end=str(uquant["end"]),
            )
        )
    return cells, excluded


def _trace_fact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": row["status"],
        "confidence": row["confidence"],
        "severity_rank": row["severity_rank"],
        "level": row["level"],
        "families": {axis: row[axis] for axis in BOOLEAN_AXES[:7]},
        "block_new_entries": row["block_new_entries"],
        "block_pyramiding": row["block_pyramiding"],
        "recommended_gross_cap": row["recommended_gross_cap"],
        "weakest_clusters": row["weakest_clusters"],
        "action_candidates": row["action_candidates"],
        "execution_owner": row["execution_owner"],
    }


def _classify_scalar(trade: Any, base: Any, sentinel: Any, *, risk_predicate: Any) -> str:
    if None in (trade, base, sentinel):
        return "NOT_COMPARABLE"
    if trade == base == sentinel:
        return "AGREE_ALL"
    return classify_boolean_axis(
        trade=bool(risk_predicate(trade)),
        base=bool(risk_predicate(base)),
        sentinel=bool(risk_predicate(sentinel)),
    )


def replay(root: Path, *, trade_root: Path, workers: int, scope: str) -> None:
    registry = json.loads((root / "benchmarks/risk_differential_source_registry.json").read_text())
    capability = json.loads((root / "benchmarks/risk_capability_registry.json").read_text())
    validate_registry_checkout(trade_root, registry["trade"])
    cells, excluded = _replay_cells(root, scope)
    runtime = root / ".risk_differential_runtime"
    data_view = runtime / "trade_data"
    build_trade_data_view(root / "data/frozen", data_view)
    cache_dir = runtime / "cells" / _adapter_cache_identity(root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    trace_path = root / "artifacts/sentinel/risk_differential/trade_challenger_trace.json.gz"
    if not trace_path.is_file():
        raise RuntimeError("sealed challenger trace is required; run seal-trade-trace first")
    trace_payload = json.loads(gzip.decompress(trace_path.read_bytes()))
    if trace_payload["payload_sha256"] != canonical_sha256(trace_payload):
        raise RuntimeError("sealed challenger trace has an invalid canonical seal")
    if trace_payload["source_registry_sha256"] != registry["payload_sha256"]:
        raise RuntimeError("sealed challenger trace is not source-registry bound")
    reusable_trade = {item["cell_id"]: item["trace"] for item in trace_payload["cells"]}
    reusable_uquant: dict[str, dict[str, Any]] = {}
    for old_cache in sorted((runtime / "cells").rglob("*.json")):
        try:
            old_payload = json.loads(old_cache.read_text(encoding="utf-8"))
            if old_payload.get("runtime_identity", {}).get("python_hash_seed") != "0":
                continue
            old_cell_id = str(old_payload["cell"]["cell_id"])
            old_uquant = old_payload["uquant"]
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        existing = reusable_uquant.get(old_cell_id)
        if existing is not None and canonical_sha256(existing) != canonical_sha256(old_uquant):
            raise RuntimeError(f"conflicting deterministic uquant cache for {old_cell_id}")
        reusable_uquant[old_cell_id] = old_uquant
    pending: list[ReplayCell] = []
    results: list[dict[str, Any]] = []
    for cell in cells:
        cache = cache_dir / f"{hashlib.sha256(cell.cell_id.encode()).hexdigest()}.json"
        if cache.is_file():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("runtime_identity", {}).get("python_hash_seed") == "0":
                results.append(cached)
            else:
                pending.append(cell)
        else:
            pending.append(cell)
    args = [
        (
            cell,
            str(root / "data/frozen"),
            str(trade_root),
            str(data_view),
            reusable_uquant.get(cell.cell_id),
            reusable_trade.get(cell.cell_id),
        )
        for cell in pending
    ]
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_run_replay_cell, item): item[0] for item in args}
        for index, future in enumerate(as_completed(futures), start=1):
            cell = futures[future]
            result = future.result()
            cache = cache_dir / f"{hashlib.sha256(cell.cell_id.encode()).hexdigest()}.json"
            _write(cache, result)
            results.append(result)
            print(f"[{index}/{len(pending)}] {cell.cell_id}", flush=True)
    contract = json.loads((root / "benchmarks/risk_differential_contract.json").read_text())
    axes = tuple(contract["axes"])
    classifications = (
        "AGREE_ALL",
        "TRADE_ONLY",
        "BASE_ONLY",
        "SENTINEL_ONLY",
        "TRADE_AND_SENTINEL_NOT_BASE",
        "TRADE_AND_BASE_NOT_SENTINEL",
        "BASE_AND_SENTINEL_NOT_TRADE",
        "NOT_COMPARABLE",
    )
    axis_counts: dict[str, Counter[str]] = {axis: Counter() for axis in axes}
    axis_counts["warning_level"] = Counter()
    exclusive: list[dict[str, Any]] = []
    sealed_cells: list[dict[str, Any]] = []
    for result in sorted(results, key=lambda item: item["cell"]["cell_id"]):
        cell = result["cell"]
        days = []
        equity_scale = float(result["uquant"]["portfolio_equity"][0])
        if equity_scale <= 0:
            raise RuntimeError(f"invalid initial equity for {cell['cell_id']}")
        for index, date in enumerate(result["uquant"]["dates"]):
            base = result["uquant"]["base"][index]
            sentinel = result["uquant"]["sentinel"][index]
            trade = result["trade"]["trade"][index]
            classifications_for_day: dict[str, str] = {}
            for axis in BOOLEAN_AXES:
                classification = classify_boolean_axis(
                    trade=trade[axis], base=base[axis], sentinel=sentinel[axis]
                )
                classifications_for_day[axis] = classification
                axis_counts[axis][classification] += 1
            warning = _classify_scalar(
                trade["severity_rank"],
                base["severity_rank"],
                sentinel["severity_rank"],
                risk_predicate=lambda value: int(value) > 0,
            )
            classifications_for_day["warning_level"] = warning
            axis_counts["warning_level"][warning] += 1
            gross = _classify_scalar(
                trade["recommended_gross_cap"],
                base["recommended_gross_cap"],
                sentinel["recommended_gross_cap"],
                risk_predicate=lambda value: float(value) < 1.0,
            )
            classifications_for_day["recommended_gross_cap"] = gross
            axis_counts["recommended_gross_cap"][gross] += 1
            for axis in axes:
                if axis not in classifications_for_day:
                    classifications_for_day[axis] = "NOT_COMPARABLE"
                    axis_counts[axis]["NOT_COMPARABLE"] += 1
            action = result["uquant"]["actionability"].get(date, {"buy": 0, "pyramid": 0, "gross": 0.0})
            for axis, classification in classifications_for_day.items():
                if classification not in {"TRADE_ONLY", "TRADE_AND_SENTINEL_NOT_BASE"}:
                    continue
                exclusive.append(
                    {
                        "event_id": f"{cell['cell_id']}:{date}:{axis}",
                        "date": date,
                        "axis": axis,
                        "classification": classification,
                        "window": cell["window"],
                        "universe": cell["universe"],
                        "family": cell["family"],
                        "trade_level": trade["level"],
                        "trade_confidence": trade["confidence"],
                        "actionable_buy_intents": int(action["buy"]),
                        "actionable_pyramid_intents": int(action["pyramid"]),
                        "existing_gross_exposure": float(action["gross"]),
                        "base_already_protected": bool(
                            base["block_new_entries"]
                            or base["block_pyramiding"]
                            or float(base["recommended_gross_cap"]) < 1.0
                        ),
                        "base_gross_cap": base["recommended_gross_cap"],
                        "sentinel_gross_suggestion": sentinel["recommended_gross_cap"],
                        "trade_gross_suggestion": trade["recommended_gross_cap"],
                        "outcome_identity": {f"{horizon}d": None for horizon in contract["outcome_horizons"]},
                    }
                )
            days.append(
                {
                    "date": date,
                    "base": _trace_fact(base),
                    "sentinel": _trace_fact(sentinel),
                    "trade": _trace_fact(trade),
                    "classification": classifications_for_day,
                    "actionability": action,
                    "portfolio_equity": result["uquant"]["portfolio_equity"][index] / equity_scale,
                }
            )
        sealed_cells.append(
            {
                **cell,
                "status": "SUCCESS",
                "sessions": len(days),
                "decision_digest_sha256": result["uquant"]["decision_digest_sha256"],
                "warmup_health": result["trade"]["warmup_health"],
                "days": days,
            }
        )
    sealed_cells.extend(excluded)
    provenance = {
        "uquant_starting_commit": registry["uquant"]["commit"],
        "trade_commit": registry["trade"]["commit"],
        "source_registry_sha256": registry["payload_sha256"],
        "contract_sha256": contract["payload_sha256"],
        "capability_registry_sha256": capability["payload_sha256"],
        "market_data_prefix_sha256": hashlib.sha256(
            b"".join(
                path.name.encode() + path.read_bytes()
                for path in sorted((root / "data/frozen").glob("*.csv"))
            )
        ).hexdigest(),
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
        },
        "adapter_sha256": _adapter_cache_identity(root),
        "sealed_trade_challenger_trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        "scope": scope,
    }
    target = root / "artifacts/sentinel/risk_differential"
    _write(
        target / "capability_inventory.json",
        _seal(
            {
                "schema_version": 1,
                "provenance": provenance,
                "capabilities": capability["capabilities"],
                "counts": dict(
                    Counter(item["mapping_status"] for item in capability["capabilities"])
                ),
            }
        ),
    )
    daily_payload = _seal(
        {
            "schema_version": 1,
            "provenance": provenance,
            "cells": [
                {"cell_id": item["cell_id"], "days": item["days"]}
                for item in sealed_cells
                if item.get("status") == "SUCCESS"
            ],
        }
    )
    daily_bytes = gzip.compress(canonical_bytes(daily_payload), compresslevel=9, mtime=0)
    daily_sha256 = hashlib.sha256(daily_bytes).hexdigest()
    atomic_write_bytes(target / "risk_differential_daily.json.gz", daily_bytes)
    matrix_cells = []
    for item in sealed_cells:
        if item.get("status") != "SUCCESS":
            matrix_cells.append(item)
            continue
        days = item["days"]
        matrix_cells.append(
            {
                **{key: value for key, value in item.items() if key != "days"},
                "daily_trace_sha256": hashlib.sha256(canonical_bytes(days)).hexdigest(),
                "daily_trace_artifact": "risk_differential_daily.json.gz",
            }
        )
    _write(
        target / "risk_differential_matrix.json",
        _seal(
            {
                "schema_version": 1,
                "provenance": provenance,
                "summary": {
                    "cells": len(sealed_cells),
                    "daily_trace_pairs": len(results),
                    "sessions": sum(item.get("sessions", 0) for item in sealed_cells),
                    "status": "COMPLETE" if scope == "all" else "OFFICIAL_SCOPE_COMPLETE",
                    "daily_trace_artifact_sha256": daily_sha256,
                },
                "axis_counts": {
                    axis: {name: int(axis_counts[axis].get(name, 0)) for name in classifications}
                    for axis in (*axes, "warning_level")
                },
                "cells": matrix_cells,
            }
        ),
        compact=True,
    )
    _write(
        target / "exclusive_events.json",
        _seal(
            {
                "schema_version": 1,
                "provenance": provenance,
                "events_frozen_before_outcome_analysis": True,
                "status": "FROZEN",
                "events": sorted(exclusive, key=lambda item: item["event_id"]),
            }
        ),
    )


def preregister(
    root: Path,
    *,
    baseline_root: Path,
    trade_root: Path,
    frozen_at_utc: str | None = None,
) -> None:
    current = json.loads((root / "benchmarks/current_heads_comparison_contract.json").read_text())
    contract = _seal(
        {
            "schema_version": 1,
            "contract_id": "risk-differential-closure-v1",
            "source_matrix_contract_sha256": current["payload_sha256"],
            "axes": [
                "market_velocity",
                "breadth_structure",
                "covariance_stress",
                "leadership_damage",
                "live_book_damage",
                "capital_damage",
                "concentration_damage",
                "block_new_entries",
                "block_pyramiding",
                "recommended_gross_cap",
                "layered_protection",
                "cluster_trim",
                "cooldown_or_reentry_lock",
                "execution_owner",
            ],
            "outcome_horizons": [1, 3, 5, 10, 20],
            "shock_definition": {"horizon_sessions": 20, "portfolio_drawdown_lte": -0.08},
            "episode_merge_sessions": 5,
            "minimum_exclusive_episodes": 5,
            "minimum_distinct_windows": 2,
            "minimum_universe_families": 2,
            "official_pools": current["official_pools"],
            "windows": current["windows"],
            "parameter_search_allowed": False,
        }
    )
    _write(root / "benchmarks/risk_differential_contract.json", contract)
    risk_source_sha = hash_selected_sources(trade_root, TRADE_RISK_FILES)
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("git executable is required to bind the starting commit")
    uquant_sha = subprocess.run(  # nosec B603 - absolute git executable and fixed argv
        [git_executable, "rev-parse", "HEAD"],
        cwd=baseline_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    trade_sha = (trade_root / ".frozen_commit").read_text(encoding="utf-8").strip()
    if uquant_sha != STARTING_MAIN or trade_sha != TRADE_COMMIT:
        raise RuntimeError("source checkout moved after the closure baseline freeze")
    existing_registry = root / "benchmarks/risk_differential_source_registry.json"
    if frozen_at_utc is None and existing_registry.is_file():
        frozen_at_utc = json.loads(existing_registry.read_text())["frozen_at_utc"]
    if frozen_at_utc is None:
        frozen_at_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry = _seal(
        {
            "schema_version": 1,
            "registry_id": "risk-differential-closure-v1",
            "frozen_at_utc": frozen_at_utc,
            "uquant": {
                "repository": "ychenracing/uquant",
                "commit": uquant_sha,
                "python_source_sha256": hash_python_sources(baseline_root),
                "lock_sha256": hash_lock_files(
                    baseline_root, ("pyproject.toml", "requirements.txt", "uv.lock")
                ),
            },
            "trade": {
                "repository": "ychenracing/trade",
                "commit": trade_sha,
                "python_source_sha256": TRADE_FULL_SOURCE_SHA256,
                "python_source_identity_source": "sealed current-heads registry at identical commit",
                "lock_sha256": TRADE_FULL_LOCK_SHA256,
                "risk_source_files": list(TRADE_RISK_FILES),
                "risk_source_sha256": risk_source_sha,
                "read_only": True,
            },
        }
    )
    _write(root / "benchmarks/risk_differential_source_registry.json", registry)
    capabilities = capability_inventory()
    capability_payload = _seal(
        {
            "schema_version": 1,
            "registry_id": "trade-risk-capability-inventory-v1",
            "trade_commit": TRADE_COMMIT,
            "capabilities": [
                {**record.__dict__}
                if hasattr(record, "__dict__")
                else {
                    "capability_id": record.capability_id,
                    "trade_source": list(record.trade_source),
                    "category": record.category,
                    "uquant_base_equivalent": list(record.uquant_base_equivalent),
                    "sentinel_equivalent": list(record.sentinel_equivalent),
                    "mapping_status": record.mapping_status,
                    "action_classification": record.action_classification,
                    "exact_transfer_possible": record.exact_transfer_possible,
                    "economic_counterfactual_supported": record.economic_counterfactual_supported,
                    "production_promotion_allowed_this_phase": False,
                    "rationale": record.rationale,
                }
                for record in capabilities
            ],
        }
    )
    _write(root / "benchmarks/risk_capability_registry.json", capability_payload)
    policy_identity: dict[str, object] = {
        "policies": [asdict(item) for item in POLICY_SET],
    }
    holdout_identity = _seal(
        {
            "schema_version": 1,
            "identity_id": "risk-differential-future-holdout-v1",
            "activation_session": "2026-08-24",
            "uquant_source_commit": STARTING_MAIN,
            "trade_source_commit": TRADE_COMMIT,
            "trade_python_source_sha256": TRADE_FULL_SOURCE_SHA256,
            "risk_differential_contract_sha256": contract["payload_sha256"],
            "capability_registry_sha256": capability_payload["payload_sha256"],
            "counterfactual_policy_set_sha256": canonical_sha256(policy_identity),
            "effective_config_sha256": "ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13",
            "data_contract_sha256": "f1555d2f5527b83899ade8f934f67de8df6050aa2ebc7453d0d4245c618e2aeb",
            "parameter_changes_from_observation": False,
            "production_authority_changes_from_observation": False,
            "no_backfill": True,
        }
    )
    _write(root / "benchmarks/risk_differential_holdout_identity.json", holdout_identity)


def seal_initial_evidence(root: Path) -> None:
    registry = json.loads((root / "benchmarks/risk_differential_source_registry.json").read_text())
    contract = json.loads((root / "benchmarks/risk_differential_contract.json").read_text())
    capability = json.loads((root / "benchmarks/risk_capability_registry.json").read_text())
    matrix = json.loads((root / "benchmarks/current_heads_competitor_matrix.json").read_text())
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in matrix["cells"]:
        if item["system"] not in {"uquant", "trade"}:
            continue
        key = (item["axis"], item["window"], item["name"])
        cells.setdefault(key, {})[item["system"]] = {
            "status": item["status"],
            "metrics": item["metrics"],
            "provenance": item["provenance"],
        }
    rows: list[dict[str, Any]] = []
    for key in sorted(cells):
        pair = cells[key]
        rows.append(
            {
                "cell_id": "/".join(key),
                "axis": key[0],
                "window": key[1],
                "universe": key[2],
                "uquant": pair.get("uquant"),
                "trade": pair.get("trade"),
                "daily_trace_status": "PINNED_REPLAY_REQUIRED",
                "differential_status": "NOT_COMPARABLE",
            }
        )
    axis_counts = {
        axis: {
            "AGREE_ALL": 0,
            "TRADE_ONLY": 0,
            "BASE_ONLY": 0,
            "SENTINEL_ONLY": 0,
            "TRADE_AND_SENTINEL_NOT_BASE": 0,
            "TRADE_AND_BASE_NOT_SENTINEL": 0,
            "BASE_AND_SENTINEL_NOT_TRADE": 0,
            "NOT_COMPARABLE": len(rows),
        }
        for axis in contract["axes"]
    }
    provenance = {
        "uquant_starting_commit": registry["uquant"]["commit"],
        "trade_commit": registry["trade"]["commit"],
        "source_registry_sha256": registry["payload_sha256"],
        "contract_sha256": contract["payload_sha256"],
        "capability_registry_sha256": capability["payload_sha256"],
        "current_heads_matrix_sha256": matrix["payload_sha256"],
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    target = root / "artifacts/sentinel/risk_differential"
    _write(
        target / "capability_inventory.json",
        _seal(
            {
                "schema_version": 1,
                "provenance": provenance,
                "capabilities": capability["capabilities"],
                "counts": dict(Counter(x["mapping_status"] for x in capability["capabilities"])),
            }
        ),
    )
    _write(
        target / "risk_differential_matrix.json",
        _seal(
            {
                "schema_version": 1,
                "provenance": provenance,
                "summary": {
                    "cells": len(rows),
                    "ready_metric_pairs": sum(
                        bool(r["uquant"])
                        and bool(r["trade"])
                        and isinstance(r["uquant"], dict)
                        and isinstance(r["trade"], dict)
                        and r["uquant"]["status"] == r["trade"]["status"] == "SUCCESS"
                        for r in rows
                    ),
                    "daily_trace_pairs": 0,
                    "status": "PINNED_REPLAY_REQUIRED",
                },
                "axis_counts": axis_counts,
                "cells": rows,
            }
        ),
    )
    _write(
        target / "exclusive_events.json",
        _seal(
            {
                "schema_version": 1,
                "provenance": provenance,
                "events_frozen_before_outcome_analysis": True,
                "events": [],
                "status": "INSUFFICIENT_COMPARABLE_TRACE",
            }
        ),
    )
    (target / "README.md").write_text(
        "# Risk Differential Closure\n\nResearch-only sealed evidence. Production risk authority is unchanged.\n",
        encoding="utf-8",
    )


def main() -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        environment = {**os.environ, "PYTHONHASHSEED": "0"}
        os.execve(  # nosec B606 - exact current interpreter, no shell
            sys.executable, [sys.executable, *sys.argv], environment
        )
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("preregister", "seal-initial-evidence", "seal-trade-trace", "replay")
    )
    parser.add_argument("--baseline-root", type=Path, default=root)
    parser.add_argument("--trade-root", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--scope", choices=("official", "all"), default="all")
    parser.add_argument("--frozen-at-utc")
    args = parser.parse_args()
    if args.command in {"preregister", "seal-trade-trace", "replay"} and args.trade_root is None:
        parser.error("--trade-root is required")
    if args.command == "preregister":
        preregister(
            root,
            baseline_root=args.baseline_root,
            trade_root=args.trade_root,
            frozen_at_utc=args.frozen_at_utc,
        )
    elif args.command == "seal-initial-evidence":
        seal_initial_evidence(root)
    elif args.command == "seal-trade-trace":
        seal_trade_trace(root, args.trade_root)
    else:
        replay(root, trade_root=args.trade_root, workers=args.workers, scope=args.scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
