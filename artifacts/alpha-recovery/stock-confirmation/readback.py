"""Read sealed diagnostics, compare fixed inputs, and preserve their exact bytes."""
import base64
import gzip
import hashlib
import json
import sys
from pathlib import Path

scenario = sys.argv[1]
root = Path.cwd()
path = next(Path("/tmp/alpha-candidate").rglob(scenario + ".json.gz"))
raw = path.read_bytes()
payload = json.loads(gzip.decompress(raw))
assert payload["completed"] is True
assert payload["commit"] == "1abcdc6eef2a2f92bd7c0c05233504b67d7fffcd"
base_path = root / "artifacts/alpha-recovery" / (
    "remove308-prefix.json.gz" if scenario == "remove308-prefix" else f"uquant-{scenario}-bull.json.gz"
)
baseline = json.loads(gzip.decompress(base_path.read_bytes()))
summary = {"scenario": scenario, "candidate_commit": payload["commit"],
           "baseline_commit": baseline["commit"], "diagnostic_only": True}
errors = []
if scenario != "remove308-prefix":
    for key in ("config", "symbols", "interval", "seed", "environment", "adapter_sha256", "runner_sha256"):
        if payload[key] != baseline[key]:
            errors.append({"field": key, "baseline": baseline[key], "candidate": payload[key]})
    nonproduction = lambda p: {k: v for k, v in p["source_files"].items() if not k.startswith("uquant/")}
    if nonproduction(payload) != nonproduction(baseline):
        errors.append({"field": "nonproduction_files"})
    changed = sorted(k for k, v in payload["source_files"].items()
                     if v != baseline["source_files"].get(k))
    assert changed == ["uquant/portfolio/ordinary.py", "uquant/portfolio/pipeline.py"], changed
    summary.update(fixed_inputs_verified=True, changed_source_files=changed,
                   baseline_metrics=baseline["metrics"], candidate_metrics=payload["metrics"],
                   trace_keys=list(payload["trace"]) if isinstance(payload["trace"], dict) else "list")
    trace = payload["trace"]
    if isinstance(trace, list):
        summary["candidate_events"] = [{k: row[k] for k in ("date", "risk", "sector_guard_active", "equity", "actual_gross", "new_fills")} for row in trace if row["new_fills"]]
        summary["baseline_events"] = [{k: row[k] for k in ("date", "risk", "sector_guard_active", "equity", "actual_gross", "new_fills")} for row in baseline["trace"] if row["new_fills"]]
        first = next(((left, right) for left, right in zip(baseline["trace"], trace, strict=True) if left["orders"] != right["orders"] or left["fills"] != right["fills"]), None)
        if first:
            summary["first_executable_divergence"] = {side: {k: row[k] for k in ("date", "risk", "leaders", "orders", "fills")} for side, row in zip(("baseline", "candidate"), first, strict=True)}
    if isinstance(trace, dict):
        summary["trace_field_types"] = {k: type(v).__name__ for k, v in trace.items()}
        for key in ("fills", "orders", "trades"):
            if key in trace:
                summary[key] = trace[key]
else:
    if payload["runtime"] != baseline["runtime"]:
        errors.append({"field": "runtime", "baseline": baseline["runtime"], "candidate": payload["runtime"]})
    assert payload["contract_sha256"] == baseline["contract_sha256"]
    assert [r["session"] for r in payload["rows"]] == [r["session"] for r in baseline["rows"]]
    summary.update(native_transport_verified=True, sessions=len(payload["rows"]),
                   baseline_final_equity=baseline["rows"][-1]["equity"],
                   candidate_final_equity=payload["final_equity"],
                   candidate_fills=[{"session": r["session"], "fills": r["fills"]}
                                    for r in payload["rows"] if r["fills"]],
                   baseline_fills=[{"session": r["session"], "fills": r["fills"]}
                                   for r in baseline["rows"] if r["fills"]])
summary["comparison_errors"] = errors
summary["fixed_inputs_verified"] = not errors
print("DIAGNOSTIC_SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
print("RAW_METADATA " + json.dumps({
    "scenario": scenario, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    "git_blob_sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
}), flush=True)
encoded = base64.b64encode(raw).decode()
for offset in range(0, len(encoded), 12000):
    print("RAW_GZIP " + encoded[offset:offset + 12000], flush=True)

if errors:
    raise SystemExit("Comparison identity mismatch retained above; not a verified matched experiment")
