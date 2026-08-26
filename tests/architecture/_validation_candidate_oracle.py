"""Run frozen validation behavior collectors across approved source relocation."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

from uquant.validation import generalization_reference as policy_module
from uquant.validation import holdout, holdout_runtime

from . import _validation_oracle as frozen

_FROZEN_HOLDOUT_SOURCE_SHA256 = (
    "2879721b7aeeee947361bd4d5dbf90851c1256fad11711b5e110f28c6994c894"
)


def _frozen_generalization_binding(root: Path) -> tuple[str, str]:
    champion = json.loads(
        (root / "artifacts/phase2/champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    provenance = champion["provenance"]
    return str(provenance["head"]), str(provenance["source_sha256"])


def build_candidate_behavior(root: Path) -> dict[str, object]:
    """Collect behavior with only reviewed source-provenance inputs projected."""

    root = root.resolve()
    frozen_binding = _frozen_generalization_binding(root)
    with (
        patch.object(
            holdout,
            "holdout_source_sha256",
            return_value=_FROZEN_HOLDOUT_SOURCE_SHA256,
        ),
        patch.object(
            holdout_runtime,
            "holdout_source_sha256",
            return_value=_FROZEN_HOLDOUT_SOURCE_SHA256,
        ),
        patch.object(
            policy_module,
            "_head_and_source",
            return_value=frozen_binding,
        ),
        tempfile.TemporaryDirectory(prefix="uquant-validation-candidate-oracle-") as raw,
    ):
        temporary = Path(raw)
        failures = [
            *frozen._generalization_failures(root, temporary),
            *frozen._holdout_failures(root, temporary),
            *frozen._sentinel_failures(temporary),
        ]
        payload: dict[str, object] = {
            "success": {
                "generalization": frozen._generalization_success(root),
                "holdout": frozen._holdout_success(root, temporary),
                "sentinel": frozen._sentinel_success(root),
            },
            "failure_order": failures,
        }
    return cast(
        dict[str, object],
        json.loads(json.dumps(payload, allow_nan=False, sort_keys=True)),
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: _validation_candidate_oracle.py REPOSITORY_ROOT")
    print(
        json.dumps(
            build_candidate_behavior(Path(sys.argv[1])),
            allow_nan=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("build_candidate_behavior",)
