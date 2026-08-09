"""Immutable benchmark universes shared by new and frozen legacy replays."""

from __future__ import annotations

FIXED_POOL_SIZES = (1, 3, 5, 9, 15, 22, 32)
PRIMARY_POOL_SIZES = (3, 5, 9, 15, 22, 32)

_FULL_UNIVERSE = (
    "sz300308",
    "sz300502",
    "sz300394",
    "sh688498",
    "sz002281",
    "sh601869",
    "sh600487",
    "sh688256",
    "sh688041",
    "sh688008",
    "sh603986",
    "sz300223",
    "sh688110",
    "sh688766",
    "sz002371",
    "sh688012",
    "sh688072",
    "sh688082",
    "sh688120",
    "sh688037",
    "sh688361",
    "sz300604",
    "sh688200",
    "sh688019",
    "sz300054",
    "sz002409",
    "sz300666",
    "sh688233",
    "sh688268",
    "sh688146",
    "sh688300",
    "sh603688",
)

POOLS: dict[str, tuple[str, ...]] = {
    "single": _FULL_UNIVERSE[:1],
    "a": ("sz300308", "sz300502", "sz300394"),
    "b": (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688008",
        "sh603986",
    ),
    "c": (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688008",
        "sh603986",
        "sz002409",
        "sh688072",
        "sh688300",
        "sz300054",
    ),
    "d": (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688498",
        "sh601869",
        "sh688256",
        "sh688008",
        "sh603986",
        "sh688072",
        "sh688082",
        "sh688120",
        "sh688300",
        "sz300054",
        "sh688361",
        "sz300604",
    ),
    "f22": _FULL_UNIVERSE[:22],
    "e": _FULL_UNIVERSE,
}

if tuple(sorted(len(symbols) for symbols in POOLS.values())) != FIXED_POOL_SIZES:
    raise RuntimeError("fixed benchmark pool sizes do not match the acceptance contract")

# The one-name replay is a required boundary/causality cell, not a production
# portfolio.  Section 26 defines primary Bull and Random sizes as
# 3/5/9/15/22/32, so only those pools feed portfolio-performance gates.
PRIMARY_POOLS: dict[str, tuple[str, ...]] = {
    name: symbols for name, symbols in POOLS.items() if len(symbols) > 1
}

if tuple(sorted(len(symbols) for symbols in PRIMARY_POOLS.values())) != (
    PRIMARY_POOL_SIZES
):
    raise RuntimeError("primary benchmark pool sizes do not match the acceptance contract")
