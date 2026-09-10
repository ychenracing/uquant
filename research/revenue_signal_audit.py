"""Fixed dated-revenue information screen, never an executable return claim."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import correlation, mean
from typing import Any, cast

import numpy as np

from research.opportunity_capture_audit import label, rank_ic, ranks
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.validation.manifest import verify_data_manifest


def report_signals(rows: list[dict[str, Any]], date: str) -> dict[str, Any] | None:
    available = [r for r in rows if r["status"] == "verified_numeric_original"
                 and r["disclosed_date"] < date]
    periods = {}
    for row in sorted(available, key=lambda r: r["disclosed_date"]):
        periods[row["period_index"]] = row
    if not periods:
        return None
    latest = periods[max(periods)]
    previous = periods.get(latest["period_index"] - 1)
    growth = latest["cumulative_revenue_yoy_percent"]
    return {"revenue_yoy": growth,
            "revenue_yoy_change": growth - previous["cumulative_revenue_yoy_percent"] if previous else None,
            "period": latest["period"], "disclosed_date": latest["disclosed_date"],
            "source_sha256": latest["sha256"],
            "previous_source_sha256": previous["sha256"] if previous else None}


def partial_rank_ic(signal: list[float], outcomes: list[float], momentum: list[float]) -> float | None:
    if len(signal) < 4:
        return None
    x, y, z = (np.asarray(ranks(v), dtype=float) for v in (signal, outcomes, momentum))
    design = np.column_stack([np.ones(len(z)), z])
    x -= design @ np.linalg.lstsq(design, x, rcond=None)[0]
    y -= design @ np.linalg.lstsq(design, y, rcond=None)[0]
    if np.linalg.norm(x) < 1e-10 or np.linalg.norm(y) < 1e-10:
        return None
    return float(correlation(x.tolist(), y.tolist()))


def analyze(panel: list[dict[str, Any]], native: Path, prices_dir: Path) -> dict[str, Any]:
    sealed = json.loads((native / "result.json").read_text())
    seal = sealed.pop("canonical_sha256")
    if hashlib.sha256(canonical_json_bytes(sealed)).hexdigest() != seal or sealed["status"] != "COMPLETE":
        raise ValueError("native source result is incomplete or corrupt")
    if sealed["identity"]["source_sha256"] != "3ad3f4b7e63215484c118d05a864a5c17390ddf5ca5442d624ab92f044fa04bc":
        raise ValueError("wrong preregistered main-policy producer")
    if hashlib.sha256((native / "observations.jsonl.gz").read_bytes()).hexdigest() != sealed["raw_sha256"]:
        raise ValueError("native observations changed")
    if verify_data_manifest(prices_dir) != sealed["identity"]["data"]:
        raise ValueError("forward prices differ from the native frozen inputs")
    reports = defaultdict(list)
    for row in panel:
        reports[row["symbol"]].append(row)
    prices = {}
    for path in prices_dir.glob('*.csv'):
        with path.open() as stream:
            prices[path.stem] = {r["date"]: float(r["open"]) for r in csv.DictReader(stream)
                                 if r["date"] <= "2026-08-05"}
    anchors, dates = [], []
    with gzip.open(native / "observations.jsonl.gz", "rt") as stream:
        for index, line in enumerate(stream):
            row = json.loads(line)
            dates.append(row["date"])
            assert row["date"] <= "2026-08-05"
            if index % 60 == 0:
                obs = row["observation"]
                allowed = set(obs["strategic_universe_roles"]["tradable_symbols"])
                anchors.append((index, [x for x in obs["leader_scores"] if x["symbol"] in allowed]))
    records, exclusions = [], []
    for index, leaders in anchors:
        features = []
        for leader in leaders:
            symbol = leader["symbol"]
            financial = report_signals(reports[symbol], dates[index])
            momentum = leader["components"].get("raw_ret120")
            if financial is None or not isinstance(momentum, (int, float)) or not math.isfinite(momentum):
                exclusions.append({"date": dates[index], "symbol": symbol, "reason": "missing_causal_input"})
                continue
            features.append({"symbol": symbol, "industry": leader["industry"],
                             "ret120": momentum, **financial})
        for subset in ("full", "nonoptical"):
            for signal in ("revenue_yoy", "revenue_yoy_change"):
                pool = [x for x in features if x[signal] is not None
                        and (subset == "full" or x["industry"] != "optical")]
                for horizon in (20, 60):
                    labels = [label(prices, x["symbol"], dates, index, horizon) for x in pool]
                    if len(pool) < 4 or any(v is None for v in labels):
                        exclusions.append({"date": dates[index], "subset": subset, "signal": signal,
                                           "horizon": horizon, "reason": "small_or_censored_population"})
                        continue
                    returns = [v["gross_return"] for v in cast(list[dict[str, Any]], labels)]
                    values, momentum = [x[signal] for x in pool], [x["ret120"] for x in pool]
                    records.append({"date": dates[index], "subset": subset, "signal": signal,
                                    "horizon": horizon, "n": len(pool), "features": pool, "labels": labels,
                                    "rank_ic": rank_ic(values, returns),
                                    "partial_rank_ic": partial_rank_ic(values, returns, momentum)})
    groups = defaultdict(list)
    for row in records:
        for period in ("all", "2023-24" if row["date"] < "2025" else "2025-26"):
            groups[(row["subset"], row["signal"], row["horizon"], period)].append(row)
    summary = []
    for (subset, signal, horizon, period), rows in sorted(groups.items()):
        item = dict(subset=subset, signal=signal, horizon=horizon, period=period, anchors=len(rows))
        for metric in ("rank_ic", "partial_rank_ic"):
            values = [r[metric] for r in rows if r[metric] is not None]
            item[metric] = mean(values) if values else None
            item[metric + "_anchors"] = len(values)
        summary.append(item)
    return {"diagnostic_only": True, "fresh_oos": False, "native_seal": seal, "summary": summary,
            "records": records, "exclusions": exclusions}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(json.loads(args.panel.read_text()), args.native, args.prices)
    result["panel_sha256"] = hashlib.sha256(args.panel.read_bytes()).hexdigest()
    with gzip.open(args.output, "wt") as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False)
