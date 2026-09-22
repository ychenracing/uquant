"""Read-only inventory for the explicitly scoped code-audit corrections."""
from pathlib import Path
import json
import subprocess

root = Path.cwd()
def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()
paths = git('ls-files').splitlines()
report = {'base': git('rev-parse', 'HEAD'), 'instructions': {}, 'tests': [], 'matches': {}}
for path in paths:
    if path.endswith('AGENTS.md'):
        report['instructions'][path] = Path(path).read_text()
    if path.startswith('tests/') and any(term in path for term in ('broker', 'cli', 'opportunity', 'engine', 'market', 'portfolio', 'compatibility', 'import_contract')):
        report['tests'].append(path)
needles = (
    'capital drawdown relapse in restored holdings',
    'market-backed portfolio break in incomplete restoration',
    'capital guard cooldown after failed restoration',
    'ordinary_repair_origin:', 'core_transfer:', 'core_transfer_session:',
    '_load_allocator', '_assembly_method', '_engine_function',
    'bind_engine_decision', 'bind_causal_risk_timeline',
)
for path in paths:
    if not path.endswith('.py') or not path.startswith(('uquant/', 'tests/architecture/')):
        continue
    lines = Path(path).read_text().splitlines()
    for i, line in enumerate(lines):
        for needle in needles:
            if needle in line:
                report['matches'].setdefault(needle, []).append({
                    'path': path, 'line': i + 1,
                    'context': '\n'.join(f'{j+1}: {lines[j]}' for j in range(max(0, i-3), min(len(lines), i+5)))})
out = root / 'artifacts/code-audit-fix'
out.mkdir(parents=True, exist_ok=True)
(out / 'INSPECTION.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'base': report['base'], 'nested_instructions': list(report['instructions']),
                  'test_paths': len(report['tests']), 'matches': {k: len(v) for k,v in report['matches'].items()}}))
