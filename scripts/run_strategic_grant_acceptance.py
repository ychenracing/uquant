"""Run the bounded strategic-grant economic and identity acceptance."""

# ruff: noqa: E402 - direct execution must prefer this checkout's packages

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.strategic_evidence.forced_owner import (
    NATIVE_ELIGIBILITY_DATE,
    run_forced_owner_economic_cell,
)
from uquant.atomic_io import atomic_write_text
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.provenance.fingerprints import source_surface_fingerprint
from uquant.provenance.surfaces import load_source_surface_registry
from uquant.validation.manifest import verify_data_manifest

CONTRACT_PATH = ROOT / "benchmarks" / "strategic_grant_acceptance_contract.json"
CLOSURE_CONTRACT_PATH = ROOT / "benchmarks" / "strategic_evidence_closure_contract.json"
GRANT_CASE_IDS = (
    "baseline",
    "native-sz300308",
    "native-sz300502",
    "native-sz300394",
)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strip_fields(value: object, ignored: frozenset[str]) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_fields(item, ignored)
            for key, item in value.items()
            if str(key) not in ignored
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_strip_fields(item, ignored) for item in value]
    return value


def _baseline_views(result: Mapping[str, Any], ignored: frozenset[str]) -> dict[str, object]:
    trace = result["decision_trace"]
    if not isinstance(trace, list):
        raise ValueError("baseline decision trace is malformed")
    targets = [
        {
            "date": row["date"],
            "targets": row["targets"],
            "target_gross": row["target_gross"],
        }
        for row in trace
    ]
    account = result["final_account"]
    if not isinstance(account, Mapping):
        raise ValueError("baseline final account is malformed")
    orders = json.loads(json.dumps(result["order_ledger"]))
    event_order_ids: dict[str, str] = {}
    physical_order_ids: dict[str, str] = {}
    for index, order in enumerate(orders, start=1):
        canonical_order_id = f"ECONOMIC_ORDER_{index:06d}"
        physical_order_ids[str(order["order_id"])] = canonical_order_id
        order["order_id"] = canonical_order_id
    fills = json.loads(json.dumps(account["fills"]))
    for fill in fills:
        event_id = str(fill.get("event_id", ""))
        physical_order_id = str(fill["order_id"])
        if event_id:
            event_order_ids.setdefault(
                event_id, f"ECONOMIC_ORDER_{len(event_order_ids) + 1:06d}"
            )
        matched_order_id = event_order_ids.get(event_id) or physical_order_ids.get(
            physical_order_id
        )
        if matched_order_id is None:
            raise ValueError("baseline fill has no matching economic order")
        fill["order_id"] = matched_order_id
    return {
        "targets": _strip_fields(targets, ignored),
        "orders": _strip_fields(orders, ignored),
        "fills": _strip_fields(fills, ignored),
        "positions": _strip_fields(result["daily_replay_evidence"], ignored),
        "equity": _strip_fields(result["equity_curve"], ignored),
    }


def run_baseline(contract: Mapping[str, Any]) -> dict[str, object]:
    baseline = contract["baseline"]
    if not isinstance(baseline, Mapping):
        raise ValueError("strategic grant baseline contract is malformed")
    engine = ProductionEngine(ROOT / "data" / "frozen")
    result = engine.backtest(
        symbols=tuple(str(item) for item in baseline["symbols"]),
        start=str(baseline["start"]),
        end=str(baseline["end"]),
    )
    ignored = frozenset(str(item) for item in contract["ignored_non_economic_fields"])
    actual_sha256 = {
        name: _canonical_sha256(value)
        for name, value in _baseline_views(result, ignored).items()
    }
    if actual_sha256 != baseline["expected_sha256"]:
        raise RuntimeError("strategic grant baseline economic path differs")
    actual_metrics = {
        str(name): result[str(name)]
        for name in baseline["expected_metrics"]
    }
    if actual_metrics != baseline["expected_metrics"]:
        raise RuntimeError("strategic grant baseline metrics differ")
    first_positive = next(
        row["date"] for row in result["decision_trace"] if float(row["target_gross"]) > 0.0
    )
    if first_positive != baseline["expected_first_positive_target_session"]:
        raise RuntimeError("strategic grant baseline first target session differs")
    return {
        "first_positive_target_session": first_positive,
        "metrics": actual_metrics,
        "sha256": actual_sha256,
    }


def _grant_case_specs(contract: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    return (
        {"case_id": "baseline", "kind": "baseline"},
        *(
            {
                "case_id": f"native-{spec['owner']}",
                "date": str(spec["date"]),
                "kind": "native_eligibility",
                "owner": str(spec["owner"]),
            }
            for spec in contract["native_eligibility"]
        ),
    )


def _run_native_cell(
    contract: Mapping[str, Any],
    spec: Mapping[str, str],
) -> dict[str, object]:
    closure = json.loads(CLOSURE_CONTRACT_PATH.read_text(encoding="utf-8"))
    symbols = tuple(str(item) for item in closure["matrix"]["canonical_universe"])
    baseline = contract["baseline"]
    if not isinstance(baseline, Mapping):
        raise ValueError("strategic grant baseline contract is malformed")
    owner = spec["owner"]
    session = spec["date"]
    cell, _ = run_forced_owner_economic_cell(
        ROOT / "data" / "frozen",
        control_id=f"STRATEGIC_GRANT:{owner}",
        symbols=symbols,
        owner=owner,
        mode=NATIVE_ELIGIBILITY_DATE,
        date=session,
        target_gross=0.95,
        selection_evidence={"qualification_date": session},
        start=str(baseline["start"]),
        end=str(baseline["end"]),
        cfg=DEFAULT_CONFIG,
    )
    if cell.status != "SUCCESS":
        detail = cell.error or str(cell.selection_evidence)
        raise RuntimeError(f"native eligibility {owner} ended as {cell.status}: {detail}")
    return {
        "date": session,
        "final_account_sha256": cell.final_account_sha256,
        "owner": owner,
        "status": cell.status,
        "trace_sha256": cell.trace_sha256,
    }


def _run_native_cells(contract: Mapping[str, Any]) -> list[dict[str, object]]:
    return [
        _run_native_cell(contract, spec)
        for spec in _grant_case_specs(contract)
        if spec["kind"] == "native_eligibility"
    ]


def _execute_case(
    contract: Mapping[str, Any],
    *,
    case_id: str,
) -> dict[str, object]:
    specs = {spec["case_id"]: spec for spec in _grant_case_specs(contract)}
    spec = specs.get(case_id)
    if spec is None:
        raise ValueError("unknown strategic grant case")
    if spec["kind"] == "baseline":
        return run_baseline(contract)
    return _run_native_cell(contract, spec)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_identity_payload(
    contract: Mapping[str, Any],
    spec: Mapping[str, str],
) -> dict[str, object]:
    closure = json.loads(CLOSURE_CONTRACT_PATH.read_text(encoding="utf-8"))
    registry = load_source_surface_registry(ROOT)
    return {
        "case": dict(spec),
        "closure_contract_sha256": _canonical_sha256(closure),
        "config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "frozen_data": verify_data_manifest(ROOT / "data" / "frozen"),
        "full_package_source_sha256": source_surface_fingerprint(
            ROOT, "full_package_v1"
        ),
        "grant_contract_sha256": _canonical_sha256(contract),
        "production_source_sha256": code_fingerprint(),
        "runner_source_sha256": _sha256_file(Path(__file__)),
        "runtime": runtime_environment_provenance(ROOT),
        "schema_version": 1,
        "source_surface_registry_sha256": registry.canonical_sha256,
        "validation_runner_source_sha256": source_surface_fingerprint(
            ROOT, "validation_runner_v1"
        ),
    }


def _read_cache(path: Path, *, identity: str) -> dict[str, object] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, Mapping) or set(envelope) != {
        "identity",
        "payload",
        "sha256",
    }:
        return None
    payload = envelope.get("payload")
    if (
        envelope.get("identity") != identity
        or not isinstance(payload, Mapping)
        or envelope.get("sha256") != _canonical_sha256(payload)
    ):
        return None
    return {str(key): value for key, value in payload.items()}


def _write_cache(
    path: Path,
    *,
    identity: str,
    payload: Mapping[str, object],
) -> None:
    envelope = {
        "identity": identity,
        "payload": dict(payload),
        "sha256": _canonical_sha256(payload),
    }
    atomic_write_text(path, json.dumps(envelope, indent=2, sort_keys=True) + "\n")


def run_diagnostic_case(
    *,
    case_id: str,
    output: Path,
    cache_dir: Path,
) -> dict[str, object]:
    """Run one non-authoritative grant diagnostic bound to complete inputs."""

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    specs = {spec["case_id"]: spec for spec in _grant_case_specs(contract)}
    if case_id not in GRANT_CASE_IDS or case_id not in specs:
        raise ValueError("unknown strategic grant case")
    spec = specs[case_id]
    identity_payload = _cache_identity_payload(contract, spec)
    identity = _canonical_sha256(identity_payload)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{case_id}-{identity}.json"
    cached = _read_cache(cache_path, identity=identity)
    if cached is None:
        case = _execute_case(contract, case_id=case_id)
        _write_cache(cache_path, identity=identity, payload=case)
        cache_hit = False
    else:
        case = cached
        cache_hit = True
    result: dict[str, object] = {
        "authoritative_acceptance": False,
        "cache_hit": cache_hit,
        "cache_identity": identity,
        "cache_identity_payload": identity_payload,
        "case": case,
        "diagnostic_only": True,
        "selected_case": case_id,
        "status": "PASS",
    }
    atomic_write_text(output, json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def run_acceptance(output: Path) -> dict[str, object]:
    """Run exactly the bounded strategic-grant contract and persist compact facts."""

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    result: dict[str, object] = {
        "baseline": run_baseline(contract),
        "native_eligibility": _run_native_cells(contract),
        "status": "PASS",
    }
    atomic_write_text(output, json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", dest="case_id", choices=GRANT_CASE_IDS)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    if args.case_id is None:
        if args.cache_dir is not None:
            parser.error("--cache-dir requires --case")
        run_acceptance(args.output)
    else:
        if args.cache_dir is None:
            parser.error("--case requires --cache-dir")
        run_diagnostic_case(
            case_id=args.case_id,
            output=args.output,
            cache_dir=args.cache_dir,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
