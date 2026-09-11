"""Dated original-quarter priority among otherwise eligible core candidates."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path


def order_known_candidates(candidates: list[str], growth: dict[str, float]) -> list[str]:
    """Missing data keeps its original slot; equal values keep technical order."""
    ranked = iter(sorted((s for s in candidates if s in growth), key=lambda s: -growth[s]))
    return [next(ranked) if s in growth else s for s in candidates]


@lru_cache(maxsize=1)
def _reports() -> tuple[tuple[str, int, str, float | None], ...]:
    path = Path(__file__).resolve().parents[1] / 'contracts/resources/quarter_revenue_v1.json'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != 'a96c1c2e2b46eed0ccf1a5df13ee5856e47154e4c7e6ed733b8a1cfbeb21b9ab':
        raise ValueError('quarter revenue source differs from reviewed version')
    return tuple((str(row[0]), int(row[1]), str(row[2]),
                  None if row[3] is None else float(row[3])) for row in json.loads(raw)['rows'])


def quarter_priority(candidates: list[str], as_of: str) -> list[str]:
    latest: dict[str, tuple[int, float | None]] = {}
    for symbol, period, disclosed, growth in _reports():
        if symbol in candidates and disclosed < as_of and (symbol not in latest or period > latest[symbol][0]):
            latest[symbol] = (period, growth)
    return order_known_candidates(candidates, {s: value for s, (_, value) in latest.items() if value is not None})
