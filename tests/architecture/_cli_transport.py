"""Fail-closed transport proof for split CLI owners."""

from __future__ import annotations

import ast
import copy
import hashlib


def _functions(source: str) -> dict[str, ast.FunctionDef]:
    functions: dict[str, ast.FunctionDef] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            assert node.name not in functions, f"duplicate transport owner: {node.name}"
            functions[node.name] = node
    return functions


def _statement(source: str) -> ast.stmt:
    body = ast.parse(source).body
    assert len(body) == 1
    return body[0]


def _assert_exact(actual: ast.AST, expected: ast.AST, *, label: str) -> None:
    assert ast.dump(actual, include_attributes=False) == ast.dump(
        expected,
        include_attributes=False,
    ), f"unexpected CLI owner transport {label}"


def _body_without_exact_return(
    function: ast.FunctionDef,
    expected_return: str,
) -> list[ast.stmt]:
    assert function.body, f"empty transport stage: {function.name}"
    _assert_exact(
        function.body[-1],
        _statement(expected_return),
        label=f"return for {function.name}",
    )
    return copy.deepcopy(function.body[:-1])


def _unit_sha256(node: ast.AST) -> str:
    return hashlib.sha256(
        ast.dump(node, include_attributes=False).encode("utf-8")
    ).hexdigest()


def _project_name_loads(
    function: ast.FunctionDef,
    replacements: dict[str, str],
) -> None:
    observed = dict.fromkeys(replacements, 0)

    class Projector(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.expr:
            if isinstance(node.ctx, ast.Load) and node.id in replacements:
                observed[node.id] += 1
                return ast.copy_location(
                    ast.Name(id=replacements[node.id], ctx=node.ctx),
                    node,
                )
            return node

    Projector().visit(function)
    assert all(count > 0 for count in observed.values())


def _project_local_identifiers(
    function: ast.FunctionDef,
    replacements: dict[str, tuple[str, int]],
) -> None:
    """Project reviewed local-only names without relaxing the owner AST proof."""

    observed = dict.fromkeys(replacements, 0)

    class Projector(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.expr:
            if node.id in replacements:
                observed[node.id] += 1
                return ast.copy_location(
                    ast.Name(id=replacements[node.id][0], ctx=node.ctx),
                    node,
                )
            return node

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.ExceptHandler:
            self.generic_visit(node)
            if node.name in replacements:
                observed[node.name] += 1
                node.name = replacements[node.name][0]
            return node

    Projector().visit(function)
    assert observed == {
        name: expected_count
        for name, (_, expected_count) in replacements.items()
    }


def public_cli_seam_transport_unit_digests(
    *,
    frozen_source: str,
    current_source: str,
    projections: tuple[tuple[str, dict[str, str]], ...],
) -> tuple[str, ...]:
    """Invert explicit public seam variables back to immutable local call names."""

    frozen = _functions(frozen_source)
    current = _functions(current_source)
    digests: list[str] = []
    for name, replacements in projections:
        projected = copy.deepcopy(current[name])
        _project_name_loads(projected, replacements)
        _assert_exact(projected, frozen[name], label=f"public CLI seam {name}")
        digests.append(_unit_sha256(frozen[name]))
    return tuple(digests)


def current_heads_adapter_transport_unit_digests(
    *,
    frozen_source: str,
    current_source: str,
    current_adapter_source: str,
) -> tuple[str, ...]:
    """Project the retired dynamic adapter loader onto one finite public seam."""

    current_tree = ast.parse(current_source)
    adapter_imports = tuple(
        sorted(
            (alias.name, alias.asname)
            for node in current_tree.body
            if isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "research.window_competitor_adapter"
            for alias in node.names
        )
    )
    assert adapter_imports == ()

    adapter = _functions(current_adapter_source)["run_replay_task"]
    expected_adapter = _functions(
        '''
def run_replay_task(
    task: Task,
    *,
    pools: dict[str, tuple[str, ...]],
    windows: dict[str, tuple[str, str]],
    repository_root: Path,
) -> dict[str, Any]:
    """Run one bounded replay from the explicitly reviewed checkout."""

    expected_source = (
        repository_root / "research/window_competitor_adapter.py"
    ).resolve()
    if Path(__file__).resolve() != expected_source:
        raise RuntimeError("cannot load the read-only competitor adapter")
    global POOLS, WINDOWS
    original_pools, original_windows = POOLS, WINDOWS
    POOLS, WINDOWS = pools, windows
    try:
        return _run(task)
    finally:
        POOLS, WINDOWS = original_pools, original_windows
'''
    )["run_replay_task"]
    _assert_exact(adapter, expected_adapter, label="current-heads public adapter")

    frozen = _functions(frozen_source)
    current = _functions(current_source)
    frozen_execute = frozen["_execute_competitor_request"]
    projected_execute = copy.deepcopy(current["_execute_competitor_request"])
    frozen_tries = [node for node in frozen_execute.body if isinstance(node, ast.Try)]
    projected_tries = [
        node for node in projected_execute.body if isinstance(node, ast.Try)
    ]
    assert len(frozen_tries) == len(projected_tries) == 1
    frozen_try = frozen_tries[0]
    projected_try = projected_tries[0]
    assert len(frozen_try.body) >= 6 and len(projected_try.body) >= 5
    _assert_exact(
        projected_try.body[1],
        _statement(
            "from research.window_competitor_adapter "
            "import Task as WindowAdapterTask"
        ),
        label="current-heads task import seam",
    )
    _assert_exact(
        projected_try.body[2],
        _statement(
            "from research.window_competitor_adapter import run_replay_task"
        ),
        label="current-heads replay import seam",
    )
    _assert_exact(
        projected_try.body[3],
        _statement(
            '''
legacy_task = WindowAdapterTask(
    request.system,
    request.name,
    request.window,
    paths["qwen_root"],
    paths["aquant_root"],
    paths["trade_root"],
    str(data_dir),
    str(Path(paths["trade_data_root"]) / request.window),
)
'''
        ),
        label="current-heads task seam",
    )
    _assert_exact(
        projected_try.body[4],
        _statement(
            '''
raw = run_replay_task(
    legacy_task,
    pools={request.name: execution_symbols},
    windows={request.window: (request.start, request.end)},
    repository_root=Path(paths["repository_root"]),
)
'''
        ),
        label="current-heads replay seam",
    )
    projected_try.body[1:5] = copy.deepcopy(frozen_try.body[1:6])
    _assert_exact(
        projected_execute,
        frozen_execute,
        label="current-heads replay owner",
    )
    return (
        _unit_sha256(frozen["_load_legacy_adapter"]),
        _unit_sha256(frozen_execute),
    )


def _project_production_observation_cli_seams(
    function: ast.FunctionDef,
    expected: dict[str, int],
) -> None:
    """Invert the finite typed CLI seam back to the immutable direct calls."""

    observed = dict.fromkeys(expected, 0)

    class Projector(ast.NodeTransformer):
        def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
            self.generic_visit(node)
            value = node.value
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "observation_cli_seams"
                and not value.args
                and not value.keywords
            ):
                return node
            assert node.attr in expected, f"unknown production observation seam: {node.attr}"
            observed[node.attr] += 1
            projected_name = (
                "_fsync_checkpoint_directory"
                if node.attr == "fsync_checkpoint_directory"
                else node.attr
            )
            return ast.copy_location(ast.Name(id=projected_name, ctx=node.ctx), node)

    Projector().visit(function)
    assert observed == expected


def _production_observation_loader_projection_digests(
    *,
    frozen_source: str,
    current_cli_source: str,
) -> tuple[str, ...]:
    """Project the retired dynamic Future Holdout loader onto finite imports."""

    imported: list[tuple[str, str | None]] = []
    current_tree = ast.parse(current_cli_source)
    for node in current_tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "uquant.validation.holdout.cli_operations"
        ):
            imported.extend((alias.name, alias.asname) for alias in node.names)
    assert tuple(sorted(imported)) == tuple(
        sorted(
            (
                ("CANONICAL_JOURNAL_CHECKPOINT_PATH", None),
                ("CANONICAL_JOURNAL_PATH", None),
                ("CANONICAL_LOCAL_LANE_REPORT_PATH", None),
                ("build_local_lane_report", None),
                ("load_journal_checkpoint", None),
                ("read_trusted_execution_journal", None),
                ("write_journal_checkpoint", None),
            )
        )
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "exec"
        for node in ast.walk(current_tree)
    )
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "importlib" or alias.name.startswith("importlib.") for alias in node.names)
        for node in ast.walk(current_tree)
    )

    frozen_tree = ast.parse(frozen_source)
    start = next(
        index
        for index, node in enumerate(frozen_tree.body)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_FUTURE_HOLDOUT_SPEC"
            for target in node.targets
        )
    )
    loader_units = frozen_tree.body[start : start + 11]
    assert len(loader_units) == 11
    return tuple(_unit_sha256(node) for node in loader_units)


def _without_exact_import_modules(
    function: ast.FunctionDef,
    modules: frozenset[str],
) -> tuple[ast.FunctionDef, tuple[tuple[str, str, str | None], ...]]:
    projected = copy.deepcopy(function)
    aliases: list[tuple[str, str, str | None]] = []
    body: list[ast.stmt] = []
    for node in projected.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module in modules
        ):
            aliases.extend(
                (node.module, alias.name, alias.asname) for alias in node.names
            )
        else:
            body.append(node)
    projected.body = body
    return projected, tuple(sorted(aliases))


def generalization_ablation_public_owner_transport_unit_digests(
    *,
    frozen_source: str,
    current_source: str,
) -> tuple[str, ...]:
    """Project five exact public imports back onto the immutable replay owner."""

    frozen = _functions(frozen_source)["_replay_cell"]
    current = _functions(current_source)["_replay_cell"]
    modules = frozenset(
        {
            "research.first_divergence",
            "uquant.validation.promotion",
        }
    )
    frozen_without_imports, frozen_aliases = _without_exact_import_modules(
        frozen, modules
    )
    current_without_imports, current_aliases = _without_exact_import_modules(
        current, modules
    )
    assert frozen_aliases == tuple(
        sorted(
            (
                ("research.first_divergence", "_CAUSAL_STAGES", "TRACE_STAGES"),
                ("research.first_divergence", "_canonical_stages", None),
                ("research.first_divergence", "_trace_row", None),
                ("research.first_divergence", "_validate_trace_interval", None),
                ("uquant.validation.promotion", "_compact", None),
            )
        )
    )
    assert current_aliases == tuple(
        sorted(
            (
                ("research.first_divergence", "CAUSAL_STAGES", "TRACE_STAGES"),
                ("research.first_divergence", "canonical_stages", "_canonical_stages"),
                ("research.first_divergence", "trace_row", "_trace_row"),
                (
                    "research.first_divergence",
                    "validate_trace_interval",
                    "_validate_trace_interval",
                ),
                (
                    "uquant.validation.promotion",
                    "compact_promotion_payload",
                    "_compact",
                ),
            )
        )
    )
    _assert_exact(
        current_without_imports,
        frozen_without_imports,
        label="generalization ablation replay owner",
    )
    return (_unit_sha256(frozen),)


def production_observation_transport_unit_digests(
    *,
    frozen_source: str,
    current_source: str,
    current_cli_source: str,
) -> tuple[str, ...]:
    """Expand named current stages back into the two immutable CLI owners."""

    frozen = _functions(frozen_source)
    current = _functions(current_source)

    verify = copy.deepcopy(current["verify_backup_checkpoint"])
    assert len(verify.body) == 7
    _assert_exact(
        verify.body[2],
        _statement("raw, files, status_value = _validated_backup_manifest(root)"),
        label="manifest call",
    )
    _assert_exact(
        verify.body[3],
        _statement("_validate_backup_carriers(root, files)"),
        label="carrier call",
    )
    _assert_exact(
        verify.body[4],
        _statement(
            "_validate_backup_receipt("
            "root, raw=raw, files=files, status_value=status_value)"
        ),
        label="receipt call",
    )
    _assert_exact(
        verify.body[5],
        _statement("_validate_backup_inventory(root, files)"),
        label="inventory call",
    )
    verify.body = [
        *copy.deepcopy(verify.body[:2]),
        *_body_without_exact_return(
            current["_validated_backup_manifest"],
            "return raw, files, status_value",
        ),
        *copy.deepcopy(current["_validate_backup_carriers"].body),
        *copy.deepcopy(current["_validate_backup_receipt"].body),
        *copy.deepcopy(current["_validate_backup_inventory"].body),
        copy.deepcopy(verify.body[-1]),
    ]
    _assert_exact(
        verify,
        frozen["verify_backup_checkpoint"],
        label="expanded verify owner",
    )

    projected_functions: list[ast.FunctionDef] = []
    for name, expected_seams in (
        ("create_backup_checkpoint", {"atomic_write_bytes": 1, "atomic_write_text": 1}),
        ("add_backup_evidence", {"atomic_write_bytes": 1, "atomic_write_text": 1}),
        (
            "seal_backup_receipt",
            {
                "atomic_write_bytes": 1,
                "atomic_write_text": 1,
                "fsync_checkpoint_directory": 1,
            },
        ),
        (
            "_observation_lock",
            {"acquire_file_lock": 1, "release_file_lock": 1},
        ),
    ):
        projected = copy.deepcopy(current[name])
        _project_production_observation_cli_seams(projected, expected_seams)
        if name == "_observation_lock":
            _project_local_identifiers(
                projected,
                {
                    "release_errors": ("cleanup_errors", 7),
                    "release_error": ("cleanup_error", 4),
                },
            )
        _assert_exact(projected, frozen[name], label=f"typed seam owner {name}")
        projected_functions.append(projected)

    run = copy.deepcopy(current["run_production_observation"])
    assert len(run.body) == 5 and isinstance(run.body[-1], ast.With)
    transaction = run.body[-1]
    assert len(transaction.body) >= 3
    _assert_exact(
        transaction.body[1],
        _statement(
            "backup, receipt, steps, broker, holdout_account, journal, "
            "journal_checkpoint = _prepare_observation_run("
            "args, paths=paths, root=root, account=account)"
        ),
        label="preparation call",
    )
    transaction.body = [
        copy.deepcopy(transaction.body[0]),
        *_body_without_exact_return(
            current["_prepare_observation_run"],
            "return (backup, receipt, steps, broker, holdout_account, journal, "
            "journal_checkpoint)",
        ),
        *copy.deepcopy(transaction.body[2:]),
    ]
    _project_production_observation_cli_seams(
        run,
        {
            "append_holdout_snapshot": 1,
            "atomic_write_text": 1,
            "build_local_lane_report": 1,
            "create_backup_checkpoint": 1,
            "generate_future_holdout_replay": 1,
            "uquant_main": 1,
        },
    )
    _assert_exact(
        run,
        frozen["run_production_observation"],
        label="expanded run owner",
    )
    return (
        *(_unit_sha256(function) for function in projected_functions),
        _unit_sha256(verify),
        _unit_sha256(run),
        *_production_observation_loader_projection_digests(
            frozen_source=frozen_source,
            current_cli_source=current_cli_source,
        ),
    )
