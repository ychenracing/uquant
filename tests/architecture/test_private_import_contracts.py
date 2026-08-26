from __future__ import annotations

import ast
import dataclasses
import json
import subprocess
from collections.abc import Mapping

import pytest

from ._analysis import ROOT, architecture_snapshot, measured_debt
from ._analysis_debt import (
    _CONFIG_RELOCATED_PRIVATE_IMPORTS,
    _EXECUTION_RELOCATED_PRIVATE_IMPORTS,
    _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS,
    _RISK_RELOCATED_PRIVATE_IMPORTS,
    _VALIDATION_RELOCATED_PRIVATE_IMPORTS,
)
from ._governance_inventory import (
    ARCHITECTURE_REFERENCE_TREE,
    CURRENT_GOVERNED_SCRIPTS,
    GOVERNED_SCRIPTS,
)
from ._private_imports import (
    GOVERNED_ROOTS,
    PRIVATE_IMPORT_REFERENCE_TREE,
    REMEDIATION_START_COMMIT,
    build_inventory_from_immutable_git,
    current_governed_sources,
    load_inventory,
    scan_governed_private_edges,
    verify_inventory_seal,
)


def _immutable_public_script_definitions(relative: str) -> tuple[str, ...]:
    source = subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:{relative}"],
        cwd=ROOT,
        text=True,
    )
    return tuple(
        node.name
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    )


def _literal_script_all(relative: str) -> tuple[str, ...]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ]
    assert len(assignments) == 1
    value = ast.literal_eval(assignments[0].value)
    assert isinstance(value, tuple)
    assert all(isinstance(name, str) and not name.startswith("_") for name in value)
    return value


def _canonical_private_acceptance_allowlist() -> list[object]:
    baseline = json.loads(
        (ROOT / "artifacts/architecture_refactor/baseline_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    architecture_debt = baseline["architecture_debt"]
    assert isinstance(architecture_debt, Mapping)
    final = architecture_debt["final_acceptance_allowlist"]
    assert isinstance(final, Mapping)
    value = final["cross_module_private_imports"]
    assert isinstance(value, list)
    return value


def test_architecture_private_import_inventory_is_immutable_and_sealed() -> None:
    payload = load_inventory()
    verify_inventory_seal(payload)
    assert payload["immutable_review"] == {
        "commit": REMEDIATION_START_COMMIT,
        "tree": PRIVATE_IMPORT_REFERENCE_TREE,
    }
    assert payload == build_inventory_from_immutable_git()
    assert payload["counts"] == {
        "direct": 393,
        "qualified": 24,
        "total": 417,
    }
    assert payload["direct_by_root"] == {
        "research": 11,
        "scripts": 0,
        "uquant": 382,
    }
    assert payload["qualified_by_root"] == {
        "research": 0,
        "scripts": 0,
        "uquant": 24,
    }


def test_architecture_private_import_inventory_accounts_for_all_relocation_buckets() -> None:
    payload = load_inventory()
    rows = payload["direct_private_imports"]
    assert isinstance(rows, list)
    uquant_ids = {
        str(row["id"])
        for row in rows
        if isinstance(row, Mapping) and row["root"] == "uquant"
    }
    relocation_sets = (
        _CONFIG_RELOCATED_PRIVATE_IMPORTS,
        _EXECUTION_RELOCATED_PRIVATE_IMPORTS,
        _RISK_RELOCATED_PRIVATE_IMPORTS,
        _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS,
        _VALIDATION_RELOCATED_PRIVATE_IMPORTS,
    )
    current_intersections = tuple(uquant_ids & values for values in relocation_sets)
    assert tuple(len(values) for values in current_intersections) == (
        123,
        17,
        29,
        35,
        178,
    )
    assert sum(map(len, current_intersections)) == 382
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(current_intersections)
        for right in current_intersections[index + 1 :]
    )
    assert uquant_ids == set().union(*current_intersections)


def test_architecture_raw_scanner_treats_a_relocated_id_as_current_live_debt() -> None:
    known_relocated_id = (
        "uquant.account.codec:uquant.account.validation_common:_finite_number"
    )
    assert known_relocated_id in _CONFIG_RELOCATED_PRIVATE_IMPORTS
    mutation = {
        "uquant/account/codec.py": (
            "from uquant.account.validation_common import _finite_number\n"
        ),
        "uquant/account/validation_common.py": (
            "def _finite_number(value: object) -> float:\n"
            "    return float(value)\n"
        ),
    }
    observed = scan_governed_private_edges(mutation)
    assert [row["id"] for row in observed["direct"]] == [known_relocated_id]


def test_architecture_live_analyzer_separates_current_debt_from_historical_projection() -> None:
    known_relocated_id = (
        "uquant.account.migrations:uquant.account.codec:_read_account_payload"
    )
    assert known_relocated_id in _CONFIG_RELOCATED_PRIVATE_IMPORTS
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    sources["uquant/account/migrations.py"] += (
        "\nfrom .codec import _read_account_payload\n"
    )
    snapshot = architecture_snapshot(source_texts=sources)
    graph = snapshot["import_graph"]
    assert isinstance(graph, Mapping)
    current = {
        str(row["id"])
        for row in graph["cross_module_private_imports"]
        if isinstance(row, Mapping)
    }
    assert known_relocated_id in current
    assert known_relocated_id in {
        str(row["id"])
        for row in measured_debt(snapshot)["cross_module_private_imports"]
    }
    inventory = load_inventory()
    inventory_rows = inventory["direct_private_imports"]
    assert isinstance(inventory_rows, list)
    frozen_uquant_ids = {
        str(row["id"])
        for row in inventory_rows
        if isinstance(row, Mapping) and row["root"] == "uquant"
    }
    for task, frozen_ids in (
        (5, _CONFIG_RELOCATED_PRIVATE_IMPORTS),
        (6, _EXECUTION_RELOCATED_PRIVATE_IMPORTS),
        (7, _RISK_RELOCATED_PRIVATE_IMPORTS),
        (8, _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS),
        (9, _VALIDATION_RELOCATED_PRIVATE_IMPORTS),
    ):
        projected = graph[f"task{task}_relocated_private_imports"]
        assert isinstance(projected, list)
        assert {
            str(row["id"])
            for row in projected
            if isinstance(row, Mapping)
        } == frozen_uquant_ids & frozen_ids


@pytest.mark.parametrize(
    "probe_path",
    (
        "research/governance_direct_private_probe.py",
        "scripts/governance_direct_private_probe.py",
    ),
)
def test_architecture_live_analyzer_measures_nonproduction_direct_private_edges(
    probe_path: str,
) -> None:
    sources = current_governed_sources()
    sources[probe_path] = (
        "from uquant.account.validation_common import _finite_number\n"
        "value = _finite_number(1)\n"
    )
    snapshot = architecture_snapshot(governed_source_texts=sources)
    graph = snapshot["import_graph"]
    assert isinstance(graph, Mapping)
    expected_id = (
        f"{probe_path.removesuffix('.py').replace('/', '.')}:"
        "uquant.account.validation_common:_finite_number"
    )
    assert expected_id in {
        str(row["id"])
        for row in graph["cross_module_private_imports"]
        if isinstance(row, Mapping)
    }
    assert expected_id in {
        str(row["id"])
        for row in measured_debt(snapshot)["cross_module_private_imports"]
    }


@pytest.mark.parametrize("governed_root", ("uquant", "research", "scripts"))
def test_architecture_live_analyzer_measures_qualified_private_edges(
    governed_root: str,
) -> None:
    sources = current_governed_sources()
    probe_path = f"{governed_root}/governance_qualified_private_probe.py"
    sources[probe_path] = (
        "import uquant.account.validation_common as common\n"
        "value = common._finite_number\n"
    )
    snapshot = architecture_snapshot(governed_source_texts=sources)
    graph = snapshot["import_graph"]
    assert isinstance(graph, Mapping)
    expected = {
        "importer": probe_path.removesuffix(".py").replace("/", "."),
        "imported_from": "uquant.account.validation_common",
        "name": "_finite_number",
    }
    rows = [
        row
        for row in graph["cross_module_private_imports"]
        if isinstance(row, Mapping) and row.get("importer") == expected["importer"]
    ]
    assert len(rows) == 1
    assert {key: rows[0][key] for key in expected} == expected
    assert rows[0]["kind"] == "qualified"
    assert rows[0]["context"] == "Load"
    assert rows == [
        row
        for row in measured_debt(snapshot)["cross_module_private_imports"]
        if row.get("importer") == expected["importer"]
    ]


@pytest.mark.parametrize(
    "source",
    (
        "import uquant.account.validation_common as common\nvalue = common._finite_number\n",
        "from uquant.account import validation_common\nvalue = validation_common._finite_number\n",
        "import uquant.account.validation_common\n"
        "value = uquant.account.validation_common._finite_number\n",
    ),
)
def test_architecture_raw_scanner_rejects_qualified_private_import_evasion(
    source: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/validation_common.py": (
            "def _finite_number(value: object) -> float:\n"
            "    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert [row["name"] for row in observed["qualified"]] == ["_finite_number"]


@pytest.mark.parametrize(
    ("source", "owner", "bucket", "evidence"),
    (
        pytest.param(
            "import uquant\n"
            "value = uquant.account.validation_common._finite_number\n",
            None,
            "qualified",
            "_finite_number",
            id="package-submodule-chain",
        ),
        pytest.param(
            "import sys\n"
            "import uquant.account.validation_common\n"
            "lookup = sys.modules.get\n"
            "facade = lookup('uquant.account.validation_common')\n"
            "value = facade._finite_number\n",
            None,
            "dynamic",
            "dynamic_lookup",
            id="sys-modules-get-alias",
        ),
        pytest.param(
            "import sys\n"
            "import uquant.account.validation_common\n"
            "lookup = sys.modules.__getitem__\n"
            "facade = lookup('uquant.account.validation_common')\n"
            "value = facade._finite_number\n",
            None,
            "dynamic",
            "dynamic_lookup",
            id="sys-modules-getitem-alias",
        ),
        pytest.param(
            "from uquant.account.validation_common import *\n"
            "value = _finite_number(1)\n",
            "__all__ = ('_finite_number',)\n"
            "def _finite_number(value):\n"
            "    return float(value)\n",
            "direct",
            "_finite_number",
            id="star-private-literal",
        ),
        pytest.param(
            "from uquant.account.validation_common import *\n"
            "value = _finite_number(1)\n",
            "__all__ = ('finite_number',)\n"
            "globals()['__all__'] = ('_finite_number',)\n"
            "def _finite_number(value):\n"
            "    return float(value)\n",
            "dynamic",
            "unbounded_namespace",
            id="reflective-all-rebind",
        ),
        pytest.param(
            "import uquant.account.validation_common as common\n"
            "__all__ = ('common',)\n"
            "for name in __all__:\n"
            "    value = globals()[name]\n"
            "    consume(value)\n",
            None,
            "dynamic",
            "dynamic_transport",
            id="bounded-reflection-module-transport",
        ),
        pytest.param(
            "import uquant.account.validation_common as common\n"
            "scope = globals()\n"
            "value = scope['common']._finite_number\n",
            None,
            "dynamic",
            "unbounded_namespace",
            id="namespace-result-alias",
        ),
        pytest.param(
            "import uquant.account.validation_common as common\n"
            "lookup = globals().get\n"
            "value = lookup('common')._finite_number\n",
            None,
            "dynamic",
            "unbounded_namespace",
            id="namespace-method-alias",
        ),
    ),
)
def test_architecture_raw_scanner_rejects_reviewed_fail_closed_bypasses(
    source: str,
    owner: str | None,
    bucket: str,
    evidence: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/validation_common.py": owner
        or "def _finite_number(value):\n    return float(value)\n",
        "uquant/account/__init__.py": "from . import validation_common\n",
        "uquant/__init__.py": "from . import account\n",
    }
    observed = scan_governed_private_edges(mutation)
    key = "kind" if bucket == "dynamic" else "name"
    assert evidence in {str(row[key]) for row in observed[bucket]}
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    "governed_root",
    GOVERNED_ROOTS,
)
@pytest.mark.parametrize(
    ("source", "kind"),
    (
        (
            "import uquant.account.validation_common as common\n"
            "value = vars(common)['finite_number']\n",
            "unbounded_namespace",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "value = common.__dict__['finite_number']\n",
            "unbounded_namespace",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "value = getattr(common, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "globals().update(vars(common))\n",
            "unbounded_namespace",
        ),
        (
            "import sys\n"
            "facade = sys.modules['uquant.account.validation_common']\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import sys\n"
            "value = getattr(\n"
            "    sys.modules['uquant.account.validation_common'],\n"
            "    '_finite_number',\n"
            ")\n",
            "dynamic_lookup",
        ),
        (
            "import sys\n"
            "registry = sys.modules\n"
            "facade = registry['uquant.account.validation_common']\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import importlib\n"
            "value = getattr(\n"
            "    importlib.import_module('uquant.account.validation_common'),\n"
            "    '_finite_number',\n"
            ")\n",
            "dynamic_lookup",
        ),
        (
            "import importlib\n"
            "loader_module = importlib\n"
            "facade = loader_module.import_module(\n"
            "    'uquant.account.validation_common'\n"
            ")\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import importlib\n"
            "value = importlib.import_module(\n"
            "    'uquant.account.validation_common'\n"
            ")._finite_number\n",
            "dynamic_lookup",
        ),
        (
            "import builtins\n"
            "facade = builtins.__import__(\n"
            "    'uquant.account.validation_common',\n"
            "    fromlist=('_finite_number',),\n"
            ")\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "def consume(value: object) -> object:\n"
            "    return value\n"
            "value = consume(common)\n",
            "dynamic_transport",
        ),
        (
            "from importlib import import_module\n"
            "facade = import_module('uquant.account.validation_common')\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import importlib\n"
            "module_name = 'uquant.account.validation_common'\n"
            "facade = importlib.import_module(module_name)\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "lookup = getattr\n"
            "value = lookup(common, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "alias = common\n"
            "value = getattr(alias, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "dump = vars\n"
            "value = dump(common)\n",
            "unbounded_namespace",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "value = globals()['common']._finite_number\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "value = locals()['common']._finite_number\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "value = vars()['common']._finite_number\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "scope = globals\n"
            "key = 'common'\n"
            "value = scope()[key]._finite_number\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "value = globals().get('common')._finite_number\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "value = globals().__getitem__('common')._finite_number\n",
            "dynamic_lookup",
        ),
        (
            "from builtins import getattr as lookup\n"
            "import uquant.account.validation_common as common\n"
            "value = lookup(common, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import builtins\n"
            "import uquant.account.validation_common as common\n"
            "value = builtins.getattr(common, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "setattr(common, '_finite_number', object())\n",
            "dynamic_lookup",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "delattr(common, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "loader = __import__\n"
            "facade = loader(\n"
            "    'uquant.account.validation_common',\n"
            "    fromlist=('_finite_number',),\n"
            ")\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import sys\n"
            "module_name = 'uquant.account.validation_common'\n"
            "facade = sys.modules[module_name]\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import sys\n"
            "registry = sys.modules\n"
            "module_name = 'uquant.account.validation_common'\n"
            "facade = registry.get(module_name)\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "import sys\n"
            "module_name = 'uquant.account.validation_common'\n"
            "facade = sys.modules.__getitem__(module_name)\n"
            "value = getattr(facade, '_finite_number')\n",
            "dynamic_lookup",
        ),
        (
            "from uquant.account.validation_common import *\n"
            "value = _finite_number(1)\n",
            "unbounded_namespace",
        ),
        (
            "import uquant.account.validation_common as common\n"
            "evaluate = eval\n"
            "value = evaluate('common._finite_number', globals(), locals())\n",
            "source_exec",
        ),
        ("compile('value = 1', '<dynamic>', 'exec')\n", "source_exec"),
        ("exec('value = 1', globals(), globals())\n", "source_exec"),
    ),
)
def test_architecture_raw_scanner_rejects_dynamic_private_transport_evasion(
    governed_root: str,
    source: str,
    kind: str,
) -> None:
    mutation = {
        f"{governed_root}/governance_dynamic_private_probe.py": source,
        "uquant/account/validation_common.py": (
            "def _finite_number(value: object) -> float:\n"
            "    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert kind in {str(row["kind"]) for row in observed["dynamic"]}
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert kind in {
        str(row["kind"])
        for row in measured_debt(snapshot)["cross_module_private_imports"]
    }


def test_architecture_raw_scanner_accepts_only_literal_bounded_star_exports() -> None:
    mutation = {
        "uquant/account/codec.py": (
            "from uquant.account.validation_common import *\n"
            "value = finite_number(1)\n"
        ),
        "uquant/account/validation_common.py": (
            "__all__ = ('finite_number',)\n"
            "def finite_number(value: object) -> float:\n"
            "    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed == {"direct": [], "qualified": [], "dynamic": []}


def test_architecture_raw_scanner_accepts_bounded_local_export_reflection() -> None:
    mutation = {
        "uquant/account/codec.py": (
            "from uquant.account.validation_common import *\n"
            "__all__ = ('finite_number',)\n"
            "for _name in __all__:\n"
            "    _value = globals()[_name]\n"
            "    if callable(_value):\n"
            "        _value.__module__ = __name__\n"
        ),
        "uquant/account/validation_common.py": (
            "__all__ = ('finite_number',)\n"
            "def finite_number(value: object) -> float:\n"
            "    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed == {"direct": [], "qualified": [], "dynamic": []}


@pytest.mark.parametrize(
    "declaration",
    (
        "",
        "__all__ = []\n",
        "__all__ = ()\n",
        "__all__ = ('finite_number', 'finite_number')\n",
        "__all__ = tuple(('finite_number',))\n",
        "__all__ = ('finite_number',)\n__all__ = ('finite_number',)\n",
        "__all__ = ('finite_number',)\n__all__ += ('other',)\n",
        "__all__ = ('finite_number',)\n__all__.append('other')\n",
    ),
)
def test_architecture_raw_scanner_rejects_unproved_star_exports(
    declaration: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": (
            "from uquant.account.validation_common import *\n"
            "value = finite_number(1)\n"
        ),
        "uquant/account/validation_common.py": (
            f"{declaration}"
            "def finite_number(value: object) -> float:\n"
            "    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert [row["kind"] for row in observed["dynamic"]] == [
        "unbounded_namespace"
    ]


def test_architecture_governed_scripts_expose_only_frozen_public_start_surface() -> None:
    for historical in GOVERNED_SCRIPTS:
        current = CURRENT_GOVERNED_SCRIPTS.get(historical, historical)
        assert _literal_script_all(current) == _immutable_public_script_definitions(
            historical
        )


def test_architecture_governed_scripts_have_no_dynamic_private_transport() -> None:
    observed = scan_governed_private_edges(current_governed_sources())
    script_dynamic = [
        row for row in observed["dynamic"] if str(row["importer"]).startswith("scripts.")
    ]
    assert script_dynamic == []


def test_architecture_risk_facade_exposes_two_explicit_runtime_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uquant.risk as risk_facade

    def base_risk(**_kwargs: object) -> object:
        return object()

    def update_anchors(**_kwargs: object) -> tuple[str, ...]:
        return ("anchor",)

    monkeypatch.setattr(risk_facade, "_assess_base_risk", base_risk)
    monkeypatch.setattr(risk_facade, "_update_dynamic_anchors", update_anchors)
    assert risk_facade.base_risk_assessor() is base_risk
    assert risk_facade.dynamic_anchor_updater() is update_anchors
    assert "_risk_runtime_seam" not in vars(risk_facade)


def test_architecture_generalization_facades_expose_finite_runtime_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uquant.validation.generalization as generalization_facade
    import uquant.validation.generalization_reference as reference_facade

    def git_stdout(*_args: object, **_kwargs: object) -> str:
        return "head"

    def source_fingerprint(_root: object) -> str:
        return "a" * 64

    def data_manifest(_root: object) -> dict[str, object]:
        return {"snapshot_id": "frozen"}

    def head_and_source(_root: object) -> tuple[str, str]:
        return "b" * 40, "c" * 64

    monkeypatch.setattr(generalization_facade, "_git_stdout", git_stdout)
    monkeypatch.setattr(
        generalization_facade,
        "_production_source_fingerprint",
        source_fingerprint,
    )
    monkeypatch.setattr(generalization_facade, "verify_data_manifest", data_manifest)
    monkeypatch.setattr(reference_facade, "_head_and_source", head_and_source)
    capabilities = generalization_facade.current_runtime_capabilities()
    assert capabilities.git_stdout is git_stdout
    assert capabilities.production_source_fingerprint is source_fingerprint
    assert capabilities.verify_data_manifest is data_manifest
    assert reference_facade.head_and_source_capability() is head_and_source


def test_architecture_holdout_facades_expose_only_finite_typed_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uquant.validation.holdout as holdout_facade
    import uquant.validation.holdout_runtime as runtime_facade

    facade_values = {
        "AI_ERA_WINDOWS": {"window": ("start", "end")},
        "REQUIRED_FUTURE_HOLDOUT_SHA256": "a" * 64,
        "STRATEGY_ACCOUNT_CODE_SHA256": "b" * 64,
        "PRIOR_CLOSE_ACCOUNT_SHA256": "c" * 64,
        "_strategy_source_paths": lambda _root: (),
        "_source_sha256": lambda *_args, **_kwargs: "d" * 64,
        "_git_strategy_relatives": lambda _root, *, commit: (commit,),
        "_strategy_cli_sha256": lambda _root, *, from_git=None: from_git or "f" * 64,
        "_repository_root": lambda: ROOT,
        "validate_prior_close_account": lambda *_args, **_kwargs: None,
        "current_holdout_binding": lambda _root=None: object(),
    }
    runtime_values = {
        "holdout_source_sha256": lambda _root: "e" * 64,
        "validate_prior_close_account": lambda *_args, **_kwargs: None,
        "replay_future_holdout": lambda **_kwargs: {},
        "atomic_write_text": lambda *_args, **_kwargs: None,
        "_artifact_bundle_lock": lambda *_args, **_kwargs: None,
        "_read_protected_artifact": lambda *_args, **_kwargs: b"",
        "os": object(),
    }
    for name, value in facade_values.items():
        monkeypatch.setattr(holdout_facade, name, value)
    for name, value in runtime_values.items():
        monkeypatch.setattr(runtime_facade, name, value)

    facade_capabilities = holdout_facade.current_facade_capabilities()
    assert tuple(field.name for field in dataclasses.fields(facade_capabilities)) == (
        "ai_era_windows",
        "required_future_holdout_sha256",
        "strategy_account_code_sha256",
        "prior_close_account_sha256",
        "strategy_source_paths",
        "source_sha256",
        "git_strategy_relatives",
        "strategy_cli_sha256",
        "repository_root",
        "validate_prior_close_account",
        "current_holdout_binding",
    )
    assert tuple(
        getattr(facade_capabilities, field.name)
        for field in dataclasses.fields(facade_capabilities)
    ) == tuple(facade_values.values())

    runtime_capabilities = runtime_facade.current_runtime_capabilities()
    assert tuple(field.name for field in dataclasses.fields(runtime_capabilities)) == (
        "holdout_source_sha256",
        "validate_prior_close_account",
        "replay_future_holdout",
        "atomic_write_text",
        "artifact_bundle_lock",
        "read_protected_artifact",
        "os_adapter",
    )
    assert tuple(
        getattr(runtime_capabilities, field.name)
        for field in dataclasses.fields(runtime_capabilities)
    ) == tuple(runtime_values.values())

    observed_stars: set[tuple[str, str]] = set()
    for relative, source in current_governed_sources().items():
        tree = ast.parse(source)
        observed_stars.update(
            (relative, node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and any(alias.name == "*" for alias in node.names)
        )
    assert observed_stars == set()


def test_architecture_current_raw_private_debt_matches_canonical_acceptance_allowlist() -> None:
    expected = _canonical_private_acceptance_allowlist()
    assert expected == []
    observed = scan_governed_private_edges(current_governed_sources())
    current = [*observed["direct"], *observed["qualified"], *observed["dynamic"]]
    assert current == expected
    snapshot = architecture_snapshot()
    graph = snapshot["import_graph"]
    assert isinstance(graph, Mapping)
    assert graph["cross_module_private_imports"] == expected
    assert measured_debt(snapshot)["cross_module_private_imports"] == expected
