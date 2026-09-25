"""Limited one-at-a-time mechanism ablation on the unchanged production base.

The first batch is fixed before any run. Each case disables exactly one
mechanism and keeps data, fees, classification and every other rule equal.
The expected role is written down beforehand; results are reported with the
first divergent fill, never used to pick the best-looking combination.
Concentration and risk/accounting responsibilities are not ablation targets.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.engine import ProductionEngine

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ROLE: Mapping[str, str] = {
    "industry_rotation_enabled": "rotate the core book toward the leading industry",
    "confidence_sizing_enabled": "scale entry weight by signal confidence",
    "regime_factor_blend_enabled": "blend leader factors by market regime",
}


def ablated_config(mechanism: str, base: SystemConfig = DEFAULT_CONFIG) -> SystemConfig:
    """Return the production config with one reviewed mechanism switched off."""
    if mechanism not in EXPECTED_ROLE:
        raise ValueError(f"mechanism is not in the frozen ablation batch: {mechanism}")
    ablated = type(f"Without_{mechanism}", (SystemConfig,), {"__slots__": (), mechanism: False})
    config = ablated(**asdict(base))
    if not isinstance(config, SystemConfig):
        raise TypeError("ablation did not produce a SystemConfig")
    return config


def _run(cfg: SystemConfig, data_dir: Path, symbols: Sequence[str], start: str, end: str) -> dict[str, Any]:
    engine = object.__new__(ProductionEngine)
    engine._initialize(data_dir, cfg)
    return engine.backtest(symbols=symbols, start=start, end=end)


def _fills(result: Mapping[str, Any]) -> list[tuple[str, str, str, int]]:
    return [(str(f["fill_date"]), str(f["symbol"]), str(f["side"]), int(f["shares"])) for f in result["final_account"]["fills"]]


def compare(base: Mapping[str, Any], variant: Mapping[str, Any]) -> dict[str, Any]:
    left, right = _fills(base), _fills(variant)
    first = next((index for index, pair in enumerate(zip(left, right, strict=False)) if pair[0] != pair[1]),
                 None if len(left) == len(right) else min(len(left), len(right)))
    keys = ("final_wealth_multiple", "max_drawdown", "account_orders", "fees")
    return {
        "delta": {key: variant[key] - base[key] for key in keys},
        "first_divergent_fill": None if first is None else {
            "index": first,
            "base": left[first] if first < len(left) else None,
            "variant": right[first] if first < len(right) else None,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "frozen")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--mechanisms", nargs="*", default=sorted(EXPECTED_ROLE))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    base = _run(DEFAULT_CONFIG, args.data_dir, args.symbols, args.start, args.end)
    report = {
        "window": {"start": args.start, "end": args.end},
        "base_metrics": {key: base[key] for key in ("final_wealth_multiple", "max_drawdown")},
        "cases": [
            {"mechanism": name, "expected_role": EXPECTED_ROLE[name],
             **compare(base, _run(ablated_config(name), args.data_dir, args.symbols, args.start, args.end))}
            for name in args.mechanisms
        ],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
