"""Read-only mature-sector prerequisites from already verified native observations.

This does not simulate orders or claim complete entry permission. Current
structure/liquidity and all account restrictions still need native validation.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from uquant.config import DEFAULT_CONFIG


def audit(directory: Path, *, before: str) -> dict[str, Any]:
    cfg = DEFAULT_CONFIG
    identity = json.loads((directory / "identity.json").read_text())
    if identity["end"] > "2026-08-05":
        raise ValueError("protected future input is outside this audit")
    hits: list[dict[str, Any]] = []
    for path in sorted((directory / "observations").glob("*.json")):
        if path.stem >= before:
            break
        day = json.loads(path.read_text())["observation"]
        observed = day["observation"]
        roles = observed["strategic_universe_roles"]
        if roles["as_of"] != path.stem:
            raise ValueError("stale native roles")
        available = set(roles["tradable_symbols"]) & set(roles["available_symbols"])
        groups: dict[str, list[str]] = defaultdict(list)
        for leader in observed["leader_scores"]:
            symbol = leader["symbol"]
            if (symbol in available and leader["mature"] and leader["score"] >= .82
                    and leader["confidence"] >= cfg.leader_min_confidence
                    and leader["industry"] != "unknown"
                    and leader["components"].get("unknown_industry", 1.) < .5
                    and day["state"]["leader_tenure"].get(symbol, 0) >= cfg.leader_tenure_days):
                groups[leader["industry"]].append(symbol)
        supported = {industry: sorted(symbols) for industry, symbols in sorted(groups.items())
                     if len(symbols) >= cfg.strategic_cohort_min_size}
        if supported:
            hits.append({"date": path.stem, "groups": supported,
                         "opportunity": day["decision"]["opportunity"],
                         "risk": day["decision"]["risk"],
                         "held_symbols": sorted(day["state"]["positions"])})
    return {"scope": "read_only_prerequisites_not_counterfactual_returns",
            "native_identity": identity, "exclusive_end": before,
            "selection": "actual available tradable members; same known industry; credible .82; mature and own/peer tenure5; existing full quorum3",
            "remaining_guards": "current structure/liquidity/history, stale evidence, actual funding, repair order provenance and all risk controls",
            "hit_sessions": len(hits), "hits": hits}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--before", default="2024-02-22")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.case_dir, before=args.before)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print({"case": result["native_identity"]["case_id"], "hit_sessions": result["hit_sessions"],
           "first_hits": result["hits"][:2]})


if __name__ == "__main__":
    main()
