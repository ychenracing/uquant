"""Apply reviewed source edits, never to main and never over concurrent changes."""
from pathlib import Path
import ast
import json
import hashlib
import os
import runpy
import subprocess

ROOT = Path.cwd()
BASE = '7bc5cd5e20038c94ab5cf556ce107104692236ac'
assert os.environ['GITHUB_REF'] == 'refs/heads/codex/code-audit-fixes-20260922'
changed = set()
originals = {}

def replace(path, before, after, count=1):
    p = ROOT / path
    text = p.read_text()
    found = text.count(before)
    if found == 0 and after in text:
        return
    if found != count:
        raise RuntimeError(f'{path}: expected {count} exact replacements, got {found}: {before[:100]!r}')
    originals.setdefault(path, p.read_bytes())
    p.write_text(text.replace(before, after))
    changed.add(path)

def write(path, source):
    p = ROOT / path
    if p.exists() and p.read_text() == source:
        return
    originals.setdefault(path, p.read_bytes() if p.exists() else None)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source)
    changed.add(path)

def replace_function(path, name, source):
    p = ROOT / path
    text = p.read_text()
    node = next(n for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name)
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
    lines = text.splitlines(keepends=True)
    current = ''.join(lines[start:node.end_lineno]).rstrip('\n')
    source = source.rstrip('\n')
    if current == source:
        return
    baseline = subprocess.check_output(['git','show',f'{BASE}:{path}'], text=True)
    old_node = next(n for n in ast.parse(baseline).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name)
    old_start = min([old_node.lineno, *(d.lineno for d in old_node.decorator_list)])-1
    old = '\n'.join(baseline.splitlines()[old_start:old_node.end_lineno])
    if current != old:
        raise RuntimeError(f'{path}:{name}: source differs from reviewed baseline')
    originals.setdefault(path, p.read_bytes())
    p.write_text(''.join(lines[:start]) + source + '\n' + ''.join(lines[node.end_lineno:]))
    changed.add(path)

ledger_path = ROOT/'artifacts/code-audit-fix/PATCHES_APPLIED.json'
ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
for script in sorted((ROOT/'artifacts/code-audit-fix/patches').glob('*.py')):
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    if script.name in ledger:
        if ledger[script.name] != digest:
            raise RuntimeError(f'{script.name}: use a new patch for a follow-up correction')
        continue
    originals.clear()
    before_changed = set(changed)
    try:
        runpy.run_path(str(script), init_globals={'replace':replace,'write':write,'replace_function':replace_function,'ROOT':ROOT,'BASE':BASE})
        for path in changed:
            if path.endswith('.py'):
                ast.parse((ROOT/path).read_text(), filename=path)
    except BaseException:
        for path, content in originals.items():
            p = ROOT/path
            if content is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(content)
        changed = before_changed
        raise
    ledger[script.name] = digest
    ledger_path.write_text(json.dumps(ledger,indent=2)+'\n')
(ROOT/'artifacts/code-audit-fix/APPLIED.json').write_text(json.dumps({'base':BASE,'changed':sorted(changed)},indent=2)+'\n')
print(json.dumps({'changed_files':sorted(changed)}))
