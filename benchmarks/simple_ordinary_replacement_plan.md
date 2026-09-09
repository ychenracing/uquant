# Simple ordinary strategy: fixed replacement decision

Frozen before current-input comparator replay, 2026-09-09.
Base: `2932e0b5f2fa14c22f585050893fb7c26c5352fc`.

## Question and scope

Can the existing `monthly_ma60_ma120_ret120_three_names_v1` comparator
justify replacing the ordinary strategy? Reuse `research/cross_ai_benchmark.py`
without changing its economic rules, production risk engine, fees or execution.
This is one comparator, not a parameter search or a new production mode.

The existing rule reviews monthly, admits liquid names with
close > MA60 > MA120 and positive trailing 120-session return, ranks by that
return, holds at most three names, and exits after two closes below MA120.
Its existing weight, industry, correlation and risk caps remain unchanged.
No strategic grants are created by the standalone comparator.

Old stage-1 results are historical evidence, not current acceptance: their
source/config/runner identities differ from current main. Do not overwrite them.

## Fixed comparison

Reuse the current production raw from the preceding closure only after verifying
its production/config/data/universe/runtime/runner identity. It has unchanged
production economics after normal reverts. Benchmark source identity is distinct.

Run a tiny real next-open sentinel first, then the following current-input
comparator scenes exactly once. Capture complete raw/account/state and recompute
wealth, drawdown, orders, costs and symbol attribution before using results.

| Scene | Interval | Minimum wealth | Maximum DD | Maximum orders |
|---|---|---:|---:|---:|
| remove_all_three | 2023-01-03 to 2026-08-05 | 2.397615989680009 | 0.30 | 22 |
| remove_all_three plus exclusion sz300666 | same | 1.0 | 0.30 | 22 |
| no_optical H1-2023 | 2023-01-03 to 2023-06-30 | 1.3753099655116032 | 0.2442425185317515 | 12 |
| full, descriptive standalone control | 2023-01-03 to 2026-08-05 | not an integration acceptance test | 0.30 | 22 |

All three removal requirements are prerequisites to production replacement.
The full standalone result cannot demonstrate preservation of strategic owners;
that requires a separate integrated replay if replacement is supported.
Integration must also preserve the prior full wealth floor 29.206146144512992,
DD <= 0.30 and orders <= 22. No existing acceptance contract is weakened.

## Decision and bounded continuation

- If the comparator clears the prerequisites, replace the ordinary path, remove
  superseded logic, verify integrated owner preservation and existing acceptance,
  and merge only if the integrated result passes and complexity does not increase.
- If it fails, diagnose the saved raw into opportunity absence, signal loss,
  capital/risk blocking or holding/exit loss. Do not tune the rule on these results
  or integrate an unproven strategy. Deliver reusable comparison support and the
  literal decision, separately from any claim of strategy improvement.
- If that diagnosis points to missing opportunities/data, distinguish a hypothesis
  from proof: this one comparator cannot establish that all existing signals fail.
- No Future Holdout, real account, new dependency, new production config, or
  repeated old matrix. Identical economic paths do not count as independent wins.
