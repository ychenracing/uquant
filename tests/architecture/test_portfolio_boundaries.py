from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
import subprocess
from collections import Counter
from typing import Any, cast

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_sha256
from uquant.portfolio import PortfolioAllocator

from . import _portfolio_trace as trace_module
from ._analysis import (
    _PORTFOLIO_RELOCATED_FUNCTION_DEBT,
    _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS,
    _PORTFOLIO_RELOCATED_TYPE_IGNORES,
    ROOT,
    architecture_snapshot,
    measured_debt,
)
from ._owner_transport import (
    architecture_private_relocation_projection,
    architecture_resource_surface_projection,
    architecture_source_surface_projection,
    validate_combined_allocator_topology,
)
from ._portfolio_inventory import build_portfolio_inventory, current_reflection_contract
from ._portfolio_trace_reference import assert_trace_seals, immutable_trace_from_archive
from ._portfolio_transport import (
    architecture_portfolio_type_ignore_projection,
    expand_portfolio_allocator_method,
)
from ._reviewed_owner_transport import expand_reviewed_architecture_owner
from ._validation_relocation import (
    GENERALIZATION_OWNERS,
    HOLDOUT_LANES_FACADE,
    HOLDOUT_OWNERS,
    HOLDOUT_RUNTIME_FACADE,
    POLICY_OWNERS,
)

_PORTFOLIO_REFERENCE_COMMIT = "4b6bedb03fb7c58914d9d5032a2514c67f41f6ba"
_PORTFOLIO_REFERENCE_TREE = "d3824f7c5d89521b8284b5de08cc1e82e3ab7ebd"
_TRACE_LOGIC_COMMIT = "3aadf021dce9ed77c2359065146e38209866789c"
_TRACE_LOGIC_BLOB = "cce9498d851d4007c57b2ba5eaa2e6f3216c444e"
_TRACE_RUNNER_SHA256 = "00672c67b31374c50e1e56e236a45609374637b86f9900d47dc550abe5b1f1c3"
_INVENTORY = ROOT / "artifacts" / "architecture_refactor" / "task8_cleanup_inventory.json"
_DAILY_TRACE = ROOT / "benchmarks" / "daily_portfolio_behavior_reference.json"
_TRACE_RUNNER = ROOT / "tests" / "architecture" / "_portfolio_trace.py"
# Current eight-field leader-cycle retirement changes instance configuration bytes;
# the immutable inventory still retains its original reflection and pickle facts.
_CURRENT_PORTFOLIO_INSTANCE_PICKLES = {
    "LeaderPortfolioPolicy": (
        "7a60fa0226709c2f383adfb4bc29a84d8afad15ea9755e8dd97b7697823452ef",
        1890,
    ),
    "PortfolioAllocator": (
        "ad450360759cc479a96f73341151a5f447e6e01a62f864c284934e738bc09b4a",
        1879,
    ),
    "RecoveryPortfolioPolicy": (
        "77183c1d7650f2b8cb466b46ad9ad0809d5daf9a8810f296eb836817c366f803",
        1893,
    ),
    "StrategicPortfolioPolicy": (
        "83018673969c690c3e6ef0a0fafbfb46f75b198c680f956a47c8ec2d8d1ebb84",
        1895,
    ),
}
_CURRENT_PORTFOLIO_MODE_SHA256 = {
    "double_optimized": "dacd1e67ff1ae3210a6217d87ea7d43ec19e0552529bb57e0eaa389a34420af0",
    "normal": "ddbc4423be3b5d4e4a9b77c60acba6df7c4849bd7c2412e07a9703caee16aa46",
    "optimized": "ddbc4423be3b5d4e4a9b77c60acba6df7c4849bd7c2412e07a9703caee16aa46",
    "windows_no_fcntl": "dacd1e67ff1ae3210a6217d87ea7d43ec19e0552529bb57e0eaa389a34420af0",
}
_IMPLEMENTATION_IDENTITIES = {
    "uquant/portfolio.py": (
        "264a1a463b60929d6cefcb234eddbb2644cfdd93d1b3e1484a81a0dcde26a2d1",
        128958,
    ),
    "uquant/portfolio_leaders.py": (
        "64be28bc8fb707372d987eb529f60c9c2792b3c4bf60e3da724fa6bcd92fed77",
        57412,
    ),
    "uquant/portfolio_strategic.py": (
        "df416789de3f0ac9d3e88676ca5a00af00203d9578a153ef11011517c45dc1e8",
        60050,
    ),
    "uquant/portfolio_recovery.py": (
        "bb77595cf31fe435d9b42c9abe48918ae891f3dfec56f66cead7365d7d0a62d8",
        14156,
    ),
}
_FIXED_REFERENCE_COUNTS = {
    "uquant/portfolio.py": 17,
    "uquant/portfolio_leaders.py": 11,
    "uquant/portfolio_strategic.py": 12,
    "uquant/portfolio_recovery.py": 6,
}
_IMPORT_CONSUMER_COUNTS = {
    "uquant/portfolio.py": 11,
    "uquant/portfolio_leaders.py": 2,
    "uquant/portfolio_strategic.py": 1,
    "uquant/portfolio_recovery.py": 1,
}
_ALLOCATOR_OWNER_METHODS = {
    "uquant/portfolio/allocator.py": ("_confirmed_recovery_gross", "allocate"),
    "uquant/portfolio/risk_reduction.py": (
        "_risk_attribution_mechanism",
        "_risk_retention_score",
        "_risk_retention_vector",
        "_risk_lifecycle_rank",
        "_subset_retention_vector",
        "_sparse_risk_reduce",
        "_risk_reduction_metadata",
        "_turnover_aware_sector_cap",
    ),
    "uquant/portfolio/freeze.py": (
        "_commit_frozen_exit_state",
        "_frozen_existing_targets",
    ),
    "uquant/portfolio/pipeline.py": ("_allocate_strategy",),
}
_ALLOCATOR_PACKAGE_PATHS = (
    "uquant/portfolio/__init__.py",
    "uquant/portfolio/allocator.py",
    "uquant/portfolio/freeze.py",
    "uquant/portfolio/pipeline.py",
    "uquant/portfolio/risk_reduction.py",
)
_LEADERS_OWNER_METHODS = {
    "uquant/portfolio/leaders/admission.py": (
        "_conviction_shares",
        "_conviction_evidence_qualified",
        "_correlations",
        "_admission_utility",
        "_dynamic_k",
    ),
    "uquant/portfolio/leaders/lifecycle.py": (
        "_session_clock",
        "_session_distance",
        "_rotation_allowed",
        "_update_leader_cycle_arm",
        "_retention_score",
        "_leader_lifecycle_exit_confirmed",
        "_industry_handoff",
    ),
    "uquant/portfolio/leaders/targets.py": (
        "_cap_opportunity_gross",
        "_leader_targets",
    ),
}
_LEADERS_PACKAGE_PATHS = (
    "uquant/portfolio/leaders/__init__.py",
    *_LEADERS_OWNER_METHODS,
)
_CHECKPOINT3_PACKAGE_PATHS = (
    "uquant/portfolio/strategic/__init__.py",
    "uquant/portfolio/strategic/discovery.py",
    "uquant/portfolio/strategic/lifecycle.py",
    "uquant/portfolio/strategic/targets.py",
)
_CHECKPOINT4_PACKAGE_PATHS = (
    "uquant/portfolio/recovery/__init__.py",
    "uquant/portfolio/recovery/admission.py",
    "uquant/portfolio/recovery/substitution.py",
    "uquant/portfolio/recovery/targets.py",
)
_LEADERS_TRANSPORT_NAMES = {
    "_session_distance": "_leader_session_distance",
}


def _git_source(path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{_PORTFOLIO_REFERENCE_TREE}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _assert_inventory_seals(payload: dict[str, Any]) -> None:
    assert payload["baseline_commit"] == _PORTFOLIO_REFERENCE_COMMIT
    assert payload["baseline_tree"] == _PORTFOLIO_REFERENCE_TREE
    assert payload["contract"] == "uquant-task8-pre-replacement-portfolio-inventory-v1"
    assert payload["canonical_sha256"] == canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "canonical_sha256"}
    )
    assert tuple(entry["path"] for entry in payload["entries"]) == tuple(
        _IMPLEMENTATION_IDENTITIES
    )


def _function_nodes(source: str) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}


def _immutable_allocator_methods() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(_git_source("uquant/portfolio.py"))
    allocator = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PortfolioAllocator"
    )
    return {node.name: node for node in allocator.body if isinstance(node, ast.FunctionDef)}


def _immutable_policy_methods(path: str, class_name: str) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(_git_source(path))
    policy = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {node.name: node for node in policy.body if isinstance(node, ast.FunctionDef)}


def _normalized_method(node: ast.FunctionDef) -> str:
    normalized = copy.deepcopy(node)
    normalized.decorator_list = []
    if normalized.args.args and normalized.args.args[0].arg == "self":
        normalized.args.args[0].annotation = None
    if (
        normalized.body
        and isinstance(normalized.body[0], ast.Expr)
        and isinstance(normalized.body[0].value, ast.Constant)
        and isinstance(normalized.body[0].value.value, str)
    ):
        normalized.body[0].value.value = inspect.cleandoc(normalized.body[0].value.value)
    return ast.dump(normalized, include_attributes=False)


def _project_causal_lifecycle_exit(node: ast.FunctionDef) -> ast.FunctionDef:
    """Project only the exact session clock change back to the frozen counter."""
    observation = ast.parse(
        '''
clock = f"lifecycle_exit_session:{symbol}"
session = date.toordinal()
previous = frame.loc[:date].index[-2].toordinal() if len(frame.loc[:date]) > 1 else 0
observed = account.candidate_tenure.get(clock, 0)
if observed > session:
    raise ValueError("lifecycle exit observations must be causal")
if observed != session:
    streak = account.replacement_tenure.get(key, 0) if observed == previous else 0
    account.replacement_tenure[key] = streak + 1 if broken else 0
    account.candidate_tenure[clock] = session
elif not broken:
    account.replacement_tenure[key] = 0
'''
    ).body
    projected = copy.deepcopy(node)
    start = -len(observation) - 2
    assert [ast.dump(item) for item in projected.body[start:-2]] == [
        ast.dump(item) for item in observation
    ]
    projected.body[start:-2] = ast.parse(
        "account.replacement_tenure[key] = "
        "account.replacement_tenure.get(key, 0) + 1 if broken else 0"
    ).body
    return projected


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def immutable_portfolio_inventory() -> dict[str, Any]:
    return cast(dict[str, Any], build_portfolio_inventory(ROOT))


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def immutable_portfolio_trace(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    return cast(
        dict[str, object],
        immutable_trace_from_archive(
            root=ROOT,
            destination=tmp_path_factory.mktemp("portfolio-immutable-trace") / "snapshot",
            baseline_commit=_PORTFOLIO_REFERENCE_COMMIT,
            baseline_tree=_PORTFOLIO_REFERENCE_TREE,
            implementation_identities=_IMPLEMENTATION_IDENTITIES,
            runner=_TRACE_RUNNER,
            runner_sha256=_TRACE_RUNNER_SHA256,
            logic_blob=_TRACE_LOGIC_BLOB,
        ),
    )


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def candidate_portfolio_traces() -> tuple[
    list[dict[str, Any]], Counter[str], list[dict[str, object]]
]:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    counts: Counter[str] = Counter()
    diagnostics: list[dict[str, object]] = []
    original = trace_module._legacy_economic_event_visible

    def counted_call(name: str, kwargs: dict[str, object]) -> bool:
        # Count actual wrapper invocations before the immutable trace projection.
        counts[name] += 1
        return original(name, kwargs)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(trace_module, "_legacy_economic_event_visible", counted_call)
        traces = [
            trace_module.portfolio_trace_replay(
                name=expected["name"],
                start=expected["requested_start"],
                end=expected["requested_end"],
                symbols=tuple(expected["symbols"]),
                root=ROOT,
                diagnostics=diagnostics,
            )
            for expected in payload["scenarios"]
        ]
    return traces, counts, diagnostics


def test_portfolio_cleanup_inventory_is_exactly_derived_before_replacement(
    immutable_portfolio_inventory: dict[str, Any],
) -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    _assert_inventory_seals(payload)
    assert payload == immutable_portfolio_inventory
    for entry in payload["entries"]:
        path = entry["path"]
        source = _git_source(path)
        expected_sha256, expected_size = _IMPLEMENTATION_IDENTITIES[path]
        assert hashlib.sha256(source).hexdigest() == expected_sha256
        assert len(source) == expected_size
        live = entry["live_references"]
        assert len(live["immutable_fixed_path_consumers"]) == _FIXED_REFERENCE_COUNTS[path]
        assert len(live["ast_import_consumers"]) == _IMPORT_CONSUMER_COUNTS[path]
        classified = set().union(
            live["current_executable_consumers"],
            live["historical_machine_evidence_to_preserve"],
            live["documentation_references"],
            live["other_current_or_contract_consumers"],
        )
        assert classified == set(live["immutable_fixed_path_consumers"])


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "mutation", ("omit_consumer", "change_blob")
)
def test_portfolio_resigned_inventory_mutations_are_rejected(
    immutable_portfolio_inventory: dict[str, Any], mutation: str
) -> None:
    payload = copy.deepcopy(immutable_portfolio_inventory)
    if mutation == "omit_consumer":
        payload["entries"][0]["live_references"]["immutable_fixed_path_consumers"].pop()
    else:
        payload["entries"][0]["git_blob_sha1"] = "0" * 40
    payload["canonical_sha256"] = canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "canonical_sha256"}
    )
    _assert_inventory_seals(payload)
    with pytest.raises(AssertionError):
        assert payload == immutable_portfolio_inventory


def test_portfolio_public_mro_pickle_reflection_and_import_modes_are_exact() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    expected = {
        "normal": {
            "portfolio_all": payload["portfolio_public_contract"]["runtime"]["all"],
            "classes": {
                entry["class_name"]: entry["reflection_pickle_mro_contract"] for entry in payload["entries"]
            },
            "functions": payload["portfolio_public_contract"]["runtime"]["functions"],
        },
        "mode_sha256": payload["portfolio_public_contract"]["runtime"]["import_mode_sha256"],
    }
    classes = expected["normal"]["classes"]
    for method_name in ("_leader_targets", "_update_leader_cycle_arm"):
        del classes["LeaderPortfolioPolicy"]["methods"][method_name]
        for class_name in ("LeaderPortfolioPolicy", "PortfolioAllocator", "RecoveryPortfolioPolicy"):
            del classes[class_name]["inherited_method_lookup"][method_name]
    snapshot_method = {
        "descriptor": "instance",
        "module": "uquant.portfolio.strategic.discovery",
        "qualname": "StrategicPortfolioPolicy._strategic_qualification_snapshots",
        "raw_docstring": None,
        "signature": (
            "(self, *, date: 'pd.Timestamp', user_panel: 'dict[str, pd.DataFrame]', "
            "leaders: 'dict[str, LeaderScore]') -> 'dict[str, dict[str, float]]'"
        ),
    }
    classes["StrategicPortfolioPolicy"]["methods"][
        "_strategic_qualification_snapshots"
    ] = snapshot_method
    classes["PortfolioAllocator"]["methods"]["_allocate_strategy"]["raw_docstring"] = (
        "Retain each filled owner, then allocate only available common capital."
    )
    classes["StrategicPortfolioPolicy"]["methods"]["_strategic_cohort_targets"][
        "raw_docstring"
    ] = """Run the active dynamic cohort through its current strategic epoch.

        Five neighboring ATR exit bands share one position and one final target.
        The bands smooth discrete signal dates without creating sleeves or orders;
        the execution planner still receives only one target weight per symbol. A
        exited candidate must rebuild its own causal qualification. Other
        candidates continue confirmation against the same account-level risk.
        """
    for class_name, (pickle_sha256, pickle_size) in _CURRENT_PORTFOLIO_INSTANCE_PICKLES.items():
        contract = classes[class_name]
        contract["inherited_method_lookup"]["_strategic_qualification_snapshots"] = (
            "uquant.portfolio_strategic.StrategicPortfolioPolicy"
        )
        contract["instance_pickle_sha256"] = pickle_sha256
        contract["instance_pickle_size"] = pickle_size
    expected["mode_sha256"] = _CURRENT_PORTFOLIO_MODE_SHA256
    assert current_reflection_contract(ROOT) == expected
    assert expected["normal"]["classes"]["PortfolioAllocator"]["mro"] == [
        "uquant.portfolio.PortfolioAllocator",
        "uquant.portfolio_recovery.RecoveryPortfolioPolicy",
        "uquant.portfolio_leaders.LeaderPortfolioPolicy",
        "uquant.portfolio_strategic.StrategicPortfolioPolicy",
        "uquant.portfolio_core.PortfolioCore",
        "builtins.object",
    ]


def test_portfolio_historical_class_and_instance_monkeypatch_seams_remain_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    def class_seam(*args: object, **kwargs: object) -> tuple[()]:
        return ()

    monkeypatch.setattr(PortfolioAllocator, "_allocate_strategy", class_seam)
    assert allocator._allocate_strategy.__func__ is class_seam

    def instance_seam(*args: object, **kwargs: object) -> tuple[()]:
        return ()

    monkeypatch.setattr(allocator, "_allocate_strategy", instance_seam)
    assert allocator._allocate_strategy is instance_seam


def test_portfolio_historical_machine_evidence_and_requirements_remain_bytes_exact() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    historical = {
        path
        for entry in payload["entries"]
        for path in entry["live_references"]["historical_machine_evidence_to_preserve"]
    }
    paths = (
        historical - {"benchmarks/architecture_refactor_public_api.json"}
    ) | {"requirements.txt"}
    for path in paths:
        assert (ROOT / path).read_bytes() == _git_source(path)
    baseline_inventory = json.loads(
        (ROOT / "artifacts/architecture_refactor/baseline_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    frozen_public_api = _git_source("benchmarks/architecture_refactor_public_api.json")
    assert baseline_inventory["public_api_contract"] == {
        "contract_sha256": "54b1701a7ff2f90785c7dc5c16f6e99857a29d6b653d348663084a159820bf66",
        "path": "benchmarks/architecture_refactor_public_api.json",
        "sha256": hashlib.sha256(frozen_public_api).hexdigest(),
    }
    current_public_api = json.loads(
        (ROOT / "benchmarks/public_api_contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert current_public_api["contract_sha256"] == canonical_json_sha256(
        current_public_api["contract"]
    )


def test_portfolio_daily_allocation_oracle_is_fresh_immutable_and_exact(
    immutable_portfolio_trace: dict[str, object],
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    assert_trace_seals(
        payload,
        baseline_commit=_PORTFOLIO_REFERENCE_COMMIT,
        baseline_tree=_PORTFOLIO_REFERENCE_TREE,
        checkpoint_names=trace_module._CHECKPOINT_NAMES,
    )
    assert payload == immutable_portfolio_trace


def test_portfolio_resigned_trace_tamper_is_rejected(
    immutable_portfolio_trace: dict[str, object],
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    first = payload["scenarios"][0]["records"][0]
    first["checkpoint_sha256"][0]["sha256"] = "0" * 64
    first["ordered_checkpoint_sha256"] = "1" * 64
    payload["scenarios"][0]["records_sha256"] = canonical_json_sha256(payload["scenarios"][0]["records"])
    payload["payload_sha256"] = canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "payload_sha256"}
    )
    assert_trace_seals(
        payload,
        baseline_commit=_PORTFOLIO_REFERENCE_COMMIT,
        baseline_tree=_PORTFOLIO_REFERENCE_TREE,
        checkpoint_names=trace_module._CHECKPOINT_NAMES,
    )
    with pytest.raises(AssertionError):
        assert payload == immutable_portfolio_trace


@pytest.mark.parametrize("scenario_index", range(3))  # type: ignore[untyped-decorator]
def test_portfolio_candidate_daily_trace_preserves_sessions_and_checkpoint_integrity(
    candidate_portfolio_traces: tuple[
        list[dict[str, Any]], Counter[str], list[dict[str, object]]
    ],
    scenario_index: int,
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    observed, _, diagnostics = candidate_portfolio_traces
    current = observed[scenario_index]
    historical = payload["scenarios"][scenario_index]
    # The redesign intentionally supersedes old target and owner trajectories.
    # Preserve the causal replay inputs and checkpoint coverage, not their values.
    for field in ("name", "requested_start", "requested_end", "symbols", "record_count"):
        assert current[field] == historical[field]
    assert [record["date"] for record in current["records"]] == [
        record["date"] for record in historical["records"]
    ]
    assert current["records_sha256"] == canonical_json_sha256(current["records"])
    for record in current["records"]:
        assert tuple(checkpoint["name"] for checkpoint in record["checkpoint_sha256"]) == (
            trace_module._CHECKPOINT_NAMES
        )
        assert all(len(checkpoint["sha256"]) == 64 for checkpoint in record["checkpoint_sha256"])
    assert diagnostics == []


def test_portfolio_trace_dataframe_mismatch_reports_column_precision_digests() -> None:
    frame = pd.DataFrame(
        {
            "close": [1.0, 1.000000000000001],
            "signal": [0.25, float("nan")],
        }
    )

    serialized = trace_module._jsonable(frame)
    diagnostic = trace_module._diagnostic_digest_tree(serialized, depth=0)

    assert diagnostic["dataframe"] == {
        "close": {
            "precision_10": "2551942eb1b59f8ec2803a62b9a24dd3c9e05bfb7e2608f6718ad752943d1946",
            "precision_12": "2551942eb1b59f8ec2803a62b9a24dd3c9e05bfb7e2608f6718ad752943d1946",
            "precision_14": "2551942eb1b59f8ec2803a62b9a24dd3c9e05bfb7e2608f6718ad752943d1946",
            "precision_15": "d17c91dae3e57d7bdbe140d5fba2a8ada4e34f2d73343d69c439da9912f9a91d",
        },
        "signal": {
            "precision_10": "b3855cdcfc0b516491bbee699a86229bdf6aa70e0d81e218a0f3d5ecb0aa2576",
            "precision_12": "b3855cdcfc0b516491bbee699a86229bdf6aa70e0d81e218a0f3d5ecb0aa2576",
            "precision_14": "b3855cdcfc0b516491bbee699a86229bdf6aa70e0d81e218a0f3d5ecb0aa2576",
            "precision_15": "b3855cdcfc0b516491bbee699a86229bdf6aa70e0d81e218a0f3d5ecb0aa2576",
        },
    }


def test_portfolio_trace_normalizes_only_rolling_trend_r2_backend_drift() -> None:
    left = pd.DataFrame(
        {
            "ret120": [0.25],
            "trend_r2_120": [0.123456789041],
        }
    )
    right = pd.DataFrame(
        {
            "ret120": [0.25],
            "trend_r2_120": [0.123456789049],
        }
    )

    assert trace_module._jsonable(left) == trace_module._jsonable(right)

    changed_feature = right.copy()
    changed_feature.loc[0, "ret120"] += 0.000000000001
    assert trace_module._jsonable(left) != trace_module._jsonable(changed_feature)


def test_portfolio_daily_trace_visits_one_combined_owner_per_decision(
    candidate_portfolio_traces: tuple[
        list[dict[str, Any]], Counter[str], list[dict[str, object]]
    ],
) -> None:
    traces, counts, _ = candidate_portfolio_traces
    decisions = sum(int(trace["record_count"]) for trace in traces)
    assert decisions == 60
    assert counts["_allocate_strategy"] == decisions
    assert counts["_strategic_cohort_targets"] == decisions
    assert counts["_recovery_anchor_substitution"] == 0
    assert counts["_update_leader_cycle_arm"] == 0
    assert counts["_leader_targets"] == 0


def test_portfolio_allocator_real_package_owners_replace_only_portfolio_monolith() -> None:
    assert not (ROOT / "uquant/portfolio.py").exists()
    assert all((ROOT / path).is_file() for path in _ALLOCATOR_PACKAGE_PATHS)
    assert (ROOT / "uquant/portfolio_leaders.py").is_file()
    assert (ROOT / "uquant/portfolio_strategic.py").is_file()
    assert (ROOT / "uquant/portfolio_recovery.py").is_file()


def test_portfolio_allocator_preserves_risk_owners_and_validates_combined_strategy() -> None:
    immutable = _immutable_allocator_methods()
    observed: set[str] = set()
    for path, names in _ALLOCATOR_OWNER_METHODS.items():
        candidate = _function_nodes((ROOT / path).read_text(encoding="utf-8"))
        for name in names:
            observed.add(name)
            candidate_method = candidate[name]
            if name == "_allocate_strategy":
                validate_combined_allocator_topology(root=ROOT)
                continue
            candidate_method = expand_portfolio_allocator_method(
                root=ROOT,
                relative=path,
                name=name,
                candidate=None,
            )
            assert _normalized_method(candidate_method) == _normalized_method(
                immutable[name]
            )
    assert observed == set(immutable)


def test_portfolio_allocator_ast_gate_rejects_threshold_compare_and_call_order_mutations() -> None:
    immutable = _immutable_allocator_methods()
    threshold = copy.deepcopy(immutable["_confirmed_recovery_gross"])
    numeric = next(
        node
        for node in ast.walk(threshold)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    )
    numeric.value = float(numeric.value) + 0.01
    assert _normalized_method(threshold) != _normalized_method(immutable["_confirmed_recovery_gross"])

    comparison = copy.deepcopy(immutable["allocate"])
    compare = next(node for node in ast.walk(comparison) if isinstance(node, ast.Compare))
    compare.ops[0] = ast.Gt() if not isinstance(compare.ops[0], ast.Gt) else ast.Lt()
    assert _normalized_method(comparison) != _normalized_method(immutable["allocate"])

    orchestration = copy.deepcopy(immutable["_allocate_strategy"])
    body_index = next(
        index
        for index, statement in enumerate(orchestration.body[:-1])
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr))
        and isinstance(orchestration.body[index + 1], (ast.Assign, ast.AnnAssign, ast.Expr))
    )
    orchestration.body[body_index], orchestration.body[body_index + 1] = (
        orchestration.body[body_index + 1],
        orchestration.body[body_index],
    )
    assert _normalized_method(orchestration) != _normalized_method(immutable["_allocate_strategy"])


def test_portfolio_allocator_source_surface_migration_is_exact() -> None:
    immutable = json.loads(_git_source("benchmarks/source_surface_registry.json"))
    candidate = json.loads(
        (ROOT / "benchmarks/source_surface_registry.json").read_text(encoding="utf-8")
    )
    assert candidate["canonical_sha256"] == canonical_json_sha256(
        {key: value for key, value in candidate.items() if key != "canonical_sha256"}
    )
    immutable_surfaces = {surface["id"]: surface for surface in immutable["surfaces"]}
    candidate_surfaces = {surface["id"]: surface for surface in candidate["surfaces"]}
    assert tuple(candidate_surfaces) == tuple(immutable_surfaces)
    for identifier, baseline in immutable_surfaces.items():
        expected = set(baseline["source_paths"])
        if "uquant/portfolio.py" in expected:
            expected.remove("uquant/portfolio.py")
            expected.update(_ALLOCATOR_PACKAGE_PATHS)
        if "uquant/portfolio_leaders.py" in expected:
            expected.update(_LEADERS_PACKAGE_PATHS)
        if "uquant/portfolio_strategic.py" in expected:
            expected.update(_CHECKPOINT3_PACKAGE_PATHS)
        if "uquant/portfolio_recovery.py" in expected:
            expected.update(_CHECKPOINT4_PACKAGE_PATHS)
        if "uquant/validation/generalization.py" in expected:
            expected.remove("uquant/validation/generalization.py")
            expected.update(GENERALIZATION_OWNERS)
        if "uquant/validation/generalization_reference.py" in expected:
            expected.update(POLICY_OWNERS)
        if "uquant/validation/holdout.py" in expected:
            expected.remove("uquant/validation/holdout.py")
            expected.update(HOLDOUT_OWNERS)
        if HOLDOUT_RUNTIME_FACADE in expected:
            expected.update(HOLDOUT_OWNERS[5:])
        if HOLDOUT_LANES_FACADE in expected:
            expected.add(HOLDOUT_OWNERS[4])
        if {
            "uquant/risk_sentinel/cli.py",
            "uquant/risk_sentinel/validation.py",
        } & expected:
            expected.add("uquant/risk_sentinel/provenance.py")
        expected = architecture_source_surface_projection(identifier, expected)
        assert set(candidate_surfaces[identifier]["source_paths"]) == expected
        assert candidate_surfaces[identifier]["resource_paths"] == (
            architecture_resource_surface_projection(
                identifier, baseline["resource_paths"]
            )
        )
        assert {
            key: value
            for key, value in candidate_surfaces[identifier].items()
            if key not in {"source_paths", "resource_paths"}
        } == {
            key: value
            for key, value in baseline.items()
            if key not in {"source_paths", "resource_paths"}
        }


def test_portfolio_allocator_has_one_allocator_and_sparse_reducer_without_reverse_imports() -> None:
    package_sources = {
        path.relative_to(ROOT).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "uquant/portfolio").glob("*.py"))
    }
    classes = [
        node
        for tree in package_sources.values()
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PortfolioAllocator"
    ]
    reducers = [
        node
        for tree in package_sources.values()
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_sparse_risk_reduce"
    ]
    assert len(classes) == 1
    assert len(reducers) == 1
    forbidden = {
        "fcntl",
        "research",
        "scripts",
        "tests",
        "uquant.account",
        "uquant.application",
        "uquant.engine",
        "uquant.execution",
        "uquant.validation",
    }
    for path, tree in package_sources.items():
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
        }
        assert not {
            name
            for name in imports
            for blocked in forbidden
            if name == blocked or name.startswith(f"{blocked}.")
        }, path


def test_portfolio_allocator_private_and_complexity_relocations_are_exact_and_closed() -> None:
    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    assert isinstance(graph, dict)
    relocated = graph["task8_relocated_private_imports"]
    ordinary = graph["cross_module_private_imports"]
    assert architecture_private_relocation_projection(
        root=ROOT,
        task=8,
        observed={str(row["id"]) for row in relocated},
        expected=set(_PORTFOLIO_RELOCATED_PRIVATE_IMPORTS),
    ) == _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in ordinary
        if str(row["importer"]).startswith("uquant.portfolio.")
        or str(row["imported_from"]).startswith("uquant.portfolio.")
    }
    expected_allocator_functions = {
        f"{path.removesuffix('.py').replace('/', '.')}:{name}"
        for path, names in _ALLOCATOR_OWNER_METHODS.items()
        for name in names
    }
    allocator_function_debt = {
        identifier: legacy
        for identifier, legacy in _PORTFOLIO_RELOCATED_FUNCTION_DEBT.items()
        if identifier in expected_allocator_functions
    }
    assert set(allocator_function_debt) == expected_allocator_functions
    assert set(allocator_function_debt.values()) == {
        f"uquant.portfolio:PortfolioAllocator.{name}"
        for names in _ALLOCATOR_OWNER_METHODS.values()
        for name in names
    }
    observed_type_ignores = {
        str(row["id"])
        for row in snapshot["type_ignores"]
        if str(row["path"]).startswith("uquant/portfolio/")
    }
    assert architecture_portfolio_type_ignore_projection(
        root=ROOT,
        observed=observed_type_ignores,
        expected=set(_PORTFOLIO_RELOCATED_TYPE_IGNORES),
    ) == set(_PORTFOLIO_RELOCATED_TYPE_IGNORES)

    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    source_texts["uquant/portfolio/allocator.py"] += (
        "\nfrom .freeze import _unreviewed_portfolio_edge\n\n"
        "def _unreviewed_portfolio_debt() -> int:\n"
        + "".join(f"    value = {index}\n" for index in range(121))
        + "    return value\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    mutation_graph = mutation["import_graph"]
    assert isinstance(mutation_graph, dict)
    assert "uquant.portfolio.allocator:uquant.portfolio.freeze:_unreviewed_portfolio_edge" in {
        str(row["id"]) for row in mutation_graph["cross_module_private_imports"]
    }
    mutation_debt = measured_debt(mutation)
    assert "uquant.portfolio:_unreviewed_portfolio_debt" in {
        str(row["id"]) for row in mutation_debt["long_functions"]
    }


def test_portfolio_leaders_leader_owners_and_thin_facade_are_complete() -> None:
    assert all((ROOT / path).is_file() for path in _LEADERS_PACKAGE_PATHS)
    facade = ast.parse((ROOT / "uquant/portfolio_leaders.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in facade.body)
    imports = {
        alias.name
        for node in facade.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imports == {"LeaderPortfolioPolicy"}


def test_portfolio_leaders_moved_leader_methods_are_immutable_ast_exact() -> None:
    immutable = _immutable_policy_methods(
        "uquant/portfolio_leaders.py", "LeaderPortfolioPolicy"
    )
    observed: set[str] = set()
    for path, names in _LEADERS_OWNER_METHODS.items():
        candidate = _function_nodes((ROOT / path).read_text(encoding="utf-8"))
        for name in names:
            observed.add(name)
            candidate_node = copy.deepcopy(
                candidate.get(_LEADERS_TRANSPORT_NAMES.get(name, name))
            )
            if name in {"_dynamic_k", "_update_leader_cycle_arm", "_leader_targets"}:
                candidate_node = expand_reviewed_architecture_owner(
                    root=ROOT,
                    relative=path,
                    name=name,
                    candidate=None,
                )
            if name == "_leader_lifecycle_exit_confirmed":
                candidate_node = _project_causal_lifecycle_exit(candidate_node)
            candidate_node.name = name
            assert _normalized_method(candidate_node) == _normalized_method(immutable[name])
    assert observed == set(immutable)


@pytest.mark.parametrize(
    ("original", "replacement"),
    (
        ("if observed != session:", "if True:"),
        ("if observed == previous else 0", "if observed <= previous else 0"),
        ("if observed > session:", "if observed < session:"),
        ("elif not broken:", "elif broken:"),
        (">= self.cfg.replacement_confirm_days", ">= 1"),
        (">= self.cfg.min_hold_days", ">= 1"),
    ),
)
def test_portfolio_lifecycle_exit_projection_rejects_clock_and_rule_mutations(
    original: str, replacement: str,
) -> None:
    name = "_leader_lifecycle_exit_confirmed"
    source = (ROOT / "uquant/portfolio/leaders/lifecycle.py").read_text(encoding="utf-8")
    method_source = ast.unparse(_function_nodes(source)[name])
    assert method_source.count(original) == 1
    mutated = _function_nodes(method_source.replace(original, replacement))[name]
    immutable = _immutable_policy_methods(
        "uquant/portfolio_leaders.py", "LeaderPortfolioPolicy"
    )[name]

    with pytest.raises(AssertionError):
        projected = _project_causal_lifecycle_exit(mutated)
        assert _normalized_method(projected) == _normalized_method(immutable)


def test_portfolio_leaders_ast_gate_rejects_leader_rule_mutations() -> None:
    immutable = _immutable_policy_methods(
        "uquant/portfolio_leaders.py", "LeaderPortfolioPolicy"
    )

    threshold = copy.deepcopy(immutable["_dynamic_k"])
    numeric = next(
        node
        for node in ast.walk(threshold)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    )
    numeric.value = float(numeric.value) + 0.01
    assert _normalized_method(threshold) != _normalized_method(immutable["_dynamic_k"])

    comparison = copy.deepcopy(immutable["_rotation_allowed"])
    compare = next(node for node in ast.walk(comparison) if isinstance(node, ast.Compare))
    compare.ops[0] = ast.Gt() if not isinstance(compare.ops[0], ast.Gt) else ast.Lt()
    assert _normalized_method(comparison) != _normalized_method(immutable["_rotation_allowed"])

    boolean = copy.deepcopy(immutable["_leader_targets"])
    bool_op = next(node for node in ast.walk(boolean) if isinstance(node, ast.BoolOp))
    bool_op.op = ast.Or() if isinstance(bool_op.op, ast.And) else ast.And()
    assert _normalized_method(boolean) != _normalized_method(immutable["_leader_targets"])

    sort_key = copy.deepcopy(immutable["_cap_opportunity_gross"])
    sorted_call = next(
        node
        for node in ast.walk(sort_key)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "sorted"
        and any(keyword.arg == "key" for keyword in node.keywords)
    )
    key = next(keyword for keyword in sorted_call.keywords if keyword.arg == "key")
    key.value = ast.Lambda(
        args=ast.arguments(
            posonlyargs=[],
            args=[ast.arg(arg="item")],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=ast.Constant(value=0),
    )
    assert _normalized_method(sort_key) != _normalized_method(
        immutable["_cap_opportunity_gross"]
    )

    mutation_order = copy.deepcopy(immutable["_update_leader_cycle_arm"])
    body_index = next(
        index
        for index, statement in enumerate(mutation_order.body[:-1])
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr))
        and isinstance(
            mutation_order.body[index + 1], (ast.Assign, ast.AnnAssign, ast.Expr)
        )
    )
    mutation_order.body[body_index], mutation_order.body[body_index + 1] = (
        mutation_order.body[body_index + 1],
        mutation_order.body[body_index],
    )
    assert _normalized_method(mutation_order) != _normalized_method(
        immutable["_update_leader_cycle_arm"]
    )


def test_portfolio_leaders_private_and_complexity_relocations_are_exact() -> None:
    expected_functions = {
        f"{path.removesuffix('.py').replace('/', '.')}:{name}"
        for path, names in _ALLOCATOR_OWNER_METHODS.items()
        for name in names
    } | {
        (
            f"{path.removesuffix('.py').replace('/', '.')}:"
            f"{_LEADERS_TRANSPORT_NAMES.get(name, name)}"
        )
        for path, names in _LEADERS_OWNER_METHODS.items()
        for name in names
    }
    allocator2_functions = {
        identifier
        for identifier in _PORTFOLIO_RELOCATED_FUNCTION_DEBT
        if identifier in expected_functions
    }
    assert allocator2_functions == expected_functions
    assert {
        legacy
        for legacy in _PORTFOLIO_RELOCATED_FUNCTION_DEBT.values()
        if legacy.startswith("uquant.portfolio_leaders:")
    } == {
        f"uquant.portfolio_leaders:LeaderPortfolioPolicy.{name}"
        for names in _LEADERS_OWNER_METHODS.values()
        for name in names
    }

    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    assert isinstance(graph, dict)
    assert architecture_private_relocation_projection(
        root=ROOT,
        task=8,
        observed={str(row["id"]) for row in graph["task8_relocated_private_imports"]},
        expected=set(_PORTFOLIO_RELOCATED_PRIVATE_IMPORTS),
    ) == _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in graph["cross_module_private_imports"]
        if str(row["importer"]).startswith("uquant.portfolio.leaders")
        or str(row["imported_from"]).startswith("uquant.portfolio.leaders")
    }

    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    source_texts["uquant/portfolio/leaders/admission.py"] += (
        "\nfrom .lifecycle import _unreviewed_leader_edge\n\n"
        "def _unreviewed_leader_debt() -> int:\n"
        + "".join(f"    value = {index}\n" for index in range(121))
        + "    return value\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    mutation_graph = mutation["import_graph"]
    assert isinstance(mutation_graph, dict)
    assert (
        "uquant.portfolio.leaders.admission:"
        "uquant.portfolio.leaders.lifecycle:_unreviewed_leader_edge"
    ) in {str(row["id"]) for row in mutation_graph["cross_module_private_imports"]}
    mutation_debt = measured_debt(mutation)
    assert "uquant.portfolio_leaders:_unreviewed_leader_debt" in {
        str(row["id"]) for row in mutation_debt["long_functions"]
    }
