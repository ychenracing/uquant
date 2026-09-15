"""Check recovered completed diagnostics without replaying or relabeling them."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--baseline-root", type=Path, required=True)
parser.add_argument("--h2-control", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent
producer = "1aaf83d12117d341626d82a87a87d9d9e263bbc9"
report = {"status": "MATCHED_DIAGNOSTICS_NOT_JOINT_ACCEPTANCE", "producer": producer, "comparisons": {}}
for name in ("a", "e", "e-h2", "remove308", "remove502"):
    native = name.startswith("remove")
    candidate = root / (name + ".json.gz")
    baseline = (root.parent / "capital-reentry" / ("control-" + name + ".json.gz") if native
                else args.h2_control if name == "e-h2"
                else args.baseline_root / ("current-" + name + ".json.gz"))
    c, b = (json.loads(gzip.decompress(p.read_bytes())) for p in (candidate, baseline))
    if native:
        assert c["status"] == b["status"] == "COMPLETE"
        assert not c["replay_error"] and not b["replay_error"]
        assert c["source"]["head"] == producer
        assert c["source"]["patch_sha256"] == b["source"]["patch_sha256"] == hashlib.sha256(b"").hexdigest()
        for key in ("runtime", "scenario", "runner_sha256"):
            assert c[key] == b[key], (name, key)
        for key in b["identities"]:
            if key not in ("head", "tree", "production_source_sha256"):
                assert c["identities"][key] == b["identities"][key], (name, key)
        rows, date = "rows", "session"
    else:
        assert c["completed"] and b["completed"] and c["commit"] == producer
        for key in ("environment", "config", "interval", "seed", "symbols", "adapter_sha256", "runner_sha256"):
            assert c[key] == b[key], (name, key)
        changed = {p for p in c["source_files"] | b["source_files"]
                   if c["source_files"].get(p) != b["source_files"].get(p)}
        assert changed == {"uquant/risk/capital.py", "uquant/risk/assessment.py"}, (name, changed)
        rows, date = "trace", "date"
    pairs = list(zip(b[rows], c[rows], strict=True))
    assert all(x[date] == y[date] for x, y in pairs)
    differences = [{"session": x[date], "baseline": x["equity"], "candidate": y["equity"]}
                   for x, y in pairs if x["equity"] != y["equity"]]
    keys = ("final_wealth", "max_drawdown", "account_orders", "annual_turnover",
            "actual_strategic_epoch_count", "distinct_owner_count")
    report["comparisons"][name] = {
        "sessions": len(pairs), "baseline": {k: b["metrics"][k] for k in keys if k in b["metrics"]},
        "candidate": {k: c["metrics"][k] for k in keys if k in c["metrics"]},
        "changed_equity_sessions": len(differences),
        "first_equity_difference": differences[0] if differences else None,
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
    }
target = root / "completed-comparison.json"
if target.exists():
    raise RuntimeError("Preserve the existing comparison; do not overwrite it")
target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
print(json.dumps(report, indent=2))
