"""Future-holdout strategy, CLI, account, and runtime source identity."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import ast
import hashlib
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config import DEFAULT_CONFIG, config_fingerprint
from ...data import DataStore
from ..ai_era import runtime_environment_provenance
from ..universe import AIUniverse, load_ai_universe
from .capabilities import holdout_facade_capabilities
from .contract import (
    ACCOUNT_EXECUTION_FIELDS as _ACCOUNT_EXECUTION_FIELDS,
)
from .contract import (
    CLI_OPERATIONAL_COMMANDS as _CLI_OPERATIONAL_COMMANDS,
)
from .contract import (
    COMMIT_PATTERN as _COMMIT,
)
from .contract import (
    LAST_IN_SAMPLE_DATE,
    PRIOR_CLOSE_ACCOUNT_SHA256,
    STRATEGY_ACCOUNT_CODE_SHA256,
    STRATEGY_ANCHOR_COMMIT,
    STRATEGY_CLI_SHA256,
    STRATEGY_SOURCE_SHA256,
)
from .contract import (
    SHA256_PATTERN as _SHA256,
)
from .contract import (
    STRATEGY_FIXED_RELATIVES as _STRATEGY_FIXED_RELATIVES,
)
from .contract import (
    STRATEGY_OPERATIONAL_RELATIVES as _STRATEGY_OPERATIONAL_RELATIVES,
)
from .contract import (
    canonical_sha256 as _canonical_sha256,
)
from .contract import (
    git_executable as _git_executable,
)
from .contract import (
    repository_root as _repository_root,
)


@dataclass(frozen=True, slots=True)
class HoldoutBinding:
    """Exact candidate and locked runtime identities bound by a manifest."""

    production_commit: str
    production_source_sha256: str
    strategy_source_sha256: str
    strategy_cli_sha256: str
    effective_config_sha256: str
    universe_sha256: str
    industry_sha256: str
    python_full_version: str
    numpy_version: str
    pandas_version: str
    uv_version: str
    uv_lock_sha256: str

    def __post_init__(self) -> None:
        if not _COMMIT.fullmatch(self.production_commit):
            raise ValueError("holdout production commit must be a full Git SHA")
        for field in (
            "production_source_sha256",
            "strategy_source_sha256",
            "strategy_cli_sha256",
            "effective_config_sha256",
            "universe_sha256",
            "industry_sha256",
            "uv_lock_sha256",
        ):
            if not _SHA256.fullmatch(getattr(self, field)):
                raise ValueError(f"holdout {field} must be SHA-256")
        for field in (
            "python_full_version",
            "numpy_version",
            "pandas_version",
            "uv_version",
        ):
            if not getattr(self, field):
                raise ValueError(f"holdout {field} must be non-empty")


def _state_hashes(account_payload: Mapping[str, Any], *, as_of: str) -> dict[str, str]:
    if account_payload.get("last_successful_run") != as_of or account_payload.get("data_hash_as_of") != as_of:
        raise ValueError("holdout account is not the exact prior-close state")
    positions = account_payload.get("positions")
    pending = account_payload.get("pending_orders")
    if not isinstance(positions, Mapping) or not isinstance(pending, list):
        raise ValueError("holdout account positions or pending orders are malformed")
    tranches = {
        str(symbol): value.get("tranches", [])
        for symbol, value in positions.items()
        if isinstance(value, Mapping)
    }
    strategy = {key: value for key, value in account_payload.items() if key not in _ACCOUNT_EXECUTION_FIELDS}
    return {
        "as_of": as_of,
        "account_sha256": _canonical_sha256(dict(account_payload)),
        "positions_sha256": _canonical_sha256(dict(positions)),
        "tranches_sha256": _canonical_sha256(tranches),
        "pending_orders_sha256": _canonical_sha256(pending),
        "strategy_state_sha256": _canonical_sha256(strategy),
    }


def validate_prior_close_account(
    account_payload: Mapping[str, Any],
    *,
    frozen_data_dir: str | Path,
) -> None:
    """Require the unchanged frozen-candidate state and frozen data prefix."""

    capabilities = holdout_facade_capabilities()
    expected_code_sha256 = (
        STRATEGY_ACCOUNT_CODE_SHA256
        if capabilities is None
        else capabilities.strategy_account_code_sha256
    )
    expected_account_sha256 = (
        PRIOR_CLOSE_ACCOUNT_SHA256
        if capabilities is None
        else capabilities.prior_close_account_sha256
    )
    if account_payload.get("code_hash") != expected_code_sha256:
        raise ValueError("holdout account is not from the exact frozen candidate")
    if account_payload.get("data_hash_as_of") != LAST_IN_SAMPLE_DATE:
        raise ValueError("holdout account data hash is not bound to the prior close")
    symbols = account_payload.get("data_hash_symbols")
    if not isinstance(symbols, list) or not symbols or any(not isinstance(symbol, str) for symbol in symbols):
        raise ValueError("holdout account data hash symbols are malformed")
    expected = (
        DataStore(frozen_data_dir)
        .manifest(
            symbols,
            as_of=LAST_IN_SAMPLE_DATE,
        )
        .digest
    )
    if account_payload.get("data_hash") != expected:
        raise ValueError("holdout account data hash does not match the frozen prefix")
    if _canonical_sha256(dict(account_payload)) != expected_account_sha256:
        raise ValueError("holdout account differs from the authenticated continuous replay")


def _holdout_industry_sha256(universe: AIUniverse) -> str:
    payload = [
        {
            "symbol": member.symbol,
            "industry": member.industry,
            "effective_from": member.effective_from.isoformat(),
            "effective_to": member.effective_to.isoformat() if member.effective_to else None,
        }
        for member in universe.members
    ]
    return _canonical_sha256(payload)


def _holdout_source_paths(root: Path) -> tuple[Path, ...]:
    fixed = (
        root / "benchmarks/reference_registry.json",
        root / "benchmarks/config_parameter_governance.json",
        root / "benchmarks/future_holdout_contract.json",
        root / "pyproject.toml",
        root / "requirements.txt",
        root / "uv.lock",
    )
    python_sources = tuple((root / "uquant").rglob("*.py"))
    resources = tuple((root / "uquant/validation/resources").glob("*.json"))
    paths = tuple(sorted({*fixed, *python_sources, *resources}))
    if any(not path.is_file() for path in paths) or not python_sources or not resources:
        raise RuntimeError("cannot resolve exact holdout production source")
    return paths


def _is_strategy_relative(relative: str) -> bool:
    if relative in _STRATEGY_OPERATIONAL_RELATIVES:
        return False
    if relative in _STRATEGY_FIXED_RELATIVES:
        return True
    path = Path(relative)
    return relative.startswith("uquant/") and "__pycache__" not in path.parts and path.suffix != ".pyc"


def _strategy_source_paths(root: Path) -> tuple[Path, ...]:
    package_paths = tuple(
        path
        for path in (root / "uquant").rglob("*")
        if path.is_file() and _is_strategy_relative(path.relative_to(root).as_posix())
    )
    fixed_paths = tuple(root / relative for relative in _STRATEGY_FIXED_RELATIVES)
    paths = tuple(sorted({*package_paths, *fixed_paths}))
    resources = tuple(path for path in paths if path.is_relative_to(root / "uquant/validation/resources"))
    if not paths or not resources or any(path.is_symlink() or not path.is_file() for path in paths):
        raise RuntimeError("cannot resolve complete anchored strategy source")
    return paths


def _strategy_source_sha256(root: Path) -> str:
    """Hash the complete current decision/state source and resource inventory."""

    base = Path(root).resolve()
    return _source_sha256(_strategy_source_paths(base), root=base)


def _assigned_names(statement: ast.stmt) -> set[str]:
    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return set()
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return {node.id for target in targets for node in ast.walk(target) if isinstance(node, ast.Name)}


def _loaded_names(statement: ast.stmt) -> set[str]:
    return {
        node.id
        for node in ast.walk(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _adds_operational_parser(statement: ast.stmt) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in _CLI_OPERATIONAL_COMMANDS
        for node in ast.walk(statement)
    )


def _safe_parser_value(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        return True
    if isinstance(value, (ast.List, ast.Tuple)):
        return all(_safe_parser_value(item) for item in value.elts)
    return isinstance(value, ast.Name) and value.id in {"float", "int", "str"}


def _safe_operational_parser_statement(
    statement: ast.stmt,
    *,
    operational_names: set[str],
) -> bool:
    value: ast.expr | None = None
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            return False
        value = statement.value
    elif isinstance(statement, ast.Expr):
        value = statement.value
    if not isinstance(value, ast.Call):
        return False
    calls = [node for node in ast.walk(value) if isinstance(node, ast.Call)]
    if len(calls) != 1 or not isinstance(value.func, ast.Attribute):
        return False
    receiver = value.func.value
    if (
        not isinstance(receiver, ast.Name)
        or receiver.id not in {"sub", *operational_names}
        or value.func.attr not in {"add_argument", "add_parser", "add_subparsers"}
    ):
        return False
    return all(_safe_parser_value(item) for item in value.args) and all(
        item.arg is not None and _safe_parser_value(item.value) for item in value.keywords
    )


def _parser_strategy_body(body: list[ast.stmt]) -> list[ast.stmt]:
    operational_names: set[str] = set()
    retained: list[ast.stmt] = []
    for statement in body:
        assigned = _assigned_names(statement)
        if _adds_operational_parser(statement) or _loaded_names(statement) & operational_names:
            operational_names.update(assigned)
            if _safe_operational_parser_statement(
                statement,
                operational_names=operational_names,
            ):
                continue
        retained.append(statement)
    return retained


def _command_guard(statement: ast.stmt) -> str | None:
    if not isinstance(statement, ast.If) or statement.orelse or not isinstance(statement.test, ast.Compare):
        return None
    comparison = statement.test
    if (
        len(comparison.ops) != 1
        or not isinstance(comparison.ops[0], ast.Eq)
        or len(comparison.comparators) != 1
        or not isinstance(comparison.left, ast.Attribute)
        or comparison.left.attr != "command"
        or not isinstance(comparison.left.value, ast.Name)
        or comparison.left.value.id != "args"
        or not isinstance(comparison.comparators[0], ast.Constant)
        or not isinstance(comparison.comparators[0].value, str)
    ):
        return None
    return comparison.comparators[0].value


def _cli_strategy_ast(source: bytes) -> bytes:
    """Compile the production CLI decision/config/persistence path to canonical AST."""

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise RuntimeError("cannot compile the anchored production CLI") from exc
    retained: list[ast.stmt] = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name == "_parser":
                statement.body = _parser_strategy_body(statement.body)
            elif statement.name == "main":
                statement.body = [
                    item for item in statement.body if _command_guard(item) not in _CLI_OPERATIONAL_COMMANDS
                ]
        retained.append(statement)
    tree.body = retained
    return ast.dump(
        tree,
        annotate_fields=True,
        include_attributes=False,
    ).encode("utf-8")


def _strategy_cli_sha256(root: Path, *, from_git: str | None = None) -> str:
    """Hash compiled CLI semantics that can affect decisions or persisted state."""

    base = Path(root).resolve()
    path = base / "uquant/cli.py"
    source = (
        path.read_bytes()
        if from_git is None
        else subprocess.run(
            [_git_executable(), "-C", str(base), "show", f"{from_git}:uquant/cli.py"],
            check=True,
            capture_output=True,
        ).stdout  # nosec B603
    )
    return hashlib.sha256(_cli_strategy_ast(source)).hexdigest()


def _git_strategy_relatives(root: Path, *, commit: str) -> tuple[str, ...]:
    completed = subprocess.run(
        [
            _git_executable(),
            "-C",
            str(root),
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            "uquant",
            *_STRATEGY_FIXED_RELATIVES,
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    return tuple(
        sorted(relative for relative in completed.stdout.splitlines() if _is_strategy_relative(relative))
    )


def _validated_strategy_source_sha256(root: Path) -> str:
    capabilities = holdout_facade_capabilities()
    strategy_source_paths = (
        _strategy_source_paths
        if capabilities is None
        else capabilities.strategy_source_paths
    )
    source_sha256 = _source_sha256 if capabilities is None else capabilities.source_sha256
    git_strategy_relatives = (
        _git_strategy_relatives
        if capabilities is None
        else capabilities.git_strategy_relatives
    )
    paths = strategy_source_paths(root)
    current_relatives = tuple(path.relative_to(root).as_posix() for path in paths)
    anchored_relatives = git_strategy_relatives(root, commit=STRATEGY_ANCHOR_COMMIT)
    if current_relatives != anchored_relatives:
        raise RuntimeError("strategy source inventory drifted from the Task 8 anchor")
    anchored_sha256 = source_sha256(
        paths,
        root=root,
        from_git=STRATEGY_ANCHOR_COMMIT,
    )
    current_sha256 = source_sha256(paths, root=root)
    if anchored_sha256 != STRATEGY_SOURCE_SHA256 or current_sha256 != anchored_sha256:
        raise RuntimeError("strategy source bytes drifted from the Task 8 anchor")
    return current_sha256


def _validated_strategy_cli_sha256(root: Path) -> str:
    capabilities = holdout_facade_capabilities()
    strategy_cli_sha256 = (
        _strategy_cli_sha256 if capabilities is None else capabilities.strategy_cli_sha256
    )
    anchored = strategy_cli_sha256(root, from_git=STRATEGY_ANCHOR_COMMIT)
    current = strategy_cli_sha256(root)
    if anchored != STRATEGY_CLI_SHA256 or current != anchored:
        raise RuntimeError("production CLI decision path drifted from the Task 8 anchor")
    return current


def _strategy_account_code_sha256(root: Path) -> str:
    """Reconstruct the exact code fingerprint written by the frozen candidate."""

    capabilities = holdout_facade_capabilities()
    expected_code_sha256 = (
        STRATEGY_ACCOUNT_CODE_SHA256
        if capabilities is None
        else capabilities.strategy_account_code_sha256
    )
    completed = subprocess.run(
        [
            _git_executable(),
            "-C",
            str(root),
            "ls-tree",
            "-r",
            "--name-only",
            STRATEGY_ANCHOR_COMMIT,
            "--",
            "uquant",
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    package_sources = tuple(
        sorted(
            relative
            for relative in completed.stdout.splitlines()
            if Path(relative).parent == Path("uquant") and Path(relative).suffix == ".py"
        )
    )
    if not package_sources:
        raise RuntimeError("cannot resolve the frozen account code inventory")
    digest = hashlib.sha256()
    for relative in (
        *package_sources,
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
    ):
        content = subprocess.run(
            [
                _git_executable(),
                "-C",
                str(root),
                "show",
                f"{STRATEGY_ANCHOR_COMMIT}:{relative}",
            ],
            check=True,
            capture_output=True,
        ).stdout  # nosec B603
        digest.update(Path(relative).name.encode())
        digest.update(content)
    value = digest.hexdigest()
    if value != expected_code_sha256:
        raise RuntimeError("frozen account code anchor differs from the exact candidate")
    return value


def _source_sha256(paths: Sequence[Path], *, root: Path, from_git: str | None = None) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = (
            path.read_bytes()
            if from_git is None
            else subprocess.run(
                [_git_executable(), "-C", str(root), "show", f"{from_git}:{relative}"],
                check=True,
                capture_output=True,
            ).stdout  # nosec B603
        )
        relative_bytes = relative.encode()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def holdout_source_sha256(repository_root: str | Path) -> str:
    """Hash every production, validation, contract, environment, and lock input."""

    root = Path(repository_root).resolve()
    return _source_sha256(_source_paths(root), root=root)


def current_holdout_binding(repository_root: str | Path | None = None) -> HoldoutBinding:
    """Resolve a clean exact-HEAD production/runtime binding for post-checkout evidence."""

    owning_root = _repository_root().resolve()
    root = owning_root if repository_root is None else Path(repository_root).resolve()
    if root != owning_root:
        raise ValueError("holdout binding requires the owning repository root")
    paths = _source_paths(root)
    relative_paths = [
        "uquant",
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
        "benchmarks/future_holdout_contract.json",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
    ]
    status = subprocess.run(
        [
            _git_executable(),
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--",
            *relative_paths,
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    if status.stdout.strip():
        raise RuntimeError("holdout provenance requires committed production source")
    completed = subprocess.run(
        [_git_executable(), "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    head = completed.stdout.strip()
    source = _source_sha256(paths, root=root)
    if not _COMMIT.fullmatch(head) or _source_sha256(paths, root=root, from_git=head) != source:
        raise RuntimeError("holdout production source does not match exact HEAD")
    universe = load_ai_universe()
    runtime = runtime_environment_provenance(root)
    _strategy_account_code_sha256(root)
    return HoldoutBinding(
        production_commit=head,
        production_source_sha256=source,
        strategy_source_sha256=_validated_strategy_source_sha256(root),
        strategy_cli_sha256=_validated_strategy_cli_sha256(root),
        effective_config_sha256=config_fingerprint(DEFAULT_CONFIG),
        universe_sha256=universe.sha256,
        industry_sha256=_industry_sha256(universe),
        python_full_version=runtime["python_full_version"],
        numpy_version=runtime["numpy_version"],
        pandas_version=runtime["pandas_version"],
        uv_version=runtime["uv_version"],
        uv_lock_sha256=runtime["uv_lock_sha256"],
    )


__all__ = (
    "HoldoutBinding",
    "_state_hashes",
    "validate_prior_close_account",
    "_industry_sha256",
    "_source_paths",
    "_is_strategy_relative",
    "_strategy_source_paths",
    "_strategy_source_sha256",
    "_assigned_names",
    "_loaded_names",
    "_adds_operational_parser",
    "_safe_parser_value",
    "_safe_operational_parser_statement",
    "_parser_strategy_body",
    "_command_guard",
    "_cli_strategy_ast",
    "_strategy_cli_sha256",
    "_git_strategy_relatives",
    "_validated_strategy_source_sha256",
    "_validated_strategy_cli_sha256",
    "_strategy_account_code_sha256",
    "_source_sha256",
    "holdout_source_sha256",
    "current_holdout_binding",
)

_industry_sha256 = _holdout_industry_sha256
_source_paths = _holdout_source_paths
adds_operational_parser = _adds_operational_parser
assigned_names = _assigned_names
cli_strategy_ast = _cli_strategy_ast
command_guard = _command_guard
git_strategy_relatives = _git_strategy_relatives
industry_sha256 = _industry_sha256
is_strategy_relative = _is_strategy_relative
loaded_names = _loaded_names
parser_strategy_body = _parser_strategy_body
safe_operational_parser_statement = _safe_operational_parser_statement
safe_parser_value = _safe_parser_value
source_sha256 = _source_sha256
source_paths = _source_paths
state_hashes = _state_hashes
strategy_account_code_sha256 = _strategy_account_code_sha256
strategy_cli_sha256 = _strategy_cli_sha256
strategy_source_sha256 = _strategy_source_sha256
strategy_source_paths = _strategy_source_paths
validated_strategy_cli_sha256 = _validated_strategy_cli_sha256
validated_strategy_source_sha256 = _validated_strategy_source_sha256
