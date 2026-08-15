# Phase 2 Task 7 — Fail-closed independent ablation infrastructure

## Scope and status

This task builds the ablation registry, isolated carrier materialization, sequential
runner, raw evidence schema, and runnability proof. It does not make a
KEEP/DELETE/INCONCLUSIVE decision and does not delete production code; those actions
belong to Task 8.

Implementation checkpoint status: the infrastructure and carrier-validation evidence
are complete. The clean-HEAD baseline plus 13 full-contract replays are pending at this
checkpoint and will be appended below after their single-process execution. A partial
checkpoint can never be emitted as complete evidence.

The reviewed production source remains anchored to
`7f80436373b6da03536e15ff1908c010bfb92eb3`. No frozen champion, promotion baseline,
generalization baseline, generalization policy, seed, window, market rule, or safety
rule was edited.

## Immutable registry

Registry artifact:
`artifacts/phase2/ablations/registry.json`

- Registry canonical SHA-256:
  `71787c3fb1bf874bf111f83a4123f225d4aad1cc895b67ee1bf5b5ba62810370`
- Reviewed production source SHA-256:
  `f4bbcec1342d9ff3f5e606c5ba54a05ee839b5b94a3e1110830e49afb2244bc9`
- Runnable experiments: 13, each with one unique reviewed carrier hash.
- Explicit inactive compatibility exclusions: 2.

| Experiment | Subsystem | Carrier | Exact change/path | Reviewed carrier SHA-256 |
|---|---|---|---|---|
| `without_sector_guard` | sector guard | config | `sector_guard_enabled=false` | `618f5b6a2163307d454e3b4d22eb9e0e16157524b488b023417b0c6f3da57886` |
| `without_chronic_overlay` | chronic overlay | config | `chronic_overlay_enabled=false` | `eb148771589c2caaac98c153d5145302e0ae343eef9251a38bec99382ac7a42d` |
| `without_transition_overlay` | transition overlay | config | `transition_overlay_enabled=false` | `c10391f7105895f540858734ed5813c13de0ea2242b8207c9b2c66e466ac2a7a` |
| `without_capital_budget_ladder` | capital budget ladder | config | `capital_budget_ladder_enabled=false` | `ec612c0e18ddfaf94fbe2a4153b6319d9901e2d420a5b84e67735b28ff4b95e5` |
| `without_challenger_scout` | challenger scout | config | `challenger_scout_enabled=false` | `76ab5fbc8d734e489f484cfebba28a66f39f5b3c6836408976eda348a67c6f24` |
| `without_conviction_weighting` | conviction weighting | config | `conviction_weighting_enabled=false` | `50d0eeed080060b4b370bf2e7cda87060f7ffcaf9175cb6e436af54a5eded253` |
| `without_recovery_conviction_weighting` | recovery conviction weighting | config | `recovery_conviction_weighting_enabled=false` | `d7090c7ead3cb8f0c1472c45e9184a712df1a72b3bcd1d6b6fde8608249d0ea2` |
| `without_tactical_rebound_probe` | tactical rebound/probe | patch | `uquant/portfolio.py` | `a30417ed0d9afd9d6dc99b24729971c258d4f744b8e276c0dfa7291c5e324a1b` |
| `without_strategic_trailing` | strategic trailing | patch | `uquant/portfolio_strategic.py` | `87ef953f34f36f62cec49500e25ef0dd64eb6469adda6e2074b672ddf7619cb8` |
| `without_restoration_special_handling` | restoration special handling | patch | `uquant/portfolio_strategic.py` | `9eb03bdd4d39493f00eea578d90fdf4816b8716e16be85c185c56306e9508b74` |
| `without_add_tranche` | add tranche | patch | `uquant/portfolio_leaders.py` | `601ddb6ddfd7fdd358012e71e2d181de4a5b2abe15104a36c9d94c9fec7e3986` |
| `without_replacement_rotation` | replacement/rotation | patch | `uquant/portfolio_leaders.py` | `a2c0262f63bd8de2f5a11515bfeb8afbf3a34e03a503eeb72371d24f33c6160d` |
| `without_dynamic_risk_anchors` | dynamic risk anchors | config | `dynamic_risk_anchors_enabled=false` | `17a7273e35ca7a3e36300536f592e85ce3575147291e45f04ad253efd96ec7b2` |

The frozen configuration has
`hierarchical_industry_shrinkage_enabled=false` and
`group_balanced_reference_enabled=false`. The registry records
`hierarchical_industry_shrinkage` and `group_balanced_reference` as
`inactive_in_frozen_config`; it does not manufacture no-op experiments or claim a
behavior divergence for inactive compatibility switches.

Carrier hashes are independently anchored in code. Editing a patch and recomputing its
self-declared JSON hash is rejected. Patch carriers are applied with
`git apply --check`, are limited to the single reviewed path, are committed with fixed
identity/time in a detached worktree, and are reverified clean after replay. Config
carriers retain the exact baseline source tree and apply exactly one false override.

The protected rules are T+1, price limits, suspension, lot sizing, fees/slippage,
data causality, cash constraints, PIT, and fail-closed validation. Account, broker,
config governance, data, execution, risk, sector risk, and validation paths are not
patchable carriers. The registry also prevents the eight carrier config fields from
overlapping protected accounting, execution, capacity, and fail-closed fields.

## Fixed execution contracts

The schedule is reconstructed from hashed frozen artifacts without resampling:

| Contract | Records | Economic | Valid | Known replay error | Insufficient |
|---|---:|---:|---:|---:|---:|
| Phase 1 Performance | 45 | 45 | 45 | 0 | 0 |
| AI-era Generalization | 234 | 192 | 191 | 1 | 42 |
| Combined per baseline/carrier | 279 | 237 | 236 | 1 | 42 |

The Generalization contract retains base seed `20260810`, seed indexes `0..4`, pool
sizes `5, 9, 15, 20`, a 120-session lookback, the six fixed 2023+ windows, all frozen
scenario memberships, the one known `REPLAY_ERROR`, and all 42
`INSUFFICIENT_SAMPLE` records. It does not hide, repair, or re-sign the frozen state.
The 19 frozen policy failures remain in the untouched frozen evidence/policy; Task 7
does not reinterpret them.

Fixed artifact SHA-256 anchors:

- `benchmarks/promotion_baseline.json`:
  `b3067ae1bde683d832f9593d80eeea2616d1c934f41291e85ab36d9a6a695bc2`
- `benchmarks/ai_era_generalization_baseline.json`:
  `2a463f9f7ea63fc01564089af96399f1bdf3ff2023414c9c9a9935e09e2e9c10`
- `benchmarks/ai_era_generalization_policy.json`:
  `15d0ed3746fd7c223aa89edbff26a97b7de7c0a7f9763f168e8fa93a97f5dda3`
- `artifacts/phase2/champion-generalization-matrix.json`:
  `926ea8419ab8aad7a05577eee56aeefa90c33cc7faa4e1ee1d2bbbaac77439cc`

## Runner and evidence schema

`scripts/run_phase2_ablation.py` exposes:

- `validate`: materialize and import every carrier in an isolated clean checkout,
  verify fresh account construction, and emit deterministic provenance.
- `run --baseline-only`: replay the exact baseline once into an atomic,
  content-addressed checkpoint.
- `run --experiment EXPERIMENT_ID`: execute exactly one major subsystem carrier in
  one process, compare it with the bound baseline, and atomically checkpoint it.
- hidden `worker`: import production only from the detached checkout, replay the
  exact content-addressed schedule sequentially, and emit raw cell metrics and hashed
  causal traces. It is an internal subprocess boundary, not a second strategy.

Every run binding includes registry, reviewed base/source, clean orchestrator HEAD,
fixed contracts, schedule hash, baseline config fingerprint, data snapshot, Python,
NumPy, pandas, uv, platform, runner, and `uv.lock`. Workers run with
`PYTHONHASHSEED=0`, single-thread numerical-library limits, one process, and a fresh
`AccountState.empty` inside each unchanged production backtest. A checkout is checked
clean both before and after the worker.

The baseline checkpoint is reused only when its entire binding and content hash match.
A variant checkpoint is bound to exactly one experiment and carrier; it is not reused
as another variant. `--rerun` must reproduce the exact prior content or fails closed.
The aggregator succeeds only with all 13 distinct worker hashes, exact 13/13 registry
identity, exact 279-record/status coverage for every experiment, all nine raw delta
dimensions, aggregate valid counts, and a required behavior divergence. Until then it
emits `complete=false` progress only.

Per valid cell, the evidence carries variant-minus-baseline final wealth, max drawdown,
account orders, acute return when applicable, gross and annual turnover, top-1 and
top-3 PnL concentration, and PnL HHI. Aggregates retain medians, worst values, p10
wealth/acute return, p90 drawdown/orders/turnover, and worst concentration. First
divergence is the earliest date across all fixed cells, with causal-stage hashes ordered
as reference context, leaders, risk, opportunity, targets, orders, and fills. There is
no materiality threshold or Task 8 classification in the schema.

## TDD evidence

All commands used `UV_CACHE_DIR=/tmp/uquant-uv-cache` because the default root uv
cache is not writable in this execution sandbox.

| Cycle | RED evidence | GREEN evidence |
|---|---|---|
| Core evidence types | Focused collection failed importing missing `AblationCell`. | Core focused set: `8 passed`. |
| Frozen schedule and isolation | Focused collection failed importing missing `build_contract_schedule`. | Schedule plus config/patch materialization set: `11 passed`. |
| Carrier validation CLI | Deterministic validation test failed because `scripts/run_phase2_ablation.py` did not exist. | Validation double-run test passed; all 13 carriers materialized/imported. |
| Baseline and divergence | Focused tests failed on missing exact baseline checkout and hashed divergence APIs. | Both tests: `2 passed`. |
| Real replay cell | Focused test failed on missing `_replay_cell`. | Real `ProductionEngine` cell: `1 passed` in about 2.4 seconds. |
| Raw comparison | Focused test failed on missing `_compare_worker_payloads`. | Per-cell/aggregate/first-divergence comparison: `1 passed`. |
| Orchestration schema | Two tests failed on missing `_validate_worker_payload` and `_write_checkpoint`. | Both: `2 passed`. |
| Complete aggregation | Test failed on missing `_validate_experiment_checkpoints`. | Exact 13-carrier identity test: `1 passed`. |
| Serialized worker regression | A real detached-checkout smoke replay completed, but validation rejected canonical JSON because object keys were reordered. | Canonical round-trip regression and real subprocess smoke both passed (`1 cell`, `1 trace`). |
| Final self-review hardening | Four tests failed for a re-sealed patch, cross-cell date ordering, invalid concentration, and missing exact checkpoint schedule coverage. | All four: `4 passed`. |
| Raw-dimension aggregation hardening | Missing one checkpoint delta dimension was incorrectly accepted. | Exact nine-dimension validator test: `1 passed`. |

Representative final focused command and output:

```text
UV_CACHE_DIR=/tmp/uquant-uv-cache uv run pytest -q tests/test_phase2_ablation.py
...................                                                      [100%]
```

## Carrier-validation evidence

Deterministic command, executed twice with byte-identical output:

```text
UV_CACHE_DIR=/tmp/uquant-uv-cache uv run python scripts/run_phase2_ablation.py validate \
  --source-root . \
  --registry artifacts/phase2/ablations/registry.json \
  --data-dir data/frozen \
  --output /tmp/task7-validation-a.json
cmp /tmp/task7-validation-a.json /tmp/task7-validation-b.json
```

- Validation artifact file SHA-256:
  `cbe5779650f8371591e63426d8ab2f1c54688476588f58c84fe3605011dfeadf`
- Bound runner SHA-256:
  `6a2d08646a15acfb2dddde4250393defdc7810711679c5846aaae7e9e2cf9b6c`
- `uv.lock` SHA-256:
  `4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61`
- Data snapshot: `20260809T094222Z-causal-tech-index-rebase`
- Data manifest SHA-256:
  `343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d`
- Checksums SHA-256:
  `ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29`
- Verified data files: 36
- Runtime: Python `3.12.13`, NumPy `2.5.1`, pandas `3.0.5`, uv `0.11.33`
- Coverage: 13/13 runnable carriers; 279 scheduled records and 237 economic
  attempts per baseline/carrier contract.

## Full-contract replay evidence

Pending clean-HEAD execution. The approved resumable plan is:

1. Commit the runner and this registry/report checkpoint; require a clean exact HEAD.
2. Run `--baseline-only` once into a checkpoint directory outside the worktree.
3. Run each of the 13 exact `--experiment` commands sequentially in one process, one
   carrier per command; never run variants in parallel.
4. Record per-command wall time, 45+234/237 coverage, worker/checkpoint hash, and exact
   replay command here after each completion.
5. Accept the final artifact only when the runner emits `complete=true` with 13/13
   authenticated checkpoints and a required first divergence for every carrier.

No partial result in this section is a completion claim.

## Verification

Current pre-replay verification:

- Focused Task 7 tests: `19 passed`.
- Full repository pytest after final self-review hardening: exit `0`; 983 tests
  collected.
- Task 7 Ruff format/check: passed.
- Task 7 strict mypy:
  `uv run mypy research/ablation.py research/ablation_registry.py scripts/run_phase2_ablation.py`
  passed with `Success: no issues found in 3 source files`.
- `git diff --check`: passed.

Expanded whole-repository strict mypy reports one pre-existing error at
`scripts/run_pareto_evidence.py:143` (a `Mapping[str, Any]` assigned after a branch
inferred as `dict[str, Any]`). The same command against a detached exact
`7f80436373b6da03536e15ff1908c010bfb92eb3` checkout reports the identical single
error across 59 source files. It is outside Task 7 and was not changed. This is a
deferred observation for final review/Task 10 Engineering Gate against the actual CI
command.

## Concerns

- Full baseline plus 13 variant replay evidence is intentionally not claimed at this
  implementation checkpoint. Its estimated scale is one 237-economic baseline plus
  3,081 variant economic attempts. The content-addressed sequential checkpoint plan
  prevents a partial run from being mistaken for complete evidence.
- The whole-repository mypy observation above is inherited from the exact baseline;
  Task 7 scoped strict mypy is green.
