"""Offline integrity checks for Independent Risk Sentinel contracts."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from uquant.validation.universe import load_ai_universe

from .calibration import load_calibration_contract
from .provenance import legacy_sentinel_source_fingerprint

_FORBIDDEN_IMPORTS: Final = (
    "uquant.engine",
    "uquant.execution",
    "uquant.portfolio",
    "uquant.risk",
    "uquant.risk_sentinel.calibration",
)
_OFFLINE_MODULES: Final = {"calibration.py", "validation.py"}


def validate_contracts(repository_root: str | Path | None = None) -> dict[str, object]:
    """Validate sealed inputs and prove live evaluation import isolation."""

    root = (
        Path(repository_root).resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    universe = load_ai_universe()
    calibration = load_calibration_contract()
    package = root / "uquant" / "risk_sentinel"
    violations: list[str] = []
    for path in sorted(package.glob("*.py")):
        if path.name in _OFFLINE_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 1 and node.module == "calibration":
                    names = ("uquant.risk_sentinel.calibration",)
                elif node.level == 0 and node.module is not None:
                    names = (node.module,)
            violations.extend(
                f"{path.relative_to(root)}:{name}"
                for name in names
                if any(
                    name == item or name.startswith(f"{item}.")
                    for item in _FORBIDDEN_IMPORTS
                )
            )
    if violations:
        raise RuntimeError(f"Sentinel import isolation failed: {sorted(violations)}")
    return {
        "calibration_contract_sha256": calibration.sha256,
        "import_isolation": "PASS",
        "sentinel_source_sha256": legacy_sentinel_source_fingerprint(root),
        "universe_sha256": universe.sha256,
    }
