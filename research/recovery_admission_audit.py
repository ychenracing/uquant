"""Observe existing recovery candidates on a verified empty native prefix.

This reads the actual risk/leader inputs and original candidate predicates. It
does not allocate capital, mutate the producer account, or estimate returns.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
from pathlib import Path

import pandas as pd

from research.cross_ai_acceptance import read_case
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.recovery.cohort_admission import (
    _filter_recovery_candidates,
    _scan_recovery_evidence,
)
from uquant.types import AccountState, LeaderScore, Risk, RiskAssessment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = code_fingerprint()
    verified = read_case(args.prefix, case="champion", interval=["2025-04-01", "2025-05-09"], source=source)
    assert verified["metrics"]["account_orders"] == 0
    symbols = ("sz300308", "sz300394", "sz300502")
    engine = ProductionEngine("data/frozen")
    engine._load(symbols)
    panel = {symbol: engine._features[symbol] for symbol in symbols}
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    rows = []
    with gzip.open(args.prefix / "observations.jsonl.gz", "rt") as stream:
        for line in stream:
            native = json.loads(line)
            observation, state = native["observation"], native["state"]
            assert not state["positions"] and not state["anchor_weights"]
            scores = {item["symbol"]: LeaderScore(**item) for item in observation["leader_scores"]
                      if item["symbol"] in symbols}
            payload = observation["risk_assessment"]
            risk = RiskAssessment(**{**payload, "state": Risk(payload["state"]),
                                      "reasons": tuple(payload["reasons"])})
            account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
            account.replacement_tenure = copy.deepcopy(state["replacement_tenure"])
            date = pd.Timestamp(native["date"])
            candidates, depth = _scan_recovery_evidence(
                policy, date=date, user_panel=panel, leaders=scores, account=account,
            )
            weak_market = max(float(risk.evidence["broad_ret120"]), float(risk.evidence["tech_ret120"])) <= DEFAULT_CONFIG.recovery_cohort_weak_market_ret120
            filtered, _, count, floor = _filter_recovery_candidates(
                policy, date=date, risk=risk, user_panel=panel, account=account,
                candidates=candidates, crash_depth=depth, level1_recovery_repair=False,
                risk_neutral_recovery_transfer=False, weak_secular_market=weak_market,
            )
            rows.append({"date": native["date"], "opportunity": native["decision"]["opportunity"],
                         "risk": risk.state.value, "frozen": risk.freeze_new_risk,
                         "risk_reasons": risk.reasons, "candidates": [s.symbol for s in candidates],
                         "filtered": [s.symbol for s in filtered], "depth": depth,
                         "deep_count": count, "admission_depth": floor,
                         "original_positions": state["positions"]})
    assert code_fingerprint() == source
    result = {"scope": "observational_candidate_audit_not_executable_profit",
              "source_sha256": source, "native_prefix_seal": verified["canonical_sha256"],
              "producer_account_unchanged": True, "rows": rows}
    with args.output.open("x") as stream:
        json.dump(result, stream, sort_keys=True)
        stream.write("\n")
    for row in rows:
        if row["filtered"]:
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
