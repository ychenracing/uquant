"""Inspect the missing early deployment evidence; not a full LOO acceptance run."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from uquant.engine import code_fingerprint
from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.validation.absolute_generalization import build_leave_one_out_scenarios, load_absolute_generalization_contract
from uquant.validation.absolute_generalization.replay import _point_in_time_symbols, run_absolute_generalization_replay_sessions

contract = load_absolute_generalization_contract(root / 'benchmarks/absolute_generalization_acceptance_contract.json')
scenario = next(s for s in build_leave_one_out_scenarios(contract) if s.removed_symbol == 'sz300308')
fingerprint = code_fingerprint()
replay = run_absolute_generalization_replay_sessions(
    scenario, data_dir=root / 'data/frozen', start='2023-01-03', end='2023-06-30',
    strategic_symbols=('sz300308',),
    symbols_for_session=lambda date: _point_in_time_symbols(scenario=scenario, session=str(date.date()))[0],
)
rows = []
for observation in replay.observations:
    decision = json.loads(observation.decision_payload.canonical_json)
    rows.append({'session': observation.session, 'equity': observation.equity,
                 'decision': decision, 'fills': [json.loads(f.canonical_json) for f in observation.new_fills]})
assert code_fingerprint() == fingerprint
payload = {'status': 'PREFIX_DIAGNOSTIC_ONLY', 'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
           'source_fingerprint': fingerprint, 'runtime': runtime_environment_provenance(root),
           'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           'removed_symbol': scenario.removed_symbol, 'contract_sha256': scenario.contract_sha256,
           'rows': rows, 'final_account': json.loads(replay.final_account_payload.canonical_json)}
path = Path(__file__).with_name('remove308-prefix.json.gz')
path.write_bytes(gzip.compress(json.dumps(payload, sort_keys=True).encode(), mtime=0))
print(json.dumps({'sessions': len(rows), 'final_equity': replay.final_equity, 'source': fingerprint}), flush=True)
