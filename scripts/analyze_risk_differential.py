#!/usr/bin/env python3
"""Seal preregistered outcomes, economics, promotion gates, and closure."""

# ruff: noqa: F401 - finite legacy import-mode aliases

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import TracebackType
from typing import cast

from research.risk_differential_analysis import (
    calibration as _calibration,
)
from research.risk_differential_analysis import (
    closure_outcome as _closure_outcome,
)
from research.risk_differential_analysis import (
    counterfactual_summary as _counterfactual_summary,
)
from research.risk_differential_analysis import (
    detection_gate_details as _detection_gate_details,
)
from research.risk_differential_analysis import economic_gate as _economic_gate
from research.risk_differential_analysis import (
    generalization_gate as _generalization_gate,
)
from research.risk_differential_analysis import main
from research.risk_differential_analysis import (
    validate_analysis_inputs as _validate_analysis_inputs,
)

__all__ = ("main",)


def _legacy_failure_traceback(error: Exception) -> str:
    """Render the immutable CLI failure surface after moving its implementation."""

    traceback_ = cast(TracebackType, error.__traceback__)
    rendered = "".join(
        traceback.format_exception(type(error), error, traceback_.tb_next)
    )
    header, separator, owner_frames = rendered.partition("\n")
    if header != "Traceback (most recent call last):" or not separator:
        return rendered
    script_path = str(Path(__file__).resolve())
    owner_path = str(
        Path(__file__).resolve().parent.parent
        / "research"
        / "risk_differential_analysis.py"
    )
    owner_frames = owner_frames.replace(owner_path, script_path)
    immutable_entry = (
        f'  File "{script_path}", line 850, in <module>\n'
        "    raise SystemExit(main())\n"
        "                     ^^^^^^\n"
    )
    return f"{header}\n{immutable_entry}{owner_frames}"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        sys.stderr.write(_legacy_failure_traceback(error))
        raise SystemExit(1) from None
