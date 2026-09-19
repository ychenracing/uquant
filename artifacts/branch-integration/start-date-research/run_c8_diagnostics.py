import concurrent.futures
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "artifacts/branch-integration"))
from run_pairs import CONTRACT, ROOT, run

f = json.loads(CONTRACT.read_text())
u = f["universe"]
jobs = [
    (
        "c8-entry",
        ROOT.parent / "uquant-c8-entry",
        ("loo-sz300308", [s for s in u if s != "sz300308"], "2023-01-03", 1),
    ),
    ("c8-risk", ROOT.parent / "uquant-c8-risk", ("full-offset5", u, "2023-01-10", 1)),
    ("c8-risk", ROOT.parent / "uquant-c8-risk", ("full-offset5-cost2", u, "2023-01-10", 2)),
]


def go(job):
    return run(
        job,
        runs=ROOT.parent / "robustness-runs",
        data_dir=ROOT / "data/frozen",
        contract=CONTRACT,
        end=json.loads((ROOT / "artifacts/branch-integration/START_DATE_CONTRACT.json").read_text())["end"],
    )


with concurrent.futures.ThreadPoolExecutor(max_workers=3) as p:
    results = list(p.map(go, jobs))
    for result in results:
        print(json.dumps(result), flush=True)
raise SystemExit(any(r["exit_code"] for r in results))
