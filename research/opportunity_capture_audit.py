"""Fixed, read-only signal diagnostics over sealed native account observations.

Forward open returns describe information, not executable counterfactual PnL.
Run as a module so research/statistics.py cannot shadow the standard library.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from statistics import correlation, mean, median
from typing import Any, cast

from research.cross_ai_acceptance import read_case
from uquant.validation.manifest import verify_data_manifest

SOURCE = "b928db51ea6a7bbdfbe79588408717241200d44b3d98b2c39d7668b28e90259d"
END = "2026-08-05"
NAMES = ("full", "remove_all_three", "minus_sz300666", "no_optical_h1")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ranks(values: list[float]) -> list[float]:
    return [sum(v < x for v in values) + (values.count(x) + 1) / 2 for x in values]


def rank_ic(scores: list[float], outcomes: list[float]) -> float | None:
    if len(scores) < 3 or len(set(scores)) < 2 or len(set(outcomes)) < 2:
        return None
    return correlation(ranks(scores), ranks(outcomes))


def select(leaders: list[dict[str, Any]], method: str) -> list[str]:
    """Selection has no price-label argument and uses only observed features."""
    if method == "industry_first":
        groups = defaultdict(list)
        for item in leaders:
            groups[item["industry"]].append(item)
        best = min(groups, key=lambda g: (-median(
            x["components"]["industry_rotation_strength"] for x in groups[g]), g))
        leaders = groups[best]
    key: Callable[[dict[str, Any]], float] = (lambda x: x["score"]) if method == "score" else (
        lambda x: x["components"]["raw_ret120"])
    return [x["symbol"] for x in sorted(leaders, key=lambda x: (-key(x), x["symbol"]))[:3]]


def valid_features(item: dict[str, Any]) -> bool:
    values = (item["score"], item["components"].get("raw_ret120"),
              item["components"].get("industry_rotation_strength"))
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def label(prices: dict[str, dict[str, float]], symbol: str, dates: list[str], index: int, horizon: int) -> dict[str, Any] | None:
    if index + horizon + 1 >= len(dates):
        return None
    start, end = dates[index + 1], dates[index + horizon + 1]
    if not (start > dates[index] and end <= END):
        raise RuntimeError('Evidence validation failed: start > dates[index] and end <= END')
    a, b = prices[symbol].get(start), prices[symbol].get(end)
    if a is None or b is None or not all(math.isfinite(v) and v > 0 for v in (a, b)):
        return None
    return {"entry_date": start, "exit_date": end, "entry_open": a,
            "exit_open": b, "gross_return": b / a - 1}


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = defaultdict(list)
    for row in records:
        for period in ("all", "2023-24" if row["date"] < "2025" else "2025-26"):
            groups[(row["cohort"], row["horizon"], period)].append(row)
    result = []
    for (cohort, horizon, period), rows in sorted(groups.items()):
        strategies = {}
        for method in ("score", "momentum", "industry_first"):
            excess = [r["methods"][method]["excess"] for r in rows]
            strategies[method] = {"mean_excess": mean(excess), "median_excess": median(excess),
                                  "positive_fraction": mean(x > 0 for x in excess),
                                  "mean_gross_return": mean(r["methods"][method]["return"] for r in rows)}
        result.append({"cohort": cohort, "horizon": horizon, "period": period,
                       "anchors": len(rows), "pool_mean_return": mean(r["pool_return"] for r in rows),
                       "score_ic": mean(v for r in rows if (v := r["score_ic"]) is not None)
                       if any(r["score_ic"] is not None for r in rows) else None,
                       "momentum_ic": mean(v for r in rows if (v := r["momentum_ic"]) is not None)
                       if any(r["momentum_ic"] is not None for r in rows) else None,
                       "methods": strategies})
    return result


def analyze(root: Path, name: str, prices: dict[str, dict[str, float]]) -> dict[str, Any]:
    case = {"minus_sz300666": "remove_all_three", "no_optical_h1": "no_optical"}.get(name, name)
    end = "2023-06-30" if name == "no_optical_h1" else END
    read_case(root, case=case, interval=["2023-01-03", end], source=SOURCE,
              extra_excluded_symbols=("sz300666",) if name == "minus_sz300666" else ())
    sealed = json.loads((root / "result.json").read_text())
    anchors: list[tuple[int, list[dict[str, Any]]]] = []
    records, industry_records = [], []
    events: list[dict[str, Any]] = []
    stages: Counter[str] = Counter()
    held_days: Counter[str] = Counter()
    mature_days: Counter[str] = Counter()
    ready_days: Counter[str] = Counter()
    exposures: defaultdict[str, float] = defaultdict(float)
    with gzip.open(root / "observations.jsonl.gz", "rt") as stream:
        dates = []
        for index, line in enumerate(stream):
            row = json.loads(line)
            dates.append(row["date"])
            if not (dates[-1] <= end):
                raise RuntimeError('Evidence validation failed: dates[-1] <= end')
            observation, ledger = row["observation"], row["ledger"]
            leaders = observation["leader_scores"]
            tradable = set(observation["strategic_universe_roles"]["tradable_symbols"])
            leaders = [x for x in leaders if x["symbol"] in tradable]
            allocation = observation["risk_assessment"]["evidence"].get("core_allocation", {})
            symbols = allocation.get("symbols", {})
            if index % 60 == 0:
                anchors.append((index, leaders))
            for item in leaders:
                s = item["symbol"]
                mature_days[s] += item["mature"]
                detail = symbols.get(s, {})
                block = detail.get("entry", {}).get("block", "UNOBSERVED")
                ready_days[s] += block == "READY"
                if item["mature"] and ledger["position_weights"].get(s, 0) <= 0:
                    stages[block] += 1
            for symbol, weight in ledger["position_weights"].items():
                held_days[symbol] += weight > 0
                exposures[symbol] += weight
            events.extend({k: fill[k] for k in ("symbol", "signal_date", "fill_date", "side", "mechanism", "gross_value")}
                          for fill in row["new_fills"])
    if not (dates == sorted(set(dates))):
        raise RuntimeError('Evidence validation failed: dates == sorted(set(dates))')
    missing: list[dict[str, Any]] = []
    feature_exclusions: list[dict[str, Any]] = []
    for index, leaders in anchors:
        feature_exclusions.extend({"date": dates[index], "symbol": x["symbol"], "reason": "nonfinite_contemporaneous_feature"}
                                  for x in leaders if not valid_features(x))
        leaders = [x for x in leaders if valid_features(x)]
        for cohort in ("all", "mature", "nonoptical", "nonoptical_mature"):
            pool = [x for x in leaders if ("mature" not in cohort or x["mature"])
                    and ("nonoptical" not in cohort or x["industry"] != "optical")]
            if len(pool) < 3:
                missing.append({"date": dates[index], "cohort": cohort, "reason": "fewer_than_three"})
                continue
            selected = {m: select(pool, m) for m in ("score", "momentum", "industry_first")}
            for horizon in (20, 60):
                labels = {x["symbol"]: label(prices, x["symbol"], dates, index, horizon) for x in pool}
                if any(v is None for v in labels.values()):
                    missing.append({"date": dates[index], "cohort": cohort, "horizon": horizon,
                                    "reason": "censored_or_missing_endpoint", "symbols": [s for s, v in labels.items() if v is None]})
                    continue
                valid_labels = cast(dict[str, dict[str, Any]], labels)
                outcomes = [valid_labels[x["symbol"]]["gross_return"] for x in pool]
                average = mean(outcomes)
                methods = {}
                for method, symbols_selected in selected.items():
                    value = mean(valid_labels[s]["gross_return"] for s in symbols_selected)
                    methods[method] = {"selected": symbols_selected, "return": value, "excess": value - average}
                records.append({"date": dates[index], "cohort": cohort, "horizon": horizon,
                                "pool_count": len(pool), "pool_return": average,
                                "score_ic": rank_ic([x["score"] for x in pool], outcomes),
                                "momentum_ic": rank_ic([x["components"]["raw_ret120"] for x in pool], outcomes),
                                "methods": methods, "labels": labels,
                                "features": [{"symbol": x["symbol"], "industry": x["industry"], "mature": x["mature"],
                                              "score": x["score"], "ret120": x["components"]["raw_ret120"],
                                              "industry_strength": x["components"]["industry_rotation_strength"]} for x in pool]})
                for industry in sorted({x["industry"] for x in pool}):
                    members = [x for x in pool if x["industry"] == industry]
                    industry_records.append({"date": dates[index], "cohort": cohort, "horizon": horizon,
                                             "industry": industry, "members": len(members),
                                             "gross_return": mean(valid_labels[x["symbol"]]["gross_return"] for x in members)})
    date_index = {d: i for i, d in enumerate(dates)}
    for event in events:
        event["forward_labels"] = {str(h): label(prices, event["symbol"], dates, date_index[event["signal_date"]], h) for h in (20, 60)}
    by_symbol = sealed["attribution"]["by_symbol"]
    capture = {s: {"net_pnl": by_symbol.get(s, {}).get("total_pnl", 0), "held_days": held_days[s],
                   "mean_account_weight": exposures[s] / len(dates), "mature_days": mature_days[s],
                   "ready_days": ready_days[s]} for s in sorted(set(mature_days) | set(by_symbol))}
    return {"native_readback": True, "result_seal": sealed["canonical_sha256"],
            "raw_sha256": sealed["raw_sha256"], "observations_file_sha256": digest(root / "observations.jsonl.gz"),
            "source": SOURCE, "config_sha256": sealed["identity"]["config_sha256"],
            "data_identity": sealed["identity"]["data"], "sessions": len(dates),
            "metrics": sealed["metrics"], "summary": summarize(records), "records": records,
            "industry_records": industry_records, "excluded": missing, "feature_exclusions": feature_exclusions,
            "capture": capture,
            "mature_unheld_symbol_day_blocks": dict(stages), "actual_fill_labels": events,
            "net_industry_pnl": {s: x["total_pnl"] for s, x in sealed["attribution"]["by_industry"].items()}}


def bull_diagnostic(path: Path, prices: dict[str, dict[str, float]], dates: list[str]) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    first = {}
    for row in raw["rows"]:
        # This trace deliberately has a different, lossless observer schema.
        for leader in row["leaders"]:
            if (leader["symbol"] in ("sz300308", "sz300394", "sz300502")
                    and leader["mature"] and leader["symbol"] not in first):
                first[leader["symbol"]] = row["date"]
    return {"input_sha256": digest(path), "matched_saved_decisions": raw["matched_saved_decisions"],
            "first_mature": first, "labels": [
                {"symbol": symbol, "signal_date": day, "kind": kind,
                 "forward": {str(h): label(prices, symbol, dates, dates.index(day), h) for h in (20, 60)}}
                for symbol in ("sz300308", "sz300394", "sz300502")
                for kind, day in (("first_mature", first.get(symbol)), ("original_first_entry", "2025-07-30")) if day]}


def universe_provenance() -> dict[str, Any]:
    path = Path("uquant/contracts/resources/ai_universe_manifest.json")
    manifest = json.loads(path.read_text())
    rows = []
    for member in manifest["members"]:
        with Path(f"data/frozen/{member['symbol']}.csv").open() as stream:
            first_date = next(csv.DictReader(stream))["date"]
        rows.append({**member, "first_price_date": first_date,
                     "effective_from_equals_first_price": first_date == member["effective_from"]})
    return {"manifest_file_sha256": digest(path), "manifest_seal": manifest["canonical_sha256"],
            "members": rows, "interpretation": "Metadata gap, not proof of look-ahead: price history and retrospective review alone do not establish contemporaneous AI membership knowledge."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--bull", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data_identity = verify_data_manifest(Path("data/frozen"))
    prices, hashes = {}, {}
    for path in sorted(Path("data/frozen").glob("*.csv")):
        with path.open() as stream:
            prices[path.stem] = {r["date"]: float(r["open"]) for r in csv.DictReader(stream) if r["date"] <= END}
        hashes[path.name] = digest(path)
    result: dict[str, Any] = {"diagnostic_only": True, "future_holdout_used": False, "price_file_hashes": hashes, "cases": {}}
    result["universe_provenance"] = universe_provenance()
    for name in NAMES:
        result["cases"][name] = analyze(args.runs / name, name, prices)
        if result["cases"][name]["data_identity"] != data_identity:
            raise ValueError("forward-label data differs from native observation data")
        print(name, "native readback and analysis complete", flush=True)
    dates = sorted(d for d in prices["sh000300"] if "2025-04-01" <= d <= END)
    result["bull"] = bull_diagnostic(args.bull, prices, dates)
    args.output.write_bytes(gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0))
    print("saved", args.output, digest(args.output), flush=True)


if __name__ == "__main__":
    main()
