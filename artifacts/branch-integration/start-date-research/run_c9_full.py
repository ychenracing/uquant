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
    ("full-offset5", u, plan["clusters"]["2023"]["5"], 1),
    ("full-offset5-cost2", u, plan["clusters"]["2023"]["5"], 2),
    ("full", u, plan["clusters"]["2023"]["0"], 1),
]
cases.extend(
    ("loo-" + symbol, [s for s in u if s != symbol], plan["clusters"]["2023"]["0"], 1)
    for symbol in ("sz300308", "sz300502")
)
cases.append(
    (
        "remove_all_three",
        [s for s in u if s not in {"sz300308", "sz300502", "sz300394"}],
        plan["clusters"]["2023"]["0"],
        1,
    )
)
cases.extend(("full-offset" + offset, u, plan["clusters"]["2023"][offset], 1) for offset in ("1", "10"))
cases.extend(
    [
        ("d-continuous_ai_era", f["pools"]["d"], plan["clusters"]["2023"]["0"], 1),
        ("full-cost2", u, plan["clusters"]["2023"]["0"], 2),
    ]
)
jobs = [("c9", ROOT.parent / "uquant-c9", case) for case in cases]


def go(job):
    return run(
        job,
        runs=ROOT.parent / "robustness-runs",
        data_dir=ROOT / "data/frozen",
        contract=CONTRACT,
        end=json.loads((ROOT / "artifacts/branch-integration/START_DATE_CONTRACT.json").read_text())["end"],
    )


with concurrent.futures.ThreadPoolExecutor(max_workers=6) as p:
    results = list(p.map(go, jobs))
    for result in results:
        print(json.dumps(result), flush=True)
raise SystemExit(any(r["exit_code"] for r in results))
