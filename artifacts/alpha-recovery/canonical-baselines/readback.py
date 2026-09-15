"""Validate and preserve canonical baselines without repeating candidate replays."""
import base64
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

arm, pool = sys.argv[1:]
path = next(Path("/tmp/canonical-baseline").rglob(f"{arm}-{pool}.json.gz"))
raw = path.read_bytes()
p = json.loads(gzip.decompress(raw))
root = Path("artifacts/alpha-recovery")
candidate = json.loads(gzip.decompress((root / "stock-confirmation" / f"{pool}.json.gz").read_bytes()))
assert p["completed"] and candidate["completed"]
assert p["commit"] == ("cf8fecff76564fd4ed87faa0da336a06d433fd93" if arm == "historical" else "6f8de5c02ff1161b78ef42e63e91f36d35c6bfc0")
s = {"arm": arm, "pool": pool, "baseline_commit": p["commit"], "candidate_commit": candidate["commit"]}
if pool == "remove308-prefix":
    assert p["runtime"] == candidate["runtime"]
    assert p["config"] == candidate["config"]
    assert p["contract_sha256"] == candidate["contract_sha256"]
    assert p["interval"] == candidate["interval"]
    assert p["seed"] == candidate["seed"]
    assert [r["session"] for r in p["rows"]] == [r["session"] for r in candidate["rows"]]
    s.update(runtime=p["runtime"], baseline_equity=p["final_equity"], candidate_equity=candidate["final_equity"])
else:
    for key in ("environment", "symbols", "interval", "seed", "adapter_sha256", "runner_sha256"):
        assert p[key] == candidate[key], key
    shared = set(p["config"]) & set(candidate["config"])
    differences = {key: [p["config"][key], candidate["config"][key]] for key in shared
                   if p["config"][key] != candidate["config"][key]}
    assert not differences
    if arm == "current":
        assert p["config"] == candidate["config"]
    s.update(runtime=p["environment"], baseline_metrics=p["metrics"], candidate_metrics=candidate["metrics"],
             configuration_shared_values_equal=True,
             historical_only_config=sorted(set(p["config"]) - set(candidate["config"])),
             candidate_only_config=sorted(set(candidate["config"]) - set(p["config"])))
    old_name = ("uquant-baseline" if arm == "historical" else "uquant") + f"-{pool}-bull.json.gz"
    old = json.loads(gzip.decompress((root / old_name).read_bytes()))
    s["old_runtime_bridge_metrics_equal"] = p["metrics"] == old["metrics"]
    assert s["old_runtime_bridge_metrics_equal"]
    assert [r["date"] for r in p["trace"]] == [r["date"] for r in candidate["trace"]]
    if arm == "current" and pool == "e":
        selected_dates = {"2025-02-28", "2025-03-03", "2025-04-03", "2025-05-08",
                          "2025-06-24", "2025-07-09", "2025-08-05", "2025-09-08",
                          "2025-09-09", "2025-09-12", "2025-09-22", "2025-10-21",
                          "2025-11-13", "2026-01-05"}
        selected = {}
        for label, payload in (("current", p), ("candidate", candidate)):
            out = []
            blockers = Counter()
            for row in payload["trace"]:
                allocation = row["risk_evidence"].get("core_allocation", {})
                names = allocation.get("symbols", {})
                if row["date"] >= "2025-09-09":
                    for symbol in ("sz300308", "sz300502", "sz300394"):
                        info = names.get(symbol, {})
                        blockers.update([str((symbol, info.get("entry_gate"), info.get("restore_block")))])
                if row["date"] in selected_dates:
                    out.append({
                        "date": row["date"], "risk": row["risk"], "opportunity": row["opportunity"],
                        "equity": row["equity"], "gross": row["actual_gross"],
                        "sector_guard": row["sector_guard_active"],
                        "risk_evidence": {k: v for k, v in row["risk_evidence"].items()
                            if k in ("freeze_new_risk", "capital_budget_level", "capital_budget_repair_streak",
                                     "sector_guard_active", "sector_recovery_streak", "reduction_level",
                                     "transition_damage", "shock_state", "chronic_level")},
                        "allocation_freeze": allocation.get("freeze_new_risk"),
                        "cores": {symbol: {k: v for k, v in names.get(symbol, {}).items()
                                  if k in ("entry", "entry_gate", "restore_block", "proposal_weight", "budget_checks")}
                                  for symbol in ("sz300308", "sz300502", "sz300394")},
                    })
            selected[label] = {"dates": out, "post_september_block_counts": dict(blockers)}
        s["recovery_path"] = selected
files = lambda q: {k: v for k, v in q["source_files"].items()
                   if k.startswith("data/frozen/") or k == "uv.lock"}
assert files(p) == files(candidate)
s["frozen_inputs_equal"] = True
print("BASELINE_SUMMARY " + json.dumps(s, sort_keys=True), flush=True)
print("RAW_METADATA " + json.dumps({"arm": arm, "pool": pool, "size": len(raw),
    "sha256": hashlib.sha256(raw).hexdigest(),
    "git_blob_sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}), flush=True)
encoded = base64.b64encode(raw).decode()
for i in range(0, len(encoded), 12000):
    print("RAW_GZIP " + encoded[i:i + 12000], flush=True)
