"""Isolated runtime adapters for the pinned three-way differential replay."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from research.candidate_runner import CandidateRunner
from research.risk_differential import normalize_trade_governance, normalize_uquant_decision

INITIAL_CASH = 2_000_000.0
TRADE_NAME_HINTS = {
    "600487": "Hengtong optical communication",
    "603688": "Quartz silicon wafer",
    "688110": "Dosilicon memory",
    "688146": "electronic specialty gas",
    "688200": "Huafeng semiconductor equipment",
    "688233": "Shengong silicon wafer",
    "688766": "Primarius memory",
}


@dataclass(frozen=True, slots=True)
class ReplayCell:
    cell_id: str
    axis: str
    window: str
    universe: str
    family: str
    symbols: tuple[str, ...]
    start: str
    end: str


def build_trade_data_view(source: Path, target: Path) -> None:
    """Create the six-digit, read-only view expected by the challenger."""

    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.csv")):
        stem = path.stem[2:] if path.stem[:2] in {"sh", "sz", "bj"} else path.stem
        link = target / f"{stem}.csv"
        if link.exists() or link.is_symlink():
            if link.resolve() != path.resolve():
                raise RuntimeError(f"trade data link is not source-bound: {link}")
            continue
        os.symlink(path.resolve(), link)


def _uquant_actionability(observations: tuple[Any, ...]) -> dict[str, dict[str, float | int]]:
    quantities: dict[str, int] = {}
    result: dict[str, dict[str, float | int]] = {}
    for trace in observations:
        for _fill_date, side, symbol, shares, _price, _reason in trace.fills:
            signed = int(shares) if side.upper() == "BUY" else -int(shares)
            quantities[symbol] = max(0, quantities.get(symbol, 0) + signed)
        buys = [order for order in trace.orders if order[0].upper() == "BUY"]
        result[trace.date] = {
            "buy": len(buys),
            "pyramid": sum(quantities.get(order[1], 0) > 0 for order in buys),
            "gross": sum(max(0.0, float(item[1])) for item in trace.targets),
        }
    return result


def run_uquant_cell(cell: ReplayCell, data_dir: Path) -> dict[str, Any]:
    trace = CandidateRunner(data_dir).trace_cell(
        symbols=cell.symbols,
        start=cell.start,
        end=cell.end,
        universe=cell.universe,
        scenario=cell.window,
    )
    base_rows = []
    sentinel_rows = []
    for decision in trace.observations:
        base, sentinel = normalize_uquant_decision(decision)
        base_rows.append(asdict(base))
        sentinel_rows.append(asdict(sentinel))
    return {
        "dates": [item.date for item in trace.observations],
        "base": base_rows,
        "sentinel": sentinel_rows,
        "actionability": _uquant_actionability(trace.observations),
        "portfolio_equity": [float(item.equity) / INITIAL_CASH for item in trace.observations],
        "decision_digest_sha256": __import__("hashlib")
        .sha256(
            json.dumps(
                [asdict(item) for item in trace.observations],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .hexdigest(),
    }


def run_trade_cell(cell: ReplayCell, trade_root: Path, data_view: Path) -> dict[str, Any]:
    root_text = str(trade_root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    route = importlib.import_module("regime_adaptive")
    base_policy = route.qf.PortfolioPolicy()
    visible_references = tuple(
        symbol for symbol in base_policy.regime_symbols if (data_view / f"{symbol}.csv").is_file()
    )
    if not visible_references:
        raise RuntimeError("challenger has no visible regime references")
    policy = replace(base_policy, regime_symbols=visible_references)
    six_digit_symbols = tuple(symbol[2:] for symbol in cell.symbols)
    symbol_names = {symbol: TRADE_NAME_HINTS.get(symbol, symbol) for symbol in six_digit_symbols}
    with contextlib.redirect_stdout(io.StringIO()):
        result = route.ProductionReplayEngine(INITIAL_CASH, policy=policy).run(
            symbol_names,
            cell.start,
            cell.end,
            data_dir=str(data_view),
            regime_data_dir=str(data_view),
            leader_data_dir=str(data_view),
            indicator_state="warm",
        )
    warmup = str(result.get("warmup_health", {}).get("warmup_status", "UNOBSERVABLE"))
    rows = []
    for raw in result.get("risk_governance_series", ()):
        item = {
            **raw,
            "warmup_status": warmup,
            "gross_cap_derived_from_pinned_level_contract": True,
            "execution_owner": "trade_cross_market_overlay",
            "action_candidates": tuple(
                action
                for action, active in (
                    ("FREEZE_NEW_RISK", raw.get("block_new_entries")),
                    ("BLOCK_PYRAMID", raw.get("block_pyramids")),
                )
                if active
            ),
        }
        rows.append(asdict(normalize_trade_governance(item)))
    equity = result["equity_curve"]["assets"]
    return {
        "dates": [str(item["date"]) for item in result.get("risk_governance_series", ())],
        "trade": rows,
        "portfolio_equity": [float(value) / INITIAL_CASH for value in equity.tolist()],
        "warmup_health": result.get("warmup_health"),
        "terminal_risk_opinion": result.get("risk_opinion"),
    }
