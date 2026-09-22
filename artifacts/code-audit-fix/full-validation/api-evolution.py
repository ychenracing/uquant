"""Capture only reviewed API evolution; preserve the sealed historical snapshot."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from tests.architecture._analysis import canonical_sha256, public_api_snapshot, public_module_names
from tests.architecture._cross_vintage_api_projection import cross_vintage_api_projection

ROOT = Path.cwd()
old = json.loads((ROOT / 'benchmarks/public_api_contract.json').read_text())
prior = json.loads((ROOT / 'benchmarks/public_api_pr73_delta.json').read_text())
assert canonical_sha256(old['contract']) == old['contract_sha256'] == prior['base_contract_sha256']
old['contract']['modules'].update(prior['modules'])
assert canonical_sha256(old['contract']) == prior['contract_sha256']
expected = cross_vintage_api_projection(old['contract'])
observed = public_api_snapshot()
# These are exactly the owner/annotation/input additions reviewed in F01-F14
# and the preceding main holdout expected-session repair. No economic trace,
# setting, account schema, enum or existing parameter default is recaptured.
allowed = {
    'uquant', 'uquant.application', 'uquant.application.backtest',
    'uquant.application.decision', 'uquant.broker_contract', 'uquant.engine',
    'uquant.models.ordinary_state', 'uquant.portfolio.allocator',
    'uquant.portfolio.freeze', 'uquant.validation.holdout.service',
    'uquant.validation.holdout_runtime',
}
assert set(observed['modules']) - set(expected['modules']) == {'uquant.models.ordinary_state'}
assert not set(expected['modules']) - set(observed['modules'])
changed = {name for name in set(expected['modules']) | set(observed['modules'])
           if expected['modules'].get(name) != observed['modules'].get(name)}
assert changed == allowed, sorted(changed ^ allowed)
for key in set(expected) - {'modules', 'cli_help'}:
    assert observed[key] == expected[key], key
help_changes = {name for name in expected['cli_help'] if observed['cli_help'][name] != expected['cli_help'][name]}
assert help_changes == {'uquant account-sync'}, sorted(help_changes)
assert '--config CONFIG' in observed['cli_help']['uquant account-sync']
# A compact diff keeps all unmodified original contracts authoritative.
delta = {'base_projected_sha256': canonical_sha256(expected),
         'modules': {name: observed['modules'][name] for name in sorted(changed)},
         'cli_help': {name: observed['cli_help'][name] for name in sorted(help_changes)},
         'result_sha256': canonical_sha256(observed)}
path = ROOT / 'tests/fixtures/code_audit_api_delta.json'
assert not path.exists()
data = (json.dumps(delta, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()
path.write_bytes(data)
seal = hashlib.sha256(data).hexdigest()
helper = ROOT / 'tests/architecture/_code_audit_api_projection.py'
assert not helper.exists()
helper.write_text('''"""Sealed API evolution for the reviewed code-audit changes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ._analysis import canonical_sha256

_DELTA_SHA256 = "SEAL"
_ALLOWED_MODULES = __MODULE_SET__


def code_audit_api_projection(contract):
    path = Path(__file__).resolve().parents[1] / "fixtures/code_audit_api_delta.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _DELTA_SHA256
    delta = json.loads(raw)
    assert canonical_sha256(contract) == delta["base_projected_sha256"]
    assert set(delta["modules"]) == _ALLOWED_MODULES
    assert set(delta["cli_help"]) == {"uquant account-sync"}
    expected = copy.deepcopy(contract)
    expected["modules"].update(delta["modules"])
    expected["cli_help"].update(delta["cli_help"])
    assert canonical_sha256(expected) == delta["result_sha256"]
    return expected
'''.replace('SEAL', seal).replace('__MODULE_SET__', 'frozenset(' + repr(tuple(sorted(allowed))) + ')'))
p = ROOT / 'tests/architecture/test_public_api_contracts.py'
s = p.read_text();needle = 'from ._cross_vintage_api_projection import cross_vintage_api_projection'
assert s.count(needle) == 1
s = s.replace(needle, needle + '\nfrom ._code_audit_api_projection import code_audit_api_projection')
needle = '    modules = expected["modules"]';assert s.count(needle) == 1
s = s.replace(needle, '    expected = code_audit_api_projection(cross_vintage_api_projection(expected))\n' + needle)
s = s.replace('    assert observed == cross_vintage_api_projection(expected)', '    assert observed == expected')
p.write_text(s)
print(json.dumps({'delta_sha256': seal, 'bytes': len(data), 'modules': sorted(changed),
                  'result_sha256': delta['result_sha256']}))
