"""Native prefix diagnostic using unchanged point-in-time removal transport."""
import dataclasses
import gzip
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import traceback

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import numpy as np
from uquant.config import DEFAULT_CONFIG
from uquant.engine import code_fingerprint
from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.validation.absolute_generalization import build_leave_one_out_scenarios, load_absolute_generalization_contract
from uquant.validation.absolute_generalization.replay import _point_in_time_symbols, run_absolute_generalization_replay_sessions

output = Path(sys.argv[2])
contract = load_absolute_generalization_contract(root / "benchmarks/absolute_generalization_acceptance_contract.json")
scenario = next(s for s in build_leave_one_out_scenarios(contract) if s.removed_symbol == "sz300308")
def inputs():
    paths = subprocess.check_output(
        ["git", "ls-files", "uquant", "data/frozen", "benchmarks", "uv.lock", "pyproject.toml"],
        cwd=root, text=True,
    ).splitlines()
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}
before = inputs()
fingerprint = code_fingerprint()
random.seed(0)
np.random.seed(0)
payload = {
    "status": "PREFIX_DIAGNOSTIC_ONLY", "completed": False,
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "source_fingerprint": fingerprint, "runtime": runtime_environment_provenance(root),
    "source_files": before, "config": dataclasses.asdict(DEFAULT_CONFIG), "seed": 0,
    "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "removed_symbol": scenario.removed_symbol, "contract_sha256": scenario.contract_sha256,
    "interval": {"start": "2023-01-03", "end": "2023-06-30"},
}
try:
    replay = run_absolute_generalization_replay_sessions(
        scenario, data_dir=root / "data/frozen", start="2023-01-03", end="2023-06-30",
        strategic_symbols=("sz300308",),
        symbols_for_session=lambda date: _point_in_time_symbols(scenario=scenario, session=str(date.date()))[0],
    )
    payload["rows"] = [
        {"session": o.session, "equity": o.equity,
         "decision": json.loads(o.decision_payload.canonical_json),
         "fills": [json.loads(f.canonical_json) for f in o.new_fills]}
        for o in replay.observations
    ]
    payload["final_account"] = json.loads(replay.final_account_payload.canonical_json)
    payload["final_equity"] = replay.final_equity
    assert code_fingerprint() == fingerprint and inputs() == before
    payload["completed"] = True
except Exception:
    payload["error"] = traceback.format_exc()
    raise
finally:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(json.dumps(payload, sort_keys=True).encode(), mtime=0))
    print(json.dumps({k: v for k, v in payload.items()
                      if k in ("commit", "completed", "final_equity", "source_fingerprint", "error")}), flush=True)
