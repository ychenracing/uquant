"""Verify and restore a paused workspace; never launch its jobs."""
import argparse,hashlib,json,os,zipfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--parts',required=True);a=p.parse_args()
base=Path(__file__).resolve().parent;meta=json.loads((base/'SNAPSHOT.json').read_text());out=Path(a.output).resolve()
if out.exists() and any(out.iterdir()):raise SystemExit('Output must be a new or empty directory; existing work will not be overwritten.')
out.mkdir(parents=True,exist_ok=True);archive=out/'workspace-snapshot.zip';whole=hashlib.sha256();total=0
with archive.open('xb') as f:
 for row in meta['parts']:
  b=(Path(a.parts)/row['name']).read_bytes()
  assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'],row['name']
  assert total==row['offset'];f.write(b);whole.update(b);total+=len(b)
assert total==meta['bytes'] and whole.hexdigest()==meta['sha256'],'archive mismatch'
with zipfile.ZipFile(archive) as z:
 m=json.loads(z.read('MANIFEST.json'))
 for row in m['files']:
  target=(out/row['path']).resolve()
  if not target.is_relative_to(out):raise ValueError('Unsafe path')
  b=z.read('objects/'+row['sha256'])
  assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'],row['path']
  target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b);target.chmod(row['mode'])
 (out/'RESTORED_MANIFEST.json').write_text(json.dumps(m,indent=2))
print(f"Verified and restored {len(m['files'])} files to {out}. Jobs remain paused. Read HANDOFF_PROMPT.md before resuming.")
