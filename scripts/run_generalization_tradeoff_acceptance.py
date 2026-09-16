"""Evaluate immutable generalization-tradeoff replay artifacts."""

from __future__ import annotations

import argparse
import csv
import functools
import gzip
import hashlib
import json
import math
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.generalization_tradeoff_native import evaluate_native
from uquant.validation.statistics import linear_quantile

CONTRACT = Path("benchmarks/generalization_tradeoff_acceptance.json")
ECONOMIC_FIELDS = (
    "initial_cash",
    "commission_rate",
    "min_commission",
    "stamp_duty",
    "transfer_fee",
    "slippage",
)
LEGACY_NATIVE_RUNNER_SHA256 = "2e099c863b063d9f59fdd5dc4e34698bc4197eb12b54f75bccc8870fe1bbe2ba"
FULL_CONFIG_NATIVE_RUNNER_SHA256 = "435e5019ca500da9d8b52891c0c7b7deb61bad2c61ab2eedd2bb02bb97c8fca2"
AUDITED_NATIVE_RUNNERS = {
    LEGACY_NATIVE_RUNNER_SHA256: "legacy dataclasses.asdict projection",
    FULL_CONFIG_NATIVE_RUNNER_SHA256: "complete cfg.to_dict serialization",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _geomean(values: Sequence[float]) -> float:
    if not values or any(value <= 0 or not math.isfinite(value) for value in values):
        raise ValueError("geometric mean requires finite positive values")
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def _paired_wealth_ratios(
    names: Sequence[str], cells: Mapping[str, Mapping[str, Mapping[str, object]]]
) -> dict[str, float]:
    return {
        name: _number(cells["candidate"][name]["wealth"], "wealth")
        / _number(cells["historical"][name]["wealth"], "wealth")
        for name in names
        if name in cells["candidate"] and name in cells["historical"]
    }


def _common_sessions(root: Path) -> list[str]:
    calendars: list[set[str]] = []
    for symbol in ("sh000300", "sh000682"):
        with (root / "data" / "frozen" / f"{symbol}.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            calendars.append({row["date"][:10] for row in csv.DictReader(stream)})
    return sorted(calendars[0] & calendars[1])


def _runner_compatibility(payloads: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    native = {str(p["runner_sha256"]) for p in payloads if p["method"] == "native_public_backtest"}
    unknown = native - AUDITED_NATIVE_RUNNERS.keys()
    if unknown:
        raise ValueError(f"unknown native runner identities: {sorted(unknown)}")
    reuse = [p for p in payloads if p["method"] == "native_public_trace_reuse"]
    reuse_runners = {str(p["runner_sha256"]) for p in reuse}
    reuse_adapters = {str(p.get("adapter_sha256")) for p in reuse}
    if len(reuse_runners) > 1 or len(reuse_adapters) > 1:
        raise ValueError("mixed trace-reuse runner or adapter identities")
    return {
        "native": {digest: AUDITED_NATIVE_RUNNERS[digest] for digest in sorted(native)},
        "native_equivalent_migration": len(native) > 1,
        "trace_reuse_runner_sha256": next(iter(reuse_runners), None),
        "trace_reuse_adapter_sha256": next(iter(reuse_adapters), None),
    }


def _expected(contract: Mapping[str, Any]) -> dict[str, dict[str, tuple[list[str], str, str, float]]]:
    universe = list(contract["universe"])
    start, end = contract["window"]["start"], contract["window"]["end"]
    core = {"sz300308", "sz300502", "sz300394"}
    optical = core | {"sh600487", "sh601869"}
    common: dict[str, tuple[list[str], str, str, float]] = {
        "full": (universe, start, end, 1.0),
        "remove_all_three": ([s for s in universe if s not in core], start, end, 1.0),
        "no_optical": ([s for s in universe if s not in optical], start, end, 1.0),
    }
    for pool, symbols in contract["pools"].items():
        for window, dates in contract["performance_windows"].items():
            common[f"{pool}-{window}"] = (list(symbols), dates["start"], dates["end"], 1.0)
    for removed in universe:
        common[f"loo-{removed}"] = ([s for s in universe if s != removed], start, end, 1.0)
    candidate = dict(common)
    # Offset dates are validated against the artifact inputs/data index below; the contract fixes
    # the ordinal rather than spelling out those dates.
    stress_removals = {"full": None, "remove308": "sz300308", "remove502": "sz300502"}
    for scenario in contract["stress"]["scenarios"]:
        if scenario not in stress_removals:
            raise ValueError(f"unsupported contract stress scenario: {scenario}")
        removed = stress_removals[scenario]
        symbols = [s for s in universe if s != removed]
        for offset in contract["stress"]["start_session_offsets"]:
            candidate[f"{scenario}-offset{offset}"] = (symbols, "@offset" + str(offset), end, 1.0)
        candidate[f"{scenario}-cost2"] = (symbols, "@offset0", end, 2.0)
    return {"candidate": candidate, "historical": common}


def _read_artifact(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("artifact root must be an object")
    return value


def _validate_reuse(payload: Mapping[str, Any]) -> None:
    reused = payload.get("reused_from")
    if not isinstance(reused, Mapping) or set(reused) != {"path", "sha256"}:
        raise ValueError("trace reuse needs exact reused_from path and sha256")
    path = Path(str(reused["path"]))
    if not path.is_absolute() or not path.is_file() or _sha(path) != reused["sha256"]:
        raise ValueError("reused source is unavailable or has wrong digest")
    original = _read_artifact(path)
    if original.get("completed") is not True or payload.get("source_head") != original.get("commit"):
        raise ValueError("reused source is incomplete or commit differs")
    for identity in ("runner_sha256", "adapter_sha256"):
        if payload.get(identity) != original.get(identity):
            raise ValueError(f"reused {identity} differs")
    if payload.get("symbols") != sorted(original.get("symbols", [])):
        raise ValueError("reused symbols differ")
    interval = original.get("interval")
    if (
        not isinstance(interval, Mapping)
        or payload.get("start") != interval.get("start")
        or payload.get("end") != interval.get("end")
    ):
        raise ValueError("reused interval differs")
    if payload.get("config") != original.get("config") or payload.get("runtime") != original.get(
        "environment"
    ):
        raise ValueError("reused config or runtime differs")
    source_files = original.get("source_files")
    if not isinstance(source_files, Mapping):
        raise ValueError("reused source-file manifest missing")
    projected_inputs = {
        ("data/" + key.removeprefix("data/frozen/") if key.startswith("data/frozen/") else key): value
        for key, value in source_files.items()
        if isinstance(key, str)
        and (
            (key.startswith("uquant/") and (key.endswith(".py") or key.endswith(".json")))
            or key == "benchmarks/reference_registry.json"
            or key.startswith("data/frozen/")
        )
    }
    if payload.get("inputs") != projected_inputs:
        raise ValueError("reused input manifest projection differs")
    trace = original.get("trace")
    result = payload.get("result")
    if not isinstance(trace, list) or not isinstance(result, Mapping):
        raise ValueError("reused trace/result missing")
    curve: list[dict[str, object]] = []
    fills: list[Mapping[str, object]] = []
    for row in trace:
        if not isinstance(row, Mapping):
            raise ValueError("reused trace rows must be objects")
        new_fills = row.get("new_fills")
        if not isinstance(new_fills, list) or any(not isinstance(fill, Mapping) for fill in new_fills):
            raise ValueError("reused new_fills must be a list of objects")
        curve.append({"date": row.get("date"), "equity": row.get("equity")})
        fills.extend(new_fills)
    if result.get("equity_curve") != curve:
        raise ValueError("reused equity projection differs")
    metrics = original.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("reused metrics missing")
    for field in ("final_wealth", "max_drawdown", "account_orders", "annual_turnover", "gross_turnover"):
        if result.get(field) != metrics.get(field):
            raise ValueError(f"reused {field} projection differs")
    fees = math.fsum(
        _number(fill.get(name, 0), name)
        for fill in fills
        for name in ("commission", "stamp_duty", "transfer_fee")
    )
    slippage = math.fsum(_number(fill.get("slippage_cost", 0), "slippage_cost") for fill in fills)
    if not math.isclose(_number(result.get("fees"), "fees"), fees, abs_tol=1e-10) or not math.isclose(
        _number(result.get("slippage_cost"), "slippage_cost"), slippage, abs_tol=1e-10
    ):
        raise ValueError("reused cost projection differs")


def _validate_cell(
    payload: Mapping[str, Any],
    expected: tuple[list[str], str, str, float],
    *,
    case: str,
    contract_hashes: set[str],
    expected_dates: Sequence[str],
) -> dict[str, object]:
    symbols, start, end, multiplier = expected
    if (
        payload.get("method") not in {"native_public_backtest", "native_public_trace_reuse"}
        or payload.get("schema_version") != 1
    ):
        raise ValueError("unsupported replay format")
    if payload.get("method") == "native_public_trace_reuse":
        _validate_reuse(payload)
    if payload.get("case") != case or payload.get("contract_sha256") not in contract_hashes:
        raise ValueError("case or contract identity mismatch")
    if payload.get("completed") is not True:
        raise ValueError("replay is incomplete")
    if payload.get("symbols") != sorted(symbols) or payload.get("end") != end:
        raise ValueError("symbols or window mismatch")
    actual_start = payload.get("start")
    if start.startswith("@offset"):
        if not expected_dates or actual_start != expected_dates[0]:
            raise ValueError("invalid stress offset start")
    elif actual_start != start:
        raise ValueError("window mismatch")
    if _number(payload.get("cost_multiplier"), "cost_multiplier") != multiplier:
        raise ValueError("cost multiplier mismatch")
    result = payload.get("result")
    config = payload.get("config")
    if not isinstance(result, Mapping) or not isinstance(config, Mapping):
        raise ValueError("missing result or config")
    curve = result.get("equity_curve")
    if not isinstance(curve, list) or len(curve) < 2 or payload.get("sessions") != len(curve):
        raise ValueError("invalid equity curve length")
    dates: list[str] = []
    equities: list[float] = []
    for row in curve:
        if not isinstance(row, Mapping) or not isinstance(row.get("date"), str):
            raise ValueError("invalid equity row")
        dates.append(row["date"])
        equities.append(_number(row.get("equity"), "equity"))
    if dates != list(expected_dates):
        raise ValueError("equity dates do not exactly match frozen sessions")
    if any(value <= 0 for value in equities):
        raise ValueError("equity must be positive")
    initial_cash = _number(config.get("initial_cash"), "initial_cash")
    wealth = _number(result.get("final_wealth"), "final_wealth")
    if wealth <= 0 or not math.isclose(wealth, equities[-1] / initial_cash, rel_tol=1e-10):
        raise ValueError("wealth/curve reconciliation failed")
    peak, drawdown = equities[0], 0.0
    for equity in equities:
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)
    reported_dd = _number(result.get("max_drawdown"), "max_drawdown")
    if not math.isclose(drawdown, reported_dd, abs_tol=1e-10):
        raise ValueError("drawdown/curve reconciliation failed")
    orders = result.get("account_orders")
    if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
        raise ValueError("account_orders must be a nonnegative integer")
    for key in ("source_head", "source_tree", "runner_sha256"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError(f"missing {key}")
    if not isinstance(payload.get("inputs"), Mapping) or not payload["inputs"]:
        raise ValueError("missing input digests")
    raw_metrics = {
        name: value
        for name, value in result.items()
        if name != "equity_curve" and isinstance(value, (str, int, float, bool, type(None)))
    }
    for name, value in raw_metrics.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    return {"wealth": wealth, "drawdown": reported_dd, "orders": orders, "raw_metrics": raw_metrics}


@functools.lru_cache(maxsize=8)
def _committed_manifest(root: Path, source: str) -> dict[str, str]:
    try:
        names = subprocess.check_output(
            ["git", "-C", str(root), "ls-tree", "-r", "--name-only", source, "--", "uquant"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"source commit unavailable: {source}") from exc
    selected = [name for name in names if name.endswith((".py", ".json"))]
    selected.append("benchmarks/reference_registry.json")
    manifest: dict[str, str] = {}
    for name in selected:
        try:
            content = subprocess.check_output(
                ["git", "-C", str(root), "show", f"{source}:{name}"], stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"committed input unavailable: {name}") from exc
        manifest[name] = hashlib.sha256(content).hexdigest()
    for path in (root / "data" / "frozen").rglob("*"):
        if path.is_file() and ".cache" not in path.parts:
            manifest["data/" + str(path.relative_to(root / "data" / "frozen"))] = _sha(path)
    return manifest


@functools.lru_cache(maxsize=8)
def _source_config(root: Path, source: str, cost_multiplier: float) -> dict[str, object]:
    try:
        archive = subprocess.check_output(
            ["git", "-C", str(root), "archive", "--format=tar", source, "uquant"],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot archive source config: {source}") from exc
    program = """
import dataclasses, json, sys
sys.path.insert(0, sys.argv[1])
from uquant.config import DEFAULT_CONFIG, config_fingerprint
cfg = DEFAULT_CONFIG
multiplier = float(sys.argv[2])
if multiplier == 2:
    cfg = cfg.override(**{name: getattr(cfg, name) * 2 for name in
        ('commission_rate','min_commission','stamp_duty','transfer_fee','slippage')})
print(json.dumps({'full': cfg.to_dict(), 'public': dataclasses.asdict(cfg),
                  'fingerprint': config_fingerprint(cfg)}, sort_keys=True, allow_nan=False))
"""
    with tempfile.TemporaryDirectory(prefix="uquant-config-") as folder:
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
            stream.extractall(folder, filter="data")
        try:
            output = subprocess.check_output(
                [sys.executable, "-c", program, folder, str(cost_multiplier)],
                text=True,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"cannot evaluate committed source config: {source}") from exc
    value = json.loads(output)
    if not isinstance(value, dict):
        raise ValueError("committed source config output is malformed")
    return value


def _normalize_config(root: Path, payload: Mapping[str, Any]) -> dict[str, object]:
    raw = payload["config"]
    if not isinstance(raw, Mapping):
        raise ValueError("config must be an object")
    recovered = _source_config(
        root, str(payload["source_head"]), _number(payload["cost_multiplier"], "cost_multiplier")
    )
    full, public = recovered["full"], recovered["public"]
    if payload["method"] == "native_public_backtest" and payload["runner_sha256"] not in {
        LEGACY_NATIVE_RUNNER_SHA256,
        FULL_CONFIG_NATIVE_RUNNER_SHA256,
    }:
        raise ValueError("native replay runner is not an audited identity")
    if raw != full:
        if payload["runner_sha256"] != LEGACY_NATIVE_RUNNER_SHA256 or raw != public:
            raise ValueError("config is neither full source config nor audited legacy 13-field projection")
        representation = "legacy_dataclass_projection"
    else:
        representation = "full_to_dict"
    result = payload["result"]
    assert isinstance(result, Mapping)
    if (
        payload["method"] == "native_public_backtest"
        and result.get("effective_config_sha256") != recovered["fingerprint"]
    ):
        raise ValueError("effective_config_sha256 does not match recovered full source config")
    encoded_raw = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    encoded_full = json.dumps(full, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {
        "raw_representation": representation,
        "raw_config_sha256": hashlib.sha256(encoded_raw).hexdigest(),
        "effective_config_sha256": recovered["fingerprint"],
        "full_config_sha256": hashlib.sha256(encoded_full).hexdigest(),
        "full_config": full,
    }


def _validate_input_digests(root: Path, payload: Mapping[str, Any]) -> None:
    inputs = payload["inputs"]
    assert isinstance(inputs, Mapping)
    source = str(payload["source_head"])
    if inputs != _committed_manifest(root, source):
        raise ValueError("input manifest is not the complete committed-source/live-data manifest")
    try:
        tree = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", f"{source}:uquant"], text=True
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError("source tree is unavailable") from exc
    if payload["source_tree"] != tree:
        raise ValueError("source_tree does not match source_head:uquant")


def _native_evidence(
    root: Path,
    native_shards: Path | None,
    candidate_trees: set[str],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    if native_shards is None:
        return {"status": "INCOMPLETE", "passed": False, "failures": ["missing --native-shards"]}
    if len(candidate_trees) != 1:
        return {
            "status": "INCOMPLETE",
            "passed": False,
            "failures": ["paired candidate source tree is missing or mixed"],
        }
    current_tree = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD:uquant"], text=True
    ).strip()
    if candidate_trees != {current_tree}:
        return {
            "status": "INCOMPLETE",
            "passed": False,
            "failures": ["paired candidate source tree differs from native candidate checkout"],
        }
    return evaluate_native(root, root, current_tree, native_shards, contract)


def evaluate(
    root: Path,
    runs: Path,
    native_summary: Path | None = None,
    native_shards: Path | None = None,
) -> dict[str, object]:
    contract_path = root / CONTRACT
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract_hash = _sha(contract_path)
    original_contract_hash = str(contract["original_contract_sha256"])
    contract_hashes = {contract_hash, original_contract_hash}
    expected = _expected(contract)
    missing: list[str] = []
    invalid: list[str] = []
    failures: list[str] = []
    cells: dict[str, dict[str, dict[str, object]]] = {"candidate": {}, "historical": {}}
    config_identities: dict[str, dict[str, dict[str, object]]] = {"candidate": {}, "historical": {}}
    payloads: dict[str, dict[str, Mapping[str, Any]]] = {"candidate": {}, "historical": {}}
    file_hashes: dict[str, str] = {}
    all_index_dates = _common_sessions(root)
    for role, cases in expected.items():
        for case, specification in cases.items():
            path = runs / role / f"{case}.json.gz"
            label = f"{role}/{case}"
            if not path.exists():
                missing.append(label)
                continue
            file_hashes[label] = _sha(path)
            try:
                payload = _read_artifact(path)
                _, specified_start, specified_end, _ = specification
                if specified_start.startswith("@offset"):
                    offset = int(specified_start.removeprefix("@offset"))
                    canonical_dates = [
                        date
                        for date in all_index_dates
                        if contract["window"]["start"] <= date <= specified_end
                    ]
                    actual_start = canonical_dates[offset]
                else:
                    actual_start = specified_start
                expected_dates = [date for date in all_index_dates if actual_start <= date <= specified_end]
                cells[role][case] = _validate_cell(
                    payload,
                    specification,
                    case=case,
                    contract_hashes=contract_hashes,
                    expected_dates=expected_dates,
                )
                _validate_input_digests(root, payload)
                config_identities[role][case] = _normalize_config(root, payload)
                cells[role][case]["config_identity"] = config_identities[role][case]
                payloads[role][case] = payload
            except (OSError, EOFError, json.JSONDecodeError, ValueError, TypeError) as exc:
                invalid.append(f"{label}: {exc}")
    # Identity checks apply even when other evidence is absent.
    runner_identities: dict[str, dict[str, object]] = {}
    for role in ("candidate", "historical"):
        ps = list(payloads[role].values())
        if ps:
            trees = {str(p["source_tree"]) for p in ps}
            if len(trees) != 1:
                invalid.append(f"{role}: mixed source trees")
            if role == "historical" and {str(p["source_head"]) for p in ps} != {
                contract["historical_source"]
            }:
                invalid.append("historical: source head is not frozen historical source")
            standards = [p for p in ps if p.get("cost_multiplier") == 1.0]
            for identity in ("inputs", "runtime"):
                if standards and len({json.dumps(p[identity], sort_keys=True) for p in standards}) != 1:
                    invalid.append(f"{role}: mixed {identity}")  # noqa: PERF401
            try:
                runner_identities[role] = _runner_compatibility(standards)
            except ValueError as exc:
                invalid.append(f"{role}: {exc}")
            for field in ECONOMIC_FIELDS:
                if standards and len({_number(p["config"].get(field), field) for p in standards}) != 1:
                    invalid.append(f"{role}: mixed {field}")  # noqa: PERF401
            standard_full_configs = {
                str(config_identities[role][str(p["case"])]["full_config_sha256"]) for p in standards
            }
            if len(standard_full_configs) != 1:
                invalid.append(f"{role}: mixed complete effective configs")
            for p in ps:
                if p.get("cost_multiplier") == 2.0:
                    base_payload = payloads[role].get(str(p["case"]).replace("-cost2", "-offset0"))
                    if base_payload is not None:
                        base_identity = config_identities[role][str(base_payload["case"])]
                        cost_identity = config_identities[role][str(p["case"])]
                        full_config = base_identity["full_config"]
                        assert isinstance(full_config, Mapping)
                        expected_config = dict(full_config)
                        for field in ECONOMIC_FIELDS[1:]:
                            expected_config[field] = 2 * _number(base_payload["config"].get(field), field)
                        if cost_identity["full_config"] != expected_config:
                            invalid.append(f"{role}/{p['case']}: config is not exact 2x fee override")
    candidate_standard = next(iter(payloads["candidate"].values()), None)
    historical_standard = next(iter(payloads["historical"].values()), None)
    if candidate_standard is not None and historical_standard is not None:
        for field in ECONOMIC_FIELDS:
            if _number(candidate_standard["config"].get(field), field) != _number(
                historical_standard["config"].get(field), field
            ):
                invalid.append(f"candidate/historical {field} differs")  # noqa: PERF401
    paired_names = [
        f"{pool}-{window}" for pool in contract["pools"] for window in contract["performance_windows"]
    ]
    performance_ratios = _paired_wealth_ratios(paired_names, cells)
    performance_aggregate: dict[str, float] = {}
    if len(performance_ratios) == len(paired_names):
        performance_aggregate = {
            "geometric_wealth_retention": _geomean(list(performance_ratios.values())),
            "minimum_cell_wealth_retention": min(performance_ratios.values()),
        }
        if (
            performance_aggregate["geometric_wealth_retention"]
            < contract["performance"]["geometric_mean_wealth_retention"]
        ):
            failures.append("performance geometric wealth retention")
        if (
            performance_aggregate["minimum_cell_wealth_retention"]
            < contract["performance"]["minimum_cell_wealth_retention"]
        ):
            failures.append("performance minimum cell wealth retention")
    main = cells["candidate"].get("full")
    if main:
        if main["wealth"] < contract["performance"]["main_minimum_wealth"]:
            failures.append("main wealth")
        if main["drawdown"] > contract["performance"]["main_maximum_drawdown"]:
            failures.append("main drawdown")
        if main["orders"] > contract["performance"]["main_maximum_orders"]:
            failures.append("main orders")
    robustness = contract["paired_robustness"]
    loo_names = [f"loo-{symbol}" for symbol in contract["universe"]]
    loo_ratios = _paired_wealth_ratios(loo_names, cells)
    loo_aggregate: dict[str, float] = {}
    if all(n in cells["candidate"] and n in cells["historical"] for n in loo_names):
        cw = [_number(cells["candidate"][n]["wealth"], "wealth") for n in loo_names]
        hw = [_number(cells["historical"][n]["wealth"], "wealth") for n in loo_names]
        cd = [_number(cells["candidate"][n]["drawdown"], "drawdown") for n in loo_names]
        hd = [_number(cells["historical"][n]["drawdown"], "drawdown") for n in loo_names]
        wealth_probability = robustness["wealth_quantile_probability"]
        drawdown_probability = robustness["drawdown_quantile_probability"]
        lower_count = robustness["lower_quartile_count"]
        lower_c, lower_h = _geomean(sorted(cw)[:lower_count]), _geomean(sorted(hw)[:lower_count])
        p90c = linear_quantile(cd, drawdown_probability)
        p90h = linear_quantile(hd, drawdown_probability)
        loo_aggregate = {
            "positive_fraction": sum(w > 1 for w in cw) / len(contract["universe"]),
            "candidate_p10_wealth": linear_quantile(cw, wealth_probability),
            "candidate_p90_drawdown": p90c,
            "historical_p90_drawdown": p90h,
            "p90_drawdown_delta": p90c - p90h,
            "candidate_worst_drawdown": max(cd),
            "geometric_wealth_retention": _geomean(list(loo_ratios.values())),
            "candidate_lower_wealth_geomean": lower_c,
            "historical_lower_wealth_geomean": lower_h,
            "lower_wealth_geomean_ratio": lower_c / lower_h,
        }
        if loo_aggregate["positive_fraction"] < robustness["minimum_positive_fraction"]:
            failures.append("LOO positive fraction")
        if loo_aggregate["candidate_p10_wealth"] < robustness["minimum_p10_wealth"]:
            failures.append("LOO p10 wealth")
        if loo_aggregate["candidate_p90_drawdown"] > robustness["maximum_p90_drawdown"]:
            failures.append("LOO p90 drawdown")
        if loo_aggregate["candidate_worst_drawdown"] > robustness["maximum_worst_drawdown"]:
            failures.append("LOO worst drawdown")
        if loo_aggregate["geometric_wealth_retention"] < robustness["minimum_geometric_wealth_retention"]:
            failures.append("LOO geometric wealth retention")
        if not (
            (
                lower_c >= robustness["primary_wealth_improvement_ratio"] * lower_h
                and p90c <= p90h + robustness["primary_drawdown_tolerance"]
            )
            or (
                p90c <= p90h - robustness["alternative_drawdown_improvement"]
                and lower_c >= robustness["alternative_minimum_wealth_retention"] * lower_h
            )
        ):
            failures.append("LOO primary improvement")
    cross_required = contract["paired_robustness"]["cross_industry_required"]
    cross = [n for n in cross_required if n in cells["candidate"] and n in cells["historical"]]
    cross_ratios = {
        n: _number(cells["candidate"][n]["wealth"], "wealth")
        / _number(cells["historical"][n]["wealth"], "wealth")
        for n in cross
    }
    cross_aggregate: dict[str, object] = {
        "definition": "frozen repository classification; no_optical removes the five contract symbols",
        "ratios": cross_ratios,
    }
    if len(cross) == len(cross_required):
        cross_geomean = _geomean(list(cross_ratios.values()))
        cross_aggregate["geometric_wealth_retention"] = cross_geomean
        if (
            any(
                _number(cells["candidate"][n]["wealth"], "wealth") <= 1
                or cross_ratios[n] < robustness["cross_industry_minimum_cell_wealth_retention"]
                for n in cross
            )
            or cross_geomean < robustness["cross_industry_minimum_geometric_wealth_retention"]
        ):
            failures.append("cross-industry robustness")
    stress_aggregates: dict[str, dict[str, object]] = {}
    for scenario in contract["stress"]["scenarios"]:
        names = [f"{scenario}-offset{x}" for x in contract["stress"]["start_session_offsets"]]
        cost = f"{scenario}-cost2"
        if all(n in cells["candidate"] for n in [*names, cost]):
            base_wealth = _number(cells["candidate"][names[0]]["wealth"], "wealth")
            starts = [_number(cells["candidate"][n]["wealth"], "wealth") / base_wealth for n in names]
            cost_ratio = _number(cells["candidate"][cost]["wealth"], "wealth") / base_wealth
            dds = [_number(cells["candidate"][n]["drawdown"], "drawdown") for n in [*names, cost]]
            stress_aggregates[scenario] = {
                "offset_wealth_retention": dict(zip(names, starts, strict=True)),
                "median_start_wealth_retention": linear_quantile(starts, 0.5),
                "minimum_start_wealth_retention": min(starts),
                "cost_wealth_retention": cost_ratio,
                "maximum_drawdown": max(dds),
            }
            if (
                cost_ratio < contract["stress"]["minimum_cost_wealth_retention"]
                or stress_aggregates[scenario]["median_start_wealth_retention"]
                < contract["stress"]["minimum_median_start_wealth_retention"]
                or stress_aggregates[scenario]["minimum_start_wealth_retention"]
                < contract["stress"]["minimum_start_wealth_retention"]
                or stress_aggregates[scenario]["maximum_drawdown"] > contract["stress"]["maximum_drawdown"]
            ):
                failures.append(f"stress {scenario}")
    candidate_trees = {str(p["source_tree"]) for p in payloads["candidate"].values()}
    native = _native_evidence(root, native_shards, candidate_trees, contract)
    if native_summary is not None:
        native["untrusted_summary"] = {
            "status": "BLOCKED",
            "reason": "summary flags and seals do not validate underlying CellArtifact account integrity",
            "sha256": _sha(native_summary),
        }
    status = (
        "INVALID"
        if invalid
        else "INCOMPLETE"
        if missing or native["status"] not in {"PASS", "FAIL"}
        else "NOT_MET"
        if failures or not native["passed"]
        else "COMPLETE"
    )
    return {
        "contract_id": contract["contract_id"],
        "contract_hash": contract_hash,
        "original_contract_hash": original_contract_hash,
        "status": status,
        "passed": status == "COMPLETE",
        "missing": missing,
        "invalid": invalid,
        "violations": failures,
        "native_absolute": native,
        "file_hashes": file_hashes,
        "runner_identities": runner_identities,
        "cells": cells,
        "performance_ratios": performance_ratios,
        "performance_aggregate": performance_aggregate,
        "loo_ratios": loo_ratios,
        "loo_aggregate": loo_aggregate,
        "cross_industry": cross_aggregate,
        "stress_aggregates": stress_aggregates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-summary", type=Path)
    parser.add_argument("--native-shards", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite output")
    report = evaluate(args.root.resolve(), args.runs.resolve(), args.native_summary, args.native_shards)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
