#!/usr/bin/env python3
"""Seal preregistered outcomes, economics, promotion gates, and closure."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any, cast

import pandas as pd

from research.risk_counterfactual import POLICY_SET, classify_promotion
from research.risk_differential import forward_outcomes, merge_episodes
from research.risk_differential_models import canonical_bytes, canonical_sha256
from uquant.atomic_io import atomic_write_text


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    payload["payload_sha256"] = canonical_sha256(payload)
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )


def _validate_analysis_inputs(
    matrix: dict[str, Any],
    daily: dict[str, Any],
    daily_gzip: bytes,
    exclusive: dict[str, Any],
    raw: dict[str, Any],
    negative_controls: dict[str, Any],
    capability: dict[str, Any],
) -> None:
    artifacts = {
        "matrix": matrix,
        "daily": daily,
        "frozen exclusive": exclusive,
        "counterfactual raw": raw,
        "negative controls": negative_controls,
        "capability registry": capability,
    }
    for name, payload in artifacts.items():
        if payload.get("payload_sha256") != canonical_sha256(payload):
            raise RuntimeError(f"{name} canonical seal is invalid")
    try:
        compressed_payload = json.loads(gzip.decompress(daily_gzip))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("daily gzip payload is invalid") from exc
    if compressed_payload != daily:
        raise RuntimeError("daily gzip-to-payload binding is invalid")

    if not exclusive.get("events_frozen_before_outcome_analysis", False):
        raise RuntimeError("exclusive events are not frozen before outcome analysis")
    if exclusive.get("outcomes_filled_after_identity_freeze", False):
        raise RuntimeError("exclusive input is already outcome-filled rather than frozen")

    provenance_keys = (
        "contract_sha256",
        "capability_registry_sha256",
        "source_registry_sha256",
        "adapter_sha256",
        "market_data_prefix_sha256",
        "sealed_trade_challenger_trace_sha256",
        "trade_commit",
        "uquant_starting_commit",
    )
    matrix_provenance = matrix.get("provenance", {})
    for name, payload in (("daily", daily), ("frozen exclusive", exclusive)):
        provenance = payload.get("provenance", {})
        if any(provenance.get(key) != matrix_provenance.get(key) for key in provenance_keys):
            raise RuntimeError(f"matrix-to-{name} provenance binding is invalid")
    if matrix_provenance.get("capability_registry_sha256") != capability["payload_sha256"]:
        raise RuntimeError("matrix-to-capability-registry binding is invalid")
    if matrix_provenance.get("trade_commit") != capability.get("trade_commit"):
        raise RuntimeError("capability-registry trade commit binding is invalid")

    raw_provenance = raw.get("provenance", {})
    if raw_provenance.get("risk_differential_matrix_sha256") != matrix["payload_sha256"]:
        raise RuntimeError("raw-to-matrix binding is invalid")
    if raw_provenance.get("daily_trace_gzip_sha256") != hashlib.sha256(daily_gzip).hexdigest():
        raise RuntimeError("raw-to-daily-gzip binding is invalid")
    if raw_provenance.get("frozen_exclusive_events_sha256") != exclusive["payload_sha256"]:
        raise RuntimeError("raw-to-frozen-exclusive binding is invalid")
    for key in ("trade_commit", "uquant_starting_commit"):
        if raw_provenance.get(key) != matrix_provenance.get(key):
            raise RuntimeError(f"raw-to-matrix {key} binding is invalid")

    matrix_cells = {
        item["cell_id"] for item in matrix.get("cells", ()) if item.get("status") == "SUCCESS"
    }
    daily_cells = {item["cell_id"] for item in daily.get("cells", ())}
    if matrix_cells != daily_cells:
        raise RuntimeError("matrix-to-daily cell coverage binding is invalid")
    event_cells = {
        item["event_id"].rsplit(":", 2)[0] for item in exclusive.get("events", ())
    }
    if not event_cells.issubset(daily_cells):
        raise RuntimeError("exclusive-event-to-daily cell binding is invalid")
    raw_cells = {item["cell_id"] for item in raw.get("cells", ())}
    if not raw_cells.issubset(daily_cells):
        raise RuntimeError("counterfactual-to-daily cell binding is invalid")


def _event_outcomes(
    events: list[dict[str, Any]], cells: dict[str, dict[str, Any]], market: pd.Series
) -> list[dict[str, Any]]:
    output = []
    for event in events:
        cell_id = event["event_id"].rsplit(":", 2)[0]
        cell = cells[cell_id]
        dates = [item["date"] for item in cell["days"]]
        equity = [float(item["portfolio_equity"]) for item in cell["days"]]
        market_values = [float(market.loc[pd.Timestamp(date)]) for date in dates]
        outcome = forward_outcomes([event["date"]], dates, equity, market_values, horizons=(1, 3, 5, 10, 20))[
            0
        ]["outcomes"]
        output.append({**event, "outcome_identity": outcome})
    return output


def _episodes(events: list[dict[str, Any]], cells: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in events:
        cell_id = item["event_id"].rsplit(":", 2)[0]
        grouped[(cell_id, item["axis"], item["classification"])].append(item)
    output = []
    for (cell_id, axis, classification), rows in sorted(grouped.items()):
        calendar = [item["date"] for item in cells[cell_id]["days"]]
        starts = merge_episodes([item["date"] for item in rows], calendar=calendar, max_gap_sessions=5)
        by_date = {item["date"]: item for item in rows}
        for date in starts:
            item = by_date[date]
            outcome_20d = item["outcome_identity"]["20d"]
            if outcome_20d is None:
                continue
            output.append(
                {
                    "episode_id": f"{cell_id}:{date}:{axis}:{classification}",
                    "cell_id": cell_id,
                    "date": date,
                    "axis": axis,
                    "classification": classification,
                    "window": item["window"],
                    "universe": item["universe"],
                    "family": item["family"],
                    "actionable_buy_intents": int(item["actionable_buy_intents"]),
                    "actionable_pyramid_intents": int(item["actionable_pyramid_intents"]),
                    "actionable": bool(item["actionable_buy_intents"] or item["actionable_pyramid_intents"]),
                    "realized_shock": bool(outcome_20d["realized_shock"]),
                    "forward_20d_return": float(outcome_20d["forward_portfolio_return"]),
                    "forward_20d_mdd": float(outcome_20d["max_drawdown"]),
                }
            )
    return output


def _axis_signal(day: dict[str, Any], system: str, axis: str) -> bool | None:
    row = day[system]
    if axis == "warning_level":
        severity = row.get("severity_rank")
        return None if severity is None else int(severity) > 0
    if axis in {"block_new_entries", "block_pyramiding"}:
        value = row.get(axis)
        return None if value is None else bool(value)
    if axis == "recommended_gross_cap":
        value = row.get(axis)
        return None if value is None else float(value) < 1.0
    # These actions are not present in the canonical system trace.  Inferring them
    # from warning severity would mix axes and falsely create causal evidence.
    return None


def _calibration(
    cells: list[dict[str, Any]], system: str, *, axis: str = "warning_level"
) -> dict[str, Any]:
    warning_episodes: list[dict[str, Any]] = []
    shock_episodes: list[dict[str, Any]] = []
    silent_bull_days = 0
    bull_days = 0
    for cell in cells:
        if cell.get("status") != "SUCCESS":
            continue
        dates = [item["date"] for item in cell["days"]]
        equity = [float(item["portfolio_equity"]) for item in cell["days"]]
        signals = {item["date"]: _axis_signal(item, system, axis) for item in cell["days"]}
        warning_dates = [date for date, active in signals.items() if active is True]
        starts = merge_episodes(warning_dates, calendar=dates, max_gap_sessions=5)
        outcomes = forward_outcomes(starts, dates, equity, equity)
        warning_episodes.extend(item for item in outcomes if item["outcomes"]["20d"] is not None)
        positions = {date: index for index, date in enumerate(dates)}
        shock_flags = []
        for date in dates:
            pos = positions[date]
            end = pos + 20
            if end >= len(equity):
                continue
            window = equity[pos : end + 1]
            peak = window[0]
            drawdown = 0.0
            for value in window:
                peak = max(peak, value)
                drawdown = min(drawdown, value / peak - 1.0)
            if drawdown <= -0.08:
                shock_flags.append(date)
            if equity[end] > equity[pos]:
                bull_days += 1
                if signals[date] is False:
                    silent_bull_days += 1
        shock_starts = merge_episodes(shock_flags, calendar=dates, max_gap_sessions=5)
        for shock in shock_starts:
            shock_pos = positions[shock]
            end = min(shock_pos + 20, len(equity) - 1)
            window = equity[shock_pos : end + 1]
            peak = window[0]
            depth = 0.0
            for value in window:
                peak = max(peak, value)
                depth = min(depth, value / peak - 1.0)
            prior = [
                positions[date]
                for date in warning_dates
                if shock_pos - 20 <= positions[date] <= shock_pos
            ]
            lead = shock_pos - max(prior) if prior else None
            shock_episodes.append(
                {"date": shock, "detected": bool(prior), "lead": lead, "depth": depth}
            )
    realized = [item for item in warning_episodes if item["outcomes"]["20d"]["realized_shock"]]
    false = [item for item in warning_episodes if not item["outcomes"]["20d"]["realized_shock"]]
    detected = [item for item in shock_episodes if item["detected"]]
    missed = [item for item in shock_episodes if not item["detected"]]
    evaluable = any(
        _axis_signal(item, system, axis) is not None
        for cell in cells
        if cell.get("status") == "SUCCESS"
        for item in cell["days"]
    )
    return {
        "evaluable": evaluable,
        "warning_episode_count": len(warning_episodes),
        "precision": len(realized) / len(warning_episodes) if warning_episodes else None,
        "recall": len(detected) / len(shock_episodes) if shock_episodes else None,
        "median_lead_time": median(cast(float, item["lead"]) for item in detected)
        if detected
        else None,
        "false_positive_opportunity_cost": median(
            max(0.0, item["outcomes"]["20d"]["forward_portfolio_return"]) for item in false
        )
        if false
        else 0.0,
        "caution_freeze_opportunity_cost": median(
            max(0.0, item["outcomes"]["20d"]["forward_portfolio_return"]) for item in false
        )
        if false
        else 0.0,
        "bull_silence_rate": silent_bull_days / bull_days if bull_days else None,
        "missed_shock_count": len(missed),
        "missed_shock_depth": min(
            (cast(float, item["depth"]) for item in missed), default=None
        ),
        "axis": axis,
    }


def _episode_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    realized = [item for item in rows if item["realized_shock"]]
    false = [item for item in rows if not item["realized_shock"]]
    return {
        "exclusive_episode_count": len(rows),
        "distinct_windows": len({item["window"] for item in rows}),
        "distinct_families": len({item["family"] for item in rows}),
        "precision": len(realized) / len(rows) if rows else None,
        "false_positive_opportunity_cost": median(
            max(0.0, item["forward_20d_return"]) for item in false
        )
        if false
        else 0.0,
    }


def _detection_gate(candidate: dict[str, Any], trade: dict[str, Any], base: dict[str, Any]) -> bool:
    return bool(_detection_gate_details(candidate, trade, base)["passed"])


def _detection_gate_details(
    candidate: dict[str, Any], trade: dict[str, Any], base: dict[str, Any]
) -> dict[str, Any]:
    precision = candidate.get("precision")
    base_precision = base.get("precision")
    precision_gain = (
        precision is not None
        and base_precision is not None
        and precision >= base_precision + 0.05
    )
    trade_lead = trade.get("median_lead_time")
    base_lead = base.get("median_lead_time")
    lead_gain = trade_lead is not None and base_lead is not None and trade_lead >= base_lead + 1
    recall_ok = (
        trade.get("recall") is not None
        and base.get("recall") is not None
        and trade["recall"] >= base["recall"] - 0.05
    )
    opportunity_ok = (
        candidate.get("false_positive_opportunity_cost") is not None
        and base.get("false_positive_opportunity_cost") is not None
        and candidate["false_positive_opportunity_cost"]
        <= base["false_positive_opportunity_cost"] + 0.005
    )
    silence_ok = (
        trade.get("bull_silence_rate") is not None
        and base.get("bull_silence_rate") is not None
        and trade["bull_silence_rate"] >= base["bull_silence_rate"] - 0.02
    )
    evaluable = bool(trade.get("evaluable", True) and base.get("evaluable", True))
    return {
        "evaluable": evaluable,
        "passed": bool(
            evaluable
            and (precision_gain or lead_gain)
            and recall_ok
            and opportunity_ok
            and silence_ok
        ),
        "precision_pass": bool(precision_gain),
        "lead_time_pass": bool(lead_gain),
        "recall_pass": bool(recall_ok),
        "opportunity_cost_pass": bool(opportunity_ok),
        "bull_silence_pass": bool(silence_ok),
        "precision": precision,
        "base_precision": base_precision,
        "median_lead_time": trade_lead,
        "base_median_lead_time": base_lead,
        "recall": trade.get("recall"),
        "base_recall": base.get("recall"),
        "false_positive_opportunity_cost": candidate.get(
            "false_positive_opportunity_cost"
        ),
        "base_false_positive_opportunity_cost": base.get(
            "false_positive_opportunity_cost"
        ),
        "bull_silence_rate": trade.get("bull_silence_rate"),
        "base_bull_silence_rate": base.get("bull_silence_rate"),
        "missed_shock_count": trade.get("missed_shock_count"),
        "base_missed_shock_count": base.get("missed_shock_count"),
        "missed_shock_depth": trade.get("missed_shock_depth"),
        "base_missed_shock_depth": base.get("missed_shock_depth"),
        "reasons": {
            "precision": "candidate exclusive-axis precision >= same-axis base + 5pp",
            "lead_time": "candidate same-axis median lead >= base + 1 session",
            "recall": "candidate same-axis recall degradation <= 5pp",
            "opportunity_cost": "candidate exclusive-axis false-positive cost <= same-axis base + 0.5pp",
            "bull_silence": "candidate same-axis bull-silence degradation <= 2pp",
            "missed_shocks": "count/depth reported on the same axis; recall gate supplies the preregistered bound",
        },
    }


def _counterfactual_summary(
    raw: dict[str, Any], negative_controls: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_cell_policy = {(item["cell_id"], item["policy_id"]): item for item in raw["cells"]}
    policy_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in raw["cells"]:
        if item["policy_id"] == "baseline_uquant":
            continue
        baseline = by_cell_policy[(item["cell_id"], "baseline_uquant")]
        policy_rows[item["policy_id"]].append(
            {
                **item,
                "baseline_max_drawdown": baseline["max_drawdown"],
                "real_risk_cell": bool(
                    item.get("matrix_axis", "official_pool") == "official_pool"
                    and float(baseline["max_drawdown"]) >= 0.08
                ),
                "wealth_retention": item["final_wealth"] / baseline["final_wealth"],
                "mdd_delta": baseline["max_drawdown"] - item["max_drawdown"],
                "acute_return_delta": item["acute_return"] - baseline["acute_return"],
                "order_delta": item["account_orders"] - baseline["account_orders"],
                "turnover_delta": item["gross_turnover"] - baseline["gross_turnover"],
                "order_delta_pct": (
                    (item["account_orders"] - baseline["account_orders"]) / baseline["account_orders"]
                    if baseline["account_orders"]
                    else 0.0
                ),
                "turnover_delta_pct": (
                    (item["gross_turnover"] - baseline["gross_turnover"]) / baseline["gross_turnover"]
                    if baseline["gross_turnover"]
                    else 0.0
                ),
            }
        )
    aggregate = {}
    for policy, rows in sorted(policy_rows.items()):
        official = [item for item in rows if item.get("matrix_axis", "official_pool") == "official_pool"]
        protected = official or rows
        real_risk = [item for item in protected if item["real_risk_cell"]]
        generalization = _generalization_gate(rows)
        aggregate[policy] = {
            "cells": len(rows),
            "official_cells": len(official),
            "triggered_cells": sum(item["trigger_count"] > 0 for item in protected),
            "trigger_count": sum(item["trigger_count"] for item in protected),
            "median_wealth_retention": median(item["wealth_retention"] for item in protected),
            "worst_wealth_retention": min(item["wealth_retention"] for item in protected),
            "median_mdd_delta": median(item["mdd_delta"] for item in protected),
            "worst_mdd_delta": min(item["mdd_delta"] for item in protected),
            "real_risk_cell_count": len(real_risk),
            "real_risk_cell_ids": [item["cell_id"] for item in real_risk],
            "max_real_risk_mdd_delta": max(
                (item["mdd_delta"] for item in real_risk), default=None
            ),
            "max_real_risk_acute_return_delta": max(
                (item["acute_return_delta"] for item in real_risk), default=None
            ),
            "real_risk_cells_improved_0_5pp": [
                item["cell_id"] for item in real_risk if item["mdd_delta"] >= 0.005
            ],
            "real_risk_cells_acute_improved_1_0pp": [
                item["cell_id"] for item in real_risk if item["acute_return_delta"] >= 0.01
            ],
            "best_acute_return_delta": max(item["acute_return_delta"] for item in protected),
            "worst_acute_return_delta": min(item["acute_return_delta"] for item in protected),
            "max_order_delta_pct": max(item["order_delta_pct"] for item in protected),
            "max_turnover_delta_pct": max(item["turnover_delta_pct"] for item in protected),
            "total_order_delta": sum(item["order_delta"] for item in protected),
            "total_turnover_delta": sum(item["turnover_delta"] for item in protected),
            "generalization": generalization,
        }
    archived = negative_controls or {}
    return (
        _seal(
            {
                "schema_version": 1,
                "raw_counterfactual_sha256": raw["payload_sha256"],
                "policies": aggregate,
                "negative_controls": archived,
                "cells": [row for rows in policy_rows.values() for row in rows],
            }
        ),
        aggregate,
    )


def _economic_gate(economic: dict[str, Any]) -> dict[str, Any]:
    evaluable = bool(economic)
    wealth_pass = bool(
        evaluable
        and economic["median_wealth_retention"] >= 0.99
        and economic["worst_wealth_retention"] >= 0.98
    )
    mdd_worst_pass = bool(evaluable and economic["worst_mdd_delta"] >= -0.005)
    mdd_real_risk_pass = bool(
        evaluable
        and economic.get("real_risk_cell_count", 0) > 0
        and economic.get("max_real_risk_mdd_delta") is not None
        and economic["max_real_risk_mdd_delta"] >= 0.005
    )
    acute_real_risk_pass = bool(
        evaluable
        and economic.get("real_risk_cell_count", 0) > 0
        and economic.get("max_real_risk_acute_return_delta") is not None
        and economic["max_real_risk_acute_return_delta"] >= 0.01
    )
    real_risk_protection_pass = mdd_real_risk_pass or acute_real_risk_pass
    orders_pass = bool(evaluable and economic["max_order_delta_pct"] <= 0.03)
    turnover_pass = bool(evaluable and economic["max_turnover_delta_pct"] <= 0.05)
    passed = bool(
        wealth_pass
        and mdd_worst_pass
        and real_risk_protection_pass
        and orders_pass
        and turnover_pass
    )
    return {
        "evaluable": evaluable,
        "passed": passed,
        "wealth_pass": wealth_pass,
        "mdd_worst_pass": mdd_worst_pass,
        "mdd_real_risk_pass": mdd_real_risk_pass,
        "acute_real_risk_pass": acute_real_risk_pass,
        "real_risk_protection_pass": real_risk_protection_pass,
        "orders_pass": orders_pass,
        "turnover_pass": turnover_pass,
        "acute_return_evaluable": evaluable,
        "reasons": {
            "wealth": "median >= 99% and worst protected-cell retention >= 98%",
            "mdd_worst": "no protected-cell MDD worsens by more than 0.5pp",
            "real_risk_protection": (
                "at least one baseline >= 8% MDD cell improves MDD by >= 0.5pp "
                "or acute return by >= 1.0pp"
            ),
            "orders": "protected-cell account-order increase <= 3%",
            "turnover": "protected-cell gross-turnover increase <= 5%",
            "acute_return": "reported per candidate and evaluated only inside real-risk cells",
        },
    }


def _generalization_gate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    generalization = [
        item for item in rows if item.get("matrix_axis", "official_pool") != "official_pool"
    ]
    if not generalization:
        return {
            "evaluable": False,
            "passed": False,
            "reason": "no economically replayed generalization cells",
            "cells": 0,
            "p90_drawdown_severity_worsening": None,
            "worst_mdd_delta": None,
            "worst_wealth_retention": None,
        }
    severity_worsening = pd.Series(
        [max(0.0, -float(item["mdd_delta"])) for item in generalization], dtype=float
    )
    p90 = float(severity_worsening.quantile(0.90))
    worst_mdd = min(float(item["mdd_delta"]) for item in generalization)
    worst_wealth = min(float(item["wealth_retention"]) for item in generalization)
    return {
        "evaluable": True,
        "passed": bool(p90 <= 0.005 and worst_mdd >= -0.01 and worst_wealth >= 0.98),
        "reason": "preregistered p90/worst-MDD/worst-wealth thresholds",
        "cells": len(generalization),
        "p90_drawdown_severity_worsening": p90,
        "worst_mdd_delta": worst_mdd,
        "worst_wealth_retention": worst_wealth,
    }


def _closure_outcome(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    promotion_candidates = [
        item["candidate_id"] for item in decisions if item["decision"] == "PROMOTION_CANDIDATE"
    ]
    evaluated = [
        item
        for item in decisions
        if item.get("transfer_kind") not in {"HYBRID_DIAGNOSTIC", "NEGATIVE_CONTROL"}
    ] or decisions
    material = [
        item["candidate_id"]
        for item in evaluated
        if all(
            item.get("gates", {}).get(name, False)
            for name in (
                "sample_pass",
                "detection_pass",
                "economic_pass",
                "generalization_pass",
            )
        )
    ]
    if promotion_candidates:
        final_decision = "PROMOTION_CANDIDATE_REQUIRES_FUTURE_HOLDOUT"
        conclusion_code = final_decision
    elif evaluated and all(item["decision"] == "INSUFFICIENT_SAMPLE" for item in evaluated):
        final_decision = "INCREMENTAL_EVIDENCE_INSUFFICIENT_SAMPLE"
        conclusion_code = final_decision
    else:
        final_decision = "NO_PROMOTABLE_INCREMENTAL_RISK"
        conclusion_code = "NO_INCREMENTAL_PROMOTABLE_RISK_CAPABILITY"
    return {
        "promotion_candidates": promotion_candidates,
        "trade_material_incremental_capabilities": material,
        "final_decision": final_decision,
        "conclusion_code": conclusion_code,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "artifacts/sentinel/risk_differential"
    matrix = json.loads((target / "risk_differential_matrix.json").read_text())
    daily_gzip = (target / "risk_differential_daily.json.gz").read_bytes()
    daily = json.loads(gzip.decompress(daily_gzip))
    exclusive = json.loads((target / "exclusive_events.json").read_text())
    raw = json.loads((target / "counterfactual_raw.json").read_text())
    negative_controls = json.loads((target / "negative_controls_rerun.json").read_text())
    capability = json.loads((root / "benchmarks/risk_capability_registry.json").read_text())
    _validate_analysis_inputs(
        matrix,
        daily,
        daily_gzip,
        exclusive,
        raw,
        negative_controls,
        capability,
    )
    days_by_cell = {item["cell_id"]: item["days"] for item in daily["cells"]}
    cells = {
        item["cell_id"]: {**item, "days": days_by_cell[item["cell_id"]]}
        for item in matrix["cells"]
        if item.get("status") == "SUCCESS"
    }
    market_frame = pd.read_csv(root / "data/frozen/sh000682.csv", parse_dates=["date"]).set_index("date")
    market = pd.to_numeric(market_frame["close"], errors="raise")
    identity_sha = hashlib.sha256(
        canonical_bytes(
            [
                {key: value for key, value in item.items() if key != "outcome_identity"}
                for item in exclusive["events"]
            ]
        )
    ).hexdigest()
    events = _event_outcomes(exclusive["events"], cells, market)
    episodes = _episodes(events, cells)
    exclusive_payload = _seal(
        {
            **{key: value for key, value in exclusive.items() if key not in {"events", "payload_sha256"}},
            "event_identity_sha256": identity_sha,
            "outcomes_filled_after_identity_freeze": True,
            "events": events,
            "episodes": episodes,
        }
    )
    _write(target / "exclusive_events.json", exclusive_payload)
    calibration = {
        system: _calibration(list(cells.values()), system)
        for system in ("base", "sentinel", "trade")
    }
    outcome_payload = _seal(
        {
            "schema_version": 1,
            "contract_sha256": matrix["provenance"]["contract_sha256"],
            "event_identity_sha256": identity_sha,
            "event_count": len(events),
            "complete_20d_event_count": sum(
                item["outcome_identity"]["20d"] is not None for item in events
            ),
            "right_censored_20d_event_count": sum(
                item["outcome_identity"]["20d"] is None for item in events
            ),
            "episodes": episodes,
            "calibration": calibration,
        }
    )
    _write(target / "event_outcome_analysis.json", outcome_payload)
    counterfactual, aggregate = _counterfactual_summary(
        raw,
        {
            key: value
            for key, value in negative_controls.items()
            if key not in {"schema_version", "payload_sha256"}
        },
    )
    _write(target / "counterfactual_summary.json", counterfactual)
    warning_episodes = [
        item
        for item in episodes
        if item["axis"] == "warning_level" and item["classification"] == "TRADE_ONLY"
    ]
    actionable_warning = [item for item in warning_episodes if item["actionable"]]
    decisions = []
    candidate_policies = [item for item in POLICY_SET if item.trigger_axis is not None]
    axes = sorted({item.trigger_axis for item in candidate_policies if item.trigger_axis})
    axis_calibration = {
        axis: {
            system: _calibration(list(cells.values()), system, axis=axis)
            for system in ("base", "sentinel", "trade")
        }
        for axis in axes
    }
    for policy in candidate_policies:
        candidate = policy.policy_id
        kind = policy.transfer_kind
        axis = cast(str, policy.trigger_axis)
        economic = aggregate.get(candidate, {})
        candidate_episodes = [
            item
            for item in episodes
            if item["axis"] == axis
            and item["classification"] == "TRADE_ONLY"
        ]
        if candidate == "trade_entry_freeze_shadow":
            candidate_episodes = [item for item in candidate_episodes if item["actionable_buy_intents"]]
        elif candidate == "trade_pyramid_freeze_shadow":
            candidate_episodes = [
                item for item in candidate_episodes if item["actionable_pyramid_intents"]
            ]
        candidate_metrics = _episode_metrics(candidate_episodes)
        sample_pass = bool(
            candidate_metrics["exclusive_episode_count"] >= 5
            and candidate_metrics["distinct_windows"] >= 2
            and candidate_metrics["distinct_families"] >= 2
        )
        economic_gate = _economic_gate(economic)
        economic_pass = bool(economic_gate["passed"])
        generalization = economic.get("generalization", _generalization_gate([]))
        detection_gate = _detection_gate_details(
            candidate_metrics,
            axis_calibration[axis]["trade"],
            axis_calibration[axis]["base"],
        )
        detection_pass = bool(detection_gate["passed"])
        causal_validity_pass = bool(
            kind == "EXACT_TRANSFER"
            and axis_calibration[axis]["trade"]["evaluable"]
            and axis_calibration[axis]["base"]["evaluable"]
        )
        if (
            kind == "EXACT_TRANSFER"
            and sample_pass
            and detection_pass
            and economic_pass
            and not generalization["evaluable"]
        ):
            raise RuntimeError(
                f"{candidate} reached the generalization gate without economic replay rows"
            )
        if not generalization["evaluable"]:
            failed = [
                name
                for name, passed in (
                    ("sample", sample_pass),
                    ("detection", detection_pass),
                    ("economic", economic_pass),
                    ("causal-validity", causal_validity_pass),
                )
                if not passed
            ]
            generalization = {
                **generalization,
                "reason": "not evaluated under fixed stop rule after failed " + "/".join(failed),
            }
        gate = {
            "sample_pass": sample_pass,
            "detection_pass": detection_pass,
            "economic_pass": economic_pass,
            "generalization_pass": bool(generalization["passed"]),
            "causal_validity_pass": causal_validity_pass,
            "event_sample_pass": sample_pass,
            "precision_pass": detection_gate["precision_pass"],
            "lead_time_pass": detection_gate["lead_time_pass"],
            "recall_pass": detection_gate["recall_pass"],
            "opportunity_cost_pass": detection_gate["opportunity_cost_pass"],
            "bull_silence_pass": detection_gate["bull_silence_pass"],
            "missed_shock_count_pass": detection_gate["recall_pass"],
            "missed_shock_depth_pass": detection_gate["recall_pass"],
            "wealth_pass": economic_gate["wealth_pass"],
            "mdd_worst_pass": economic_gate["mdd_worst_pass"],
            "mdd_real_risk_pass": economic_gate["mdd_real_risk_pass"],
            "acute_real_risk_pass": economic_gate["acute_real_risk_pass"],
            "real_risk_protection_pass": economic_gate["real_risk_protection_pass"],
            "acute_return_evaluable": economic_gate["acute_return_evaluable"],
            "orders_pass": economic_gate["orders_pass"],
            "turnover_pass": economic_gate["turnover_pass"],
        }
        decisions.append(
            {
                "candidate_id": candidate,
                "transfer_kind": kind,
                "candidate_axis": axis,
                "sample_metrics": candidate_metrics,
                "axis_metrics": axis_calibration[axis],
                "detection_gate": detection_gate,
                "economic_metrics": economic,
                "economic_gate": economic_gate,
                "exclusive_episode_count": candidate_metrics["exclusive_episode_count"],
                "actionable_exclusive_episode_count": sum(
                    item["actionable"] for item in candidate_episodes
                ),
                "gates": gate,
                "gate_reasons": {
                    "sample": "at least 5 non-overlapping episodes across 2 windows and 2 families",
                    **detection_gate["reasons"],
                    **economic_gate["reasons"],
                    "generalization": generalization["reason"],
                    "causal_validity": (
                        "mechanically proven exact transfer on an observable canonical axis"
                        if causal_validity_pass
                        else f"{kind} is diagnostic/nonpromotable or its axis is unobservable"
                    ),
                },
                "generalization_gate": generalization,
                "decision": classify_promotion(candidate, kind, gate),
            }
        )
    closure_outcome = _closure_outcome(decisions)
    promotion_candidates = closure_outcome["promotion_candidates"]
    promotion = _seal(
        {
            "schema_version": 1,
            "no_parameter_search": True,
            "calibration": calibration,
            "axis_calibration": axis_calibration,
            "candidates": decisions,
            "promotion_candidates": promotion_candidates,
        }
    )
    _write(target / "promotion_analysis.json", promotion)
    structural_differentials = [
        item["capability_id"]
        for item in capability["capabilities"]
        if item["mapping_status"].startswith("INCREMENTAL_")
    ]
    closure = _seal(
        {
            "schema_version": 1,
            "architecture_goal_reached": True,
            "capability_inventory_complete": True,
            "trade_material_incremental_capabilities": closure_outcome[
                "trade_material_incremental_capabilities"
            ],
            "trade_structural_differentials_not_proven_incremental": structural_differentials,
            "trade_only_warning_events": sum(
                item["axis"] == "warning_level" and item["classification"] == "TRADE_ONLY" for item in events
            ),
            "trade_only_warning_episodes": len(warning_episodes),
            "actionable_trade_only_warning_episodes": len(actionable_warning),
            "promotion_candidates": promotion_candidates,
            "rejected_capabilities": [
                item["candidate_id"] for item in decisions if item["decision"].startswith("REJECTED")
            ],
            "insufficient_sample_capabilities": [
                item["candidate_id"] for item in decisions if item["decision"] == "INSUFFICIENT_SAMPLE"
            ],
            "production_behavior_changed": False,
            "production_authority_changed": False,
            "future_holdout_required": True,
            "future_holdout": {
                "lane_id": "risk_differential_shadow",
                "status": "OBSERVING",
                "review_status": "NON_REVIEWABLE",
                "observed_sessions": 0,
                "formal_scores": None,
                "parameter_changes_from_observation": False,
                "production_authority_changes_from_observation": False,
            },
            "negative_controls": {
                "phase5_limited_gross_cap": "REJECTED",
                "phase7_exclusive_freeze": "REJECTED",
            },
            "final_decision": closure_outcome["final_decision"],
            "conclusion_code": closure_outcome["conclusion_code"],
        }
    )
    _write(target / "closure.json", closure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
