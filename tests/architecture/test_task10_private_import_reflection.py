from __future__ import annotations

import pytest

from ._analysis import architecture_snapshot, measured_debt
from ._task10_private_imports import scan_governed_private_edges


@pytest.mark.parametrize(
    ("source", "owner", "kind"),
    (
        pytest.param(
            "import importlib\n"
            "loader = getattr(importlib, 'import_module')\n"
            "facade = loader('uquant.account.validation_common')\n"
            "value = facade._finite_number\n",
            None,
            "dynamic_lookup",
            id="getattr-importlib-loader",
        ),
        pytest.param(
            "import sys\n"
            "registry = getattr(sys, 'modules')\n"
            "facade = registry.get('uquant.account.validation_common')\n"
            "value = facade._finite_number\n",
            None,
            "dynamic_lookup",
            id="getattr-sys-modules",
        ),
        pytest.param(
            "import builtins\n"
            "loader = builtins.__dict__['__import__']\n"
            "facade = loader('uquant.account.validation_common', fromlist=('*',))\n"
            "value = facade._finite_number\n",
            None,
            "unbounded_namespace",
            id="builtins-dict-import",
        ),
        pytest.param(
            "from uquant.account.validation_common import __dict__ as namespace\n"
            "value = namespace['_finite_number']\n",
            None,
            "unbounded_namespace",
            id="internal-dict-import",
        ),
        pytest.param(
            "from uquant.account.validation_common import __getattribute__ as lookup\n"
            "value = lookup('_finite_number')\n",
            None,
            "dynamic_lookup",
            id="internal-getattribute-import",
        ),
        pytest.param(
            "from uquant.account.validation_common import *\n"
            "value = __dict__['_finite_number']\n",
            "__all__ = ('__dict__',)\n"
            "def _finite_number(value):\n"
            "    return float(value)\n",
            "unbounded_namespace",
            id="star-internal-dict",
        ),
        pytest.param(
            "import importlib\n"
            "namespace = vars(importlib)\n"
            "loader = namespace['import_module']\n"
            "facade = loader('uquant.account.validation_common')\n"
            "value = facade._finite_number\n",
            None,
            "unbounded_namespace",
            id="vars-importlib-loader",
        ),
        pytest.param(
            "import importlib\n"
            "loader = importlib.__getattribute__('import_module')\n"
            "facade = loader('uquant.account.validation_common')\n"
            "value = facade._finite_number\n",
            None,
            "dynamic_lookup",
            id="importlib-getattribute-loader",
        ),
        pytest.param(
            "from uquant.account.validation_common import __builtins__ as namespace\n"
            "loader = namespace['__import__']\n"
            "facade = loader('uquant.account.validation_common', fromlist=('*',))\n"
            "value = facade._finite_number\n",
            None,
            "unbounded_namespace",
            id="internal-builtins-import",
        ),
        pytest.param(
            "namespace = __builtins__\n"
            "loader = namespace['__import__']\n"
            "facade = loader('uquant.account.validation_common', fromlist=('*',))\n"
            "value = facade._finite_number\n",
            None,
            "unbounded_namespace",
            id="implicit-builtins-namespace",
        ),
        pytest.param(
            "capability = __import__('importlib')\n"
            "facade = capability.import_module('uquant.account.validation_common')\n"
            "value = facade._finite_number\n",
            None,
            "dynamic_lookup",
            id="dynamically-loaded-importlib",
        ),
        pytest.param(
            "import importlib\n"
            "holder = {'loader': importlib}\n"
            "facade = holder['loader'].import_module(\n"
            "    'uquant.account.validation_common'\n"
            ")\n"
            "value = facade._finite_number\n",
            None,
            "dynamic_transport",
            id="capability-container-transport",
        ),
        pytest.param(
            "import sys\n"
            "import uquant.account.validation_common as common\n"
            "namespace = sys._getframe().f_globals\n"
            "value = namespace['common']._finite_number\n",
            None,
            "dynamic_lookup",
            id="sys-private-frame-namespace",
        ),
        pytest.param(
            "import sys\n"
            "import uquant.account.validation_common as common\n"
            "frame = getattr(sys, '_getframe')\n"
            "namespace = frame().f_globals\n"
            "value = namespace['common']._finite_number\n",
            None,
            "dynamic_lookup",
            id="getattr-sys-private-frame-namespace",
        ),
    ),
)
def test_task10_raw_scanner_rejects_reflective_loader_namespace_bypasses(
    source: str,
    owner: str | None,
    kind: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/validation_common.py": owner
        or "def _finite_number(value):\n    return float(value)\n",
        "uquant/account/__init__.py": "from . import validation_common\n",
        "uquant/__init__.py": "from . import account\n",
    }
    observed = scan_governed_private_edges(mutation)
    assert kind in {str(row["kind"]) for row in observed["dynamic"]}
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    ("consumer", "owner", "helper"),
    (
        pytest.param(
            "from uquant.account.validation_common import *\n"
            "value = common._finite_number\n",
            "import uquant.account.helper as common\n"
            "__all__ = ('common',)\n",
            "def _finite_number(value):\n    return float(value)\n",
            id="bounded-star-internal-module",
        ),
        pytest.param(
            "from uquant.account.validation_common import *\n"
            "facade = loader.import_module('uquant.account.helper')\n"
            "value = facade._finite_number\n",
            "import importlib as loader\n"
            "__all__ = ('loader',)\n",
            "def _finite_number(value):\n    return float(value)\n",
            id="bounded-star-loader-capability",
        ),
        pytest.param(
            "import importlib\n"
            "__all__ = ('importlib',)\n"
            "for name in __all__:\n"
            "    value = globals()[name]\n"
            "    consume(value)\n",
            "def finite_number(value):\n    return float(value)\n",
            "",
            id="bounded-reflection-loader-capability",
        ),
    ),
)
def test_task10_raw_scanner_rejects_module_valued_bounded_exports(
    consumer: str,
    owner: str,
    helper: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": consumer,
        "uquant/account/validation_common.py": owner,
        "uquant/account/helper.py": helper,
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert "unbounded_namespace" in {
        str(row["kind"]) for row in observed["dynamic"]
    }
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    ("owner", "bridge", "consumer", "bucket", "evidence"),
    (
        pytest.param(
            "import uquant.account.helper as common\n",
            "",
            "from uquant.account.validation_common import common\n"
            "value = common._finite_number\n",
            "qualified",
            "_finite_number",
            id="internal-module-reexport",
        ),
        pytest.param(
            "import importlib\nloader = importlib\npublic = loader\n",
            "",
            "from uquant.account.validation_common import public\n"
            "facade = public.import_module('uquant.account.helper')\n"
            "value = facade._finite_number\n",
            "dynamic",
            "dynamic_lookup",
            id="loader-capability-reexport",
        ),
        pytest.param(
            "import uquant\npublic = uquant.account.helper\n",
            "",
            "from uquant.account.validation_common import public\n"
            "value = public._finite_number\n",
            "qualified",
            "_finite_number",
            id="package-chain-assignment-reexport",
        ),
        pytest.param(
            "import uquant.account.helper as common\n",
            "from uquant.account.validation_common import common as public\n",
            "from uquant.account.bridge import public\n"
            "value = public._finite_number\n",
            "qualified",
            "_finite_number",
            id="multi-hop-module-reexport",
        ),
    ),
)
def test_task10_raw_scanner_rejects_module_valued_explicit_reexports(
    owner: str,
    bridge: str,
    consumer: str,
    bucket: str,
    evidence: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": consumer,
        "uquant/account/bridge.py": bridge,
        "uquant/account/validation_common.py": owner,
        "uquant/account/helper.py": (
            "def _finite_number(value):\n    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    key = "kind" if bucket == "dynamic" else "name"
    assert evidence in {str(row[key]) for row in observed[bucket]}
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    ("owner", "consumer", "bucket", "evidence"),
    (
        pytest.param(
            "import uquant.account.helper as common\n",
            "import uquant.account.validation_common as owner\n"
            "value = owner.common._finite_number\n",
            "qualified",
            "_finite_number",
            id="internal-module-attribute-reexport",
        ),
        pytest.param(
            "import importlib as loader\n",
            "import uquant.account.validation_common as owner\n"
            "facade = owner.loader.import_module('uquant.account.helper')\n"
            "value = facade._finite_number\n",
            "dynamic",
            "dynamic_lookup",
            id="loader-capability-attribute-reexport",
        ),
    ),
)
def test_task10_raw_scanner_rejects_module_valued_attribute_reexports(
    owner: str,
    consumer: str,
    bucket: str,
    evidence: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": consumer,
        "uquant/account/validation_common.py": owner,
        "uquant/account/helper.py": (
            "def _finite_number(value):\n    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    key = "kind" if bucket == "dynamic" else "name"
    assert evidence in {str(row[key]) for row in observed[bucket]}
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    ("owner", "case_id"),
    (
        pytest.param(
            "import uquant.account.helper as common\n"
            "if True:\n"
            "    public = common\n",
            "conditional-assignment",
            id="conditional-assignment-reexport",
        ),
        pytest.param(
            "import uquant.account.helper as common\n"
            "def install():\n"
            "    global public\n"
            "    public = common\n"
            "install()\n",
            "global-function-assignment",
            id="global-function-assignment-reexport",
        ),
        pytest.param(
            "try:\n"
            "    import uquant.account.helper as public\n"
            "except ImportError:\n"
            "    public = None\n",
            "conditional-import",
            id="conditional-import-reexport",
        ),
        pytest.param(
            "try:\n"
            "    from uquant.account import helper as public\n"
            "except ImportError:\n"
            "    public = None\n",
            "conditional-from-submodule",
            id="conditional-from-submodule-reexport",
        ),
        pytest.param(
            "try:\n"
            "    from uquant.account.bridge import public\n"
            "except ImportError:\n"
            "    public = None\n",
            "conditional-from-module-reexport",
            id="conditional-from-module-reexport",
        ),
    ),
)
def test_task10_raw_scanner_rejects_unmodelled_scope_reexports(
    owner: str,
    case_id: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": (
            "from uquant.account.owner import public\n"
            "value = public._finite_number\n"
        ),
        "uquant/account/owner.py": owner,
        "uquant/account/bridge.py": (
            "import uquant.account.helper as public\n"
        ),
        "uquant/account/helper.py": (
            "def _finite_number(value):\n    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert any(observed.values()), case_id
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"], case_id


@pytest.mark.parametrize(
    ("owner", "consumer"),
    (
        pytest.param(
            "from importlib import import_module as public\n",
            "facade = public('uquant.account.helper')\n",
            id="from-importlib-callable",
        ),
        pytest.param(
            "import importlib\npublic = importlib.import_module\n",
            "facade = public('uquant.account.helper')\n",
            id="attribute-importlib-callable",
        ),
        pytest.param(
            "from sys import modules as public\n",
            "facade = public['uquant.account.helper']\n",
            id="from-sys-registry",
        ),
        pytest.param(
            "import sys\npublic = sys.modules\n",
            "facade = public['uquant.account.helper']\n",
            id="attribute-sys-registry",
        ),
        pytest.param(
            "def install():\n"
            "    global public\n"
            "    import importlib as public\n"
            "install()\n",
            "facade = public.import_module('uquant.account.helper')\n",
            id="nested-importlib-module",
        ),
        pytest.param(
            "def install():\n"
            "    global public\n"
            "    import sys as public\n"
            "install()\n",
            "facade = public.modules['uquant.account.helper']\n",
            id="nested-sys-module",
        ),
    ),
)
def test_task10_raw_scanner_rejects_cross_module_capability_reexports(
    owner: str,
    consumer: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": (
            "from uquant.account.owner import public\n"
            f"{consumer}"
            "value = facade._finite_number\n"
        ),
        "uquant/account/owner.py": owner,
        "uquant/account/helper.py": (
            "def _finite_number(value):\n    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    "namespace_expression",
    (
        "public.__globals__",
        "getattr(public, '__globals__')",
    ),
)
def test_task10_raw_scanner_rejects_imported_function_globals_namespace(
    namespace_expression: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": (
            "from uquant.account.owner import public\n"
            f"namespace = {namespace_expression}\n"
            "value = namespace['common']._finite_number\n"
        ),
        "uquant/account/owner.py": (
            "import uquant.account.helper as common\n"
            "def public(value):\n"
            "    return common.finite_number(value)\n"
        ),
        "uquant/account/helper.py": (
            "def finite_number(value):\n    return float(value)\n"
            "_finite_number = finite_number\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert "unbounded_namespace" in {
        str(row["kind"]) for row in observed["dynamic"]
    }
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "import inspect\n"
            "from uquant.account.validation_common import finite_number\n"
            "value = inspect.getmodule(finite_number)._finite_number\n",
            id="inspect-getmodule",
        ),
        pytest.param(
            "import inspect\n"
            "import uquant.account.validation_common as common\n"
            "value = inspect.currentframe().f_globals['common']._finite_number\n",
            id="inspect-currentframe",
        ),
        pytest.param(
            "import inspect\n"
            "import uquant.account.validation_common as common\n"
            "value = inspect.stack()[0].frame.f_globals['common']._finite_number\n",
            id="inspect-stack",
        ),
        pytest.param(
            "import inspect\n"
            "from uquant.account.validation_common import finite_number\n"
            "finder = inspect.getmodule\n"
            "facade = finder(finite_number)\n"
            "value = facade._finite_number\n",
            id="inspect-getmodule-alias",
        ),
        pytest.param(
            "import pkgutil\n"
            "value = pkgutil.resolve_name(\n"
            "    'uquant.account.validation_common'\n"
            ")._finite_number\n",
            id="pkgutil-resolve-name",
        ),
        pytest.param(
            "import pydoc\n"
            "value = pydoc.locate(\n"
            "    'uquant.account.validation_common._finite_number'\n"
            ")\n",
            id="pydoc-locate",
        ),
        pytest.param(
            "import runpy\n"
            "namespace = runpy.run_module('uquant.account.validation_common')\n"
            "value = namespace['_finite_number']\n",
            id="runpy-module-namespace",
        ),
        pytest.param(
            "from inspect import getmodule\n"
            "from uquant.account.validation_common import finite_number\n"
            "value = getmodule(finite_number)._finite_number\n",
            id="inspect-from-import",
        ),
        pytest.param(
            "import inspect\n"
            "from uquant.account.validation_common import finite_number\n"
            "finder = getattr(inspect, 'getmodule')\n"
            "value = finder(finite_number)._finite_number\n",
            id="inspect-getattr",
        ),
        pytest.param(
            "import inspect\n"
            "import sys\n"
            "from uquant.account.validation_common import finite_number\n"
            "value = sys.modules['inspect'].getmodule(\n"
            "    finite_number\n"
            ")._finite_number\n",
            id="inspect-sys-modules",
        ),
        pytest.param(
            "import gc\n"
            "candidates = [\n"
            "    value for value in gc.get_objects()\n"
            "    if getattr(value, '__name__', None)\n"
            "    == 'uquant.account.validation_common'\n"
            "]\n"
            "value = candidates[0]._finite_number\n",
            id="gc-object-registry",
        ),
        pytest.param(
            "import traceback\n"
            "import uquant.account.validation_common as common\n"
            "frame = next(traceback.walk_stack(None))[0]\n"
            "value = frame.f_globals['common']._finite_number\n",
            id="traceback-frame-walk",
        ),
    ),
)
def test_task10_raw_scanner_rejects_stdlib_module_recovery(
    source: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/validation_common.py": (
            "def finite_number(value):\n    return float(value)\n"
            "_finite_number = finite_number\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


def test_task10_raw_scanner_rejects_trace_callback_frame_namespace() -> None:
    mutation = {
        "uquant/account/codec.py": (
            "import sys\n"
            "from uquant.account.validation_common import finite_number\n"
            "captured = []\n"
            "def tracer(frame, event, argument):\n"
            "    namespace = frame.f_globals\n"
            "    if namespace.get('__name__') == "
            "'uquant.account.validation_common':\n"
            "        captured.append(namespace.get('_finite_number'))\n"
            "    return tracer\n"
            "sys.settrace(tracer)\n"
            "finite_number(1, field='probe')\n"
            "sys.settrace(None)\n"
        ),
        "uquant/account/validation_common.py": (
            "def finite_number(value, *, field):\n    return float(value)\n"
            "_finite_number = finite_number\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


def test_task10_raw_scanner_allows_bounded_source_file_inspection() -> None:
    mutation = {
        "research/probe.py": (
            "import inspect\n"
            "from uquant.account.validation_common import finite_number\n"
            "path = inspect.getfile(finite_number)\n"
        ),
        "uquant/account/validation_common.py": (
            "def finite_number(value):\n    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    assert scan_governed_private_edges(mutation) == {
        "direct": [],
        "qualified": [],
        "dynamic": [],
    }


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "import sys\n"
            "from uquant.account.validation_common import finite_number\n"
            "facade = sys.modules.copy()[finite_number.__module__]\n"
            "value = facade._finite_number\n",
            id="sys-modules-copy",
        ),
        pytest.param(
            "import sys\n"
            "from uquant.account.validation_common import finite_number\n"
            "registry = dict(sys.modules.items())\n"
            "facade = registry[finite_number.__module__]\n"
            "value = facade._finite_number\n",
            id="sys-modules-items",
        ),
        pytest.param(
            "import sys\n"
            "from uquant.account.validation_common import finite_number\n"
            "facade = next(\n"
            "    value for value in sys.modules.values()\n"
            "    if value.__name__ == finite_number.__module__\n"
            ")\n"
            "value = facade._finite_number\n",
            id="sys-modules-values",
        ),
        pytest.param(
            "import importlib.util\n"
            "spec = importlib.util.find_spec(\n"
            "    'uquant.account.validation_common'\n"
            ")\n"
            "facade = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(facade)\n"
            "value = facade._finite_number\n",
            id="importlib-util-loader",
        ),
        pytest.param(
            "import importlib.util as loader\n"
            "spec = loader.find_spec('uquant.account.validation_common')\n"
            "facade = loader.module_from_spec(spec)\n"
            "spec.loader.exec_module(facade)\n"
            "value = facade._finite_number\n",
            id="importlib-util-alias",
        ),
        pytest.param(
            "from importlib import util\n"
            "spec = util.find_spec('uquant.account.validation_common')\n"
            "facade = util.module_from_spec(spec)\n"
            "spec.loader.exec_module(facade)\n"
            "value = facade._finite_number\n",
            id="from-importlib-util",
        ),
        pytest.param(
            "from importlib.util import find_spec, module_from_spec\n"
            "spec = find_spec('uquant.account.validation_common')\n"
            "facade = module_from_spec(spec)\n"
            "spec.loader.exec_module(facade)\n"
            "value = facade._finite_number\n",
            id="from-importlib-util-members",
        ),
        pytest.param(
            "import importlib.machinery\n"
            "loader = importlib.machinery.SourceFileLoader(\n"
            "    'uquant.account.validation_common',\n"
            "    'uquant/account/validation_common.py',\n"
            ")\n"
            "facade = loader.load_module()\n"
            "value = facade._finite_number\n",
            id="importlib-machinery-loader",
        ),
        pytest.param(
            "import importlib\n"
            "loader = getattr(importlib, 'util')\n"
            "spec = loader.find_spec('uquant.account.validation_common')\n"
            "facade = loader.module_from_spec(spec)\n"
            "spec.loader.exec_module(facade)\n"
            "value = facade._finite_number\n",
            id="getattr-importlib-util",
        ),
        pytest.param(
            "import importlib\n"
            "machinery = getattr(importlib, 'machinery')\n"
            "loader = machinery.SourceFileLoader(\n"
            "    'uquant.account.validation_common',\n"
            "    'uquant/account/validation_common.py',\n"
            ")\n"
            "facade = loader.load_module()\n"
            "value = facade._finite_number\n",
            id="getattr-importlib-machinery",
        ),
    ),
)
def test_task10_raw_scanner_rejects_registry_and_loader_derivations(
    source: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/validation_common.py": (
            "def finite_number(value):\n    return float(value)\n"
            "_finite_number = finite_number\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "import pickle\n"
            "value = pickle.loads(\n"
            "    b'cuquant.account.validation_common\\n_finite_number\\n.'\n"
            ")\n",
            id="pickle-literal-private-global",
        ),
        pytest.param(
            "from importlib.metadata import EntryPoint\n"
            "value = EntryPoint(\n"
            "    name='probe',\n"
            "    value='uquant.account.validation_common:_finite_number',\n"
            "    group='probe',\n"
            ").load()\n",
            id="entry-point-literal-private-global",
        ),
        pytest.param(
            "import mystery_loader\n"
            "value = mystery_loader.restore(\n"
            "    'uquant.account.validation_common:_finite_number'\n"
            ")\n",
            id="unknown-loader-literal-private-global",
        ),
        pytest.param(
            "import mystery_loader\n"
            "target = (\n"
            "    'uquant.account.validation_common:' + '_finite_number'\n"
            ")\n"
            "value = mystery_loader.restore(target)\n",
            id="unknown-loader-concatenated-private-global",
        ),
        pytest.param(
            "import mystery_loader\n"
            "module = 'uquant.account.validation_common'\n"
            "member = '_finite_number'\n"
            "target = module + ':' + member\n"
            "value = mystery_loader.restore(target)\n",
            id="unknown-loader-bound-private-global",
        ),
        pytest.param(
            "import pickle\n"
            "payload = receive_payload()\n"
            "value = pickle.loads(payload)\n",
            id="pickle-nonliteral-payload",
        ),
        pytest.param(
            "from importlib.metadata import EntryPoint\n"
            "target = receive_target()\n"
            "value = EntryPoint(name='probe', value=target, group='probe').load()\n",
            id="entry-point-nonliteral-target",
        ),
    ),
)
def test_task10_raw_scanner_rejects_serialized_private_object_recovery(
    source: str,
) -> None:
    mutation = {
        "research/probe.py": source,
        "research/__init__.py": "",
        "uquant/account/validation_common.py": (
            "def finite_number(value):\n    return float(value)\n"
            "_finite_number = finite_number\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    "source",
    (
        "import pickle as codec\n"
        "payload = receive_payload()\n"
        "value = codec.loads(payload)\n",
        "from pickle import loads as restore\n"
        "payload = receive_payload()\n"
        "value = restore(payload)\n",
        "import marshal\n"
        "payload = receive_payload()\n"
        "value = marshal.loads(payload)\n",
        "import multiprocessing.reduction as recovery\n"
        "payload = receive_payload()\n"
        "value = recovery.ForkingPickler.loads(payload)\n",
        "import numpy as np\n"
        "value = np.load(receive_path(), allow_pickle=True)\n",
        "import pandas as pd\n"
        "value = pd.read_pickle(receive_path())\n",
        "import yaml\n"
        "value = yaml.unsafe_load(receive_payload())\n",
        "import importlib.metadata as metadata\n"
        "target = receive_target()\n"
        "value = metadata.EntryPoint("
        "name='p', value=target, group='p').load()\n",
        "from importlib import metadata\n"
        "target = receive_target()\n"
        "value = metadata.EntryPoint("
        "name='p', value=target, group='p').load()\n",
        "import pkg_resources\n"
        "target = receive_target()\n"
        "value = pkg_resources.EntryPoint.parse('p=' + target).load()\n",
    ),
)
def test_task10_raw_scanner_rejects_object_recovery_provider_aliases(
    source: str,
) -> None:
    mutation = {
        "research/probe.py": source,
        "research/__init__.py": "",
        "uquant/account/validation_common.py": (
            "def finite_number(value):\n    return float(value)\n"
            "_finite_number = finite_number\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    "source",
    (
        "import yaml\n"
        "loader = yaml.UnsafeLoader(receive_payload())\n"
        "value = loader.get_single_data()\n",
        "from yaml.loader import FullLoader\n"
        "loader = FullLoader(receive_payload())\n"
        "value = loader.get_single_data()\n",
        "from yaml.cyaml import CFullLoader\n"
        "value = CFullLoader(receive_payload()).get_single_data()\n",
        "import yaml\n"
        "value = tuple(yaml.full_load_all(receive_payload()))\n",
        "import numpy as np\n"
        "value = np.lib.format.read_array("
        "receive_stream(), allow_pickle=True)\n",
        "from numpy.lib.format import read_array\n"
        "value = read_array(receive_stream(), allow_pickle=True)\n",
        "from numpy.lib._format_impl import read_array\n"
        "value = read_array(receive_stream(), allow_pickle=True)\n",
        "from numpy.lib._npyio_impl import load\n"
        "value = load(receive_stream(), allow_pickle=True)\n",
        "from numpy.lib.npyio import NpzFile\n"
        "value = NpzFile(receive_stream(), allow_pickle=True)['arr']\n",
        "import pandas as pd\n"
        "value = pd.io.pickle.read_pickle(receive_path())\n",
        "from pandas.io.pickle import read_pickle\n"
        "value = read_pickle(receive_path())\n",
        "from pandas.io.api import read_pickle\n"
        "value = read_pickle(receive_path())\n",
        "from pandas.compat.pickle_compat import loads\n"
        "value = loads(bytes.fromhex(receive_hex()))\n",
        "import pandas.compat.pickle_compat as compat\n"
        "value = compat.loads(receive_payload())\n",
        "import unittest.mock as mock\n"
        "target = '.'.join(('uquant', 'account', 'validation_common', "
        "'_finite_number'))\n"
        "value = mock.patch(target).get_original()[0]\n",
        "from unittest.mock import patch as replace\n"
        "value = replace(receive_target()).get_original()[0]\n",
        "from unittest import mock\n"
        "value = mock.patch(receive_target()).get_original()[0]\n",
        "import logging.config as config\n"
        "value = config._resolve(receive_target())\n",
        "from logging.config import BaseConfigurator\n"
        "value = BaseConfigurator({}).resolve(receive_target())\n",
        "from logging import config\n"
        "value = config._resolve(receive_target())\n",
    ),
)
def test_task10_raw_scanner_rejects_nested_object_recovery_providers(
    source: str,
) -> None:
    mutation = {
        "research/probe.py": source,
        "research/__init__.py": "",
        "uquant/account/validation_common.py": (
            "def finite_number(value):\n    return float(value)\n"
            "_finite_number = finite_number\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


@pytest.mark.parametrize(
    "source",
    (
        "import logging\nvalue = logging.getLogger(__name__)\n",
        "import numpy as np\nvalue = np.asarray((1, 2, 3))\n",
        "import pandas as pd\nvalue = pd.DataFrame({'value': [1]})\n",
        "import unittest\nvalue = unittest.TestCase\n",
        "import yaml\nvalue = yaml.safe_load('value: 1')\n",
    ),
)
def test_task10_raw_scanner_keeps_ordinary_provider_members_bounded(
    source: str,
) -> None:
    mutation = {
        "research/probe.py": source,
        "research/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert not observed["dynamic"]
