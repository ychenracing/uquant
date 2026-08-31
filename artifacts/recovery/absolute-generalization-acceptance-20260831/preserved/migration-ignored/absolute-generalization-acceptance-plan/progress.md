# SDD ledger — plan: /workspace/scratch/1a8f428176e6/absolute-generalization-acceptance-plan.md

Spec: `/workspace/scratch/1a8f428176e6/recovered_requirements/Tra/粘贴的文本 (1)(5).txt`

Execution baseline: branch `codex/absolute-generalization-acceptance`, start `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`, tree `d718c3f3c2c31659c12f72f7f206184ca2810ef4`, `origin/main=d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`.

User override: redo Tasks 1–10 from the current remote main. Earlier “do not redo Tasks 1–5” is superseded. The prohibition on the historical strategic-owner-continuity branch/backups remains binding.

Ruling: the old `d85d875e4bf30463a8dbd43130f50698d37e85c3720bd82e5301559cb182399a` seal identifies an unrecoverable lost contract preimage. Recreating a new contract while asserting that seal would be cryptographic fabrication. Task 2 will freeze identical literal semantics against current `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`, compute a new canonical seal, and preserve `d85d…` only as historical provenance. Thresholds, baseline facts, and `baseline_can_relax_absolute_limits=false` do not change. Cost if wrong: a downstream consumer expecting the lost seal must update to the new reviewed contract identity, but no economic policy is relaxed.

## Pre-flight task/interface scan

| Tasks | Producer → consumer or shared file | Finding / ruling |
|---|---|---|
| 1 → 2–10 | Baseline facts → regression expectations | Clean. Task 1 writes only ignored evidence; downstream code must not embed runtime results as relaxed thresholds. |
| 2 → 4 | Contract/scenario records → replay | Clean. Replay consumes only validated immutable records. |
| 2 → 5 | Contract/scenario identity → artifact identity | Clean. The artifact must revalidate, not trust, the caller’s identity. |
| 2 ↔ 5–8 | `absolute_generalization/__init__.py` exports | Clean if each task adds only reviewed public interfaces and updates API governance as required. |
| 3 → 7 | `linear_quantile` → literal tail policy | Clean. Task 7 is forbidden from carrying a second formula. |
| 4 → 5 | `AbsoluteGeneralizationReplay` → metric/artifact derivation | Interface shape is intentionally finalized by Task 4. Ruling: Task 5 must consume the public immutable replay record and may add validation-owned fact types without changing economic replay semantics; wrong ruling costs a small interface refactor. |
| 5 → 6 | Artifact/transition facts → reachability | Clean. SCC edges remain observed production transitions, not artifact assertions. |
| 5 → 7 | Validated cell artifacts → aggregation | Clean. Aggregation independently revalidates raw cells and recomputes summaries. |
| 6 → 7 | Health/SCC/recovery facts → seven components | Clean. UNKNOWN/malformed input remains failure, never success. |
| 7 → 8 | `AcceptanceReport` → CLI/workflow exit | Clean. CLI exits zero only for `report.passed`; workflow cannot set capability facts. |
| 8 → 9 | Deterministic runners → progressive matrix | Clean. Full 34 is gated behind representative sentinels. |
| 9 → 10 | Final evidence → PR/CI/merge | Clean. Generated traces stay outside Git; PR body carries compact verified results. |

## Per-task self-consistency scan

| Task | Tests vs implementation and files | Finding / ruling |
|---|---|---|
| 1 | Existing tests/runners; ignored report only | Ruling: do not create an empty or tracked baseline-report commit. Publish the unchanged branch anchor after coherent baseline verification; wrong ruling costs only loss of a decorative commit, not recoverability because the remote ref is verified. |
| 2 | RED contract/scenario tests; contract/package/registry implementation | Clean. Frozen contract seal and identities cannot be adapted to observed results. |
| 3 | RED statistical edge cases; one shared implementation and caller migration | Clean. |
| 4 | RED PIT/role controls; production replay orchestration and minimal shared projection | Clean. Production changes require trace-backed RED. |
| 5 | RED metric/artifact/accounting controls; validation-owned derivation | Clean. Script becomes a caller, never an imported authority. |
| 6 | RED health/outlet/transition/SCC controls; exact production projection | Clean. The acceptance predicate cannot replace production state. |
| 7 | RED component/trust-boundary controls; policy plus aggregation | Clean. `passed` has one exact conjunction. |
| 8 | RED CLI/workflow contract; thin script plus blocking workflow | Clean. Extended workflows remain manual-only. |
| 9 | Progressive economic execution; only trace-backed RED/fix if needed | Clean. No full-matrix repetition without result-affecting change. |
| 10 | Stable-candidate gates/review/PR/CI/merge/cleanup | Clean. Local skip of slow portfolio architecture does not waive required GitHub Engineering CI. |

## Task ledger

- Task 1: COMPLETE at `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`
  (tree `d718c3f3c2c31659c12f72f7f206184ca2810ef4`). Frozen data and
  contracts passed; focused suites were `85 passed` and `95 passed`; grant,
  champion, and critical production runners all returned `PASS`; every
  historical invariant matched exactly. Independent review: `READY`, no
  findings. Remote feature anchor verified at the same SHA. The rejected shell
  push was an authentication-only failure before any remote mutation; the
  authorized GitHub connector created the exact ref, which was then fetched and
  configured as the local upstream.
- Task 2: COMPLETE at `85e03f83612892e7efdfa8ee38eb73d3513dc291`
  (tree `dff35c0a7abd5176b212ca01205f0139f80cf14d`). The immutable 34-scenario
  contract and canonical leave-one-out builder are sealed at
  `af3882c594372ae0f5d4665990f5ead6bea99faaf0916f803239256c8ec6baf6`;
  `baseline_can_relax_absolute_limits=false`. Final verification: 33 focused,
  37 source/public/import, and 10 complexity/public-owner tests passed; Ruff,
  strict MyPy, compileall, and diff-check passed. Three independent review/fix
  rounds closed hostile equality, physical-file, nested runtime-shape, and
  pre-validation field-access findings. Final independent review: `READY`, no
  findings. Remote/local/upstream commit and reviewed tree were verified exact.
- Task 3: COMPLETE at `5d2fc07e8d69878b78f2e482a7026a62abd126e5`
  (tree `7421ee4feae98d8e2ceeba871a8f185045ca7983`).
  `uquant.validation.statistics.linear_quantile` is the sole finite
  `(n - 1) * probability` interpolation owner; all three legacy paths are
  one-call transports. Final verification: 64 focused/source/contract tests,
  173 legacy generalization tests, and 14 exact source/import/public/risk/
  execution gates passed; Ruff, strict MyPy (286 files), compileall, and diff
  check passed. Review fix round closed registry projection, facade metadata/
  pickle identity, and validation-order coverage findings. Frozen contract
  seal remains `af3882c...`; current registry is `5d6e2a...`; economic source
  remains `d1ef797...`. Independent re-review: `READY`, no findings. Remote,
  local, upstream, and reviewed tree were verified exact.
