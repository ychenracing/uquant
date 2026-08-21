"""Isolated runtime adapters for the pinned three-way differential replay."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd

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


def _bounded_csv_bytes(path: Path, *, as_of: str) -> bytes:
    """Return the exact CSV header and rows causally visible through ``as_of``."""

    lines = path.read_bytes().splitlines(keepends=True)
    if not lines:
        raise ValueError(f"market data CSV is empty: {path}")
    bounded = [lines[0]]
    visible_rows = 0
    for line in lines[1:]:
        if not line.strip():
            continue
        raw_date = line.split(b",", 1)[0]
        try:
            date = raw_date.decode("utf-8").lstrip("\ufeff")
        except UnicodeDecodeError as exc:
            raise ValueError(f"market data date is not UTF-8: {path}") from exc
        if date <= as_of:
            bounded.append(line)
            visible_rows += 1
    return b"" if visible_rows == 0 else b"".join(bounded)


def causal_data_prefix_sha256(source: Path, *, as_of: str) -> str:
    """Hash the actual per-file market-data bytes visible at a causal cutoff."""

    inputs = []
    for path in sorted(source.glob("*.csv"), key=lambda item: item.name):
        payload = _bounded_csv_bytes(path, as_of=as_of)
        if not payload:
            continue
        inputs.append(
            {
                "path": path.name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    if not inputs:
        raise ValueError(f"market data directory has no CSV files: {source}")
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def materialize_causal_data_view(
    source: Path,
    target: Path,
    *,
    as_of: str,
    strip_exchange_prefix: bool = False,
) -> str:
    """Materialize an immutable end-bounded view and return its content identity."""

    target.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    for path in sorted(source.glob("*.csv"), key=lambda item: item.name):
        stem = path.stem
        if strip_exchange_prefix and stem[:2] in {"sh", "sz", "bj"}:
            stem = stem[2:]
        payload = _bounded_csv_bytes(path, as_of=as_of)
        if not payload:
            continue
        name = f"{stem}.csv"
        if name in seen:
            raise ValueError(f"causal market-data view has a filename collision: {name}")
        seen.add(name)
        materialized = target / name
        materialized.write_bytes(payload)
        materialized.chmod(0o444)
    if not seen:
        raise ValueError(f"market data directory has no CSV files: {source}")
    return causal_data_prefix_sha256(source, as_of=as_of)


def _marked_close(
    data_dir: Path,
    symbol: str,
    date: str,
    cache: dict[str, pd.DataFrame],
) -> float:
    if symbol not in cache:
        path = data_dir / f"{symbol}.csv"
        if not path.is_file():
            raise RuntimeError(f"marked holding has no causal price file: {symbol}")
        cache[symbol] = pd.read_csv(path, usecols=["date", "close"])
    frame = cache[symbol]
    visible = frame.loc[frame["date"].astype(str) <= date, "close"]
    if visible.empty:
        raise RuntimeError(f"marked holding has no causal close on or before {date}: {symbol}")
    return float(visible.iloc[-1])


def _causal_activation_warmup_status(
    *,
    data_dir: Path,
    start: str,
    regime_symbols: tuple[str, ...],
    run_health: Any,
) -> str:
    """Evaluate challenger admission once using only activation-date evidence.

    ``trade`` emits a run-level warmup report and a causal daily governance
    series.  The report's indicator/reference ratios are measured strictly
    before ``start`` and are therefore valid activation evidence, but its
    regime staleness is measured at the run end.  Recompute that last component
    from physical prefixes through ``start`` so no terminal readiness is
    projected backwards onto the daily series.
    """

    if not isinstance(run_health, dict):
        return "UNOBSERVABLE"
    try:
        indicator_ratio = float(run_health["indicator_ready_ratio"])
        reference_ratio = float(run_health["reference_basket_ready_ratio"])
    except (KeyError, TypeError, ValueError):
        return "UNOBSERVABLE"

    start_ts = pd.Timestamp(start).normalize()
    regime_missing = True
    regime_stale = False
    for symbol in regime_symbols:
        path = data_dir / f"{symbol}.csv"
        if not path.is_file():
            continue
        frame = pd.read_csv(path, usecols=["date"])
        dates = pd.to_datetime(frame["date"], errors="coerce")
        visible = dates.loc[dates <= start_ts].dropna().sort_values()
        if visible.empty:
            continue
        regime_missing = False
        if len(visible) < 120 or (start_ts - visible.iloc[-1]).days > 10:
            regime_stale = True

    if regime_missing or indicator_ratio < 0.5:
        return "NOT_READY"
    if regime_stale or indicator_ratio < 1.0 or reference_ratio < 1.0:
        return "DEGRADED"
    return "READY"


def _uquant_actionability(
    observations: tuple[Any, ...], data_dir: Path
) -> dict[str, dict[str, float | int]]:
    quantities: dict[str, int] = {}
    price_frames: dict[str, pd.DataFrame] = {}
    result: dict[str, dict[str, float | int]] = {}
    for trace in observations:
        for _fill_date, side, symbol, shares, _price, _reason in trace.fills:
            signed = int(shares) if side.upper() == "BUY" else -int(shares)
            quantities[symbol] = max(0, quantities.get(symbol, 0) + signed)
        buys = [order for order in trace.orders if order[0].upper() == "BUY"]
        marked_holdings = sum(
            shares * _marked_close(data_dir, symbol, trace.date, price_frames)
            for symbol, shares in quantities.items()
            if shares > 0
        )
        if float(trace.equity) <= 0:
            raise RuntimeError(f"nonpositive replay equity on {trace.date}")
        result[trace.date] = {
            "buy": len(buys),
            "pyramid": sum(quantities.get(order[1], 0) > 0 for order in buys),
            "gross": marked_holdings / float(trace.equity),
        }
    return result


def run_uquant_cell(cell: ReplayCell, data_dir: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="uquant-risk-causal-") as temporary:
        causal_view = Path(temporary) / "data"
        data_prefix_sha256 = materialize_causal_data_view(
            data_dir, causal_view, as_of=cell.end
        )
        trace = CandidateRunner(causal_view).trace_cell(
            symbols=cell.symbols,
            start=cell.start,
            end=cell.end,
            universe=cell.universe,
            scenario=cell.window,
        )
        actionability = _uquant_actionability(trace.observations, causal_view)
    base_rows = []
    sentinel_rows = []
    for decision in trace.observations:
        base, sentinel = normalize_uquant_decision(decision)
        decision_payload = asdict(decision)
        decision_digest = hashlib.sha256(
            json.dumps(
                decision_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        identity = {"date": str(decision.date), "decision_digest_sha256": decision_digest}
        base_rows.append({**asdict(base), "decision_identity": identity})
        sentinel_rows.append({**asdict(sentinel), "decision_identity": identity})
    return {
        "dates": [item.date for item in trace.observations],
        "base": base_rows,
        "sentinel": sentinel_rows,
        "actionability": actionability,
        "portfolio_equity": [float(item.equity) / INITIAL_CASH for item in trace.observations],
        "causal_data_prefix_sha256": data_prefix_sha256,
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
    with tempfile.TemporaryDirectory(prefix="trade-risk-causal-") as temporary:
        causal_view = Path(temporary) / "data"
        data_prefix_sha256 = materialize_causal_data_view(
            data_view, causal_view, as_of=cell.end, strip_exchange_prefix=True
        )
        base_policy = route.qf.PortfolioPolicy()
        visible_references = tuple(
            symbol
            for symbol in base_policy.regime_symbols
            if (causal_view / f"{symbol}.csv").is_file()
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
                data_dir=str(causal_view),
                regime_data_dir=str(causal_view),
                leader_data_dir=str(causal_view),
                indicator_state="warm",
            )
        activation_warmup_status = _causal_activation_warmup_status(
            data_dir=causal_view,
            start=cell.start,
            regime_symbols=visible_references,
            run_health=result.get("warmup_health"),
        )
    rows = []
    for raw in result.get("risk_governance_series", ()):
        item = {
            **raw,
            "warmup_status": raw.get("warmup_status", activation_warmup_status),
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
        "causal_data_prefix_sha256": data_prefix_sha256,
        "activation_warmup_status": activation_warmup_status,
        "warmup_health": result.get("warmup_health"),
        "terminal_risk_opinion": result.get("risk_opinion"),
    }
