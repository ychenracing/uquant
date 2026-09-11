"""Summarize verified paired industry-input accounts without fitting a policy."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, cast

from research.opportunity_capture_audit import END, label, select, valid_features
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.validation.manifest import verify_data_manifest


def read_rows(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = json.loads((directory / "result.json").read_bytes())
    seal = result["canonical_sha256"]
    if hashlib.sha256(canonical_json_bytes({k: v for k, v in result.items()
                                          if k != "canonical_sha256"})).hexdigest() != seal:
        raise ValueError("result seal mismatch")
    proof = json.loads((directory / "readback.json").read_bytes())
    if (not proof["native_readback"] or proof["result_sha256"] != result["canonical_sha256"]
            or result["status"] != "COMPLETE"):
        raise ValueError("missing native readback")
    raw = directory / "observations.jsonl.gz"
    if hashlib.sha256(raw.read_bytes()).hexdigest() != result["raw_sha256"]:
        raise ValueError("observation hash mismatch")
    if hashlib.sha256((directory / "final_account.json").read_bytes()).hexdigest() != result["final_account_sha256"]:
        raise ValueError("account hash mismatch")
    with gzip.open(raw, "rt") as stream:
        rows = [json.loads(line) for line in stream]
    if [r["date"] for r in rows] != result["identity"]["session_dates"]:
        raise ValueError("session identity mismatch")
    return result, rows


def economic_targets(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [{k: t[k] for k in ("symbol", "weight", "lifecycle", "reason_code", "exit_kind")}
            for t in row["decision"]["targets"]]


def compare(old_result: dict[str, Any], new_result: dict[str, Any], old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> dict[str, Any]:
    for key in ("source_sha256", "config_sha256", "data", "runtime", "session_dates",
                "extra_excluded_symbols", "runner_sha256"):
        if old_result["identity"][key] != new_result["identity"][key]:
            raise ValueError(f"paired identity mismatch: {key}")
    first = {}
    for old, new in zip(old_rows, new_rows, strict=True):
        a, b = old["observation"], new["observation"]
        for role in ("tradable_symbols", "qualification_reference_symbols", "risk_reference_symbols"):
            if a["strategic_universe_roles"][role] != b["strategic_universe_roles"][role]:
                raise ValueError(f"paired role mismatch: {role}")
        stages = {
            "score": ([ (x["symbol"], x["score"], x["mature"]) for x in a["leader_scores"]],
                      [ (x["symbol"], x["score"], x["mature"]) for x in b["leader_scores"]]),
            "qualification": tuple({k: v for k, v in obs["strategic_qualification"].items()
                                      if k != "qualification_evidence_sha256"} for obs in (a, b)),
            "risk_state": (old["decision"]["risk"], new["decision"]["risk"]),
            "opportunity": (old["decision"]["opportunity"], new["decision"]["opportunity"]),
            "targets": (economic_targets(old), economic_targets(new)),
            "equity": (old["equity"], new["equity"]),
        }
        for stage, (left, right) in stages.items():
            if left != right and stage not in first:
                first[stage] = {"date": old["date"], "old": left, "revised": right}
    return {"paired_identity_and_roles": True, "first_divergence": first,
            "old": {k: old_result[k] for k in ("identity", "canonical_sha256", "metrics", "attribution")},
            "revised": {k: new_result[k] for k in ("identity", "canonical_sha256", "metrics", "attribution")}}


def ranking(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]], prices: dict[str, dict[str, float]]) -> dict[str, Any]:
    dates = [r["date"] for r in old_rows]
    records: list[dict[str, Any]] = []
    missing = []
    for index in range(0, len(dates), 60):
        old, new = old_rows[index], new_rows[index]
        a, b = old["observation"], new["observation"]
        tradable = set(b["strategic_universe_roles"]["tradable_symbols"])
        industries = dict(b["strategic_universe_roles"]["point_in_time_industries"])
        leaders = [{x["symbol"]: x for x in obs["leader_scores"]
                    if x["symbol"] in tradable and valid_features(x)} for obs in (a, b)]
        common = sorted(set(leaders[0]) & set(leaders[1]))
        for cohort in ("nonoptical", "nonoptical_mature"):
            symbols = [s for s in common if industries[s] != "optical" and
                       (cohort == "nonoptical" or all(ls[s]["mature"] for ls in leaders))]
            for horizon in (20, 60):
                outcomes = {s: label(prices, s, dates, index, horizon) for s in symbols}
                if len(symbols) < 3 or any(v is None for v in outcomes.values()):
                    missing.append({"date": dates[index], "cohort": cohort, "horizon": horizon,
                                    "reason": "fewer_than_three_or_missing_endpoint"})
                    continue
                valid_outcomes = cast(dict[str, dict[str, Any]], outcomes)
                pool_return = mean(v["gross_return"] for v in valid_outcomes.values())
                row: dict[str, Any] = {"date": dates[index], "cohort": cohort, "horizon": horizon,
                       "symbols": symbols, "pool_return": pool_return, "labels": outcomes}
                for arm, ls in zip(("old", "revised"), leaders, strict=True):
                    methods = {}
                    for method in ("score", "momentum", "industry_first"):
                        chosen = select([ls[s] for s in symbols], method)
                        value = mean(valid_outcomes[s]["gross_return"] for s in chosen)
                        methods[method] = {"selected": chosen, "excess": value - pool_return}
                    row[arm] = methods
                records.append(row)
    groups = defaultdict(list)
    for row in records:
        for period in ("all", "2023-24" if row["date"] < "2025" else "2025-26"):
            groups[(row["cohort"], row["horizon"], period)].append(row)
    summary = [{"cohort": key[0], "horizon": key[1], "period": key[2], "anchors": len(rows),
                **{arm: {method: mean(r[arm][method]["excess"] for r in rows)
                          for method in ("score", "momentum", "industry_first")}
                   for arm in ("old", "revised")}} for key, rows in sorted(groups.items())]
    return {"records": records, "missing": missing, "summary": summary,
            "note": "Paired mature intersections; gross forward prices, not account returns or independent OOS."}


def missed_stages(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    examples: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        observation = row["observation"]
        allocation = observation["risk_assessment"]["evidence"].get("core_allocation", {}).get("symbols", {})
        tradable = set(observation["strategic_universe_roles"]["tradable_symbols"])
        for leader in observation["leader_scores"]:
            symbol = leader["symbol"]
            if symbol not in tradable or not leader["mature"] or row["ledger"]["position_weights"].get(symbol, 0) > 0:
                continue
            block = allocation.get(symbol, {}).get("entry", {}).get("block", "UNOBSERVED")
            counts[block] += 1
            if len(examples[block]) < 3:
                examples[block].append({"date": row["date"], "symbol": symbol})
    return {"mature_unheld_symbol_days": dict(counts), "examples": dict(examples),
            "note": "Mature and unheld is not proof of a missed profitable trade; no rule change from these counts alone."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result: dict[str, Any] = {"diagnostic_only": True, "future_holdout_used": False, "cases": {}}
    data_identity = verify_data_manifest(Path("data/frozen"))
    result["label_data_identity"] = data_identity
    result["analysis_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for case in ("full", "remove_all_three", "strict"):
        old_result, old_rows = read_rows(args.runs / f"{case}_old")
        new_result, new_rows = read_rows(args.runs / f"{case}_revised")
        if old_result["identity"]["data"] != data_identity:
            raise ValueError("label prices differ from native account data")
        result["cases"][case] = compare(old_result, new_result, old_rows, new_rows)
        result["cases"][case]["missed_stages"] = missed_stages(new_rows)
        if case == "full":
            prices = {}
            for path in sorted(Path("data/frozen").glob("*.csv")):
                with path.open() as stream:
                    prices[path.stem] = {r["date"]: float(r["open"]) for r in csv.DictReader(stream) if r["date"] <= END}
            result["ranking"] = ranking(old_rows, new_rows, prices)
    with args.output.open("xb") as stream:
        stream.write(gzip.compress(json.dumps(result, allow_nan=False, sort_keys=True).encode(), mtime=0))


if __name__ == "__main__":
    main()
