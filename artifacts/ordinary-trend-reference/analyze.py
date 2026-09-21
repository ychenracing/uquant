"""One paired decision table with cash-reconciled natural-year attribution."""

import gzip
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from uquant.account.codec import account_from_dict  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "existing_metrics", ROOT / "artifacts/trend-continuity/compare.py"
)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)
BASELINE = ROOT.parent / "uquant/artifacts/targeted-mature-capital/raw"


def attribute(data):
    result = data["result"]
    fills = defaultdict(list)
    for fill in result["final_account"]["fills"]:
        fills[fill["fill_date"]].append(fill)
    previous_values = {}
    previous_equity = data["config"]["initial_cash"]
    daily, annual = {}, defaultdict(lambda: defaultdict(float))
    for day in result["daily_replay_evidence"]:
        date = day["date"]
        values = {s: shares * day["close_marks"][s] for s, shares in day["position_shares"].items()}
        pnl = {
            s: values.get(s, 0) - previous_values.get(s, 0) for s in values.keys() | previous_values.keys()
        }
        for fill in fills[date]:
            fees = sum(fill[k] for k in ("commission", "stamp_duty", "transfer_fee"))
            # Fill price already includes slippage; never subtract it twice.
            pnl[fill["symbol"]] = (
                pnl.get(fill["symbol"], 0)
                + (fill["gross_value"] if fill["side"] == "SELL" else -fill["gross_value"])
                - fees
            )
        equity = day["cash"] + sum(values.values())
        assert math.isclose(sum(pnl.values()), equity - previous_equity, rel_tol=0, abs_tol=1e-6)
        for symbol, value in pnl.items():
            daily[(date, symbol)] = value
            annual[date[:4]][symbol] += value
        previous_values, previous_equity = values, equity
    assert math.isclose(
        sum(daily.values()), result["final_equity"] - data["config"]["initial_cash"], rel_tol=0, abs_tol=1e-6
    )
    return daily, dict(annual)


rows, daily_deltas, seen_clock, positive_occurrences = [], {}, {}, defaultdict(set)
request_tables = {}
account_checks = []
for case in ("full", "2024", "loo308", "no_three"):
    bp, cp = BASELINE / f"{case}.json.gz", HERE / "raw" / f"{case}.json.gz"
    b, c = metrics.read(bp), metrics.read(cp)
    for key in ("symbols", "start", "end", "config", "runtime", "runner_sha256"):
        assert b[key] == c[key], (case, key)
    assert {k: v for k, v in b["inputs"].items() if k.startswith("data/")} == {
        k: v for k, v in c["inputs"].items() if k.startswith("data/")
    }
    bm, cm = metrics.metrics(b), metrics.metrics(c)
    for label, data, m in [("B", b, bm), ("R", c, cm)]:
        fills = data["result"]["final_account"]["fills"]
        m["fills"] = len(fills)
        account = data["result"]["final_account"]
        assert account_from_dict(account).to_dict() == account
        assert all(d["cash"] >= 0 for d in data["result"]["daily_replay_evidence"])
        assert all(f["fill_date"] > f["signal_date"] and f["shares"] > 0 for f in fills)
        account_checks.append(
            dict(
                case=case,
                variant=label,
                source=data["source_head"],
                exact_account_roundtrip=True,
                nonnegative_cash=True,
                next_session_fills=True,
            )
        )
    bd, by = attribute(b)
    cd, cy = attribute(c)
    delta = {key: cd.get(key, 0) - bd.get(key, 0) for key in cd.keys() | bd.keys()}
    daily_deltas[case] = [dict(date=d, symbol=s, delta=value) for (d, s), value in sorted(delta.items())]
    for key, value in delta.items():
        if value > 1e-6:
            positive_occurrences[key].add(case)
    years = []
    for year in sorted(by.keys() | cy.keys()):
        changes = {
            s: cy.get(year, {}).get(s, 0) - by.get(year, {}).get(s, 0)
            for s in by.get(year, {}).keys() | cy.get(year, {}).keys()
        }
        years.append(
            dict(
                year=year,
                baseline_pnl=sum(by.get(year, {}).values()),
                reference_pnl=sum(cy.get(year, {}).values()),
                delta=sum(changes.values()),
                by_symbol=changes,
            )
        )
    assert math.isclose(
        sum(y["delta"] for y in years),
        c["result"]["final_equity"] - b["result"]["final_equity"],
        rel_tol=0, abs_tol=1e-6,
    )
    with gzip.open(HERE / "raw" / f"{case}.json.requests.json.gz", "rt") as stream:
        requests = json.load(stream)
    for day in requests:
        old = seen_clock.setdefault(day["date"], day["clock"])
        assert old == day["clock"], "clock differs by pool or account start"
    selected = []
    btrace = {r["date"]: r for r in b["result"]["decision_trace"]}
    bdays = {r["date"]: r for r in b["result"]["attribution"]["daily_ledger"]}
    rfills = c["result"]["final_account"]["fills"]
    for day in requests:
        if day["clock"]["ordinal"] % 20:
            continue
        for symbol, row in day["symbols"].items():
            if "ordinary_equal_target" not in row:
                continue
            selected.append(
                dict(
                    date=day["date"],
                    symbol=symbol,
                    rank=row.get("ordinary_trend_rank"),
                    actual_weight=day["actual"].get(symbol, 0),
                    requested_weight=row["ordinary_equal_target"],
                    proposed_weight=day["proposed"].get(symbol, 0),
                    permission_open=day["permission_open"],
                    block=row.get("entry_gate"),
                    budget_checks=row.get("budget_checks", []),
                    baseline_target=next(
                        (t["weight"] for t in btrace[day["date"]]["targets"] if t["symbol"] == symbol), 0
                    ),
                    baseline_risk=btrace[day["date"]]["risk"],
                    baseline_cash_weight=bdays[day["date"]]["cash_weight"],
                    actual_fills=[
                        dict(date=f["fill_date"], side=f["side"], shares=f["shares"])
                        for f in rfills
                        if f["signal_date"] == day["date"] and f["symbol"] == symbol
                    ],
                )
            )
    ratio = cm["wealth"] / bm["wealth"]
    screen = dict(
        wealth_floor=ratio >= (0.98 if case == "full" else 0.95),
        drawdown_absolute=cm["drawdown"] <= 0.30,
        drawdown_increment=cm["drawdown"] - bm["drawdown"] <= 0.01,
    )
    burden = dict(
        operation_days=cm["operation_days"]
        <= math.ceil(max(bm["operation_days"] + 3, 1.15 * bm["operation_days"])),
        turnover=cm["turnover"] <= 1.15 * bm["turnover"],
        cost=cm["cost_burden"] <= 1.15 * bm["cost_burden"],
    )
    request_tables[case] = selected
    rows.append(
        dict(
            case=case,
            baseline=bm,
            reference=cm,
            wealth_ratio=ratio,
            screen_gates=screen,
            final_burden_gates=burden,
            annual_attribution=years,
            request_summary=dict(
                total=len(selected),
                with_fills=sum(bool(r["actual_fills"]) for r in selected),
                baseline_target_absent=sum(r["baseline_target"] == 0 for r in selected),
                permission_closed=sum(not r["permission_open"] for r in selected),
                budget_rejected=sum(
                    any(not d.get("accepted", False) for d in r["budget_checks"]) for r in selected
                ),
            ),
            baseline_source=b["source_head"],
            reference_source=c["source_head"],
            original_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (bp, cp)},
            contract_hashes={"B": b["contract_sha256"], "R": c["contract_sha256"]},
        )
    )
ratios = [r["wealth_ratio"] for r in rows]
weak = [r["wealth_ratio"] for r in rows if r["case"] in ("loo308", "no_three")]


def gm(values):
    return math.exp(statistics.mean(math.log(v) for v in values))


positive_years = sorted({y["year"] for r in rows for y in r["annual_attribution"] if y["delta"] > 1e-6})
gates = dict(
    four_geomean=gm(ratios) > 1,
    weak_geomean=gm(weak) > 1,
    neither_weak_degrades=all(v >= 1 for v in weak),
    individual=all(all(r["screen_gates"].values()) for r in rows),
    positive_year_count=len(positive_years) >= 2,
)
report = dict(
    rows=rows,
    geometric_wealth_ratio=gm(ratios),
    weak_geometric_wealth_ratio=gm(weak),
    gates=gates,
    mechanical_screen=all(gates.values()),
    positive_years=positive_years,
    positive_symbol_sessions=len(positive_occurrences),
    positive_symbol_sessions_repeated_across_pools=sum(len(v) > 1 for v in positive_occurrences.values()),
    limitation="Annual blocks and repeated pool/date/symbol observations are not independent samples. "
    "A mechanical pass still requires qualitative winner/date concentration review. "
    "Different-account baseline omissions do not establish a causal alpha-gate defect.",
    omitted=["2025", "loo502", "full_cost2", "2024_cost2", "fixed_real_legacy"],
    baseline_reuse="Same main economic source, config, native runner and frozen data; original source and prior task-contract identity retained.",
)
(HERE / "REQUEST_ATTRIBUTION.json.gz").write_bytes(
    gzip.compress(json.dumps(request_tables).encode(), mtime=0)
)
(HERE / "COMPARISON.json").write_text(json.dumps(report, indent=2))
(HERE / "ACCOUNT_CHECKS.json").write_text(json.dumps(account_checks, indent=2))
(HERE / "DAILY_ATTRIBUTION.json.gz").write_bytes(gzip.compress(json.dumps(daily_deltas).encode(), mtime=0))
print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
for r in rows:
    print(
        r["case"], r["wealth_ratio"], r["reference"]["drawdown"], r["screen_gates"], r["final_burden_gates"]
    )
