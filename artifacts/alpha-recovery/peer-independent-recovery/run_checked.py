"""Reject incomplete checkout/runtime before invoking the unchanged native runner."""
import argparse
import gzip
import json
from pathlib import Path
import subprocess
import sys

from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.provenance.fingerprints import source_surface_fingerprint

parser = argparse.ArgumentParser()
parser.add_argument("--control", type=Path, required=True)
parser.add_argument("--preflight-only", action="store_true")
args, runner_args = parser.parse_known_args()
root = Path.cwd()
control = json.loads(gzip.decompress(args.control.read_bytes()))
observed = runtime_environment_provenance(root)
if observed != control["runtime"]:
    raise RuntimeError(f"Runtime differs before replay: {observed!r}")
for surface in ("economic_decision_v1", "full_package_v1"):
    print(f"Preflight {surface}: {source_surface_fingerprint(root, surface)}", flush=True)
if args.preflight_only:
    raise SystemExit(0)
runner = root / "artifacts/alpha-recovery/capital-reentry/run_native_detailed.py"
raise SystemExit(subprocess.call([sys.executable, str(runner), *runner_args], cwd=root))
