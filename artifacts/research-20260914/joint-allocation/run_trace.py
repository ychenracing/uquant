import argparse,json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from research.performance_diagnostic import _run_trace,_source_sha256,_trace_adapter_sha256
root=Path.cwd();spec=json.loads(Path('benchmarks/promotion_baseline.json').read_text());pool,start,end,out=sys.argv[1:]
a=argparse.Namespace(source_root=str(root),data_dir=str(root/'data/frozen'),start=start,end=end,set=[],expected_patch_sha256=None,expected_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),expected_source_sha256=_source_sha256(root),expected_trace_adapter_sha256=_trace_adapter_sha256(root),symbols=','.join(spec['pools'][pool]))
t=time.monotonic();result=_run_trace(a);Path(out).write_text(json.dumps(result,default=str));print('DONE',time.monotonic()-t,result['metrics'],flush=True)
