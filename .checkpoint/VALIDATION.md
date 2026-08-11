# Validation record

Recorded at `2026-08-11T01:12:20Z`.

## Stage A

- Relevant validation tests: 72 passed.
- Engineering suite used for coverage: 478 passed; total coverage 85.05%.
- Ruff: passed.
- Mypy strict, no incremental cache: 36 source files passed.
- Bandit at the unchanged configured scan level: passed with zero findings.
- Python bytecode compilation: passed.
- Frozen `DATA_MANIFEST.json`, `SHA256SUMS`, and 36 CSV payload hashes: passed.
- Wheel and sdist build: passed.
- Source/build diff check: passed.
- Local checkpoint commit: `2c3f7f0db7360724f6aed941cae745d97670411e`.
- Worktree after checkpoint and failed-experiment rollback: clean.

## Economic gate and bounded diagnosis

- Accepted best D continuous result remains: 35.057289x wealth, 27.7826% maximum drawdown, 85 orders.
- Hard gates: wealth >= 60.59x (failed), drawdown <= 31% (passed), orders <= 100 (passed).
- Diagnostic replay identified an ownerless interval after strategic exit while the capital-budget ladder repaired.
- The single candidate strategy repair preserved the freeze and all thresholds, but pre-confirmed generic leader evidence during the final repair rung.
- Candidate replay: 11.818406x wealth, 29.7982% maximum drawdown, 110 orders, 6.9135 annual turnover.
- The candidate entered three generic leaders at 0.80 gross on 2025-02-25, refroze on 2025-02-28, moved to RISK_OFF on 2025-03-03, and reached CRISIS in April.
- This exactly reproduced the wealth result of the previously rejected capital-budget reset family. The production and test changes were fully rolled back.
- Two D multi-year replays were consumed (one diagnostic baseline and one candidate); no further long replay was run.

## Release status

- Stage C was not run because the D wealth hard gate failed.
- `main` was not modified or pushed.
- The original backup branch remains untouched.
