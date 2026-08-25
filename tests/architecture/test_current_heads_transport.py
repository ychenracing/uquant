from __future__ import annotations

import subprocess

import pytest

from ._analysis import ROOT
from ._cli_transport import current_heads_adapter_transport_unit_digests
from ._governance_inventory import ARCHITECTURE_REFERENCE_COMMIT

_FROZEN_RELATIVE = "scripts/run_current_heads_competitor_matrix.py"


def _frozen_source() -> str:
    return subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_COMMIT}:{_FROZEN_RELATIVE}"],
        cwd=ROOT,
        text=True,
    )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "research/current_heads_competitor_matrix.py",
            "from research.window_competitor_adapter import run_replay_task",
            "from research.window_competitor_adapter_typo import run_replay_task",
        ),
        (
            "research/current_heads_competitor_matrix.py",
            "pools={request.name: execution_symbols},",
            "pools=dict({request.name: execution_symbols}),",
        ),
        (
            "research/current_heads_competitor_matrix.py",
            'repository_root=Path(paths["repository_root"]),',
            'repository_root=Path(paths["repository_root"]).parent,',
        ),
        (
            "research/window_competitor_adapter.py",
            'repository_root / "research/window_competitor_adapter.py"',
            'repository_root / "research/window_competitor_adapter_typo.py"',
        ),
        (
            "research/window_competitor_adapter.py",
            "return _run(task)",
            "return dict(_run(task))",
        ),
    ),
)
def test_current_heads_transport_rejects_unknown_public_adapter_mutation(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    current_source = (
        ROOT / "research/current_heads_competitor_matrix.py"
    ).read_text(encoding="utf-8")
    adapter_source = (ROOT / "research/window_competitor_adapter.py").read_text(
        encoding="utf-8"
    )
    target = current_source if relative.endswith("matrix.py") else adapter_source
    assert original in target
    mutated = target.replace(original, mutation, 1)
    with pytest.raises(AssertionError):
        current_heads_adapter_transport_unit_digests(
            frozen_source=_frozen_source(),
            current_source=(mutated if relative.endswith("matrix.py") else current_source),
            current_adapter_source=(
                mutated if relative.endswith("adapter.py") else adapter_source
            ),
        )
