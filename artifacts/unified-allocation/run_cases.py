"""Sequential bounded invocations of the existing native runner."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('cases', nargs='+')
a = p.parse_args()
here = Path(__file__).resolve().parent
contract = here / 'ACCEPTANCE.json'
c = json.loads(contract.read_text())
universe = json.loads((here.parent / 'alpha-recovery/local-enhancement/FROZEN_SCENARIOS.json').read_text())['universe']
for case in a.cases:
    spec = c['cases'][case]
    target = a.output.resolve() / (case + '.json.gz')
    if target.exists():
        raise RuntimeError('Existing output requires reconciliation: ' + str(target))
    cmd = [sys.executable, str(here / 'replay.py'), '--root', str(a.root.resolve()),
           '--data-dir', str(here.parents[1] / 'data/frozen'), '--contract', str(contract),
           '--case', case, '--symbols', ','.join(s for s in universe if s not in spec['remove']),
           '--start', spec['start'], '--end', c['end'], '--cost-multiplier', str(spec.get('cost_multiplier', 1)),
           '--output', str(target)]
    subprocess.run(cmd, check=True)
