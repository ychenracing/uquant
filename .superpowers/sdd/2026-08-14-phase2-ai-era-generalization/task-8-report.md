# Task 8 report: authenticated ablation conclusions and minimal candidate

## Outcome

Task 8 classifies the 13 active registry subsystems as 10 `KEEP`, one `DELETE`,
and two `INCONCLUSIVE`. The only accepted deletion is `transition_overlay`. Its
dedicated production deletion, focused verification, Phase 1 gate, related
Generalization gate, and a fresh baseline-plus-12 complete registry all passed the
applicable contracts. No deletion was reverted and no compensating behavior, tuning,
seed, window, market rule, safety rule, frozen baseline, or policy change was added.

## Per-subsystem classification

All figures below were recomputed from the authenticated comparison cells. The full
nine-dimension, per-contract summaries are in `artifacts/phase2/ablations/results.json`.

| Subsystem | Decision | Evidence |
|---|---|---|
| `sector_guard` | KEEP | Removal worsens wealth, drawdown, acute return, orders, turnover, and concentration tails in both epochs. |
| `chronic_overlay` | KEEP | Phase 1 is neutral, but removal uniquely worsens observed Generalization drawdown by `0.00849898411999983` at `continuous_ai_era/subindustry__pcb`. |
| `transition_overlay` | DELETE | Valid trace divergence (`continuous_ai_era/random__05__0000`, 2025-01-16, risk) but exact zero delta on all nine dimensions over 45 Phase 1 + 191 common-valid Generalization cells; zero status transitions. |
| `capital_budget_ladder` | KEEP | Removal harms wealth/drawdown/orders/turnover/concentration and retains `execution_pass=false`; post-deletion evidence contains `VALID->REPLAY_ERROR` at `continuous_ai_era/random__05__0001` plus the frozen error's separate `REPLAY_ERROR->VALID`. |
| `challenger_scout` | INCONCLUSIVE | Task 7 and post-deletion evidence are both authenticated `invalid_experiment/no_behavior_divergence`; never relabeled as successful ablation. |
| `conviction_weighting` | INCONCLUSIVE | Task 7 and post-deletion evidence are both authenticated `invalid_experiment/no_behavior_divergence`; never relabeled as successful ablation. |
| `recovery_conviction_weighting` | KEEP | Phase 1 trading-cost protection and Generalization wealth/concentration protection; removal loses `6.52588915569515` wealth at the cited crash remove-one cell. |
| `tactical_rebound_probe` | KEEP | Removal loses `8.92638587253313` crash-tail wealth and worsens acute return, drawdown, and orders. |
| `strategic_trailing` | KEEP | Removal worsens Phase 1 wealth/drawdown and Generalization turnover/orders/concentration/tail wealth. |
| `restoration_special_handling` | KEEP | Removal loses `5.573139099976107` continuous optical-tail wealth and worsens drawdown/concentration; a separate frozen-error improvement does not cancel this. |
| `add_tranche` | KEEP | Phase 1 concentration protection and Generalization wealth/drawdown/orders/turnover/concentration protection. |
| `replacement_rotation` | KEEP | Phase 1 neutral, but removal loses `3.017175631685424` wealth at `continuous_ai_era/remove-one__sz300394` and worsens Generalization order/turnover tails. |
| `dynamic_risk_anchors` | KEEP | Removal worsens Phase 1 wealth/drawdown and Generalization wealth/drawdown/orders/turnover/concentration; a separate frozen-error improvement does not cancel this. |

Higher-is-better dimensions are `final_wealth` and `acute_return`. Lower-is-better
dimensions are `max_drawdown`, `account_orders`, `gross_turnover`, `annual_turnover`,
`top1_concentration`, `top3_concentration`, and `pnl_hhi`. Unique tail or
Generalization harm is sufficient to KEEP even if aggregate medians or Phase 1 are
neutral. This protects `chronic_overlay` and `replacement_rotation` in particular.

## Deletion commit and gates

Dedicated deletion commit:

- `e5e0fa903c9a9b26701063ae01f352af3e246a7d Remove valueless transition overlay`.
- Removed only transition overlay config, state-machine/freeze behavior, serialization
  expectation, and transition-owned tests. Shared transition-damage observations and
  thresholds used by other mechanisms remain.
- RED: `test_transition_damage_observation_does_not_create_a_standalone_freeze`
  failed because the old assessment still froze.
- GREEN: `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run pytest -q
  tests/test_risk_transitions.py tests/test_lifecycle_and_risk.py
  tests/test_config_governance.py` -> `196 passed`.
- Scoped Ruff passed; strict mypy over `uquant/config.py`, `uquant/risk.py`, and
  `uquant/config_governance.py` passed.

Phase 1 command:

```text
env UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/python -m uquant.validation promotion --data-dir data/frozen --profile full --output /tmp/uquant-phase2-task8-transition-promotion.json
```

Exit 0, `passed=true`, failures empty, 45/45 cells; artifact SHA-256
`4267e620e99ba7a2dbadc99c4acba829530e0763f8f78fb413dc7fe26b4c59b7`.

Related Generalization command:

```text
env UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/python -m uquant.validation generalization-matrix --data-dir data/frozen --window continuous_ai_era --output /tmp/uquant-phase2-task8-transition-generalization-continuous.json
```

Exit 1 solely for the known authenticated replay error; 39 records = 31 valid + one
replay error + seven insufficient-sample. Scenario keys/statuses, the known error,
and all nine metrics over the 31 comparable valid cells equal the frozen champion.
Artifact SHA-256 is
`b3efd5596d9d3a89e3f728aa22fb8f7f5a9f388f681654895e6274f6dd2ef6a4`.
The deletion was accepted; there was no revert.

## Fresh minimal registry and complete economic replay

The durable orchestration chain is:

- `aa4b313e000002adae27b32f91b5a84425c78987 Add post-deletion ablation registry`;
- `02596b25efef900757f0d3f53599b5dae1c9d4d5 Anchor post-deletion ablation evidence`.

The derived `minimal_registry.json` inherits the Task 7 contracts, invariants,
exclusions, and carriers, filters only deleted `transition_overlay`, binds production
base `e5e0fa9`, and records the deletion ledger. Task 7 `registry.json`,
`evidence_manifest.json`, and historical readback semantics remain unchanged.

Candidate validation command (run twice):

```text
UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/python scripts/run_phase2_ablation.py validate --source-root . --registry artifacts/phase2/ablations/minimal_registry.json --data-dir data/frozen --output /tmp/uquant-phase2-task8-aa4b313-validation-{a,b}.json
```

Both runs were byte-identical and passed all 12 carriers. Validation SHA-256 is
`a253896e9c75f5c69f3ab4d75b694d2ebfc3fac35913ae9a6614318b25dfeddc`;
registry canonical SHA-256 is
`37ae329f20b12e3ca8bf50a16850aa9c70990d955682bec927ba27458f3dfa91`;
production source SHA-256 is
`9bedfd5fb2bed6d3a1624efcca6f1d442c765abdee9e4749170fbb2e89536d6b`.

Fresh sequential command shape (one baseline, then each of the 12 experiment IDs,
never parallel and never repeated):

```text
env UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/python scripts/run_phase2_ablation.py run --source-root . --registry artifacts/phase2/ablations/minimal_registry.json --data-dir data/frozen --checkpoint-dir /tmp/uquant-phase2-task8-aa4b313-checkpoints --output /tmp/uquant-phase2-task8-aa4b313-progress.json --baseline-only
env UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/python scripts/run_phase2_ablation.py run --source-root . --registry artifacts/phase2/ablations/minimal_registry.json --data-dir data/frozen --checkpoint-dir /tmp/uquant-phase2-task8-aa4b313-checkpoints --output /tmp/uquant-phase2-task8-aa4b313-progress.json --experiment <one-registry-id>
```

The run started 2026-08-15 18:47:14Z and ended 2026-08-16 00:02:40Z. Baseline and
every carrier covered 279 records and 237 economic attempts. Final coverage is 12/12,
10 valid divergent + two authenticated invalid no-divergence, missing empty,
`coverage_complete=true`, and intentionally `complete=false`. The complete progress
SHA-256 is `ad3a273a0e24be474021d6c034688a9e4cec6807bd8b1dc1bf8ab375e36c7b00`.

Strict readback command:

```text
env UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/python scripts/run_phase2_ablation.py readback --repository-root /workspace/scratch/ed4d3eac8046/uquant --evidence-commit aa4b313e000002adae27b32f91b5a84425c78987 --registry-relative artifacts/phase2/ablations/minimal_registry.json --data-dir /workspace/scratch/ed4d3eac8046/uquant/.worktrees/phase2/data/frozen --checkpoint-dir /tmp/uquant-phase2-task8-aa4b313-checkpoints --replay-output /tmp/uquant-phase2-task8-aa4b313-progress.json --output /tmp/uquant-phase2-task8-aa4b313-strict-readback.json
```

Exit 0; output SHA-256 equals progress SHA-256 and `cmp` returned 0. The independent
minimal manifest file SHA-256 is
`1b32febb567f518b2babb95090eece25c46f077f182f36df7271e9021fed4ecc`,
compiled canonical SHA-256 is
`58011315ec19111ea2caba0dd1b8cba06608150ca3726d62fbceefdc53fa9a6b`,
and sealed payload SHA-256 is
`97a2620f6e3d3830a0a46364b6f89c77564012fa4492704b1b69fab1d67c51f0`.

The historical Task 7 strict command was rerun after adding the second trust root:
exit 0, 13/13, 11 valid + two invalid, `complete=false`, SHA-256
`efc4121041dbc9804670a360f8309ec81f22f709e9318aa77824073064c93b04`,
and byte-identical to the original Task 7 progress. Cross-registry manifest acceptance
is covered by a fail-closed test.

## Final artifacts and provenance

- `results.json`: SHA-256
  `04ad26833bf4780c3a9c64bfd33edd427d39c83216a5d4adc0dfb76bd6ec7ee4`.
- `conclusions.md`: SHA-256
  `92e0ecec7d16bd6b9b999f674587d4e2955d1e23c4021df1d20de0eeade82b0b`.
- Historical evidence commit/trust anchor: `9592fcca...` / `f260a58...`.
- Post-deletion evidence commit/trust anchor: `aa4b313...` / `02596b25...`.
- All classification rows include artifact kind, invalid reason, coverage,
  execution status, artifact/raw hashes, causal divergence, status transitions, and
  all nine directed dimensions for each available epoch.

## Scope and self-review

- Confirmed frozen champion/generalization baseline/policy, seeds/windows, market
  rules, safety rules, and unrelated production code were not changed.
- Confirmed `transition_overlay` alone is absent from the minimal registry and
  production/config/state/test surface; shared mechanisms remain.
- Confirmed no `no_behavior_divergence` invalid experiment is classified DELETE.
- Confirmed the capital-budget replay failure is present verbatim in authenticated
  evidence and `results.json`; no seed/policy adjustment was made.
- Confirmed historical Task 7 output is byte-identical after the new trust root.
- Reviewed diffs with `git diff --check`; final focused tests and scoped static checks
  are recorded below after their final run.

Final verification:

```text
UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/pytest -q tests/test_phase2_ablation.py
......................................                                   [100%]

UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/ruff check research/ablation_registry.py scripts/run_phase2_ablation.py tests/test_phase2_ablation.py
All checks passed!

UV_CACHE_DIR=/tmp/uquant-phase2-uv-cache .venv/bin/mypy --strict research/ablation_registry.py scripts/run_phase2_ablation.py
Success: no issues found in 2 source files

.venv/bin/python -m json.tool artifacts/phase2/ablations/results.json
exit 0

git diff --check
exit 0
```

## Concerns

The 516,291,276-byte authenticated post-deletion archive is external under `/tmp`.
Tracked registry/runner/manifest data makes it reproducible and fail-closed, but the
current archive copy is not durable storage. `complete=false` is intentional because
two fully covered experiments are authenticated invalid no-divergence results; it is
not missing coverage.
