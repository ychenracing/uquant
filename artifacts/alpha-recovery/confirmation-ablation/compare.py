"""Read back the confirmation-only diagnostic against preserved canonical controls."""
import gzip
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
report = {"status": "DIAGNOSTIC_ONLY_NOT_JOINT_ACCEPTANCE", "comparisons": {}}
for pool in ("a", "e", "remove308-prefix"):
    candidate_path = root / (pool + ".json.gz")
    baseline_path = root.parent / "canonical-baselines" / ("current-" + pool + ".json.gz")
    candidate = json.loads(gzip.decompress(candidate_path.read_bytes()))
    baseline = json.loads(gzip.decompress(baseline_path.read_bytes()))
    assert candidate["completed"] and baseline["completed"]
    assert candidate["commit"] == "dca5838868908d7801891cba3e43b5c7f47219e7"
    assert baseline["commit"] == "6f8de5c02ff1161b78ef42e63e91f36d35c6bfc0"
    common = ["config", "interval", "seed"]
    common += (["runtime", "contract_sha256", "removed_symbol"] if pool == "remove308-prefix"
               else ["environment", "symbols", "adapter_sha256", "runner_sha256"])
    if pool == "remove308-prefix":
        old_runner = (root.parent / "canonical-baselines/native_prefix.py").read_bytes()
        new_runner = (root.parent / "stock-confirmation/native_prefix.py").read_bytes()
        assert hashlib.sha256(old_runner).hexdigest() == baseline["runner_sha256"]
        assert hashlib.sha256(new_runner).hexdigest() == candidate["runner_sha256"]
        normalized = old_runner.replace(b"root = Path(sys.argv[1]).resolve()",
                                        b"root = Path(__file__).resolve().parents[3]")
        normalized = normalized.replace(b"output = Path(sys.argv[2])", b"output = Path(sys.argv[1])")
        assert normalized == new_runner
    for key in common:
        assert candidate[key] == baseline[key], (pool, key)
    changed = {p for p in baseline["source_files"] | candidate["source_files"]
               if baseline["source_files"].get(p) != candidate["source_files"].get(p)}
    assert changed == {"uquant/portfolio/ordinary.py"}, changed
    row_key, date_key = ("rows", "session") if pool == "remove308-prefix" else ("trace", "date")
    pairs = list(zip(baseline[row_key], candidate[row_key], strict=True))
    assert all(a[date_key] == b[date_key] for a, b in pairs)
    differences = [{"date": a[date_key], "baseline_equity": a["equity"],
                    "candidate_equity": b["equity"]} for a, b in pairs if a["equity"] != b["equity"]]
    result = {"baseline_commit": baseline["commit"], "candidate_commit": candidate["commit"],
              "matched_identity_fields": common, "changed_source_files": sorted(changed),
              "sessions": len(pairs), "equity_differences": differences,
              "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
              "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest()}
    if pool == "remove308-prefix":
        result.update(baseline_equity=baseline["final_equity"], candidate_equity=candidate["final_equity"])
        result["runner_bridge"] = "Verified exact scripts differ only in root/output argument resolution; replay and observation code identical. Original hashes retained."
    else:
        result.update(baseline_metrics=baseline["metrics"], candidate_metrics=candidate["metrics"])
    report["comparisons"][pool] = result
(root / "comparison.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
print(json.dumps({k: {a: b for a, b in v.items() if a != "equity_differences"}
                  for k, v in report["comparisons"].items()}, indent=2))
