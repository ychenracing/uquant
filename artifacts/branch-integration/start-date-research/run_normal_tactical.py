import concurrent.futures
import importlib.util
import json
from pathlib import Path

root = Path.cwd()
s = importlib.util.spec_from_file_location("pairs", root / "artifacts/branch-integration/run_pairs.py")
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
p = json.loads((root / "artifacts/branch-integration/START_DATE_CONTRACT.json").read_text())[
    "additional_mechanism_window"
]
rows = [
    (label, root.parent / folder, ("normal-tactical-2023", p["symbols"], p["start"], 1))
    for label, folder in [("c1", "uquant-c1"), ("tactical-original", "uquant-tactical-original")]
]


def run(row):
    return m.run(
        row,
        runs=root.parent / "mechanism-runs",
        data_dir=root / "data/frozen",
        contract=m.CONTRACT,
        end=p["end"],
    )


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(run, rows))
    for r in results:
        print(json.dumps(r), flush=True)
raise SystemExit(any(r["exit_code"] for r in results))
