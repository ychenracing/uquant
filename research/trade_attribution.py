"""Offline adapters over the canonical bounded production attribution API.

This module owns no accounting or price-horizon implementation. Research
callers use the same structured event identity and ``economic_end`` guard as
production reports.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

from uquant.attribution import ExitRecord, post_exit_diagnostics

__all__ = ["ExitRecord", "aggregate_by_mechanism", "attribute_exits"]


def attribute_exits(
    exits: Iterable[ExitRecord],
    prices: Mapping[str, pd.Series],
    *,
    economic_end: str,
    horizons: Iterable[int] = (5, 10, 20, 40),
) -> list[dict[str, Any]]:
    """Delegate bounded post-exit measurement to the production API."""

    return post_exit_diagnostics(
        exits=tuple(exits),
        prices=prices,
        economic_end=economic_end,
        horizons=tuple(horizons),
    )


def aggregate_by_mechanism(
    attributions: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, float | int]]:
    """Aggregate finite diagnostic horizons by structured mechanism identity."""

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in attributions:
        mechanism = item.get("mechanism")
        horizons = item.get("horizons")
        if not isinstance(mechanism, str) or not mechanism or not isinstance(horizons, Mapping):
            raise ValueError("post-exit attribution has malformed mechanism or horizons")
        grouped.setdefault(mechanism, []).append(item)
    output: dict[str, dict[str, float | int]] = {}
    for mechanism, items in sorted(grouped.items()):
        avoided_by_horizon: dict[str, list[float]] = {}
        regret_by_horizon: dict[str, list[float]] = {}
        for item in items:
            horizons = item["horizons"]
            if not isinstance(horizons, Mapping):  # pragma: no cover - validated above
                raise ValueError("post-exit attribution horizons are malformed")
            for horizon, value in horizons.items():
                if value is None:
                    continue
                if not isinstance(value, Mapping):
                    raise ValueError("post-exit attribution horizon is malformed")
                avoided_by_horizon.setdefault(str(horizon), []).append(float(value["avoided_loss"]))
                regret_by_horizon.setdefault(str(horizon), []).append(float(value["regret"]))
        avoided = [value for values in avoided_by_horizon.values() for value in values]
        regret = [value for values in regret_by_horizon.values() for value in values]
        bucket: dict[str, float | int] = {
            "count": len(items),
            "mean_avoided_loss": sum(avoided) / len(avoided) if avoided else 0.0,
            "mean_regret": sum(regret) / len(regret) if regret else 0.0,
        }
        for horizon, values in sorted(avoided_by_horizon.items(), key=lambda item: int(item[0])):
            bucket[f"mean_avoided_loss_{horizon}d"] = sum(values) / len(values)
        for horizon, values in sorted(regret_by_horizon.items(), key=lambda item: int(item[0])):
            bucket[f"mean_regret_{horizon}d"] = sum(values) / len(values)
        output[mechanism] = bucket
    return output
