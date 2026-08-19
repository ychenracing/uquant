# Risk Sentinel causal timeline design

## Goal

Add point-in-time, reproducible market-evidence histories for base uquant risk and
Risk Sentinel, then render the diagnostics in the existing daily report without
changing any economic decision, account state, target, order, fill, or gross cap.

## Authority boundary

- Base risk remains the only owner of risk state, gross caps, reduction levels,
  shock state, account high-water marks, cooldowns, recoveries, and sell behavior.
- Sentinel remains limited to the existing `freeze_new_risk` integration boundary.
- `risk_sentinel_causal_confirmation_enabled` defaults to `False`; Phase 6 timeline
  fields are diagnostic and cannot create new Sentinel production authority.
- `live_book_damage` and `capital_damage` may describe the current assessment only.
  They never enter historical rows, confirmation, repair, first-date, incremental,
  or earlier-family calculations.

## Shared base evidence

`uquant.risk` exposes one pure market-family snapshot function used by both the
base assessor and the timeline. It owns the existing thresholds for
`market_velocity`, `breadth_structure`, and `covariance_stress`; the history module
must not copy those thresholds. Leadership is omitted from trusted history because
the current base implementation depends on account tenure.

## Timeline and cache

`uquant.risk_sentinel.history` builds immutable rows from the first common warmed-up
market session through `as_of`. Every row resolves the canonical universe and
industry at that date, truncates all frames at that date, and evaluates Sentinel
without holdings, account drawdown, or calibration imports. A pure fold derives
two-session confirmation and three-session repair; NOT_READY, DEGRADED, or a
missing common session breaks continuity and trust.

`ProductionEngine` owns a process-local immutable cache keyed by the complete data
prefix/config identity. The cache contains no account fields and never enters
`AccountState`. Daily and replay take an `as_of` prefix from the same cached
timeline.

## Daily report and mode closure

The existing report gains one compact `Risk Sentinel` section sourced only from
the already-computed decision diagnostics. Freeze owner is one of `NONE`,
`BASE_RISK`, `SENTINEL`, `BOTH`, or `DATA_NOT_READY`. Phase 6 can render every enum
for compatibility tests, but default production cannot gain a new Sentinel-only
freeze from causal history.

Production configuration accepts only `SHADOW` and `FREEZE_ONLY`.
`LIMITED_GROSS_CAP` raises: `LIMITED_GROSS_CAP was rejected by the economic gate;
use FREEZE_ONLY or SHADOW.` The Stage 5 rejection is retained only as a compact
JSON record and review note; no Stage 5 source or equity curves are merged.

## Acceptance

Focused tests prove future-data isolation, point-in-time membership and industry,
input-order determinism, account-history isolation, full-rebuild/cache-prefix
identity, confirmation/repair continuity, report determinism, and mode rejection.
The final committed tree must pass Engineering, Phase 1, and all six Phase 2
windows, with exact equality for decisions, risk controls, targets, event IDs,
orders, fills, account economics, wealth, drawdown, orders, turnover, and acute
return. A code-identity-only account migration must preserve the economic hash.
