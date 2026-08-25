from __future__ import annotations

import pytest

from ._analysis import architecture_snapshot, measured_debt
from ._private_imports import scan_governed_private_edges

_PRIVATE_RECOVERY = (
    "owner = api.PyImport_ImportModule("
    "b'uquant.account.validation_common')\n"
    "value = api.PyObject_GetAttrString(owner, b'_finite_number')\n"
)


@pytest.mark.parametrize(
    "binding",
    (
        pytest.param(
            "import ctypes\napi = ctypes.pythonapi\n",
            id="pythonapi",
        ),
        pytest.param(
            "import ctypes as ffi\napi = ffi.pythonapi\n",
            id="aliased-pythonapi",
        ),
        pytest.param(
            "from ctypes import pythonapi\napi = pythonapi\n",
            id="from-pythonapi",
        ),
        pytest.param(
            "import ctypes\napi = ctypes.PyDLL(None)\n",
            id="pydll",
        ),
        pytest.param(
            "from ctypes import PyDLL\napi = PyDLL(None)\n",
            id="from-pydll",
        ),
        pytest.param(
            "import ctypes\napi = ctypes.CDLL(None)\n",
            id="cdll",
        ),
        pytest.param(
            "from ctypes import CDLL as loader\napi = loader(None)\n",
            id="from-cdll",
        ),
        pytest.param(
            "import ctypes\napi = ctypes.WinDLL('python.dll')\n",
            id="windll",
        ),
        pytest.param(
            "import ctypes\napi = vars(ctypes).get('pythonapi')\n",
            id="vars-pythonapi",
        ),
        pytest.param(
            "import ctypes\napi = getattr(ctypes, 'pythonapi')\n",
            id="getattr-pythonapi",
        ),
        pytest.param(
            "import ctypes\nname = 'pythonapi'\napi = vars(ctypes).get(name)\n",
            id="computed-vars-member",
        ),
        pytest.param(
            "import ctypes\nname = 'pythonapi'\napi = getattr(ctypes, name)\n",
            id="computed-getattr-member",
        ),
    ),
)
def test_architecture_raw_scanner_rejects_native_python_object_recovery(
    binding: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": binding + _PRIVATE_RECOVERY,
        "uquant/account/validation_common.py": (
            "def _finite_number(value):\n    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


def test_architecture_raw_scanner_rejects_low_level_ctypes_object_recovery() -> None:
    source = (
        "import _ctypes\n"
        "class PyObject(_ctypes._SimpleCData):\n"
        "    _type_ = 'O'\n"
        "class CharPointer(_ctypes._SimpleCData):\n"
        "    _type_ = 'z'\n"
        "class PythonFunction(_ctypes.CFuncPtr):\n"
        "    _flags_ = _ctypes.FUNCFLAG_CDECL | _ctypes.FUNCFLAG_PYTHONAPI\n"
        "    _argtypes_ = (CharPointer,)\n"
        "    _restype_ = PyObject\n"
        "handle = _ctypes.dlopen(None)\n"
        "load = PythonFunction(_ctypes.dlsym(handle, 'PyImport_ImportModule'))\n"
        "owner = load(b'uquant.account.validation_common')\n"
    )
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/validation_common.py": (
            "def _finite_number(value):\n    return float(value)\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]
    snapshot = architecture_snapshot(governed_source_texts=mutation)
    assert measured_debt(snapshot)["cross_module_private_imports"]


_BUILTIN_IMPORTER_RECOVERY = (
    "system = loader.load_module('sys')\n"
    "owner = system.modules['uquant.account.validation_common']\n"
    "value = owner._finite_number\n"
)

_TRANSPORTED_RUNTIME_CLASS_RECOVERY = (
    "loader = next(\n"
    "    item for item in getattr(root, runtime_member)()\n"
    "    if item.__name__ == 'BuiltinImporter'\n"
    ")\n"
    + _BUILTIN_IMPORTER_RECOVERY
)


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "loader = next(\n"
            "    item for item in object.__subclasses__()\n"
            "    if item.__name__ == 'BuiltinImporter'\n"
            ")\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="object-subclasses",
        ),
        pytest.param(
            "root = object\n"
            "classes = root.__subclasses__()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="aliased-object-subclasses",
        ),
        pytest.param(
            "classes = ().__class__.__base__.__subclasses__()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="derived-object-subclasses",
        ),
        pytest.param(
            "runtime_class = getattr((), class_member)\n"
            "root = getattr(runtime_class, base_member)\n"
            "classes = getattr(root, subclasses_member)()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="opaque-runtime-getattr-chain",
        ),
        pytest.param(
            "lookup = getattr\n"
            "runtime_class = lookup((), class_member)\n"
            "root = lookup(runtime_class, base_member)\n"
            "classes = lookup(root, subclasses_member)()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="aliased-opaque-runtime-getattr-chain",
        ),
        pytest.param(
            "classes = getattr(object, '__subclasses__')()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="literal-getattr-subclasses",
        ),
        pytest.param(
            "member = '__sub' + 'classes__'\n"
            "classes = getattr(object, member)()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="computed-getattr-subclasses",
        ),
        pytest.param(
            "def subclasses(member):\n"
            "    return getattr(object, member)()\n"
            "classes = subclasses(runtime_member)\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "from builtins import object as root\n"
            "def subclasses(member):\n"
            "    return getattr(root, member)()\n"
            "classes = subclasses(runtime_member)\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="from-builtins-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "import builtins\n"
            "def subclasses(member):\n"
            "    return getattr(builtins.object, member)()\n"
            "classes = subclasses(runtime_member)\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="builtins-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "root = type(object())\n"
            "def subclasses(member):\n"
            "    return getattr(root, member)()\n"
            "classes = subclasses(runtime_member)\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="derived-type-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "root = (object,)[0]\n" + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="tuple-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "root = [object][0]\n" + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="list-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "root = {'class': object}['class']\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="dict-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "def keep(value):\n"
            "    return value\n"
            "root = keep(object)\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="function-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "def keep():\n"
            "    return object\n"
            "root = keep()\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="return-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "root, = (object,)\n" + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="unpack-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "root = {'class': object}.get('class')\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="mapping-call-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "def keep():\n"
            "    yield object\n"
            "root = next(keep())\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="generator-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "store = []\n"
            "store.append(object)\n"
            "root = store[0]\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="list-mutation-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "store = {}\n"
            "store['root'] = object\n"
            "root = store['root']\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="item-mutation-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "store = {}\n"
            "store.update(root=object)\n"
            "root = store['root']\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="update-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "store = {}\n"
            "store.setdefault('root', object)\n"
            "root = store['root']\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="setdefault-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "store = []\n"
            "store += [object]\n"
            "root = store[0]\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="augassign-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "(root := object)\n" + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="namedexpr-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "for root in (object,):\n"
            "    pass\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="for-target-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "classes = next(\n"
            "    getattr(root, runtime_member)()\n"
            "    for root in (object,)\n"
            ")\n"
            "loader = next(\n"
            "    item for item in classes\n"
            "    if item.__name__ == 'BuiltinImporter'\n"
            ")\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="comprehension-target-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "class Box:\n"
            "    def stash(self, value):\n"
            "        self.root = value\n"
            "box = Box()\n"
            "box.stash(object)\n"
            "root = box.root\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="method-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "class Box:\n"
            "    pass\n"
            "box = Box()\n"
            "box.root = object\n"
            "root = box.root\n"
            + _TRANSPORTED_RUNTIME_CLASS_RECOVERY,
            id="attribute-transport-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "root = (object,)[0]\n"
            "classes = vars(root).get(runtime_member)()\n"
            "loader = next(\n"
            "    item for item in classes\n"
            "    if item.__name__ == 'BuiltinImporter'\n"
            ")\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="tuple-transport-nonliteral-vars-subclasses",
        ),
        pytest.param(
            "class Marker:\n"
            "    pass\n"
            "root = Marker.__mro__[-1]\n"
            "classes = getattr(root, runtime_member)()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="mro-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "class Marker:\n"
            "    pass\n"
            "root = Marker.mro()[-1]\n"
            "classes = getattr(root, runtime_member)()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="mro-call-nonliteral-getattr-subclasses",
        ),
        pytest.param(
            "classes = vars(object)['__subclasses__']()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="vars-subclasses",
        ),
        pytest.param(
            "namespace = vars(object)\n"
            "member = '__sub' + 'classes__'\n"
            "classes = namespace.get(member)()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="aliased-vars-subclasses",
        ),
        pytest.param(
            "def subclasses(member):\n"
            "    return vars(object).get(member)()\n"
            "classes = subclasses(runtime_member)\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="nonliteral-vars-subclasses",
        ),
        pytest.param(
            "import operator\n"
            "member = '__sub' + 'classes__'\n"
            "classes = operator.attrgetter(member)(object)()\n"
            "loader = next(item for item in classes if item.__name__ == 'BuiltinImporter')\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="operator-attrgetter-subclasses",
        ),
        pytest.param(
            "import _frozen_importlib\n"
            "loader = _frozen_importlib.BuiltinImporter\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="frozen-importlib",
        ),
        pytest.param(
            "from _frozen_importlib import BuiltinImporter as loader\n"
            + _BUILTIN_IMPORTER_RECOVERY,
            id="from-frozen-importlib",
        ),
    ),
)
def test_architecture_raw_scanner_rejects_runtime_class_loader_recovery(
    source: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/validation_common.py": (
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
    "source",
    (
        "class Holder:\n    pass\nholder = Holder()\nobject.__setattr__(holder, 'value', 1)\n",
        "name = type(RuntimeError()).__name__\n",
        "class Marker(object):\n    pass\n",
        "import operator\nvalue = operator.index(1)\n",
    ),
)
def test_architecture_raw_scanner_accepts_bounded_runtime_class_uses(source: str) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed == {"direct": [], "qualified": [], "dynamic": []}


@pytest.mark.parametrize(
    "source",
    (
        "import _imp\nvalue = _imp.init_frozen(runtime_name)\n",
        "import _frozen_importlib_external as frozen\nvalue = frozen.FileLoader\n",
        "from importlib import _bootstrap as bootstrap\nvalue = bootstrap.BuiltinImporter\n",
        "import importlib._bootstrap as bootstrap\nvalue = bootstrap.BuiltinImporter\n",
        "from importlib._bootstrap import BuiltinImporter\nvalue = BuiltinImporter.load_module('sys')\n",
        "import operator\nvalue = operator.methodcaller(runtime_member)(object)\n",
    ),
)
def test_architecture_raw_scanner_rejects_runtime_import_implementation_providers(
    source: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": source,
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]


@pytest.mark.parametrize(
    "source",
    (
        pytest.param(
            "import ctypes\n"
            "error = vars(ctypes).get('get_last_error')\n",
            id="literal-safe-vars-member",
        ),
        pytest.param(
            "import ctypes\n"
            "class Overlapped(ctypes.Structure):\n"
            "    _fields_ = (('value', ctypes.c_size_t),)\n",
            id="structure-and-size-type",
        ),
        pytest.param(
            "import ctypes\n"
            "value = ctypes.c_size_t()\n"
            "pointer = ctypes.POINTER(ctypes.c_size_t)\n"
            "reference = ctypes.byref(value)\n",
            id="pointer-and-reference",
        ),
        pytest.param(
            "from ctypes import wintypes\n"
            "handle_type = wintypes.HANDLE\n",
            id="windows-types",
        ),
    ),
)
def test_architecture_raw_scanner_accepts_bounded_ctypes_uses(source: str) -> None:
    mutation = {
        "uquant/infrastructure/file_lock.py": source,
        "uquant/infrastructure/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed == {"direct": [], "qualified": [], "dynamic": []}


@pytest.mark.parametrize(
    "loader",
    ("ctypes.WinDLL", "vars(ctypes)['WinDLL']"),
)
def test_architecture_raw_scanner_rejects_unbounded_kernel32_symbol_access(
    loader: str,
) -> None:
    mutation = {
        "uquant/account/codec.py": (
            "import ctypes\n"
            f"address = {loader}(\n"
            "    'kernel32', use_last_error=True\n"
            ").GetProcAddress\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]


def test_architecture_raw_scanner_rejects_kernel32_object_transport() -> None:
    mutation = {
        "uquant/account/codec.py": (
            "import ctypes\n"
            "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "lock = kernel.LockFileEx\n"
        ),
        "uquant/account/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed["dynamic"]


def test_architecture_raw_scanner_accepts_exact_windows_file_lock_symbols() -> None:
    mutation = {
        "uquant/infrastructure/file_lock.py": (
            "import ctypes\n"
            "if not callable(vars(ctypes).get('WinDLL')):\n"
            "    raise OSError('Windows file locking is unavailable')\n"
            "lock = ctypes.WinDLL(\n"
            "    'kernel32', use_last_error=True\n"
            ").LockFileEx\n"
            "unlock = ctypes.WinDLL(\n"
            "    'kernel32', use_last_error=True\n"
            ").UnlockFileEx\n"
            "mapped_lock = vars(ctypes)['WinDLL'](\n"
            "    'kernel32', use_last_error=True\n"
            ").LockFileEx\n"
            "mapped_unlock = vars(ctypes)['WinDLL'](\n"
            "    'kernel32', use_last_error=True\n"
            ").UnlockFileEx\n"
        ),
        "uquant/infrastructure/__init__.py": "",
        "uquant/__init__.py": "",
    }
    observed = scan_governed_private_edges(mutation)
    assert observed == {"direct": [], "qualified": [], "dynamic": []}
