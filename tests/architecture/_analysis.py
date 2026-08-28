# ruff: noqa: I001
"""Deterministic architecture and public-contract analysis used by baseline gates.

This module deliberately lives under ``tests``: it measures production code but
is not part of the production strategy surface or its code fingerprint.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import hashlib
import importlib
import inspect
import io
import json
import math
import subprocess
import tomllib
import types
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, cast

from tests.architecture._analysis_debt import (
    _CONTRACT_RELOCATIONS as _CONTRACT_RELOCATIONS,
    _DEBT_RELOCATIONS as _DEBT_RELOCATIONS,
    _MODULE_AUTHORITY_VALUES as _MODULE_AUTHORITY_VALUES,
    _NONPRODUCTION_IMPORT_AUTHORITIES as _NONPRODUCTION_IMPORT_AUTHORITIES,
    _RUNNER_AUTHORITIES as _RUNNER_AUTHORITIES,
    _CONFIG_RELOCATED_PRIVATE_IMPORT_GROUPS as _CONFIG_RELOCATED_PRIVATE_IMPORT_GROUPS,
    _CONFIG_RELOCATED_PRIVATE_IMPORTS as _CONFIG_RELOCATED_PRIVATE_IMPORTS,
    _EXECUTION_RELOCATED_PRIVATE_IMPORT_GROUPS as _EXECUTION_RELOCATED_PRIVATE_IMPORT_GROUPS,
    _EXECUTION_RELOCATED_PRIVATE_IMPORTS as _EXECUTION_RELOCATED_PRIVATE_IMPORTS,
    _RISK_RELOCATED_FUNCTION_DEBT as _RISK_RELOCATED_FUNCTION_DEBT,
    _RISK_RELOCATED_PRIVATE_IMPORT_GROUPS as _RISK_RELOCATED_PRIVATE_IMPORT_GROUPS,
    _RISK_RELOCATED_PRIVATE_IMPORTS as _RISK_RELOCATED_PRIVATE_IMPORTS,
    FINAL_BUDGETS as FINAL_BUDGETS,
    INVENTORY_PATH as INVENTORY_PATH,
    MODULE_AUTHORITIES as MODULE_AUTHORITIES,
    PUBLIC_API_PATH as PUBLIC_API_PATH,
    ROOT as ROOT,
    _MUTABLE_CALLS as _MUTABLE_CALLS,
    _MUTATING_METHODS as _MUTATING_METHODS,
    _PUBLIC_API_FACADE_PATHS as _PUBLIC_API_FACADE_PATHS,
    _PUBLIC_API_IMPLEMENTATIONS as _PUBLIC_API_IMPLEMENTATIONS,
    _EXECUTION_RELOCATED_FUNCTION_DEBT as _EXECUTION_RELOCATED_FUNCTION_DEBT,
    _EXECUTION_RELOCATED_GLOBAL_DEBT as _EXECUTION_RELOCATED_GLOBAL_DEBT,
    _PORTFOLIO_ALLOCATE_STRATEGY_DEBT as _PORTFOLIO_ALLOCATE_STRATEGY_DEBT,
    _PORTFOLIO_RELOCATED_FUNCTION_DEBT as _PORTFOLIO_RELOCATED_FUNCTION_DEBT,
    _PORTFOLIO_RELOCATED_FUNCTION_NAMES as _PORTFOLIO_RELOCATED_FUNCTION_NAMES,
    _PORTFOLIO_RELOCATED_PRIVATE_IMPORT_GROUPS as _PORTFOLIO_RELOCATED_PRIVATE_IMPORT_GROUPS,
    _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS as _PORTFOLIO_RELOCATED_PRIVATE_IMPORTS,
    _PORTFOLIO_RELOCATED_TYPE_IGNORES as _PORTFOLIO_RELOCATED_TYPE_IGNORES,
    _VALIDATION_RELOCATED_FUNCTION_DEBT as _VALIDATION_RELOCATED_FUNCTION_DEBT,
    _VALIDATION_RELOCATED_GLOBAL_DEBT as _VALIDATION_RELOCATED_GLOBAL_DEBT,
    _VALIDATION_RELOCATED_PRIVATE_IMPORT_GROUPS as _VALIDATION_RELOCATED_PRIVATE_IMPORT_GROUPS,
    _VALIDATION_RELOCATED_PRIVATE_IMPORTS as _VALIDATION_RELOCATED_PRIVATE_IMPORTS,
    canonical_json as canonical_json,
    canonical_sha256 as canonical_sha256,
    sha256_file as sha256_file,
    sha256_tree as sha256_tree,
    module_name as module_name,
    production_sources as production_sources,
    git_python_sources as git_python_sources,
    production_source_surface as production_source_surface,
    _definition_start as _definition_start,
    _BranchCounter as _BranchCounter,
    _function_rows as _function_rows,
    _assigned_names as _assigned_names,
    _call_name as _call_name,
    _mutable_initializer as _mutable_initializer,
    _module_global_rows as _module_global_rows,
    _private_helpers as _private_helpers,
    _resolve_from as _resolve_from,
    _longest_internal_module as _longest_internal_module,
    _strongly_connected_components as _strongly_connected_components,
    _external_authority as _external_authority,
    _forbidden_authority_edge as _forbidden_authority_edge,
    _row_id as _row_id,
    architecture_snapshot as architecture_snapshot,
    measured_debt as measured_debt,
)

def _stable_default(value: object) -> dict[str, object]:
    if value is inspect.Parameter.empty:
        return {"kind": "required"}
    if isinstance(value, float) and not math.isfinite(value):
        return {"kind": "float", "value": str(value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return {"kind": "value", "value": value}
    if isinstance(value, Enum):
        return {
            "kind": "enum",
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.value,
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "kind": "dataclass",
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "sha256": canonical_sha256(dataclasses.asdict(value)),
        }
    if isinstance(value, Path):
        try:
            relative = value.relative_to(ROOT.resolve()) if value.is_absolute() else None
        except ValueError:
            stable_value = value.as_posix()
        else:
            stable_value = (
                f"<REPOSITORY_ROOT>/{relative.as_posix()}"
                if relative is not None
                else value.as_posix()
            )
        return {"kind": "path", "value": stable_value}
    if isinstance(value, (tuple, list)) and all(
        item is None or isinstance(item, (bool, int, float, str)) for item in value
    ):
        return {"kind": type(value).__name__, "value": list(value)}
    return {
        "kind": "object",
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": repr(value),
    }

def _annotation(value: object) -> str | None:
    if value is inspect.Parameter.empty or value is inspect.Signature.empty:
        return None
    if isinstance(value, str):
        return value
    return inspect.formatannotation(value)

def signature_contract(callable_: Callable[..., object]) -> dict[str, object]:
    try:
        signature = inspect.signature(callable_)
    except (TypeError, ValueError):
        return {"parameters": None, "return": None, "unavailable": True}
    return {
        "parameters": [
            {
                "name": parameter.name,
                "kind": parameter.kind.name,
                "annotation": _annotation(parameter.annotation),
                "default": _stable_default(parameter.default),
            }
            for parameter in signature.parameters.values()
        ],
        "return": _annotation(signature.return_annotation),
    }

def _field_default(field: dataclasses.Field[object]) -> dict[str, object]:
    if field.default is not dataclasses.MISSING:
        return _stable_default(field.default)
    if field.default_factory is not dataclasses.MISSING:
        factory = cast(Callable[..., object], field.default_factory)
        return {
            "kind": "factory",
            "callable": f"{factory.__module__}.{factory.__qualname__}",
        }
    return {"kind": "required"}

def _source_public_names(path: Path, module: types.ModuleType) -> list[str]:
    explicit = getattr(module, "__all__", None)
    if explicit is not None:
        return list(explicit)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _assigned_names(target):
                    if name.isupper():
                        names.add(name)
    return sorted(names)

def _class_contract(value: type[object]) -> dict[str, object]:
    methods: dict[str, object] = {}
    properties: list[str] = []
    for name, member in value.__dict__.items():
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            properties.append(name)
            continue
        if isinstance(member, (staticmethod, classmethod)):
            member = member.__func__
        if inspect.isfunction(member):
            methods[name] = signature_contract(member)
    return {
        "signature": signature_contract(value),
        "methods": {name: methods[name] for name in sorted(methods)},
        "properties": sorted(properties),
    }

def public_module_contract(module: str, root: Path = ROOT) -> dict[str, object]:
    imported = importlib.import_module(module)
    relative = Path(*module.split("."))
    module_path = root / relative.with_suffix(".py")
    if not module_path.exists():
        module_path = root / relative / "__init__.py"
    implementation = _PUBLIC_API_IMPLEMENTATIONS.get(module)
    names_path = (
        root / Path(*implementation.split(".")).with_suffix(".py")
        if implementation is not None
        else module_path
    )
    names = _source_public_names(names_path, imported)
    callables: dict[str, object] = {}
    classes: dict[str, object] = {}
    dataclass_contracts: dict[str, object] = {}
    enum_contracts: dict[str, object] = {}
    for name in names:
        value = getattr(imported, name)
        if inspect.isclass(value):
            classes[name] = _class_contract(value)
            if dataclasses.is_dataclass(value):
                dataclass_contracts[name] = {
                    "fields": [
                        {
                            "name": field.name,
                            "type": _annotation(field.type),
                            "default": _field_default(field),
                        }
                        for field in dataclasses.fields(value)
                    ]
                }
            if issubclass(value, Enum):
                enum_contracts[name] = [
                    {"name": member.name, "value": member.value} for member in value
                ]
        elif inspect.isfunction(value):
            callables[name] = signature_contract(value)
    return {
        "path": _PUBLIC_API_FACADE_PATHS.get(
            module,
            module_path.relative_to(root).as_posix(),
        ),
        "public_names": names,
        "functions": {name: callables[name] for name in sorted(callables)},
        "classes": {name: classes[name] for name in sorted(classes)},
        "dataclasses": {
            name: dataclass_contracts[name] for name in sorted(dataclass_contracts)
        },
        "enums": {name: enum_contracts[name] for name in sorted(enum_contracts)},
    }

def public_module_names(root: Path = ROOT) -> tuple[str, ...]:
    return tuple(
        module_name(root, path)
        for path in production_sources(root)
        if path.name != "__main__.py"
    )

def cli_help_snapshot(root: Path = ROOT) -> dict[str, str]:
    from argparse import ArgumentParser, _SubParsersAction

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    scripts = project["scripts"]
    if not isinstance(scripts, dict) or not scripts:
        raise AssertionError("[project.scripts] must declare at least one CLI")
    result: dict[str, str] = {}

    def collect(current: ArgumentParser, command: str) -> None:
        current.prog = command
        result[command] = current.format_help()
        for action in current._actions:
            if not isinstance(action, _SubParsersAction):
                continue
            for name, child in action.choices.items():
                collect(child, f"{command} {name}")

    for script, entrypoint in sorted(scripts.items()):
        if not isinstance(script, str) or not isinstance(entrypoint, str):
            raise AssertionError("project script names and entrypoints must be strings")
        module_name_, separator, _ = entrypoint.partition(":")
        if separator != ":":
            raise AssertionError(f"project script entrypoint is malformed: {entrypoint}")
        module = importlib.import_module(module_name_)
        parser_factory = getattr(module, "_parser", None)
        if not callable(parser_factory):
            raise AssertionError(f"project script {script} has no complete _parser registry")
        collect(parser_factory(), script)
    return {name: result[name] for name in sorted(result)}

def _captured_exception(call: Callable[[], object]) -> dict[str, object]:
    try:
        call()
    except Exception as exc:
        return {"type": type(exc).__name__, "message": str(exc)}
    raise AssertionError("exception characterization case did not raise")

def typical_exception_snapshot() -> dict[str, object]:
    from uquant.account import account_from_dict
    from uquant.config import DEFAULT_CONFIG
    from uquant.data import normalize_symbol
    from uquant.engine import ProductionEngine
    from uquant.types import ACCOUNT_SCHEMA_VERSION

    return {
        "account_future_schema": _captured_exception(
            lambda: account_from_dict({"schema_version": ACCOUNT_SCHEMA_VERSION + 1})
        ),
        "config_unknown_override": _captured_exception(
            lambda: DEFAULT_CONFIG.override(not_a_governed_parameter=True)
        ),
        "invalid_symbol": _captured_exception(lambda: normalize_symbol("not-a-symbol")),
        "pre_ai_backtest": _captured_exception(
            lambda: ProductionEngine(ROOT / "data" / "frozen").backtest(
                symbols=("sz300308",), start="2022-12-30", end="2023-01-10"
            )
        ),
    }

def decision_fill_account_trace(root: Path = ROOT) -> dict[str, object]:
    from uquant.config import config_fingerprint
    from uquant.engine import ProductionEngine
    from uquant.types import AccountState

    symbols = ("sz300308", "sz300502", "sz300394")
    engine = ProductionEngine(root / "data" / "frozen")
    account = AccountState.empty(2_000_000.0)
    initial_payload = account.to_dict()
    engine.decide(symbols=symbols, as_of="2023-01-03", account=account)
    decision = engine.decide(symbols=symbols, as_of="2023-01-04", account=account)
    account.pending_orders = list(decision.pending_orders)
    fills = engine.execution.execute_open(
        date=importlib.import_module("pandas").Timestamp("2023-01-05"),
        account=account,
        panel={symbol: engine._raw[symbol] for symbol in symbols},
    )
    return {
        "inputs": {
            "symbols": list(symbols),
            "warmup_decision_date": "2023-01-03",
            "signal_date": "2023-01-04",
            "fill_date": "2023-01-05",
            "initial_cash": 2_000_000.0,
        },
        "initial_account_sha256": canonical_sha256(initial_payload),
        "decision": decision.canonical_payload(
            effective_config_sha256=config_fingerprint(engine.cfg)
        ),
        "fills": [dataclasses.asdict(fill) for fill in fills],
        "account_after": account.to_dict(),
        "account_after_sha256": canonical_sha256(account.to_dict()),
    }

def public_api_snapshot(
    modules: Iterable[str] | None = None, root: Path = ROOT
) -> dict[str, object]:
    from uquant import __version__
    from uquant.config import DEFAULT_CONFIG, config_fingerprint
    from uquant.types import ACCOUNT_SCHEMA_VERSION, AccountState

    selected = tuple(modules or public_module_names(root))
    empty_account = AccountState.empty(DEFAULT_CONFIG.initial_cash).to_dict()
    return {
        "package_version": __version__,
        "modules": {module: public_module_contract(module, root) for module in selected},
        "flat_config_serialization": {
            "field_order": [field.name for field in dataclasses.fields(DEFAULT_CONFIG)],
            "values": dataclasses.asdict(DEFAULT_CONFIG),
            "sha256": config_fingerprint(DEFAULT_CONFIG),
        },
        "account_state_schema": {
            "schema_version": ACCOUNT_SCHEMA_VERSION,
            "field_order": [field.name for field in dataclasses.fields(AccountState)],
            "serialized_key_order": list(empty_account),
            "empty_state": empty_account,
            "empty_state_sha256": canonical_sha256(empty_account),
        },
        "cli_help": cli_help_snapshot(),
        "typical_exceptions": typical_exception_snapshot(),
        "decision_fill_account_trace": decision_fill_account_trace(root),
    }

def _authority(path: str) -> str:
    if path.startswith("uquant/"):
        return "production"
    if path.startswith("data/frozen/"):
        return "frozen_data"
    if path.startswith("benchmarks/"):
        return "reviewed_contract"
    if path.startswith("artifacts/"):
        return "machine_evidence"
    if path.startswith("tests/"):
        return "test"
    if path.startswith("research/"):
        return "research"
    if path.startswith("scripts/"):
        return "operator_script"
    if path.startswith("docs/") or path in {"README.md", "LICENSE", "AGENTS.md"}:
        return "documentation"
    if path.startswith(".github/"):
        return "ci"
    if path in {"pyproject.toml", "uv.lock", "requirements.txt"}:
        return "dependency_or_build"
    return "repository_metadata"

def tracked_file_inventory(root: Path, commit: str) -> dict[str, object]:
    output = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--long", commit],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    entries: list[dict[str, object]] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        header, path_bytes = raw.split(b"\t", 1)
        mode, kind, oid, size = header.decode("ascii").split()
        path = path_bytes.decode("utf-8")
        entries.append(
            {
                "path": path,
                "mode": mode,
                "kind": kind,
                "git_oid": oid,
                "bytes": None if size == "-" else int(size),
                "authority": _authority(path),
            }
        )
    entries.sort(key=lambda row: str(row["path"]))
    counts: dict[str, int] = defaultdict(int)
    for row in entries:
        counts[str(row["authority"])] += 1
    return {
        "commit": commit,
        "entries": entries,
        "entry_count": len(entries),
        "authority_counts": dict(sorted(counts.items())),
        "canonical_sha256": canonical_sha256(entries),
    }

def representative_replay(
    *,
    name: str,
    start: str,
    end: str,
    symbols: Sequence[str],
    root: Path = ROOT,
    account_code_hash: str | None = None,
) -> dict[str, object]:
    from uquant.engine import ProductionEngine

    result = ProductionEngine(root / "data" / "frozen").backtest(
        symbols=symbols,
        start=start,
        end=end,
    )
    metrics = {
        key: result[key]
        for key in (
            "start",
            "end",
            "final_wealth",
            "final_equity",
            "max_drawdown",
            "account_orders",
            "submitted_account_orders",
            "gross_turnover",
            "annual_turnover",
            "effective_config_sha256",
        )
    }
    raw_final_account = result["final_account"]
    if not isinstance(raw_final_account, dict):
        raise AssertionError("representative replay final account must be a mapping")
    final_account = dict(raw_final_account)
    if account_code_hash is not None:
        if not isinstance(final_account.get("code_hash"), str):
            raise AssertionError("representative account lacks its source identity")
        final_account["code_hash"] = account_code_hash
    economic_account = _strategic_economic_account(final_account)
    return {
        "name": name,
        "symbols": list(symbols),
        "requested_start": start,
        "requested_end": end,
        "metrics": metrics,
        "decision_digests_sha256": _strategic_economic_decisions_sha256(result),
        "final_account_sha256": canonical_sha256(economic_account),
        "daily_replay_evidence_sha256": canonical_sha256(result["daily_replay_evidence"]),
    }


def _strategic_economic_decisions_sha256(result: dict[str, Any]) -> str:
    event_order_ids: dict[str, str] = {}
    digests: list[str] = []
    for source in cast(list[dict[str, Any]], result["decision_trace"]):
        row = json.loads(json.dumps(source))
        for target in row["targets"]:
            target.pop("grant_id", None)
            target.pop("epoch_id", None)
        for order in row["orders"]:
            order.pop("grant_id", None)
            order.pop("epoch_id", None)
            event_id = str(order.get("event_id", ""))
            key = event_id or str(order["order_id"])
            event_order_ids.setdefault(key, str(order["order_id"]))
            order["order_id"] = event_order_ids[key]
        digests.append(
            hashlib.sha256(
                json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
    return canonical_sha256(digests)


def _strategic_economic_account(source: dict[str, Any]) -> dict[str, Any]:
    account = json.loads(json.dumps(source))
    account["schema_version"] = 5
    for key in (
        "active_strategic_epoch_id",
        "flat_book_capital_repair",
        "protected_weight_epoch_ids",
        "recovery_owner_epoch_id",
        "strategic_cash_rearm",
        "strategic_epochs",
        "strategic_qualification_universe_identity",
        "strategic_restore_epoch_ids",
        "strategic_risk_universe_identity",
        "strategic_successor_qualification",
        "strategic_tradable_universe_identity",
    ):
        account.pop(key, None)
    groups: list[list[dict[str, Any]]] = []
    indexes: dict[tuple[str, ...], int] = {}
    for order in account["order_ledger"]:
        key = (
            ("STRATEGIC_GRANT_EVENT", str(order["grant_id"]), str(order["event_id"]))
            if order.get("grant_id") and order.get("event_id")
            else ("PHYSICAL_ORDER", str(order["order_id"]))
        )
        index = indexes.setdefault(key, len(groups))
        if index == len(groups):
            groups.append([])
        groups[index].append(order)
    collapsed: list[dict[str, Any]] = []
    event_order_ids: dict[str, str] = {}
    for group in groups:
        first = dict(group[0])
        last = group[-1]
        filled_shares = sum(int(order["filled_shares"]) for order in group)
        remaining_shares = int(last["remaining_shares"])
        first.update(
            status=last["status"],
            requested_shares=filled_shares + remaining_shares,
            filled_shares=filled_shares,
            remaining_shares=remaining_shares,
            attempts=max(int(order["attempts"]) for order in group),
            last_update_date=last["last_update_date"],
            last_event=last["last_event"],
            replaced_by=last["replaced_by"],
            cancel_reason=last["cancel_reason"],
        )
        collapsed.append(first)
        for order in group:
            event_id = str(order.get("event_id", ""))
            if event_id:
                event_order_ids[event_id] = str(first["order_id"])
    account["order_ledger"] = collapsed
    account["next_order_sequence"] = len(collapsed) + 1
    for fill in account["fills"]:
        event_id = str(fill.get("event_id", ""))
        if event_id in event_order_ids:
            fill["order_id"] = event_order_ids[event_id]

    def strip_added_identity(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: strip_added_identity(item)
                for key, item in value.items()
                if key
                not in {
                    "account_identity",
                    "epoch_id",
                    "grant_id",
                    "strategic_grant",
                    "strategic_qualification",
                }
            }
        if isinstance(value, list):
            return [strip_added_identity(item) for item in value]
        return value

    return cast(dict[str, Any], strip_added_identity(account))

def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value

def quiet_stderr() -> contextlib.AbstractContextManager[io.StringIO]:
    return contextlib.redirect_stderr(io.StringIO())
