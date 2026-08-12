# 2025-2026 Target-Window Outperformance Design

**Date:** 2026-08-12  
**Status:** Approved by the user's request to implement all three workstreams  
**Target interval:** 2025-01-02 through 2026-07-31, inclusive

## Objective

Make uquant outperform the frozen qwenquant, AQuant, and trade implementations on every promotion pool A-E during the target interval. The comparison must use one execution contract and one frozen data snapshot. A result is not considered complete merely because an aggregate score improves.

## Non-negotiable comparison contract

- Initial cash: CNY 2,000,000 for every system/pool cell.
- Signal timing: close at `t`; earliest execution at the next tradable open.
- No intraday exits or future/pre-listing visibility.
- Account orders are netted by `(fill_date, symbol, side)`; virtual sleeves do not count as separate broker orders.
- All systems use the same frozen symbol panels and the same A-E pool definitions.
- Source commit, Python-source hash, data fingerprint, pool hash, and adapter version are recorded in the artifact.
- Missing roots, source-hash drift, missing dates, ambiguous signal/fill linkage, or incomplete cells fail closed.

The exact matrix contains 20 cells: four systems times five pools. Each cell records final wealth, maximum drawdown, account-order count, turnover, and acute-window return. The acute window is derived from the target interval and recorded explicitly in the artifact; no system-specific window is permitted.

## Definition of “全面超越”

For each pool A-E and each competitor, uquant must satisfy all of the following on the exact target interval:

1. final wealth is no lower;
2. maximum drawdown is no higher;
3. account-order count is no higher;
4. acute-window return is no lower;
5. at least one of the four metrics is strictly better.

The final report must show every pairwise predicate. A weighted average, rank sum, or strength in another pool cannot cancel a losing predicate. If any predicate remains false, the result is reported as not yet achieved.

## Workstream 1: exact competitor matrix

Restore the deleted research-only common adapter outside the production package and port it to the current repository layout. Add a target-window mode that emits exactly 15 frozen-competitor cells. Join those with five fresh uquant cells under a schema validator that requires all 20 unique cells. Keep competitor imports and subprocess execution isolated from production code.

## Workstream 2: D-pool Alpha ownership and rearm

Use causal daily traces to find the first date where D loses an economically valid entry that the stronger A/E path takes. The diagnosis must distinguish:

- evidence present or absent;
- selected owner/sleeve;
- exposure/risk budget;
- cooldown and rearm state;
- order suppression reason.

The repair must be general lifecycle logic. It may release stale ownership or rearm a valid cohort when observable evidence changes, but may not key on pool name, pool length, symbol, calendar date, or target-window membership. The gate is not “D improves”: D must cease missing the identified entry while A, B, C, and E preserve their qualifying paths and order discipline.

## Workstream 3: acute-crash defense without Alpha destruction

Risk control and Alpha ownership are separate state dimensions. An acute defense may reduce executable exposure when causal shock evidence appears, but it must preserve the cohort's ownership/rearm information so recovery does not require rediscovery from a blank state. The smallest candidate that addresses the first acute-loss divergence is preferred.

Rejected designs are not retried:

- the prior multi-evidence CRISIS mode that reduced wealth without improving drawdown;
- global removal of universe-size compatibility behavior;
- a forced 50% high-confidence young-cohort account cap;
- large parameter sweeps, copied competitor sleeves, or baseline weakening.

## Acceptance and safety gates

The implementation is accepted only if:

- the exact 20-cell artifact is complete and provenance-valid;
- all 15 pairwise pool/competitor comparisons pass the four-metric predicate;
- uquant's point-in-time and execution semantics remain valid;
- daily/batch equivalence tests pass;
- the complete automated test suite passes;
- no forbidden pool/symbol/date special case is present;
- the final artifact can be reproduced from documented commands.

The user's narrowed time horizon means older full-cycle wealth is diagnostic rather than a blocking economic gate. It does not relax correctness, provenance, or causal-timing tests.

## Rollback policy

Each production candidate is a small, separately testable change. A candidate that fails the target-window gate is removed rather than hidden behind a favorable aggregate. Research artifacts and failed-candidate notes remain available for audit, but failed production logic is not retained.
