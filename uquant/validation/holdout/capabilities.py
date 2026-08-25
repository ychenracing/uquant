"""Finite typed capability scopes for the two legacy holdout facades."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Protocol


class StrategySourcePaths(Protocol):
    def __call__(self, root: Path) -> tuple[Path, ...]: ...


class SourceSha256(Protocol):
    def __call__(
        self,
        paths: Sequence[Path],
        *,
        root: Path,
        from_git: str | None = None,
    ) -> str: ...


class GitStrategyRelatives(Protocol):
    def __call__(self, root: Path, *, commit: str) -> tuple[str, ...]: ...


class StrategyCliSha256(Protocol):
    def __call__(self, root: Path, *, from_git: str | None = None) -> str: ...


class RepositoryRoot(Protocol):
    def __call__(self) -> Path: ...


class ValidatePriorCloseAccount(Protocol):
    def __call__(
        self,
        account_payload: Mapping[str, Any],
        *,
        frozen_data_dir: str | Path,
    ) -> None: ...


class CurrentHoldoutBinding(Protocol):
    def __call__(
        self,
        repository_root: str | Path | None = None,
    ) -> Any: ...


class HoldoutSourceSha256(Protocol):
    def __call__(self, repository_root: str | Path) -> str: ...


class ReplayFutureHoldout(Protocol):
    def __call__(
        self,
        *,
        repository_root: str | Path,
        account_path: str | Path,
        journal_path: str | Path | None = None,
        trusted_journal_checkpoint: Any = None,
        contract: Any = None,
        lane_id: str = "champion_pre_sentinel",
    ) -> dict[str, Any]: ...


class AtomicWriteText(Protocol):
    def __call__(
        self,
        destination: str | Path,
        text: str,
        *,
        protected_paths: Iterable[str | Path] = (),
    ) -> None: ...


class ArtifactBundleLock(Protocol):
    def __call__(
        self,
        repository_root: Path,
        carrier_paths: Sequence[Path] = (),
    ) -> AbstractContextManager[None]: ...


class ReadProtectedArtifact(Protocol):
    def __call__(self, path: str | Path, *, label: str) -> bytes: ...


class OSAdapter(Protocol):
    name: str


@dataclass(frozen=True, slots=True)
class HoldoutFacadeCapabilities:
    ai_era_windows: Mapping[str, tuple[str, str]]
    required_future_holdout_sha256: str
    strategy_account_code_sha256: str
    prior_close_account_sha256: str
    strategy_source_paths: StrategySourcePaths
    source_sha256: SourceSha256
    git_strategy_relatives: GitStrategyRelatives
    strategy_cli_sha256: StrategyCliSha256
    repository_root: RepositoryRoot
    validate_prior_close_account: ValidatePriorCloseAccount
    current_holdout_binding: CurrentHoldoutBinding


@dataclass(frozen=True, slots=True)
class HoldoutRuntimeCapabilities:
    holdout_source_sha256: HoldoutSourceSha256
    validate_prior_close_account: ValidatePriorCloseAccount
    replay_future_holdout: ReplayFutureHoldout
    atomic_write_text: AtomicWriteText
    artifact_bundle_lock: ArtifactBundleLock
    read_protected_artifact: ReadProtectedArtifact
    os_adapter: OSAdapter


_FACADE_CAPABILITIES: ContextVar[HoldoutFacadeCapabilities | None] = ContextVar(
    "uquant_holdout_facade_capabilities",
    default=None,
)
_RUNTIME_CAPABILITIES: ContextVar[HoldoutRuntimeCapabilities | None] = ContextVar(
    "uquant_holdout_runtime_capabilities",
    default=None,
)


def holdout_facade_capabilities() -> HoldoutFacadeCapabilities | None:
    return _FACADE_CAPABILITIES.get()


def holdout_runtime_capabilities() -> HoldoutRuntimeCapabilities | None:
    return _RUNTIME_CAPABILITIES.get()


@contextmanager
def holdout_facade_scope(capabilities: HoldoutFacadeCapabilities) -> Iterator[None]:
    token = _FACADE_CAPABILITIES.set(capabilities)
    try:
        yield
    finally:
        _FACADE_CAPABILITIES.reset(token)


@contextmanager
def holdout_runtime_scope(capabilities: HoldoutRuntimeCapabilities) -> Iterator[None]:
    token = _RUNTIME_CAPABILITIES.set(capabilities)
    try:
        yield
    finally:
        _RUNTIME_CAPABILITIES.reset(token)


def scoped_capability_wrapper[**Parameters, Result, Capabilities](
    function: Callable[Parameters, Result],
    *,
    capabilities: Callable[[], Capabilities],
    scope: Callable[[Capabilities], AbstractContextManager[None]],
) -> Callable[Parameters, Result]:
    @wraps(function)
    def delegated(*args: Parameters.args, **kwargs: Parameters.kwargs) -> Result:
        with scope(capabilities()):
            return function(*args, **kwargs)

    return delegated


__all__ = (
    "HoldoutFacadeCapabilities",
    "HoldoutRuntimeCapabilities",
    "holdout_facade_capabilities",
    "holdout_facade_scope",
    "holdout_runtime_capabilities",
    "holdout_runtime_scope",
    "scoped_capability_wrapper",
)
