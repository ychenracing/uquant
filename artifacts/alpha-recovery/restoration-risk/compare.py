"""Read preserved resolved-recovery diagnostics against matching controls."""

import argparse
import gzip
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path


root = Path(__file__).resolve().parent
repo = root.parents[2]
candidate_commit = "01b324491f13ed63209954279b5b7ece1ce165a9"
baseline_commit = "6f8de5c02ff1161b78ef42e63e91f36d35c6bfc0"
expected_changes = {"uquant/portfolio/pipeline.py"}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--h2-control", type=Path,
                    default=root.parent / "risk-repair/current-e-h2.json.gz")
args = parser.parse_args()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def changed_leaf_paths(a, b, path=""):
    if type(a) is not type(b):
        yield path
    elif isinstance(a, dict):
        for key in sorted(a.keys() | b.keys()):
            child = path + "/" + key
            if key not in a or key not in b:
                yield child
            else:
                yield from changed_leaf_paths(a[key], b[key], child)
    elif isinstance(a, list):
        if len(a) != len(b):
            yield path + "/length"
        for left, right in zip(a, b):
            yield from changed_leaf_paths(left, right, path + "/*")
    elif a != b:
        yield path


def fill_economics(value):
    """Keep every fill field except source-derived grant and epoch identifiers."""
    if isinstance(value, dict):
        return {key: fill_economics(item) for key, item in value.items()
                if key not in {"epoch_id", "grant_id"}}
    if isinstance(value, list):
        return [fill_economics(item) for item in value]
    return value


report = {
    "status": "DIAGNOSTIC_ONLY_NOT_JOINT_ACCEPTANCE",
    "conclusion": "REJECT_ZERO_ECONOMIC_IMPROVEMENT_NO_PARAMETER_SWEEP",
    "comparisons": {},
}
for pool in ("a", "e", "remove308-prefix", "e-h2"):
    prefix = pool == "remove308-prefix"
    candidate_path = root / (pool + ".json.gz")
    baseline_path = (args.h2_control if pool == "e-h2" else
                     root.parent / "canonical-baselines" / ("current-" + pool + ".json.gz"))
    candidate = json.loads(gzip.decompress(candidate_path.read_bytes()))
    baseline = json.loads(gzip.decompress(baseline_path.read_bytes()))
    assert candidate["completed"] and baseline["completed"], pool
    assert candidate["commit"] == candidate_commit, pool
    assert baseline["commit"] == baseline_commit, pool
    common = ["config", "interval", "seed"]
    common += (["runtime", "contract_sha256", "removed_symbol"] if prefix else
               ["environment", "symbols", "adapter_sha256", "runner_sha256"])
    for key in common:
        assert candidate[key] == baseline[key], (pool, key)
    changed = {path for path in baseline["source_files"] | candidate["source_files"]
               if baseline["source_files"].get(path) != candidate["source_files"].get(path)}
    assert changed == expected_changes, (pool, changed)
    # Match the recorded changed-file bytes to each actual producer commit.
    for run in (baseline, candidate):
        for path in sorted(changed):
            content = subprocess.check_output(["git", "show", run["commit"] + ":" + path], cwd=repo)
            assert hashlib.sha256(content).hexdigest() == run["source_files"][path], (pool, path)
    runner_bridge = None
    if prefix:
        old_path = root.parent / "canonical-baselines/native_prefix.py"
        new_path = root.parent / "stock-confirmation/native_prefix.py"
        assert digest(old_path) == baseline["runner_sha256"]
        assert digest(new_path) == candidate["runner_sha256"]
        normalized = old_path.read_bytes().replace(
            b"root = Path(sys.argv[1]).resolve()", b"root = Path(__file__).resolve().parents[3]")
        normalized = normalized.replace(b"output = Path(sys.argv[2])", b"output = Path(sys.argv[1])")
        assert normalized == new_path.read_bytes()
        runner_bridge = {
            "baseline_sha256": baseline["runner_sha256"],
            "candidate_sha256": candidate["runner_sha256"],
            "verification": "Exact scripts differ only in root/output argument resolution; replay and observation bytes are identical.",
        }
    row_key, date_key = ("rows", "session") if prefix else ("trace", "date")
    pairs = list(zip(baseline[row_key], candidate[row_key], strict=True))
    assert all(left[date_key] == right[date_key] for left, right in pairs), pool
    equity_differences = [{"date": left[date_key], "baseline_equity": left["equity"],
                           "candidate_equity": right["equity"]}
                          for left, right in pairs if left["equity"] != right["equity"]]
    assert not equity_differences, pool
    leaf_differences = Counter()
    for left, right in pairs:
        leaf_differences.update(changed_leaf_paths(left, right))
        assert fill_economics(left["fills"]) == fill_economics(right["fills"]), (pool, left[date_key])
    result = {
        "baseline_commit": baseline["commit"],
        "candidate_commit": candidate["commit"],
        "baseline_path": ("risk-repair/current-e-h2.json.gz" if pool == "e-h2" else
                          str(baseline_path.relative_to(root.parent))),
        "candidate_path": str(candidate_path.relative_to(root.parent)),
        "baseline_sha256": digest(baseline_path),
        "candidate_sha256": digest(candidate_path),
        "baseline_status": baseline["status"],
        "candidate_status": candidate["status"],
        "matched_identity_fields": common,
        "changed_source_files": sorted(changed),
        "changed_source_hashes": {path: {"baseline": baseline["source_files"][path],
                                        "candidate": candidate["source_files"][path]}
                                  for path in sorted(changed)},
        "changed_source_bytes_verified_at_producer_commits": True,
        "interval": baseline["interval"],
        "runtime": baseline["runtime" if prefix else "environment"],
        "sessions": len(pairs),
        "equity_differences": equity_differences,
        "daily_fill_economics_identical": True,
        "fill_identity_fields_excluded_from_economic_comparison": ["epoch_id", "grant_id"],
        "raw_trace_changed_leaf_path_counts": dict(sorted(leaf_differences.items())),
    }
    if prefix:
        assert candidate["final_equity"] == baseline["final_equity"]
        result.update(baseline_equity=baseline["final_equity"], candidate_equity=candidate["final_equity"],
                      baseline_source_fingerprint=baseline["source_fingerprint"],
                      candidate_source_fingerprint=candidate["source_fingerprint"], runner_bridge=runner_bridge)
        result["raw_final_account_changed_leaf_path_counts"] = dict(sorted(Counter(
            changed_leaf_paths(baseline["final_account"], candidate["final_account"])).items()))
    else:
        assert candidate["metrics"] == baseline["metrics"], pool
        result.update(baseline_metrics=baseline["metrics"], candidate_metrics=candidate["metrics"],
                      baseline_elapsed_seconds=baseline["elapsed_seconds"],
                      candidate_elapsed_seconds=candidate["elapsed_seconds"])
    report["comparisons"][pool] = result

(root / "comparison.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
print(json.dumps({pool: {"sessions": result["sessions"], "equity_difference_count": len(result["equity_differences"]),
                        "metrics": result.get("candidate_metrics"), "final_equity": result.get("candidate_equity")}
                  for pool, result in report["comparisons"].items()}, indent=2))
