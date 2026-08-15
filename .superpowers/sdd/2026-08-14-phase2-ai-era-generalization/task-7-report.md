# Phase 2 Task 7 — Fail-closed independent ablation infrastructure

## Scope and status

This task builds the ablation registry, isolated carrier materialization, sequential
runner, raw evidence schema, and runnability proof. It does not make a
KEEP/DELETE/INCONCLUSIVE decision and does not delete production code; those actions
belong to Task 8.

Task 7 runnability status: complete. A clean-HEAD baseline and all 13 registered
carriers each ran the full 279-record/237-economic fixed schedule, sequentially and
without variant reuse. Eleven carriers produced authenticated experiment checkpoints.
Two carriers ran the entire schedule but were correctly rejected as invalid experiments
because they produced no behavior divergence; their complete raw worker evidence is
retained separately. The standard aggregator therefore remains fail-closed at
`complete=false`, 11/13, rather than misrepresenting the two invalid experiments as
successful evidence.

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

TDD/static commands used `UV_CACHE_DIR=/tmp/uquant-uv-cache`; the final long gate used
`UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache`. The default root uv cache is not writable
in this execution sandbox.

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
| Frozen concentration rounding | The clean-HEAD baseline replay reached `237/237`, then correctly produced no checkpoint because four frozen raw Top-3 values of `1.0000000000000002` exceeded a newly over-strict exact upper bound. | A fail-first raw-value regression now permits only `1e-12` machine rounding while retaining the raw value; `1.000001` and material Top-1/Top-3 inversions remain rejected. |
| Variant replay-error retention | A fail-first comparison test raised `ValueError: ablation decision traces require aligned dates` when a real variant error left a shorter trace, and the original worker aborted instead of preserving the failed cell and continuing. | Focused Task 7 set: `21 passed`; the worker now retains the exact error and partial trace, continues the fixed schedule, nulls failed-cell metrics/delta, aggregates only common `VALID` pairs, and independently authenticates status transitions and error provenance. |

Representative final focused command and output:

```text
UV_CACHE_DIR=/tmp/uquant-uv-cache uv run pytest -q tests/test_phase2_ablation.py
.....................                                                    [100%]
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

- Validation artifact file SHA-256 after replay-error retention:
  `2efe00de02fe1db3a44e37b6d7d0ea1c6de0b20fe25830456b4e3d86d9481798`
- Bound runner SHA-256:
  `7c7542fef52ae1e6b5064f579151b82da0ba24eb480b4ce7e0824bb1d63a919e`
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

The final gate ran from clean implementation HEAD
`9592fcca3860d1901a7009d799d29d20959d1699`; later report-only commits do not alter
that evidence binding. The reviewed production commit remained
`7f80436373b6da03536e15ff1908c010bfb92eb3`. Exact binding and checkpoint paths:

- Checkpoint directory:
  `/tmp/uquant-phase2-task7-9592fcca-checkpoints`
- Binding SHA-256:
  `a009bf0e97499bc4bb40fc42e9e7e6999ea9f727492ab2ab4f86f2fc2ce34daf`
- Schedule semantic SHA-256:
  `0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a`
- Schedule checkpoint file/payload SHA-256:
  `0e6f6a90fa704ec87ea49a790ab569ca6e9f60b58cc8999dc0421bbd02bdd88b` /
  `e55d0f3ac0ca78b8b6372ca790fe5fe4fb06a13126e633dafe6789ec4291e399`
- Final strict-readback progress file SHA-256:
  `fac83bcdc0136ebd84a92a1f557a07a5c102ecc98e2a9f059d09a3818389fcb4`

The exact shell form was the following, with `MODE` replaced once by
`--baseline-only` and once per registry row by `--experiment EXPERIMENT_ID`:

```text
env UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache uv run python \
  scripts/run_phase2_ablation.py run \
  --source-root . \
  --registry artifacts/phase2/ablations/registry.json \
  --data-dir data/frozen \
  --checkpoint-dir /tmp/uquant-phase2-task7-9592fcca-checkpoints \
  --output /tmp/uquant-phase2-task7-9592fcca-progress.json MODE
```

Each standard checkpoint also stores the resolved absolute executable/argument list in
`replay_command`; invalid-experiment raw sidecars store the identical execution binding,
checkout, carrier, source, data, runtime, lock, schedule, fresh-account, and
single-process provenance. A watcher copied the atomically written raw worker file for
the later variants; it did not execute or modify a replay.

The baseline completed in `22m53.933s`. It covers 279/279 records, 237/237 economic
attempts and 237 traces: Phase 1 has 45 `VALID`; Generalization retains exactly 191
`VALID`, one known `REPLAY_ERROR`, and 42 `INSUFFICIENT_SAMPLE`. Baseline checkpoint
file/payload/worker SHA-256 values are, respectively,
`b8e18a8bb70b996b8336f4893f7b51d2293ae682b5b04bf36400048fe1896eab`,
`f2264864bd6b713b91726de701cec232c10ad15c2a33c85f7364b9e8b5e76703`, and
`739531282ad66c92f8cd520b2bd527d4a513b2815b6d4a71dc857f63967fd0e4`.

### Authenticated standard checkpoints

Every row covers 279/279 records and 237/237 economic attempts. `Common valid` is
Phase 1 + Generalization; aggregation uses only those common-`VALID` pairs while status
transitions remain visible. `First divergence` names the earliest real decision-stage
hash divergence, not an error.

| Experiment | Wall time | Execution | Common valid | Status transitions | First divergence | Checkpoint file / payload / worker SHA-256 |
|---|---:|---|---:|---|---|---|
| `without_sector_guard` | `22m42.336s` | pass | 45 + 191 | none | `continuous_ai_era/industry-balanced`, 2023-03-24, `risk` | `7d454cec1a1a9a3b064b60d64a45ac9eb8ee7eaaf7ede55ce5324a845da5c7a5` / `0d7f127a9b7f0e287fe4afc34132fb7500c57ca0c201d87892ebaa9c1271025c` / `e419fcf59e86f19bfe5492082a6680d9bc15beea3fb8cfaa7c527fbad0e8ce28` |
| `without_chronic_overlay` | `23m10.697s` | pass | 45 + 191 | none | `bull_crash_2025_2026/full`, 2026-07-31, `risk` | `c0dcfa6d00573763df3c8e5738902e40cf549e0e0ea78e2c635c8ac52477d90a` / `3f66fe2b5ce7835d414f153ffd48303f584c68f8dbddb7c054c6d28484a4df1c` / `7fd284e2df22d0cacb01b6b0262f34933536f7c5c5bc44481ab2270ec463f835` |
| `without_transition_overlay` | `23m39.007s` | pass | 45 + 191 | none | `continuous_ai_era/random__05__0000`, 2025-01-16, `risk` | `4d6b8b3c1de6f41286f0084107a491cd98b8c6e587f27c3c4d6b6459f4f7cce6` / `83756197442c02abb32e5af373ce08cf9f28f83aa93b88b220e3ea5a0e526d2f` / `91f24daf28549b9e5462cd747fe71f0d46a7c368bd1bb4310c4ae64be468b25c` |
| `without_capital_budget_ladder` | `26m54.716s` | fail/result | 45 + 190 | `VALID->REPLAY_ERROR`: 1; `REPLAY_ERROR->VALID`: 1 | `continuous_ai_era/full`, 2023-03-16, `risk` | `847acc3318343a7d083364418ea97757922fac62bc3621087084a6d80664b9ed` / `902e010aef8e9087036e7062a114f0d21cd0ed165429e97e1529e161c866f1dd` / `d768b90d614e77bcde2f432d35223d6556fc3941a9c8a783560801510b70b8d2` |
| `without_recovery_conviction_weighting` | `22m57.977s` | pass | 45 + 191 | none | `bull_crash_2025_2026/remove-one__sz300502`, 2025-05-08, `targets->orders` | `c14d4c1f98d0c085d0ae3f34e47f25814fdc81f21c06e3a3c9a56bc6c4f61c8a` / `aa90aa2ca40f08f152314f2dd4661dffae8dec043c6ec10edcf257ddc3bf6b2a` / `acbd24120fe3d4fbe45e689c78c06e74720aea778884fd3dd7cdbfac79b4b7c7` |
| `without_tactical_rebound_probe` | `22m09.038s` | pass | 45 + 191 | none | `h2_2023/full`, 2023-07-05, `targets->orders` | `8ce4c4f00066047c7c833b698276391899fac0ff92fee717d477ee143af89a99` / `6a695dd89712c6be4272d29f23e2d1799b13fda17d9835c4ce91e8a5dffcd600` / `9358e1965a474d000c019eb7827c50f2c28005b6beee0ae2057d78a853a7511a` |
| `without_strategic_trailing` | `22m46.199s` | pass | 45 + 191 | none | `h2_2023/subindustry__materials`, 2023-12-26, `targets` | `4fbeac2a1e2992ba3c655cf7937a0348882702cb4bc33c0a4f75bbb9b00bcd99` / `97cdf5df87dc479175c0422dc1debd7283f9b2538c299ba357403d56ffe16cc6` / `62a21d53a7286c02979abbf30279704ff5c887c6c0d0fd8496d06484250352e2` |
| `without_restoration_special_handling` | `23m12.670s` | pass | 45 + 191 | `REPLAY_ERROR->VALID`: 1 | `h1_2024/full`, 2024-02-06, `targets->orders` | `1cc2dd88529c9c05759124b3e0a086bb3ddec22f76145e1b4f6f82e58ca5dde6` / `e88906012f9b0bfb23d9f4d9a4269946c427dedb1cc9c683d58b5a9ddbb0175b` / `afb1023c0314f529f0efdad0cc2a237346299a0492ae8621bcc3da1370e05f99` |
| `without_add_tranche` | `23m37.100s` | pass | 45 + 191 | none | `continuous_ai_era/industry-balanced`, 2023-03-27, `targets` | `edbb9609c2b9c6548bf24e1b8fc2f7eb46c07daca5f1eb58019fd071cfa85884` / `187a1224097836afebc3ba4d8c2986fe7e8ba6afd3716eb2b79e17765b949dfe` / `20c6edaa5dc7f5f61b368d95cbdd48a490356cf0de71c40eff77d68015017a27` |
| `without_replacement_rotation` | `23m41.349s` | pass | 45 + 191 | none | `continuous_ai_era/remove-one__sz300394`, 2023-06-12, `targets->orders` | `89fe8718502d65f68f6db8eb548039d9b1e37453c9045605b5c711889cf5c607` / `b7fe00f453a682f3dc868c0735444458795dd853a72aa870d8e9d72c733fac14` / `d88a98a767111b5b6bcec6893b5e761e3802a8cadc00188d8e2d4118b2bf0c45` |
| `without_dynamic_risk_anchors` | `24m26.520s` | pass | 45 + 191 | `REPLAY_ERROR->VALID`: 1 | `continuous_ai_era/full`, 2023-05-30, `risk` | `04acf032bca34ae962c846bed60859b9ca6d789abc2b4832b5519299eee3c808` / `bf952b6d98b98db1d1fa525c2a421b889441047b350134a837613959ebda9da7` / `1ba0d4fb5c94dd2b97158f5e8e8f7028bce68d30393d4da5d29f243d9e08a193` |

`without_capital_budget_ladder` retains the new 2025-08-25 error described below,
sets the failed cell's metrics/delta to null, and nevertheless continues the remaining
18 economic cells through 237/237 in the same worker process. Its Generalization
variant statuses are 190 `VALID`, one `REPLAY_ERROR`, and 42 insufficient; the frozen
known-error cell separately transitions to `VALID`. `without_restoration_special_handling`
and `without_dynamic_risk_anchors` also make the frozen known-error cell `VALID`; this
is reported as a variant transition and never changes the frozen baseline status.

### Complete but invalid no-divergence experiments

Both carriers below ran all 279 records/237 economic attempts and produced 237 traces.
Their traces are canonical-byte identical to baseline; cell statuses, metrics, and the
known error's type/message/date are also exact after excluding only the deliberately
carrier-specific binding/provenance fields. Thus every economic delta is zero and no
first divergence exists. Per the brief, the runner exits 1, writes no standard
checkpoint, and leaves the aggregator incomplete.

| Experiment | Wall time | Exact result | Raw sidecar | File / canonical worker SHA-256 |
|---|---:|---|---|---|
| `without_challenger_scout` | first `22m44.500s`; final evidence rerun `24m25.640s` | exit 1: `phase2 ablation failed closed: ablation experiment has no behavior divergence` | `/tmp/uquant-phase2-task7-9592fcca-checkpoints/raw/without_challenger_scout.worker.json` | `7e907b5fa87cfae9fef593e24a7483fd102732877f43eaf1873644a6a29c49c0` / `ad948ff35b14cca543c0b14948bf9a85d8142fdc2f9fa4bf8848d5244f642624` |
| `without_conviction_weighting` | `22m30.623s` | exit 1: `phase2 ablation failed closed: ablation experiment has no behavior divergence` | `/tmp/uquant-phase2-task7-9592fcca-checkpoints/raw/without_conviction_weighting.worker.json` | `aa9ab2455e89764af19d731d41c8edb5881d8d63caa2e5ed8820e6ad579204fb` / `64428c523c2381cf491e38ce8901ba2682ca0136b627720a719f605574587528` |

The first `without_challenger_scout` run completed its matrix and failed closed, but its
temporary raw file had already been removed before the evidence-retention ruling; it is
not used as final evidence. The one authorized rerun used the same unchanged HEAD,
binding, schedule, carrier, data, and runtime, and its watcher retained the raw sidecar
listed above. No successful shard was rerun.

Baseline plus the initial 13 carrier runs consumed `19,646.665s` (`5.457407h`) and
3,318 economic attempts. Including the single authorized challenger-scout evidence
rerun, wall time was `21,112.305s` (`5.864529h`) and 3,555 economic attempts. Replays
were sequential; heartbeat checks showed exactly one replay worker at a time.

Final strict readback invoked `run --baseline-only` against the populated binding. It
revalidated the baseline, schedule and every available standard checkpoint, emitted
`complete=false`, `completed_count=11`, `required_count=13`, and exactly these missing
IDs: `without_challenger_scout`, `without_conviction_weighting`. Independent raw
readback checked 279 cells, 237 economic flags and 237 traces for both invalid carriers,
then proved exact zero-divergence against baseline. This is the intended fail-closed
result, not a claim of complete aggregate evidence.

### Superseded fail-closed attempts and debugging

The first clean-HEAD baseline attempt at implementation commit
`a276fab2e4aa2673aa65524724189fcaa648e373` ran all 279 records/237 economic
attempts in 1,385 seconds, then failed closed during schema validation. It deliberately
created no baseline artifact; only the authenticated schedule checkpoint remained.
Root cause was not strategy or data drift: frozen evidence already contains exact raw
`top3_concentration=1.0000000000000002` for these four valid cells:

- `h2_2024/remove-one__sz300502`
- `h2_2024/remove-all-core`
- `h2_2024/tradable-no-optical`
- `h2_2024/random__20__0004`

The existing attribution/reference validator retains finite nonnegative raw
concentration values and recomputes them from exact symbol PnL. Task 7 now applies only
a `1e-12` machine-boundary tolerance while preserving the raw number; it does not clamp
or rewrite it. Baseline is restarted from the beginning after the fix commit.

The next clean-HEAD run at implementation commit
`a9b1831a9957217d986c3cf659d4dea2b8d794fa` produced a complete baseline and the
first three variant checkpoints before `without_capital_budget_ladder` encountered a
new production replay error after 1,214 seconds at economic cell 219/237. The runner
failed closed and wrote no variant checkpoint. The exact error was:

```text
ai_era_generalization/continuous_ai_era/random__05__0001
2025-08-25
RuntimeError: new SELL for sh688041 has incompatible attribution: attribution pair
LEADER/LEADER_LIFECYCLE_PROMOTION is not permitted for SELL
```

Systematic reproduction showed this was a real variant economic path, not rounding or
latent metadata drift. Immediately before the rejected order, the variant held 11,300
shares of `sh688041` at 209.86, equity was 3,532,661.27, current weight was
`0.6712837`, and the `0.6` target required a 251,821 reduction, beyond the 176,633
five-percent rebalance band. The target retained exact `LEADER` /
`LEADER_LIFECYCLE_PROMOTION` attribution and reason `repaired recovery position
graduated to core`. The frozen baseline had no `sh688041` position, target, or order
at the same cell/date. Therefore Task 7 did not weaken attribution safety or change
portfolio behavior to suppress the result.

Instead, the runner now treats a new variant `REPLAY_ERROR` as an authenticated Task 8
input. Every error binds exact type, message, date, contract, cell, execution binding,
carrier, and provenance hashes. `frozen_status` remains immutable while variant
`status` may record an observed economic `VALID -> REPLAY_ERROR` transition; baseline
status must still match the frozen contract exactly. Failed metrics and delta are
`null`, `execution_pass=false`, and aggregates cover common `VALID` pairs only while
also reporting complete record/economic/common-valid, status, error, and transition
counts. Missing fields, rewritten frozen status, self-signed carrier provenance,
malformed dates, and trace rows after the failure date are rejected. First divergence
continues to require a real pre-error hashed decision-stage divergence; the error itself
cannot manufacture one.

A real two-cell isolated worker smoke proved continuation in one process using the
affected cell and the next fixed cell. It exited zero after stderr progress `1/2` then
`2/2`: `random__05__0001` retained the exact 2025-08-25 error with 640 pre-error trace
rows, and `random__05__0002` completed `VALID -> VALID` with 869 rows. Worker artifact
file SHA-256 was
`bc64c018105037bb853c0c73cc6369ceee80ed706e315f185f804ee5225e7385` and bound
provenance SHA-256 was
`c0d96e17bff02ad299654f75ba8c2993c69a9139bc82a3d1d89fec2a9e849e1e`.
Its comparison reported `execution_pass=false`, one common-valid cell, one status
transition, a null failed-cell delta, and a real 2024-03-26 `risk` divergence from the
other cell. This smoke is continuation evidence only, not full-contract evidence.

All `/tmp/uquant-phase2-task7-a9b1831-checkpoints` artifacts are superseded after the
runner/source binding change and were not reused. The final gate documented above did
restart baseline from scratch under the new `9592fcca` binding and then executed all 13
variants sequentially.

## Verification

Verification at the source-bound implementation HEAD:

- Focused Task 7 tests: `21 passed`.
- Full repository pytest after variant-error continuation hardening: exit `0`;
  `985 passed in 231.13s`.
- Task 7 Ruff format/check: passed.
- Task 7 strict mypy:
  `uv run mypy research/ablation.py research/ablation_registry.py scripts/run_phase2_ablation.py`
  passed with `Success: no issues found in 3 source files`.
- `git diff --check`: passed.
- Final artifact strict readback: exit `0`; 11 authenticated standard checkpoints,
  exactly two missing invalid/no-divergence IDs, and both retained raw workers pass
  exact 279/237/237 coverage plus baseline zero-divergence comparison.

Expanded whole-repository strict mypy reports one pre-existing error at
`scripts/run_pareto_evidence.py:143` (a `Mapping[str, Any]` assigned after a branch
inferred as `dict[str, Any]`). The same command against a detached exact
`7f80436373b6da03536e15ff1908c010bfb92eb3` checkout reports the identical single
error across 59 source files. It is outside Task 7 and was not changed. This is a
deferred observation for final review/Task 10 Engineering Gate against the actual CI
command.

## Concerns

- All 13 carriers proved full-contract runnability, but the standard aggregate is
  intentionally `complete=false`: challenger scout and conviction weighting are
  invalid experiments under the brief because neither creates a behavior divergence.
  Task 8 must consume these raw invalid-experiment results and apply its classification
  policy without relabeling this Task 7 aggregate as complete or inventing a first
  divergence.
- The bound replay/checkpoint evidence lives outside the repository at
  `/tmp/uquant-phase2-task7-9592fcca-checkpoints`; the report records its exact paths
  and hashes, but external cleanup of `/tmp` would require an exact replay.
- The whole-repository mypy observation above is inherited from the exact baseline;
  Task 7 scoped strict mypy is green.
