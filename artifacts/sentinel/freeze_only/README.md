# Phase 4 Sentinel freeze-only evidence

> **权威级别：历史证据** — 本目录记录候选接受时的冻结经济证据，不描述当前 HEAD，也不
> 扩大 Sentinel 权限。当前边界见 [Risk Sentinel](../../../docs/RISK_SENTINEL.md)，
> 证据目录见[历史证据索引](../../README.md)。

Phase 4 is accepted with production default `FREEZE_ONLY`. Sentinel authority is confined to
the existing `RiskAssessment.freeze_new_risk` returned by `uquant.assess_risk()`; Phase 4 does
not change `target_gross_cap`, create a SELL or mutate trading state directly.

## Candidate result

The corrected `a/h1_2024` probe has no economic divergence from explicit Shadow:

| Metric | Shadow | Freeze-only | Delta |
|---|---:|---:|---:|
| Final wealth | 1.9042531401 | 1.9042531401 | 0 |
| Wealth retention | 100% | 100% | 0 pp |
| Max drawdown | 0.1567427757 | 0.1567427757 | 0 |
| Acute return | 0.0639067990 | 0.0639067990 | 0 |
| Account orders | 8 | 8 | 0 |
| Gross turnover | 2.0503083590 | 2.0503083590 | 0 |
| Annual turnover | 4.2048696854 | 4.2048696854 | 0 |

Four dates supplied incremental same-day Sentinel evidence, but base uquant had already frozen
new risk on every date. Sentinel-exclusive authority therefore never activated in this probe;
the first economic divergence is `null` and opportunity cost is zero. The exact compact record
is `candidate_acceptance.json`.

An earlier 97.860322% result was invalidated because integration incorrectly rewrote base
CAUTION's formal `freeze_new_risk` evidence as Sentinel authority even when Sentinel was
ineligible. A regression test now proves ineligible overlay preserves base evidence semantics.

## Promotion validation

- Phase 1: 30 official a-e/six-window cells and 15 protected cells, `passed=true`, no failures.
- Generalization: both explicit Shadow and Freeze-only passed 234/234 cells, including 6
  no-optical, 6 remove-all-core, 18 remove-one, 72 subindustry, 6 industry-balanced, 6 full and
  120 fixed-random cells; no replay errors.
- Shadow and Freeze-only Generalization have zero differences across all 234 economic/status
  records; normalized economic SHA-256 is
  `110d63d96dcff79098bf7e6903002c30971a1e1415502375216b795d8deb6706`.
- Fixed random pools: nearest-rank p10 wealth `0.9982387916`; p90 drawdown `0.2117666082`.
- Raw Freeze-only matrix SHA-256:
  `199773983526dc3c0a9fc48a4d51fa86e090c353356b2bd26b5b90c056c0857d`.
- Raw Shadow matrix SHA-256:
  `ad0d436209a5e7229c6d5bcab17c6ef8432e6485dada0dbf1d87b7cd725eca1c`.

`phase1_freeze_only_promotion.json` and `phase1_shadow_promotion.json` contain the complete
Phase 1 results. The compact Generalization summaries bind the large raw matrices without
committing their per-day/per-lot payloads.

## Account migration

`uquant account-code-migrate --account ACCOUNT --acknowledge-code-change` performs the only
authorized migration: it updates `code_hash` and appends one `code_identity_only` audit
event. `economic_state_sha256` excludes only those two identity/audit fields and must remain
identical before save, after save, and after strict reload. Cash, positions, tranches,
pending orders, order ledger, fills, peaks, opportunity/risk and lifecycle state are not
modifiable by this migration.
