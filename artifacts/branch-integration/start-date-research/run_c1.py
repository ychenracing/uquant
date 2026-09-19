import concurrent.futures
import importlib.util
import json
from pathlib import Path

root = Path.cwd()
s = importlib.util.spec_from_file_location("pairs", root / "artifacts/branch-integration/run_pairs.py")
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
c = json.loads(m.CONTRACT.read_text())
plan = json.loads((root / "artifacts/branch-integration/START_DATE_CONTRACT.json").read_text())
rows = [
    (
        "c1",
        (root.parent / "uquant-c1"),
        ("full" if i == 0 else f"full-offset{i}", c["universe"], plan["clusters"]["2023"][str(i)], 1),
    )
    for i in [0, 1, 5, 10]
]


def run(row):
    return m.run(
        row,
        runs=root.parent / "robustness-runs",
        data_dir=root / "data/frozen",
        contract=m.CONTRACT,
        end=c["window"]["end"],
    )


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(run, rows))
    for r in results:
        print(json.dumps(r), flush=True)
raise SystemExit(any(r["exit_code"] for r in results))
