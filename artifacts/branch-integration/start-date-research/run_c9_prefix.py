import concurrent.futures
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "artifacts/branch-integration"))
from run_pairs import CONTRACT, ROOT, run

f = json.loads(CONTRACT.read_text())
u = f["universe"]
plan = json.loads((ROOT / "artifacts/branch-integration/START_DATE_CONTRACT.json").read_text())
cases = [
    ("full", u, plan["clusters"]["2023"]["0"], 1),
    ("full-offset5", u, plan["clusters"]["2023"]["5"], 1),
    ("full-offset10", u, plan["clusters"]["2023"]["10"], 1),
    ("full-offset5-cost2", u, plan["clusters"]["2023"]["5"], 2),
]


def go(case):
    return run(
        ("c9-prefix", ROOT.parent / "uquant-c9", case),
        runs=ROOT.parent / "robustness-runs",
        data_dir=ROOT / "data/frozen",
        contract=CONTRACT,
        end="2023-11-01",
    )


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as p:
    results = list(p.map(go, cases))
for r in results:
    print(json.dumps(r), flush=True)
raise SystemExit(any(r["exit_code"] for r in results))
