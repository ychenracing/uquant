"""Verify the decision-core rescue evidence without promoting diagnostics to acceptance."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "SHA256SUMS.json"
H8_HEAD = "60f06b80fd2f1cbbacea6e23ce423c4d44871c57"

EXPECTED_METRICS = {
    "H1-e-bull.json.gz": (2.22033988920943, 0.19415497861214692, 35),
    "H1-e-h2-acute.json.gz": (0.9678968244544601, 0.03225283035943982, 6),
    "H2-e-bull.json.gz": (2.113732464878539, 0.20807737761484868, 26),
    "H2-e-h2-acute.json.gz": (0.981165134078425, 0.039816904700131106, 4),
    "H3-a-bull.json.gz": (11.655466908159205, 0.15999500872658612, 12),
    "H3-d-bull.json.gz": (8.877455075228168, 0.24280612073037622, 18),
    "H3-e-bull.json.gz": (13.55412998567718, 0.1778558115784331, 13),
    "H3-remove308.json.gz": (1.10088621139168, 0.2295064023990222, 10),
    "H3-remove502.json.gz": (1.2721552329598302, 0.17453002295951636, 13),
    "H4-e-bull.json.gz": (13.55412998567718, 0.1778558115784331, 13),
    "H4-e-h2-acute.json.gz": (0.9657825495021101, 0.03450867067628982, 4),
    "H4-native-remove502.json.gz": (1.9140110854108303, 0.24044618492815417, 14),
    "H4-remove502.json.gz": (1.2639178503319597, 0.1743785283768643, 15),
    "H5-e-bull.json.gz": (13.55412998567718, 0.1778558115784331, 13),
    "H5-native-remove308.json.gz": (0.8777724074409611, 0.23005091216666496, 11),
    "H5-native-remove502.json.gz": (2.3851418754251106, 0.23812423499266522, 20),
    "H6-e-bull.json.gz": (13.55412998567718, 0.1778558115784331, 13),
    "H6-native-remove308.json.gz": (1.1190839397190302, 0.2288891949758597, 16),
    "H6-native-remove502.json.gz": (2.3851418754251106, 0.23812423499266522, 20),
    "H7-a-bull.json.gz": (11.695294609702021, 0.1612879732848893, 12),
    "H8-a-bull.json.gz": (11.87065858669219, 0.16004817592256193, 12),
    "H8-d-bull.json.gz": (8.877455075228168, 0.24280612073037622, 18),
    "H8-e-bull.json.gz": (13.821022723557565, 0.17253036736852323, 13),
    "H8-e-h2-acute.json.gz": (0.9657825495021101, 0.03450867067628982, 4),
    "H8-e-h2-full.json.gz": (1.3955925424255622, 0.0884926952290751, 10),
    "H8-native-remove308.json.gz": (1.1190839397190302, 0.2288891949758597, 16),
    "H8-native-remove502.json.gz": (2.3851418754251106, 0.23812423499266522, 20),
    "H8-v1-a-bull.json.gz": (11.87065858669219, 0.16004817592256193, 12),
    "H8-v1-d-bull.json.gz": (8.694814031992749, 0.2739474944866921, 18),
    "H8-v1-e-bull.json.gz": (13.821022723557565, 0.17253036736852323, 13),
    "H8-v1-e-h2-acute.json.gz": (0.9664648184460801, 0.07477293882799319, 1),
}

METHOD_INVALID = {
    "H3-remove308.json.gz": "fixed future membership instead of native point-in-time removal",
    "H3-remove502.json.gz": "fixed future membership instead of native point-in-time removal",
    "H4-remove502.json.gz": "fixed future membership instead of native point-in-time removal",
    "H8-v1-e-h2-acute.json.gz": "initialized August 1 instead of required July 1 warmup",
}


def _sealed_paths() -> tuple[Path, ...]:
    traces = tuple(sorted(ROOT.glob("H*.json.gz")))
    return (*traces, ROOT / "RESEARCH_LEDGER.md", ROOT / "run_native_removal.py", ROOT / "verify.py")


def _record(path: Path) -> dict[str, int | str]:
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _write_manifest() -> None:
    manifest = {path.name: _record(path) for path in _sealed_paths()}
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _read(name: str) -> dict[str, object]:
    return json.loads(gzip.decompress((ROOT / name).read_bytes()))


def _metric(payload: dict[str, object], name: str) -> float | int:
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    value = metrics[name]
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return value


def _assert_close(actual: float | int, expected: float | int, label: str) -> None:
    if isinstance(expected, int):
        assert actual == expected, label
    else:
        assert math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=1e-12), label


def _verify_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text())
    expected_names = {path.name for path in _sealed_paths()}
    assert set(manifest) == expected_names, "sealed evidence membership differs"
    for path in _sealed_paths():
        assert manifest[path.name] == _record(path), f"sealed bytes differ: {path.name}"


def _verify_metrics() -> dict[str, dict[str, object]]:
    traces = {}
    for name, expected in EXPECTED_METRICS.items():
        trace = _read(name)
        traces[name] = trace
        for key, value in zip(("final_wealth", "max_drawdown", "account_orders"), expected, strict=True):
            _assert_close(_metric(trace, key), value, f"{name}: {key}")
    return traces


def _orders(trace: dict[str, object], date: str) -> dict[str, float]:
    days = trace["trace"]
    assert isinstance(days, list)
    day = next(row for row in days if isinstance(row, dict) and row.get("date") == date)
    orders = day["orders"]
    assert isinstance(orders, list)
    return {
        str(order["symbol"]): float(order["target_weight"])
        for order in orders
        if isinstance(order, dict) and order.get("side") == "BUY"
    }


def _verify_h8(traces: dict[str, dict[str, object]]) -> dict[str, object]:
    names = (
        "H8-a-bull.json.gz",
        "H8-d-bull.json.gz",
        "H8-e-bull.json.gz",
        "H8-e-h2-acute.json.gz",
        "H8-e-h2-full.json.gz",
    )
    for name in names:
        source = traces[name]["source"]
        environment = traces[name]["environment"]
        assert isinstance(source, dict) and source["commit"] == H8_HEAD
        assert source["patch_sha256"] is None
        assert isinstance(environment, dict) and environment["uv_version"] == "0.12.11"

    a = traces["H8-a-bull.json.gz"]
    may = _orders(a, "2025-05-08")
    october = _orders(a, "2025-10-21")
    assert may == {"sz300394": 0.073009050705, "sz300502": 0.203567091071}
    assert october == {"sz300308": 0.6, "sz300394": 0.16, "sz300502": 0.16}

    acute = traces["H8-e-h2-acute.json.gz"]
    days = acute["trace"]
    assert isinstance(days, list)
    equities = {
        row["date"]: row["equity"]
        for row in days
        if isinstance(row, dict) and row.get("date") in {"2024-08-01", "2024-09-02"}
    }
    acute_return = equities["2024-09-02"] / equities["2024-08-01"] - 1.0
    _assert_close(acute_return, -0.028553764161078243, "H8 E H2 acute return")

    for name in ("H8-native-remove308.json.gz", "H8-native-remove502.json.gz"):
        native = traces[name]
        source = native["source"]
        runtime = native["runtime"]
        assert native["diagnostic_only"] is True and native["canonical_acceptance"] is False
        assert native["status"] == "COMPLETE"
        assert isinstance(source, dict) and source["head"] == H8_HEAD
        assert isinstance(runtime, dict) and runtime["uv_version"] == "0.12.11"
        assert _metric(native, "actual_strategic_epoch_count") == 2
        assert _metric(native, "distinct_owner_count") == 2

    gates = {
        "a_bull": _metric(a, "final_wealth") >= 11.7313165203
        and _metric(a, "max_drawdown") <= 0.1689105895,
        "d_bull": _metric(traces["H8-d-bull.json.gz"], "final_wealth") >= 8.8195014189
        and _metric(traces["H8-d-bull.json.gz"], "max_drawdown") <= 0.2836861830,
        "e_bull": _metric(traces["H8-e-bull.json.gz"], "final_wealth") >= 13.3598068604
        and _metric(traces["H8-e-bull.json.gz"], "max_drawdown") <= 0.1836694955,
        "e_h2_wealth": _metric(traces["H8-e-h2-full.json.gz"], "final_wealth") >= 1.6930928947,
        "e_h2_acute": acute_return >= 0.0461081079,
        "remove308_wealth": _metric(traces["H8-native-remove308.json.gz"], "final_wealth")
        >= 0.95 * 1.1550544605,
        "remove502_wealth": _metric(traces["H8-native-remove502.json.gz"], "final_wealth")
        >= 0.95 * 2.3851418754,
    }
    assert gates == {
        "a_bull": True,
        "d_bull": True,
        "e_bull": True,
        "e_h2_wealth": False,
        "e_h2_acute": False,
        "remove308_wealth": True,
        "remove502_wealth": True,
    }
    return {"gates": gates, "acute_return": acute_return}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    if args.write_manifest:
        _write_manifest()
    _verify_manifest()
    traces = _verify_metrics()
    h8 = _verify_h8(traces)
    print(json.dumps({
        "sealed_files": len(_sealed_paths()),
        "metric_traces": len(traces),
        "method_invalid": METHOD_INVALID,
        "h8": h8,
        "overall": "NOT_MET",
        "canonical_acceptance": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
