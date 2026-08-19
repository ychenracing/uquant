# Independent Risk Sentinel Shadow Design

## Purpose

Add an independent, read-only Risk Sentinel that evaluates causal market, industry,
holding, and existing-account evidence without participating in production decisions.
The Sentinel writes separate JSON and Markdown observations. It never creates orders,
changes account state, or feeds a result into strategy, risk, allocation, execution, or
broker code.

## Non-negotiable boundary

- Do not implement P0-1 or change concentration policy.
- Do not modify `uquant/engine.py`, `uquant/risk.py`, `uquant/portfolio.py`,
  `uquant/execution.py`, `uquant/account.py`, or production account types.
- Do not add actions, sell quantities, pending signals, account peaks, cooldowns,
  recovery state, stop-loss execution, or buy/gross-cap enforcement.
- Use only `DataStore`, the canonical point-in-time AI universe, the reviewed
  reference registry, and the common `sh000300`/`sh000682` calendar.
- Offline calibration may consume post-event rows only inside the calibration module,
  and may never read past the caller's declared evaluation end.

## Components

### Models

`SentinelLevel`, `WarmupStatus`, `CoverageHealth`,
`SubindustryEvidence`, and `SentinelAssessment` are immutable validated values.
Serialization is canonical and rejects booleans-as-numbers, NaN, infinity, invalid
confidence/cap values, duplicate reasons, or unsafe NOT_READY conclusions.

### Coverage and warmup

Coverage confidence is exactly:

`0.45 * component_observation + 0.35 * subindustry_coverage + 0.20 * held_industry_mapping`.

Warmup requires causal history for each metric, complete and fresh dual-index data,
and explicit treatment of new or stale members. Missing coverage can only lower
confidence or change health from READY to DEGRADED/NOT_READY; it cannot make the
opinion safer.

### Evidence

Each security is sliced through `as_of` before metrics are calculated. Per-security
fast return, downside status, MA20 status, and volatility are aggregated within the
point-in-time subindustry using robust medians or bounded proportions. Subindustries
then receive equal weight. This prevents a large group or one extreme member from
dominating the Sentinel.

Evidence maps once to the six existing risk families:

- `market_velocity`: dual-index fast deterioration and relative speed.
- `breadth_structure`: equal-subindustry return, downside breadth, MA20 damage,
  and synchronized deterioration.
- `covariance_stress`: median cross-member correlation and volatility expansion.
- `leadership_damage`: damage among the account's existing active leaders.
- `live_book_damage`: equal-held-name synchronous damage.
- `capital_damage`: drawdown against the account's existing capital peak only.

Unavailable account evidence is marked unavailable, never synthesized. Correlated
indicators within one family produce one family vote.

### Opinion

`evaluate_sentinel()` is deterministic, side-effect free, and accepts the exact
causal frames and point-in-time mappings named in the phase report. A NOT_READY result
has no safe gross-cap suggestion. Other cap/freeze fields are observation-only and
are never imported by production modules. The artifact records whether the same
family appeared in the account's same-day base-risk event and the earliest causal
date on which the current Sentinel family was evidenced.

### Offline calibration

The calibration contract preregisters horizons 1/3/5/10/20 and shock thresholds before
results are calculated. Calibration functions take an explicit `evaluation_end`,
slice prices through that date, and keep post-event outcome calculation outside the
service/opinion import graph. Reports include precision, recall, median lead time,
false-positive opportunity cost, missed-shock depth, CAUTION freeze opportunity cost,
and bull silence rate.

### CLI and artifacts

`python -m uquant.risk_sentinel` loads causal data and a validated account snapshot,
captures the account bytes before evaluation, and verifies them unchanged afterward.
It atomically writes deterministic JSON and Markdown artifacts with source, data,
configuration, account, runtime, universe, and calendar identities. A failed run
does not replace `latest_success`.

## Provenance and overlay

The new package is sealed as an observation-only source overlay. Phase 2 research
validation accepts only the exact registered paths and bytes while requiring every
previous production path to remain identical to its reviewed source. Production root
code fingerprint and all protected economic files remain byte-identical.

## Holdout lane

`sentinel_shadow` activates on 2026-08-19, the first real stage-3 run date. It is
appended after `champion_pre_sentinel`, uses `economic_behavior=IDENTICAL`, binds
the committed Sentinel source/config/data/runtime identities, and receives no scores
or sessions before activation.

## Acceptance

Focused tests prove causal slicing, equal-subindustry weighting, coverage fail-closed
behavior, calibration boundaries, deterministic artifacts, import isolation, and
read-only account handling. Final Engineering, Phase 1, Phase 2, current-head, and
shadow-equivalence gates must pass on one committed candidate tree. Decision digests,
opportunity, risk state, targets, pending/account orders, fills, final account,
wealth, drawdown, turnover, and acute return must be exactly unchanged.
