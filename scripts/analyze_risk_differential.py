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

from research.risk_counterfactual import classify_promotion
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
                    "realized_shock": bool(item["outcome_identity"]["20d"]["realized_shock"]),
                    "forward_20d_return": float(item["outcome_identity"]["20d"]["forward_portfolio_return"]),
                    "forward_20d_mdd": float(item["outcome_identity"]["20d"]["max_drawdown"]),
                }
            )
    return output


def _calibration(cells: list[dict[str, Any]], system: str) -> dict[str, Any]:
    warning_episodes = []
    shock_episodes = []
    silent_bull_days = 0
    bull_days = 0
    for cell in cells:
        if cell.get("status") != "SUCCESS":
            continue
        dates = [item["date"] for item in cell["days"]]
        equity = [float(item["portfolio_equity"]) for item in cell["days"]]
        warning_dates = [
            item["date"]
            for item in cell["days"]
            if item[system]["severity_rank"] is not None and int(item[system]["severity_rank"]) > 0
        ]
        starts = merge_episodes(warning_dates, calendar=dates, max_gap_sessions=5)
        outcomes = forward_outcomes(starts, dates, equity, equity)
        warning_episodes.extend(outcomes)
        positions = {date: index for index, date in enumerate(dates)}
        shock_flags = []
        for date in dates:
            pos = positions[date]
            end = min(pos + 20, len(equity) - 1)
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
                if date not in warning_dates:
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
    return {
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
        "axis": "warning_level",
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
    return bool((precision_gain or lead_gain) and recall_ok and opportunity_ok and silence_ok)


def _counterfactual_summary(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    by_cell_policy = {(item["cell_id"], item["policy_id"]): item for item in raw["cells"]}
    policy_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in raw["cells"]:
        if item["policy_id"] == "baseline_uquant":
            continue
        baseline = by_cell_policy[(item["cell_id"], "baseline_uquant")]
        policy_rows[item["policy_id"]].append(
            {
                **item,
                "wealth_retention": item["final_wealth"] / baseline["final_wealth"],
                "mdd_delta": baseline["max_drawdown"] - item["max_drawdown"],
                "acute_loss_improvement": baseline["acute_return"] - item["acute_return"],
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
        aggregate[policy] = {
            "cells": len(rows),
            "triggered_cells": sum(item["trigger_count"] > 0 for item in rows),
            "trigger_count": sum(item["trigger_count"] for item in rows),
            "median_wealth_retention": median(item["wealth_retention"] for item in rows),
            "worst_wealth_retention": min(item["wealth_retention"] for item in rows),
            "median_mdd_delta": median(item["mdd_delta"] for item in rows),
            "worst_mdd_delta": min(item["mdd_delta"] for item in rows),
            "best_acute_loss_improvement": max(item["acute_loss_improvement"] for item in rows),
            "worst_acute_loss_improvement": min(item["acute_loss_improvement"] for item in rows),
            "max_order_delta_pct": max(item["order_delta_pct"] for item in rows),
            "max_turnover_delta_pct": max(item["turnover_delta_pct"] for item in rows),
            "total_order_delta": sum(item["order_delta"] for item in rows),
            "total_turnover_delta": sum(item["turnover_delta"] for item in rows),
        }
    archived = {
        "phase5_limited_gross_cap": {
            "status": "REJECTED",
            "wealth_retention": 0.9184841626984643,
            "mdd_improvement": 0.0,
            "acute_return_delta": -0.01097916003499022,
            "account_order_delta": 4,
            "gross_turnover_delta": 1.2085078385,
            "source_commit": "9a82143a3079bdd846c995962a246a66c834c1d5",
        },
        "phase7_exclusive_freeze": {
            "status": "REJECTED",
            "exclusive_events": 1,
            "actionable_buy_intents": 0,
            "economic_delta": 0.0,
            "source_commit": "c559c009db309b3815aa8a3df8b59638504acc1a",
        },
    }
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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "artifacts/sentinel/risk_differential"
    matrix = json.loads((target / "risk_differential_matrix.json").read_text())
    daily = json.loads(gzip.decompress((target / "risk_differential_daily.json.gz").read_bytes()))
    exclusive = json.loads((target / "exclusive_events.json").read_text())
    raw = json.loads((target / "counterfactual_raw.json").read_text())
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
        system: _calibration(list(cells.values()), system) for system in ("base", "sentinel", "trade")
    }
    outcome_payload = _seal(
        {
            "schema_version": 1,
            "contract_sha256": matrix["provenance"]["contract_sha256"],
            "event_identity_sha256": identity_sha,
            "episodes": episodes,
            "calibration": calibration,
        }
    )
    _write(target / "event_outcome_analysis.json", outcome_payload)
    counterfactual, aggregate = _counterfactual_summary(raw)
    _write(target / "counterfactual_summary.json", counterfactual)
    warning_episodes = [
        item
        for item in episodes
        if item["axis"] == "warning_level" and item["classification"] == "TRADE_ONLY"
    ]
    actionable_warning = [item for item in warning_episodes if item["actionable"]]
    decisions = []
    transfer = {
        "trade_entry_freeze_shadow": "EXACT_TRANSFER",
        "trade_pyramid_freeze_shadow": "EXACT_TRANSFER",
        "trade_gross_cap_shadow": "EXACT_TRANSFER",
        "trade_layered_protection_shadow": "EXACT_TRANSFER",
        "trade_cluster_trim_hybrid_shadow": "HYBRID_DIAGNOSTIC",
        "phase5_rejected_gross_cap_control": "NEGATIVE_CONTROL",
        "phase7_rejected_exclusive_freeze_control": "NEGATIVE_CONTROL",
    }
    candidate_axis = {
        "trade_entry_freeze_shadow": "block_new_entries",
        "trade_pyramid_freeze_shadow": "block_pyramiding",
        "trade_gross_cap_shadow": "recommended_gross_cap",
        "trade_layered_protection_shadow": "warning_level",
        "trade_cluster_trim_hybrid_shadow": "warning_level",
        "phase5_rejected_gross_cap_control": "recommended_gross_cap",
        "phase7_rejected_exclusive_freeze_control": "block_new_entries",
    }
    for candidate, kind in transfer.items():
        economic = aggregate.get(candidate, {})
        candidate_episodes = [
            item
            for item in episodes
            if item["axis"] == candidate_axis[candidate]
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
        economic_pass = bool(
            economic
            and economic["median_wealth_retention"] >= 0.99
            and economic["worst_wealth_retention"] >= 0.98
            and economic["worst_mdd_delta"] >= -0.005
            and economic["max_order_delta_pct"] <= 0.03
            and economic["max_turnover_delta_pct"] <= 0.05
            and (
                economic["median_mdd_delta"] >= 0.005
                or economic["best_acute_loss_improvement"] >= 0.01
            )
        )
        gate = {
            "sample_pass": sample_pass,
            "detection_pass": _detection_gate(
                candidate_metrics, calibration["trade"], calibration["base"]
            ),
            "economic_pass": economic_pass,
            "generalization_pass": False,  # nosec B105 - promotion gate, not a credential
        }
        decisions.append(
            {
                "candidate_id": candidate,
                "transfer_kind": kind,
                "candidate_axis": candidate_axis[candidate],
                "sample_metrics": candidate_metrics,
                "exclusive_episode_count": candidate_metrics["exclusive_episode_count"],
                "actionable_exclusive_episode_count": sum(
                    item["actionable"] for item in candidate_episodes
                ),
                "gates": gate,
                "decision": classify_promotion(candidate, kind, gate),
            }
        )
    promotion_candidates = [
        item["candidate_id"] for item in decisions if item["decision"] == "PROMOTION_CANDIDATE"
    ]
    promotion = _seal(
        {
            "schema_version": 1,
            "no_parameter_search": True,
            "calibration": calibration,
            "candidates": decisions,
            "promotion_candidates": promotion_candidates,
        }
    )
    _write(target / "promotion_analysis.json", promotion)
    capability = json.loads((root / "benchmarks/risk_capability_registry.json").read_text())
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
            "trade_material_incremental_capabilities": [],
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
            "final_decision": (
                "PROMOTION_CANDIDATE_REQUIRES_FUTURE_HOLDOUT"
                if promotion_candidates
                else "NO_INCREMENTAL_PROMOTABLE_RISK_CAPABILITY"
            ),
        }
    )
    _write(target / "closure.json", closure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
