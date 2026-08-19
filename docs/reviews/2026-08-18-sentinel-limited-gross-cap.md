# Sentinel limited gross-cap Phase 5 review

Date: 2026-08-19  
Outcome: **Rejected; production remains `FREEZE_ONLY`**

## Locked candidate and authority

The official candidate was locked before the paired official evidence:

| Effective Sentinel level | Candidate gross cap |
|---|---:|
| NORMAL | none |
| CAUTION | none |
| DEFENSIVE | 0.70 |
| CRITICAL | 0.50 |
| NOT_READY | none |

READY/high-confidence evidence requires two consecutive visible sessions; a narrow severe-direct
CRITICAL trigger may act in one session. Release requires three consecutive trusted low-risk
sessions. `apply_causal_hysteresis()` rebuilds both confirmation and repair from observations no
later than the decision date. It ignores input order, rejects duplicate dates, excludes future
rows and owns no durable state. Historical market-only assessment deliberately omits today's
holdings, leaders and account drawdown rather than backfilling them.

`uquant.assess_risk()` is the only production merge boundary. It first obtains the ordinary base
`RiskAssessment`, then returns:

```text
final cap = min(base cap, sentinel cap)
```

Sentinel cannot relax the base cap and does not change `Risk.state`, reduction level, shock owner,
capital budget, cooldown, recovery owner or any account high-water mark. It does not choose a
security or quantity and does not create an order. A binding cap is executed by the existing
`PortfolioAllocator`, `RISK_PRIORITY` reducer and sole order chain. Stable attribution is
`OriginSubsystem.RISK`, `AttributionMechanism.RISK_GROSS_CAP`, reason
`sentinel_gross_cap`, exit kind `portfolio_risk`.

No P0-1 or single-name concentration setting changed.

## First behavioral divergence

The exact Freeze-only/candidate pair first diverged on **2024-01-31**. Base cap was 1.00 and base
target gross was 0.877581; the confirmed DEFENSIVE candidate supplied 0.70, so the final cap and
target gross became 0.70. Targets and orders then diverged through the normal allocator: the
candidate's first order was an attributed `RISK_PRIORITY` SELL. This is allocator behavior caused
by the formal cap, not an order emitted by Sentinel.

## Paired economic result: pool a / h1_2024

Both sides used the same code, frozen data, symbols (`sz300308`, `sz300502`, `sz300394`) and
2024-01-02 through 2024-07-01 dates. The only economic switch was `FREEZE_ONLY` versus the locked
`LIMITED_GROSS_CAP` candidate.

| Metric | Freeze-only base | 70/50 candidate | Change |
|---|---:|---:|---:|
| Final wealth | 1.9042531401 | 1.7490263510 | -0.1552267892 |
| Wealth retention | — | 91.8484% | -8.1516 pp vs parity |
| Max drawdown | 15.6743% | 15.6743% | **0.0000 pp improvement** |
| Acute return | 6.3907% | 5.2928% | -1.0979 pp |
| Account orders | 8 | 12 | +4 |
| Gross turnover | 2.0503083590 | 3.2588161975 | +1.2085078385 |
| Annual turnover | 4.2048696854 | 6.6833349135 | +2.4784652281 |

The candidate failed wealth retention, Acute non-regression, order-count, turnover direction and
the required 1 pp MDD improvement in a
single pre-registered official cell. This window is independent of the known June/July 2026
selloff, so the result also prevents a 2026-only optimization claim.

## Counterfactual attribution

The paired artifact contains both full equity curves and three binding-cap segments.
For the full window:

| Attribution | Value |
|---|---:|
| Avoided drawdown | 0.000000 pp |
| Incremental cash-drag diagnostic | -10,038.66 |
| Observed post-release recovery cost | 109,968.63 (2 events) |
| Right-censored recovery cost | 1 event; unknown, not zero |
| Additional orders | 4 |
| Orders directly carrying `sentinel_gross_cap` | 2 |
| Additional turnover / initial equity | 1.2085078385 |
| Sentinel-attributed gross fill value | 1,232,602.16 |

Cash drag is explicitly non-accounting and can be negative when the extra cash happened to avoid a
negative next-session benchmark return. Each event separately records with-Sentinel/base curves,
start/end/release dates, avoided drawdown, cash drag, 20-session post-release recovery cost,
and non-overlapping order/turnover attribution through the next event. The exact evidence is
`artifacts/sentinel/gross_cap/a_h1_2024_paired.json`.

## Stress windows, generalization and ablation

Phase 4 Freeze-only remains the accepted base: 30/30 official cells, 15/15 protected cells and
234/234 Generalization cells passed. Its fixed-random p10 wealth was 0.9982387916 and p90 drawdown
was 0.2117666082; Shadow and Freeze-only had zero economic/status differences across all 234 cells.

The Phase 5 candidate failed mandatory non-regression gates in the first paired official replay.
Per the pre-registered stop rule, no additional official, no-optical, remove-core, random-pool,
order-permutation, capacity, gap or slippage matrix was run after that failure: none can reverse the
candidate's rejection, while continued threshold/variant search after seeing the result would
violate candidate locking. Stricter and wider research variants were therefore also not executed
or used to choose a replacement. Baseline, Shadow and Freeze-only retain the Phase 4 equivalence;
the only tested behavioral ablation is the pre-locked 70/50 candidate, which is rejected.

This is an explicit early-stop result, not missing promotion evidence and not a claim that the
Phase 5 tail passed. The accepted Generalization tail remains Freeze-only's; no Phase 5 tail metric
is substituted from it.

## Holdout Lane

The proposed lane identity is `sentinel_limited_gross_cap`, behavior `GROSS_CAP`, parent
`sentinel_freeze_only`. Because the candidate was never accepted or enabled by default, it has no
truthful activation session. The status artifact records `NOT_ACTIVATED_GATE_FAILED`, null
activation and `backfilled=false`. No lane was added to the active registry and no historical date
was fabricated.

## Engineering gates and review

The final rejected-candidate tree passed the complete engineering gate:

- Ruff: all checks passed.
- strict mypy: 83 source files, no issues.
- frozen data manifest: 36 files verified; snapshot
  `20260809T094222Z-causal-tech-index-rebase`.
- pytest: 1,429 passed in 24m22s.
- branch coverage: 85.09%, above the required 85%.
- compileall: passed for `uquant`, `scripts`, `research` and `tests`.
- package build: sdist and wheel built successfully.
- Bandit: passed with only existing justified `nosec` notices.
- pip-audit: locked production requirements contained no reported vulnerability.
- independent focused code review: approved with no blocking or important findings after causal
  recovery, unique cap authority, non-overlapping attribution and right-censoring fixes.

The complete Phase 1/Phase 2 economic matrices were intentionally not started for this rejected
tree. Repository policy requires them for a promoted final candidate; the first exact paired
official cell already made promotion impossible under the locked gates.

## Decision

Keep Phase 4 `FREEZE_ONLY` as the production default. Do not lower any baseline, retune the locked
candidate, activate a holdout lane, fast-forward `main`, or force a promotion. The Stage 5 code and
rejection evidence remain isolated on `codex/uquant-phase-5-limited-gross-cap` for audit and future
redesign.
