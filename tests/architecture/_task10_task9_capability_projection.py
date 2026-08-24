"""Fail-closed Task-9 projections for explicit Task-10 capability scopes."""

from __future__ import annotations

import ast
import copy
from collections import Counter
from collections.abc import Mapping, Sequence

type _ImportAtom = tuple[str, int, str, str, str | None]

_GENERALIZATION_CAPABILITY_OWNERS = frozenset(
    {
        "uquant/validation/generalization/provenance.py",
        "uquant/validation/generalization/runner.py",
        "uquant/validation/generalization_policy/evaluator.py",
    }
)


def _import_atoms(tree: ast.Module) -> Counter[_ImportAtom]:
    atoms: Counter[_ImportAtom] = Counter()
    for node in tree.body:
        if isinstance(node, ast.Import):
            atoms.update(
                ("import", 0, "", alias.name, alias.asname) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            atoms.update(
                ("from", node.level, node.module or "", alias.name, alias.asname)
                for alias in node.names
            )
    return atoms


def _assert_exact_import_delta(
    *,
    current: ast.Module,
    reviewed: ast.Module,
    added: Sequence[_ImportAtom],
    removed: Sequence[_ImportAtom],
) -> None:
    current_atoms = _import_atoms(current)
    reviewed_atoms = _import_atoms(reviewed)
    assert current_atoms - reviewed_atoms == Counter(added)
    assert reviewed_atoms - current_atoms == Counter(removed)


def _unit_name(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
        return node.name.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id
    return None


def _unit(tree: ast.Module, name: str) -> ast.stmt:
    matches = [node for node in tree.body if _unit_name(node) == name]
    assert len(matches) == 1
    return matches[0]


def _parsed_expression(source: str) -> ast.expr:
    node = ast.parse(source, mode="eval").body
    assert isinstance(node, ast.expr)
    return node


class _ExactCapabilityInverse(ast.NodeTransformer):
    def __init__(
        self,
        *,
        assignments: Sequence[str] = (),
        call_functions: Mapping[str, tuple[str, int]] | None = None,
        calls: Mapping[str, tuple[str, int]] | None = None,
        names: Mapping[str, tuple[str, int]] | None = None,
    ) -> None:
        self._assignments = {
            ast.dump(ast.parse(source).body[0], include_attributes=False): 1
            for source in assignments
        }
        self._call_functions = {
            ast.dump(_parsed_expression(source), include_attributes=False): (
                replacement,
                expected,
            )
            for source, (replacement, expected) in (call_functions or {}).items()
        }
        self._calls = {
            ast.dump(_parsed_expression(source), include_attributes=False): (
                replacement,
                expected,
            )
            for source, (replacement, expected) in (calls or {}).items()
        }
        self._names = {
            ast.dump(_parsed_expression(source), include_attributes=False): (
                replacement,
                expected,
            )
            for source, (replacement, expected) in (names or {}).items()
        }
        self.assignment_counts: Counter[str] = Counter()
        self.call_function_counts: Counter[str] = Counter()
        self.call_counts: Counter[str] = Counter()
        self.name_counts: Counter[str] = Counter()

    def visit_Assign(self, node: ast.Assign) -> ast.AST | None:
        key = ast.dump(node, include_attributes=False)
        if key in self._assignments:
            self.assignment_counts[key] += 1
            return None
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        node = self.generic_visit(node)
        assert isinstance(node, ast.Call)
        key = ast.dump(node, include_attributes=False)
        if key in self._calls:
            replacement, _ = self._calls[key]
            self.call_counts[key] += 1
            return copy.deepcopy(_parsed_expression(replacement))
        function_key = ast.dump(node.func, include_attributes=False)
        if function_key in self._call_functions:
            replacement, _ = self._call_functions[function_key]
            self.call_function_counts[function_key] += 1
            node.func = copy.deepcopy(_parsed_expression(replacement))
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        key = ast.dump(node, include_attributes=False)
        if key in self._names:
            replacement, _ = self._names[key]
            self.name_counts[key] += 1
            return copy.deepcopy(_parsed_expression(replacement))
        return node

    def assert_exact_counts(self) -> None:
        assert self.assignment_counts == Counter(self._assignments)
        assert self.call_function_counts == Counter(
            {key: expected for key, (_, expected) in self._call_functions.items()}
        )
        assert self.call_counts == Counter(
            {key: expected for key, (_, expected) in self._calls.items()}
        )
        assert self.name_counts == Counter(
            {key: expected for key, (_, expected) in self._names.items()}
        )


def _project_function_to_reviewed(
    *,
    current: ast.Module,
    reviewed: ast.Module,
    name: str,
    inverse: _ExactCapabilityInverse,
) -> None:
    current_node = _unit(current, name)
    reviewed_node = _unit(reviewed, name)
    transformed = inverse.visit(copy.deepcopy(current_node))
    assert isinstance(transformed, ast.FunctionDef)
    inverse.assert_exact_counts()
    assert ast.dump(transformed, include_attributes=False) == ast.dump(
        reviewed_node, include_attributes=False
    )
    current.body[current.body.index(current_node)] = copy.deepcopy(reviewed_node)


def _remove_exact_capability_units(
    tree: ast.Module,
    expected_source: str,
) -> None:
    for expected in ast.parse(expected_source, type_comments=True).body:
        name = _unit_name(expected)
        assert name is not None
        current = _unit(tree, name)
        assert ast.dump(current, include_attributes=False) == ast.dump(
            expected, include_attributes=False
        )
        tree.body.remove(current)


def _insert_reviewed_unit_before(
    *,
    current: ast.Module,
    reviewed: ast.Module,
    name: str,
    before: str,
) -> None:
    before_node = _unit(current, before)
    current.body.insert(current.body.index(before_node), copy.deepcopy(_unit(reviewed, name)))


def _project_generalization_provenance_capabilities(
    current: ast.Module,
    reviewed: ast.Module,
) -> None:
    _assert_exact_import_delta(
        current=current,
        reviewed=reviewed,
        added=(
            ("from", 0, "collections.abc", "Callable", None),
            ("from", 0, "contextvars", "ContextVar", None),
            ("from", 0, "dataclasses", "dataclass", None),
        ),
        removed=(("import", 0, "", "sys", None),),
    )
    _remove_exact_capability_units(
        current,
        """
@dataclass(frozen=True, slots=True)
class GeneralizationRuntimeCapabilities:
    git_stdout: Callable[..., str]
    production_source_fingerprint: Callable[[Path], str]
    verify_data_manifest: Callable[[str | Path], dict[str, Any]]

_DEFAULT_RUNTIME_CAPABILITIES = GeneralizationRuntimeCapabilities(
    git_stdout=_git_stdout,
    production_source_fingerprint=_production_source_fingerprint,
    verify_data_manifest=verify_data_manifest,
)
_RUNTIME_CAPABILITIES: ContextVar[GeneralizationRuntimeCapabilities] = ContextVar(
    "uquant_generalization_runtime_capabilities",
    default=_DEFAULT_RUNTIME_CAPABILITIES,
)

def generalization_runtime_capabilities() -> GeneralizationRuntimeCapabilities:
    return _RUNTIME_CAPABILITIES.get()

@contextmanager
def generalization_runtime_scope(
    capabilities: GeneralizationRuntimeCapabilities,
) -> Iterator[None]:
    token = _RUNTIME_CAPABILITIES.set(capabilities)
    try:
        yield
    finally:
        _RUNTIME_CAPABILITIES.reset(token)
""",
    )
    _insert_reviewed_unit_before(
        current=current,
        reviewed=reviewed,
        name="compatibility_value",
        before="_fingerprint",
    )
    _project_function_to_reviewed(
        current=current,
        reviewed=reviewed,
        name="_production_commit",
        inverse=_ExactCapabilityInverse(
            assignments=(
                "git_stdout = generalization_runtime_capabilities().git_stdout",
            ),
            call_functions={
                "git_stdout": (
                    'compatibility_value("_git_stdout", _git_stdout)',
                    2,
                )
            },
        ),
    )
    _project_function_to_reviewed(
        current=current,
        reviewed=reviewed,
        name="_immutable_validation_inputs",
        inverse=_ExactCapabilityInverse(
            assignments=("capabilities = generalization_runtime_capabilities()",),
            call_functions={
                "capabilities.verify_data_manifest": (
                    'compatibility_value("verify_data_manifest", verify_data_manifest)',
                    1,
                ),
                "capabilities.production_source_fingerprint": (
                    "compatibility_value(\n"
                    '    "_production_source_fingerprint",\n'
                    "    _production_source_fingerprint,\n"
                    ")",
                    1,
                ),
            },
        ),
    )


def _project_generalization_runner_capabilities(
    current: ast.Module,
    reviewed: ast.Module,
) -> None:
    _assert_exact_import_delta(
        current=current,
        reviewed=reviewed,
        added=(("from", 1, "provenance", "generalization_runtime_capabilities", None),),
        removed=(
            ("from", 2, "manifest", "verify_data_manifest", None),
            ("from", 1, "provenance", "compatibility_value", None),
        ),
    )
    _project_function_to_reviewed(
        current=current,
        reviewed=reviewed,
        name="run_generalization",
        inverse=_ExactCapabilityInverse(
            assignments=("capabilities = generalization_runtime_capabilities()",),
            call_functions={
                "capabilities.verify_data_manifest": (
                    'compatibility_value("verify_data_manifest", verify_data_manifest)',
                    1,
                ),
                "capabilities.production_source_fingerprint": (
                    'compatibility_value(\n    "_production_source_fingerprint",\n'
                    "    _production_source_fingerprint,\n)",
                    1,
                ),
            },
        ),
    )


def _project_generalization_evaluator_capabilities(
    current: ast.Module,
    reviewed: ast.Module,
) -> None:
    _assert_exact_import_delta(
        current=current,
        reviewed=reviewed,
        added=(
            ("from", 0, "collections.abc", "Callable", None),
            ("from", 0, "collections.abc", "Iterator", None),
            ("from", 0, "contextlib", "contextmanager", None),
            ("from", 0, "contextvars", "ContextVar", None),
        ),
        removed=(("import", 0, "", "sys", None),),
    )
    _remove_exact_capability_units(
        current,
        """
type HeadAndSource = Callable[[Path], tuple[str, str]]

_HEAD_AND_SOURCE: ContextVar[HeadAndSource] = ContextVar(
    "uquant_generalization_head_and_source",
    default=_head_and_source,
)

@contextmanager
def generalization_policy_capabilities(
    *, head_and_source: HeadAndSource
) -> Iterator[None]:
    token = _HEAD_AND_SOURCE.set(head_and_source)
    try:
        yield
    finally:
        _HEAD_AND_SOURCE.reset(token)
""",
    )
    _insert_reviewed_unit_before(
        current=current,
        reviewed=reviewed,
        name="_compatibility_head_and_source",
        before="evaluate_cell_non_regression",
    )
    _project_function_to_reviewed(
        current=current,
        reviewed=reviewed,
        name="evaluate_generalization_policy_artifact",
        inverse=_ExactCapabilityInverse(
            calls={
                "_HEAD_AND_SOURCE.get()": ("_compatibility_head_and_source", 1),
            }
        ),
    )


def _project_explicit_generalization_capabilities(
    *,
    owner: str,
    current: ast.Module,
    reviewed: ast.Module,
) -> ast.Module:
    projectors = {
        "uquant/validation/generalization/provenance.py": (
            _project_generalization_provenance_capabilities
        ),
        "uquant/validation/generalization/runner.py": (
            _project_generalization_runner_capabilities
        ),
        "uquant/validation/generalization_policy/evaluator.py": (
            _project_generalization_evaluator_capabilities
        ),
    }
    projectors[owner](current, reviewed)
    current.body = [
        node for node in current.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    reviewed.body = [
        node for node in reviewed.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert ast.dump(current, include_attributes=False) == ast.dump(
        reviewed, include_attributes=False
    )
    return current


_HOLDOUT_CAPABILITY_OWNERS = frozenset(
    {
        "uquant/validation/holdout/artifact_transaction.py",
        "uquant/validation/holdout/checkpoints.py",
        "uquant/validation/holdout/contract.py",
        "uquant/validation/holdout/replay.py",
        "uquant/validation/holdout/service.py",
        "uquant/validation/holdout/source_identity.py",
    }
)

_HOLDOUT_IMPORT_DELTAS: Mapping[
    str,
    tuple[tuple[_ImportAtom, ...], tuple[_ImportAtom, ...]],
] = {
    "uquant/validation/holdout/contract.py": (
        (("from", 1, "capabilities", "holdout_facade_capabilities", None),),
        (
            ("import", 0, "", "sys", None),
            ("from", 0, "typing", "cast", None),
        ),
    ),
    "uquant/validation/holdout/source_identity.py": (
        (("from", 1, "capabilities", "holdout_facade_capabilities", None),),
        (("from", 1, "contract", "compatibility_value", None),),
    ),
    "uquant/validation/holdout/replay.py": (
        (("from", 1, "capabilities", "holdout_runtime_capabilities", None),),
        (("from", 1, "contract", "runtime_compatibility_value", None),),
    ),
    "uquant/validation/holdout/checkpoints.py": (
        (("from", 1, "capabilities", "holdout_runtime_capabilities", None),),
        (("from", 1, "contract", "runtime_compatibility_value", None),),
    ),
    "uquant/validation/holdout/artifact_transaction.py": (
        (("from", 1, "capabilities", "holdout_runtime_capabilities", None),),
        (("from", 1, "contract", "runtime_compatibility_value", None),),
    ),
    "uquant/validation/holdout/service.py": (
        (
            ("from", 1, "capabilities", "holdout_facade_capabilities", None),
            ("from", 1, "capabilities", "holdout_runtime_capabilities", None),
        ),
        (
            ("from", 1, "contract", "compatibility_value", None),
            ("from", 1, "contract", "runtime_compatibility_value", None),
        ),
    ),
}

_HOLDOUT_ASSIGNMENT_REMOVALS: Mapping[tuple[str, str], tuple[str, ...]] = {
    ("uquant/validation/holdout/contract.py", "_validate_contract_identity"): (
        "capabilities = holdout_facade_capabilities()",
        "required_seal = (REQUIRED_FUTURE_HOLDOUT_SHA256 if capabilities is None "
        "else capabilities.required_future_holdout_sha256)",
    ),
    ("uquant/validation/holdout/contract.py", "_validate_live_schedule"): (
        "capabilities = holdout_facade_capabilities()",
        "live_windows = AI_ERA_WINDOWS if capabilities is None else capabilities.ai_era_windows",
    ),
    ("uquant/validation/holdout/source_identity.py", "validate_prior_close_account"): (
        "capabilities = holdout_facade_capabilities()",
        "expected_code_sha256 = (STRATEGY_ACCOUNT_CODE_SHA256 if capabilities is None "
        "else capabilities.strategy_account_code_sha256)",
        "expected_account_sha256 = (PRIOR_CLOSE_ACCOUNT_SHA256 if capabilities is None "
        "else capabilities.prior_close_account_sha256)",
    ),
    (
        "uquant/validation/holdout/source_identity.py",
        "_validated_strategy_source_sha256",
    ): (
        "capabilities = holdout_facade_capabilities()",
        "strategy_source_paths = (_strategy_source_paths if capabilities is None "
        "else capabilities.strategy_source_paths)",
        "source_sha256 = _source_sha256 if capabilities is None else capabilities.source_sha256",
        "git_strategy_relatives = (_git_strategy_relatives if capabilities is None "
        "else capabilities.git_strategy_relatives)",
    ),
    (
        "uquant/validation/holdout/source_identity.py",
        "_validated_strategy_cli_sha256",
    ): (
        "capabilities = holdout_facade_capabilities()",
        "strategy_cli_sha256 = (_strategy_cli_sha256 if capabilities is None "
        "else capabilities.strategy_cli_sha256)",
    ),
    (
        "uquant/validation/holdout/source_identity.py",
        "_strategy_account_code_sha256",
    ): (
        "capabilities = holdout_facade_capabilities()",
        "expected_code_sha256 = (STRATEGY_ACCOUNT_CODE_SHA256 if capabilities is None "
        "else capabilities.strategy_account_code_sha256)",
    ),
    ("uquant/validation/holdout/replay.py", "_replay_lane_context"): (
        "capabilities = holdout_runtime_capabilities()",
        "source_identity = (holdout_source_sha256 if capabilities is None "
        "else capabilities.holdout_source_sha256)",
    ),
    ("uquant/validation/holdout/replay.py", "replay_future_holdout"): (
        "capabilities = holdout_runtime_capabilities()",
        "validate_account = (validate_prior_close_account if capabilities is None "
        "else capabilities.validate_prior_close_account)",
    ),
    ("uquant/validation/holdout/replay.py", "_validate_replay_identity"): (
        "capabilities = holdout_runtime_capabilities()",
        "source_identity = (holdout_source_sha256 if capabilities is None "
        "else capabilities.holdout_source_sha256)",
    ),
    ("uquant/validation/holdout/checkpoints.py", "_checkpoint_identity_is_valid"): (
        "capabilities = holdout_runtime_capabilities()",
        "source_identity = (holdout_source_sha256 if capabilities is None "
        "else capabilities.holdout_source_sha256)",
    ),
    ("uquant/validation/holdout/artifact_transaction.py", "_artifact_snapshots"): (
        "capabilities = holdout_runtime_capabilities()",
        "read_artifact = (_read_protected_artifact if capabilities is None "
        "else capabilities.read_protected_artifact)",
        "os_adapter = os if capabilities is None else capabilities.os_adapter",
    ),
    ("uquant/validation/holdout/artifact_transaction.py", "_restore_owned_artifact"): (
        "capabilities = holdout_runtime_capabilities()",
        "read_artifact = (_read_protected_artifact if capabilities is None "
        "else capabilities.read_protected_artifact)",
    ),
    ("uquant/validation/holdout/service.py", "_manifest_repository_root"): (
        "capabilities = holdout_facade_capabilities()",
        "repository_root_capability = (_repository_root if capabilities is None "
        "else capabilities.repository_root)",
    ),
    ("uquant/validation/holdout/service.py", "_observation_metrics"): (
        "capabilities = holdout_runtime_capabilities()",
        "replay = replay_future_holdout if capabilities is None else capabilities.replay_future_holdout",
    ),
    ("uquant/validation/holdout/service.py", "build_future_holdout_manifest"): (
        "capabilities = holdout_facade_capabilities()",
        "validate_account = (validate_prior_close_account if capabilities is None "
        "else capabilities.validate_prior_close_account)",
        "current_binding = (current_holdout_binding if capabilities is None "
        "else capabilities.current_holdout_binding)",
    ),
    ("uquant/validation/holdout/service.py", "_validated_generated_replay"): (
        "capabilities = holdout_runtime_capabilities()",
        "replay_capability = (replay_future_holdout if capabilities is None "
        "else capabilities.replay_future_holdout)",
    ),
    ("uquant/validation/holdout/service.py", "_write_replay_artifact"): (
        "capabilities = holdout_runtime_capabilities()",
        "write_text = atomic_write_text if capabilities is None else capabilities.atomic_write_text",
    ),
    ("uquant/validation/holdout/service.py", "_write_decision_artifact"): (
        "capabilities = holdout_runtime_capabilities()",
        "write_text = atomic_write_text if capabilities is None else capabilities.atomic_write_text",
    ),
    ("uquant/validation/holdout/service.py", "_write_checkpoint_artifact"): (
        "capabilities = holdout_runtime_capabilities()",
        "write_text = atomic_write_text if capabilities is None else capabilities.atomic_write_text",
    ),
    ("uquant/validation/holdout/service.py", "generate_future_holdout_replay"): (
        "capabilities = holdout_runtime_capabilities()",
        "artifact_bundle_lock = (_artifact_bundle_lock if capabilities is None "
        "else capabilities.artifact_bundle_lock)",
    ),
}

_HOLDOUT_CALL_FUNCTION_INVERSES: Mapping[
    tuple[str, str],
    Mapping[str, tuple[str, int]],
] = {
    (
        "uquant/validation/holdout/source_identity.py",
        "_validated_strategy_source_sha256",
    ): {
        "strategy_source_paths": (
            'compatibility_value("_strategy_source_paths", _strategy_source_paths)',
            1,
        ),
        "git_strategy_relatives": (
            'compatibility_value("_git_strategy_relatives", _git_strategy_relatives)',
            1,
        ),
        "source_sha256": ("_source_sha256", 2),
    },
    (
        "uquant/validation/holdout/source_identity.py",
        "_validated_strategy_cli_sha256",
    ): {
        "strategy_cli_sha256": (
            'compatibility_value("_strategy_cli_sha256", _strategy_cli_sha256)',
            2,
        )
    },
    ("uquant/validation/holdout/replay.py", "_replay_lane_context"): {
        "source_identity": (
            'runtime_compatibility_value("holdout_source_sha256", holdout_source_sha256)',
            1,
        )
    },
    ("uquant/validation/holdout/replay.py", "replay_future_holdout"): {
        "validate_account": (
            'runtime_compatibility_value("validate_prior_close_account", '
            "validate_prior_close_account)",
            1,
        )
    },
    ("uquant/validation/holdout/replay.py", "_validate_replay_identity"): {
        "source_identity": (
            'runtime_compatibility_value("holdout_source_sha256", holdout_source_sha256)',
            1,
        )
    },
    ("uquant/validation/holdout/checkpoints.py", "_checkpoint_identity_is_valid"): {
        "source_identity": (
            'runtime_compatibility_value("holdout_source_sha256", holdout_source_sha256)',
            1,
        )
    },
    ("uquant/validation/holdout/artifact_transaction.py", "_artifact_snapshots"): {
        "read_artifact": (
            'runtime_compatibility_value("_read_protected_artifact", '
            "_read_protected_artifact)",
            1,
        )
    },
    ("uquant/validation/holdout/artifact_transaction.py", "_restore_owned_artifact"): {
        "read_artifact": (
            'runtime_compatibility_value("_read_protected_artifact", '
            "_read_protected_artifact)",
            1,
        )
    },
    ("uquant/validation/holdout/service.py", "_manifest_repository_root"): {
        "repository_root_capability": (
            'compatibility_value("_repository_root", _repository_root)',
            1,
        )
    },
    ("uquant/validation/holdout/service.py", "_observation_metrics"): {
        "replay": (
            'runtime_compatibility_value("replay_future_holdout", replay_future_holdout)',
            1,
        )
    },
    ("uquant/validation/holdout/service.py", "build_future_holdout_manifest"): {
        "validate_account": (
            'compatibility_value("validate_prior_close_account", '
            "validate_prior_close_account)",
            1,
        ),
        "current_binding": (
            'compatibility_value("current_holdout_binding", current_holdout_binding)',
            1,
        ),
    },
    ("uquant/validation/holdout/service.py", "_validated_generated_replay"): {
        "replay_capability": (
            'runtime_compatibility_value("replay_future_holdout", replay_future_holdout)',
            1,
        )
    },
    ("uquant/validation/holdout/service.py", "_write_replay_artifact"): {
        "write_text": (
            'runtime_compatibility_value("atomic_write_text", atomic_write_text)',
            1,
        )
    },
    ("uquant/validation/holdout/service.py", "_write_decision_artifact"): {
        "write_text": (
            'runtime_compatibility_value("atomic_write_text", atomic_write_text)',
            1,
        )
    },
    ("uquant/validation/holdout/service.py", "_write_checkpoint_artifact"): {
        "write_text": (
            'runtime_compatibility_value("atomic_write_text", atomic_write_text)',
            1,
        )
    },
    ("uquant/validation/holdout/service.py", "generate_future_holdout_replay"): {
        "artifact_bundle_lock": (
            'runtime_compatibility_value("_artifact_bundle_lock", _artifact_bundle_lock)',
            1,
        )
    },
}

_HOLDOUT_NAME_INVERSES: Mapping[
    tuple[str, str],
    Mapping[str, tuple[str, int]],
] = {
    ("uquant/validation/holdout/contract.py", "_validate_contract_identity"): {
        "required_seal": (
            'compatibility_value("REQUIRED_FUTURE_HOLDOUT_SHA256", '
            "REQUIRED_FUTURE_HOLDOUT_SHA256)",
            1,
        )
    },
    ("uquant/validation/holdout/contract.py", "_validate_live_schedule"): {
        "live_windows": (
            'compatibility_value("AI_ERA_WINDOWS", AI_ERA_WINDOWS)',
            1,
        )
    },
    ("uquant/validation/holdout/source_identity.py", "validate_prior_close_account"): {
        "expected_code_sha256": (
            'compatibility_value("STRATEGY_ACCOUNT_CODE_SHA256", '
            "STRATEGY_ACCOUNT_CODE_SHA256)",
            1,
        ),
        "expected_account_sha256": (
            'compatibility_value("PRIOR_CLOSE_ACCOUNT_SHA256", PRIOR_CLOSE_ACCOUNT_SHA256)',
            1,
        ),
    },
    (
        "uquant/validation/holdout/source_identity.py",
        "_strategy_account_code_sha256",
    ): {"expected_code_sha256": ("STRATEGY_ACCOUNT_CODE_SHA256", 1)},
    ("uquant/validation/holdout/artifact_transaction.py", "_artifact_snapshots"): {
        "os_adapter": ('runtime_compatibility_value("os", os)', 1),
    },
}


def _project_contract_capability_exports(
    current: ast.Module,
    reviewed: ast.Module,
) -> None:
    current_all = _unit(current, "__all__")
    reviewed_all = _unit(reviewed, "__all__")
    assert isinstance(current_all, ast.Assign) and isinstance(reviewed_all, ast.Assign)
    current_names = ast.literal_eval(current_all.value)
    reviewed_names = ast.literal_eval(reviewed_all.value)
    assert isinstance(current_names, tuple) and isinstance(reviewed_names, tuple)
    assert current_names == tuple(
        name
        for name in reviewed_names
        if name not in {"compatibility_value", "runtime_compatibility_value"}
    )
    current.body[current.body.index(current_all)] = copy.deepcopy(reviewed_all)


def _project_explicit_holdout_capabilities(
    *,
    owner: str,
    current: ast.Module,
    reviewed: ast.Module,
) -> ast.Module:
    added, removed = _HOLDOUT_IMPORT_DELTAS[owner]
    _assert_exact_import_delta(
        current=current,
        reviewed=reviewed,
        added=added,
        removed=removed,
    )
    if owner.endswith("/contract.py"):
        _insert_reviewed_unit_before(
            current=current,
            reviewed=reviewed,
            name="compatibility_value",
            before="LAST_IN_SAMPLE_DATE",
        )
        runtime_compatibility = _unit(reviewed, "runtime_compatibility_value")
        compatibility = _unit(current, "compatibility_value")
        current.body.insert(
            current.body.index(compatibility) + 1,
            copy.deepcopy(runtime_compatibility),
        )
        _project_contract_capability_exports(current, reviewed)

    keys = {
        key
        for mapping in (
            _HOLDOUT_ASSIGNMENT_REMOVALS,
            _HOLDOUT_CALL_FUNCTION_INVERSES,
            _HOLDOUT_NAME_INVERSES,
        )
        for key in mapping
        if key[0] == owner
    }
    for _, function_name in sorted(keys):
        _project_function_to_reviewed(
            current=current,
            reviewed=reviewed,
            name=function_name,
            inverse=_ExactCapabilityInverse(
                assignments=_HOLDOUT_ASSIGNMENT_REMOVALS.get(
                    (owner, function_name), ()
                ),
                call_functions=_HOLDOUT_CALL_FUNCTION_INVERSES.get(
                    (owner, function_name)
                ),
                names=_HOLDOUT_NAME_INVERSES.get((owner, function_name)),
            ),
        )

    current.body = [
        node for node in current.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    reviewed.body = [
        node for node in reviewed.body if not isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert ast.dump(current, include_attributes=False) == ast.dump(
        reviewed, include_attributes=False
    )
    return current

CAPABILITY_OWNERS = _GENERALIZATION_CAPABILITY_OWNERS | _HOLDOUT_CAPABILITY_OWNERS


def project_explicit_capabilities(
    *,
    owner: str,
    current: ast.Module,
    reviewed: ast.Module,
) -> ast.Module:
    if owner in _GENERALIZATION_CAPABILITY_OWNERS:
        return _project_explicit_generalization_capabilities(
            owner=owner,
            current=current,
            reviewed=reviewed,
        )
    assert owner in _HOLDOUT_CAPABILITY_OWNERS
    return _project_explicit_holdout_capabilities(
        owner=owner,
        current=current,
        reviewed=reviewed,
    )
