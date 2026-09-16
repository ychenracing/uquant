"""Compare the preregistered local release with identical current-main cells."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import subprocess
from pathlib import Path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path: Path, repo: Path) -> dict:
    data = json.loads(gzip.decompress(path.read_bytes()))
    assert data["completed"] and not data.get("error"), path
    head = data["source_head"]
    tree = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", f"{head}:uquant"], text=True,
    ).strip()
    assert tree == data["source_tree"], path
    for name, digest in data["inputs"].items():
        if name.startswith("data/"):
            content = (repo / "data/frozen" / name.removeprefix("data/")).read_bytes()
        else:
            content = subprocess.check_output(["git", "-C", str(repo), "show", f"{head}:{name}"])
        assert sha(content) == digest, (path, name)
    result = data["result"]
    equity = [float(row["equity"]) for row in result["equity_curve"]]
    assert len(equity) == data["sessions"] and all(math.isfinite(x) and x > 0 for x in equity)
    assert math.isclose(equity[-1] / data["config"]["initial_cash"], result["final_wealth"], rel_tol=1e-10)
    peak, drawdown = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    assert math.isclose(drawdown, result["max_drawdown"], abs_tol=1e-10)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads((Path(__file__).parent / "PLAN.json").read_text())
    gate = plan["economic_screen"]
    cases = gate["nominal_cases"] + gate["stress_cases"]
    rows, violations, heads, trees = [], [], set(), set()
    for case in cases:
        paths = [args.runs / label / f"{case}.json.gz" for label in ("main", args.candidate)]
        baseline, candidate = [read(path, args.repo) for path in paths]
        assert baseline["source_head"] == plan["baseline_head"]
        for key in ("case", "config", "symbols", "start", "end", "cost_multiplier", "runtime",
                    "runner_sha256", "contract_sha256", "sessions"):
            assert baseline[key] == candidate[key], (case, key)
        for name, digest in baseline["inputs"].items():
            if not name.startswith("uquant/"):
                assert candidate["inputs"][name] == digest, (case, name)
        heads.add(candidate["source_head"])
        trees.add(candidate["source_tree"])
        left, right = baseline["result"], candidate["result"]
        retention = right["final_wealth"] / left["final_wealth"]
        delta = right["max_drawdown"] - left["max_drawdown"]
        row = {"case": case, "main_wealth": left["final_wealth"], "candidate_wealth": right["final_wealth"],
               "wealth_retention": retention, "main_drawdown": left["max_drawdown"],
               "candidate_drawdown": right["max_drawdown"], "drawdown_change": delta,
               "main_orders": left["account_orders"], "candidate_orders": right["account_orders"],
               "raw": [{"path": str(p.relative_to(args.runs)), "bytes": p.stat().st_size,
                        "sha256": sha(p.read_bytes())} for p in paths]}
        rows.append(row)
        if delta > gate["maximum_drawdown_increase_per_case"]:
            violations.append(f"{case}: drawdown increase")
        if case in gate["stress_cases"] and retention < gate["minimum_stress_wealth_retention_vs_same_main_stress"]:
            violations.append(f"{case}: stress retention")
    assert len(heads) == len(trees) == 1, "Mixed candidate producers"
    nominal = [row["wealth_retention"] for row in rows if row["case"] in gate["nominal_cases"]]
    geometric = math.exp(sum(math.log(value) for value in nominal) / len(nominal))
    if geometric < gate["minimum_nominal_geometric_wealth_retention"]:
        violations.append("nominal geometric wealth retention")
    report = {"status": "ECONOMIC_SCREEN_FAIL" if violations else "ECONOMIC_SCREEN_PASS",
              "baseline_head": plan["baseline_head"], "candidate_head": next(iter(heads)),
              "candidate_production_tree": next(iter(trees)), "cases": rows,
              "nominal_geometric_wealth_retention": geometric, "violations": violations,
              "scope": "Six in-sample paired cells; not universal generalization or future profitability proof.",
              "engineering_and_capability": "Separate evidence required; this report is not merge authorization."}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "candidate_head", "nominal_geometric_wealth_retention", "violations")}))


if __name__ == "__main__":
    main()
