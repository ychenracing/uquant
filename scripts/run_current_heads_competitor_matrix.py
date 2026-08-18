#!/usr/bin/env python3
"""Build the status-preserving four-current-HEAD comparison matrix.

The execution implementation is intentionally added in a later TDD milestone;
this initial entry point freezes the adapter identity used by the source
registry and validates its two immutable input contracts.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from uquant.validation.current_heads import load_comparison_contract, load_source_registry


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the comparison contract and source registry fail closed."""

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--contract",
        type=Path,
        default=root / "benchmarks/current_heads_comparison_contract.json",
    )
    parser.add_argument(
        "--source-registry",
        type=Path,
        default=root / "benchmarks/current_heads_source_registry.json",
    )
    args = parser.parse_args(argv)
    load_comparison_contract(args.contract)
    load_source_registry(args.source_registry, adapter_path=Path(__file__).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
