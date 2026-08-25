"""Replay an AI-era causal trace from an exact clean or reviewed-patch checkout."""

# ruff: noqa: F401 - finite compatibility import-mode aliases

from __future__ import annotations

from collections.abc import Sequence
from functools import wraps
from typing import Any

from research.performance_diagnostic import PRODUCTION_FILES as _PRODUCTION_FILES
from research.performance_diagnostic import canonical_bytes as _canonical_bytes
from research.performance_diagnostic import compare_diagnostics as _owner_compare
from research.performance_diagnostic import git_command as _git
from research.performance_diagnostic import load_trace as _load_trace
from research.performance_diagnostic import main as _owner_main
from research.performance_diagnostic import performance_cli_seams
from research.performance_diagnostic import runner_provenance as _owner_runner_provenance
from research.performance_diagnostic import sha256 as _sha256
from research.performance_diagnostic import source_digest as _source_digest
from research.performance_diagnostic import source_provenance as _owner_source_provenance
from research.performance_diagnostic import source_sha256 as _source_sha256

__all__ = ("main",)


@wraps(_owner_runner_provenance)
def _runner_provenance() -> dict[str, str]:
    with performance_cli_seams(
        git_command=_git,
        runner_provenance=_owner_runner_provenance,
    ):
        return _owner_runner_provenance()


@wraps(_owner_source_provenance)
def _source_provenance(*args: Any, **kwargs: Any) -> Any:
    with performance_cli_seams(
        git_command=_git,
        runner_provenance=_owner_runner_provenance,
    ):
        return _owner_source_provenance(*args, **kwargs)


@wraps(_owner_compare)
def _compare(*args: Any, **kwargs: Any) -> Any:
    with performance_cli_seams(
        git_command=_git,
        runner_provenance=_runner_provenance,
    ):
        return _owner_compare(*args, **kwargs)


@wraps(_owner_main)
def main(argv: Sequence[str] | None = None) -> int:
    with performance_cli_seams(
        git_command=_git,
        runner_provenance=_runner_provenance,
    ):
        return _owner_main(argv)


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
