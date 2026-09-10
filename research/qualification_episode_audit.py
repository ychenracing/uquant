"""Registered all-event qualification diagnosis on immutable native observations."""

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from types import SimpleNamespace as NS
from typing import Any, cast

from uquant.contracts.strict_json import canonical_json_bytes
from uquant.portfolio.strategic.qualification_candidates import (
    independent_market_confirmation,
    strategic_candidate_meets_route,
)
from uquant.portfolio.strategic.quorum import strict_absolute_owner_quality
from uquant.validation.manifest import verify_data_manifest


def main(args: argparse.Namespace) -> None:
    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    data = args.prices
    fingerprint = verify_data_manifest(data)
    prices = {}
    for p in data.glob("*.csv"):
        with p.open() as f:
            prices[p.stem] = {
                r["date"]: float(r["open"]) for r in csv.DictReader(f) if r["date"] <= "2026-08-05"
            }
    out = []
    for case in ["full", "remove_all_three", "strict"]:
        p = args.native_root / (case + "_revised")
        result = json.loads((p / "result.json").read_text())
        seal = result.pop("canonical_sha256")
        assert (
            hashlib.sha256(canonical_json_bytes(result)).hexdigest() == seal
            and result["status"] == "COMPLETE"
        )
        assert (
            result["identity"]["source_sha256"]
            == "3ad3f4b7e63215484c118d05a864a5c17390ddf5ca5442d624ab92f044fa04bc"
        )
        assert result["identity"]["data"] == fingerprint
        assert hashlib.sha256((p / "observations.jsonl.gz").read_bytes()).hexdigest() == result["raw_sha256"]
        cfg: Any = NS(**result["identity"]["effective_config"])
        dates = []
        events: list[dict[str, Any]] = []
        counts: Counter[tuple[str, str]] = Counter()
        prev_mature: set[str] = set()
        mismatches = []
        progression = []
        previous_streaks: dict[str, int] = {}
        with gzip.open(p / "observations.jsonl.gz", "rt") as stream:
            for index, line in enumerate(stream):
                row = json.loads(line)
                date = row["date"]
                dates.append(date)
                obs = row["observation"]
                state = row["state"]
                risk: Any = NS(**obs["risk_assessment"])
                e = risk.evidence
                leaders: dict[str, Any] = {r["symbol"]: NS(**r) for r in obs["leader_scores"]}
                snap = obs["qualification_snapshots"]
                allowed = set(obs["strategic_universe_roles"]["tradable_symbols"])
                current = set()
                trace = e["core_allocation"]
                market = independent_market_confirmation(cfg=cfg, risk=risk)
                market_checks = {
                    "breadth": e["breadth20"] >= cfg.high_confidence_entry_breadth,
                    "short_market_return": min(e["broad_ret20"], e["tech_ret20"])
                    >= cfg.strategic_transition_impulse_min_market_ret20,
                    "long_market_lower": max(e["broad_ret120"], e["tech_ret120"])
                    > cfg.recovery_transition_weak_leg_ret120,
                    "long_market_upper": max(e["broad_ret120"], e["tech_ret120"])
                    <= cfg.strategic_long_cycle_max_tech_ret120,
                }
                assert market == all(market_checks.values())
                for symbol in allowed:
                    leader = leaders.get(symbol)
                    t = trace["symbols"].get(symbol, {})
                    held = t.get("held_weight", 0) > 0
                    if leader is None or not leader.mature or held:
                        continue
                    current.add(symbol)
                    owner = strict_absolute_owner_quality(
                        symbol=symbol, snapshots=snap, leaders=leaders, cfg=cfg
                    )
                    routes = [
                        route
                        for route in (
                            "established",
                            "transition",
                            "transition_impulse",
                            "persistent_industry",
                            "reversal_industry",
                        )
                        if strategic_candidate_meets_route(
                            candidate_symbol=symbol,
                            qualification_route=route,
                            snapshots={symbol: snap[symbol]} if symbol in snap else {},
                            leaders=leaders,
                            risk=risk,
                            cfg=cfg,
                        )
                    ]
                    streak = state["replacement_tenure"].get(
                        "strategic_eligibility:independent_core:" + symbol, 0
                    )
                    pred = market and owner and bool(routes)
                    entry = t.get("entry", {})
                    block = entry.get("block", "NO_ENTRY_TRACE")
                    failures = (
                        tuple(k for k, v in market_checks.items() if not v)
                        + (() if owner else ("absolute_owner_quality",))
                        + (() if routes else ("absolute_route",))
                    )
                    counts[(block, "|".join(failures) or "all_current_predicates_true")] += 1
                    if (
                        pred
                        and streak
                        != previous_streaks.get("strategic_eligibility:independent_core:" + symbol, 0) + 1
                    ):
                        progression.append(
                            {
                                "date": date,
                                "symbol": symbol,
                                "previous": previous_streaks.get(
                                    "strategic_eligibility:independent_core:" + symbol, 0
                                ),
                                "current": streak,
                                "entry": entry,
                                "new_fills": row["new_fills"],
                                "grant": state["strategic_grant"],
                            }
                        )
                    if not pred and streak > 0:
                        mismatches.append(
                            {"date": date, "symbol": symbol, "streak": streak, "failed_predicates": failures}
                        )
                    if symbol not in prev_mature:
                        events.append(
                            {
                                "date": date,
                                "index": index,
                                "symbol": symbol,
                                "industry": leader.industry,
                                "block": block,
                                "streak": streak,
                                "failed_predicates": failures,
                                "market_checks": market_checks,
                                "owner_quality": owner,
                                "routes": routes,
                                "freeze": trace["final_freeze_new_risk"],
                                "gross_cap": trace["final_gross_cap"],
                            }
                        )
                prev_mature = current
                previous_streaks = state["replacement_tenure"]
        for event in events:
            i = event["index"]
            symbol = event["symbol"]
            event["labels"] = {}
            for h in (20, 60):
                if i + h + 1 >= len(dates):
                    continue
                sample = [prices[symbol].get(d) for d in dates[i + 1 : i + h + 2]]
                if any(v is None or not math.isfinite(v) or v <= 0 for v in sample):
                    continue
                valid_sample = cast(list[float], sample)
                event["labels"][str(h)] = {
                    "return": valid_sample[-1] / valid_sample[0] - 1,
                    "mae_open": min(valid_sample) / valid_sample[0] - 1,
                    "mfe_open": max(valid_sample) / valid_sample[0] - 1,
                    "entry": dates[i + 1],
                    "exit": dates[i + h + 1],
                }
        groups = defaultdict(list)
        for event in events:
            for h, label in event["labels"].items():
                for subset in ("full", "nonoptical") if event["industry"] != "optical" else ("full",):
                    groups[(subset, event["block"], h)].append(label)
        summary = [
            {
                "subset": s,
                "block": b,
                "horizon": int(h),
                "events": len(v),
                "positive_fraction": mean(x["return"] > 0 for x in v),
                "mean_return": mean(x["return"] for x in v),
                "median_return": median(x["return"] for x in v),
                "median_mae_open": median(x["mae_open"] for x in v),
            }
            for (s, b, h), v in sorted(groups.items())
        ]
        out.append(
            {
                "case": case,
                "native_seal": seal,
                "predicate_streak_mismatches": mismatches,
                "progression_exceptions": progression,
                "mature_unheld_days": [
                    {"block": b, "failed_predicates": f, "days": n} for (b, f), n in counts.most_common()
                ],
                "events": events,
                "summary": summary,
            }
        )
        print(case, "events", len(events), "counter_mismatches", len(mismatches), flush=True)
        with gzip.open(root / "result.json.gz", "wt") as f:
            json.dump(out, f, allow_nan=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
