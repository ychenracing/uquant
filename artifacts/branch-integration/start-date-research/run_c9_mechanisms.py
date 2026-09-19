import concurrent.futures
import importlib.util
import json
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location("pairs", root / "artifacts/branch-integration/run_pairs.py")
pairs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pairs)
contract = json.loads((root / "artifacts/branch-integration/START_DATE_CONTRACT.json").read_text())
rows = []
for key, name, disabled, folder in [
    ("mechanism_window", "recovery-2025", "c9-entry-off", "uquant-c9-entry-off"),
    ("additional_mechanism_window", "normal-tactical-2023", "c9-tactical-off", "uquant-c9-tactical-off"),
]:
    window = contract[key]
    for label, checkout in [("c9", "uquant-c9"), (disabled, folder)]:
        rows.append(
            ((label, root.parent / checkout, (name, window["symbols"], window["start"], 1)), window["end"])
        )


def run(item):
    row, end = item
    return pairs.run(
        row,
        runs=root.parent / "mechanism-runs",
        data_dir=root / "data/frozen",
        contract=pairs.CONTRACT,
        end=end,
    )


with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(run, rows))
for result in results:
    print(json.dumps(result), flush=True)
raise SystemExit(any(r["exit_code"] for r in results))
