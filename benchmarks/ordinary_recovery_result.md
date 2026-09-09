# Ordinary recovery: paired evidence

Subsequent authorization, 2026-09-09: the user explicitly accepts 32 total
orders and requests completion and merge to main. Candidate C therefore passes
the four screens under that revised order ceiling. The original failed
judgments below remain unchanged; complete acceptance is tracked in PR #56.

Before revision v3, nominal readback for C was 13/14 PASS. No-optical H1 drawdown
0.2442425185317515 equals current main but exceeds the older frozen retention
limit 0.20205429957130803; that original failed judgment is retained. Subsequent risk candidates
D and E both improve H1 drawdown but fail continuous wealth retention. They
are rejected and their code is restored to C. All experiment sources and raw
evidence remain retained; see the continuation record. Main is not merged and
full L4 is not claimed.

The user subsequently authorized only that H1 comparison to use current main's
exact drawdown with no extra buffer. Revision v3 implements this scope and
preserves the earlier failure. Remaining acceptance is still required before merge.
Under v3, all 14 nominal cases pass; the original-rule verdict remains FAIL.
The frozen old-source offset5/remove_all_three replay aborts with an order
attribution identity error, so paired initial-condition acceptance is not yet
established. The single-window authorization does not replace that comparator.

## Full Performance result for C

The stable v3 candidate completed all 45 Performance units (30 official and
15 protected). Economic acceptance is FAIL: 27 distinct cells trigger 68
checks. Native artifact re-evaluation finds no additional schema, identity or
failure-claim errors. Evaluating the present contract with `authorized=False`
produces 79 failed checks; neither judgment is discarded.

Two bounded native replays of unchanged main `960539a` distinguish inherited
failures from C regressions. They are diagnostic evidence, not replacement
acceptance baselines:

| Performance cell | Main wealth | C wealth | Absolute wealth floor |
| --- | ---: | ---: | ---: |
| a/bull | 1.4783417475107592 | 1.4783417475107592 | 11.5443 |
| a/h1_2024 | 1.327722049517836 | 1.3634479995178361 | 1.512 |

Main a/bull also has the same 0.1980186769270772 drawdown and 7 orders as C.
Its interval starts 2025-04-01, but the first buy signal is 2025-07-30.
This exposes a separate-start admission/holding limitation that continuous
2023-origin wealth does not establish. Only these two Performance cells have
been paired against current main here; no claim all other failures are inherited.
These wealth, acute-return, drawdown and order failures are outside the
single no_optical/h1_2023 authorization. Fixing the old robustness comparator
alone would therefore not make C eligible to merge.

Absolute recovery-and-reachability separately emitted a sealed ERROR manifest:
`absolute recovery repeated crowning evidence is absent`. No authorization
waives that required evidence. A local memory-limit termination during parallel
LOO manifest processing is a distinct transport failure: its attempt log is
retained. A sequential cached-manifest retry was subsequently stopped after
independent acceptance failures had already blocked promotion. Neither the
resource interruption nor the missing recovery evidence is reported as a pass.

## C simplification and operator cutover

C removes the fresh `_admit_pullback` allocation function, its call and the
fresh-entry exception in frozen targets. It also removes the relative-maturity
veto from ordinary structural exits. The original ordinary early-admission
slot remains; B's maturity restriction and D/E's risk-guard changes are absent.
This is not removal of the entire historical pullback subsystem or its config.

Use the code-identity migration and account-copy rehearsal in
[OPERATIONS](../docs/OPERATIONS.md), and the historical holding rules in
[STRATEGY](../docs/STRATEGY.md). C requires neither a new account nor a new schema.
Preserve original proofs, holdings and order/event identities. A real partial
remainder must still pass current proof, risk and full-budget validation;
otherwise cancel the invalid remainder without issuing a replacement entry.
MA120 protection, cost-catastrophe exits and irreversible maturity conversion
remain active. A successful code-identity migration is not economic acceptance;
complete the remaining gates before the authorized merge and later cutover.

Historical screening, not a future-return claim or full production acceptance.
The preregistered mechanism sequence and unchanged gates are in
`ordinary_recovery_continuation.md`. Config, frozen inputs, execution and fees
are unchanged. Initial cash is 2,000,000; wealth includes initial capital.
Continuous interval: 2023-01-03 through 2026-08-05, 869 sessions. The no-optical
sentinel ends 2023-06-30, 118 sessions. Exclusions apply to the native role sets.

| Producer | Full wealth / DD / orders | Remove three | Also remove sz300666 | No optical H1 |
|---|---|---|---|---|
| Main baseline | 30.74331173 / .271470 / 20 | 2.39761599 / .202292 / 14 | .91093260 / .168164 / 18 | 1.44769470 / .244243 / 6 |
| A: absolute exit | 30.74331173 / .271470 / 20 | 2.39761599 / .202292 / 14 | .98732640 / .205508 / 24 | 1.44769470 / .244243 / 6 |
| B: mature-only ordinary admission | 30.74331173 / .271470 / 20 | 2.38489707 / .196173 / 12 | 2.89545684 / .153326 / 29 | 1.46926229 / .232086 / 4 |
| C: retire fresh long-pullback, native trend admission | 30.74331173 / .271470 / 20 | 2.39761599 / .202292 / 14 | 2.44859500 / .158389 / 32 | 1.44769470 / .244243 / 6 |

A fails strict-removal wealth and orders. B fails remove-three wealth retention
and strict-removal orders. Neither is accepted; these rows must not be replaced
by later successful runs. B's .5% wealth regression still fails the fixed floor.
Its large strict-removal gain is not an independent out-of-sample result.
C preserves all three control results and improves strict-removal wealth and
drawdown, but 32 orders exceed 22. C is also rejected by the fixed screen.
More profitable recovery can create more real subsequent trades: removing an
entry route does not mechanically reduce whole-account orders.

Each completed baseline/A/B/C case passed native raw/account readback, including
seals, source/config/runner, chronological role sets, next-session execution,
complete session schedule and recomputed accounting/performance. A/B expected
economic source identities were derived from their Git commits, not copied from
the artifacts being checked. Local and remote producer trees are byte-equal.

| Producer | Local commit | Economic source SHA256 | Raw directory |
|---|---|---|---|
| Baseline | 960539a89408cc7c1fc3937bda19c9f760095012 | 86d3541617b4f3185c94bf0f5ad2bbeedfaddecabdcf1fabd593196223459fdd | recovery-baseline-960539a |
| A | 3840444 | 8f0a6b302d37baf256cb242208de46bea3c792cd3656be34cd5533fc59089c12 | structural-exit-3840444 |
| B | 815092c26d20f9bb791d31048b03a7eb567a7a43 | 7c1218aae6ee89d8f5bdd5ace216f3a6c14ec4b6a9c39198101388a79ea4ff3f | mature-ordinary-b |
| C | bc10398d815d8e7c52b0d8c727e3139fae862235 | b928db51ea6a7bbdfbe79588408717241200d44b3d98b2c39d7668b28e90259d | retire-pullback-c |

## Mechanism evidence and limitations

Production already has real account-owned cash repair. The earlier standalone
comparator's persistent flat book does not prove production repair is missing.
A removes a relative-maturity veto that delayed an existing absolute-damage
exit; it preserves consecutive confirmation, minimum holding time, winner
protection and active strategic-owner boundaries.

B changes the reinvestment path, not merely one rejected trade. For example,
removing losing sh688200 changes account equity and later concentration trims
of profitable sh688256. Summing isolated trade PnL would miss that effect.

C, preregistered before its replay, returns to the original early-slot trend
qualification and retires only the separate fresh long-pullback allocation.
Legacy orders, proofs, real inventory and exit protection remain supported.
At the original 22-order screening decision, no candidate had passed all
screens, and no full L4 acceptance was claimed.
At that point, production main was not updated with these failed candidates.
Removing the 22-order gate or silently accepting B's control regression was
not authorized by the general request to improve the strategy. Later explicit
authorizations are recorded above. Further adaptations on these
same observed intervals are research, not fresh evidence of generalization.

## Recoverable evidence and engineering scope

`ordinary_recovery_20260909_evidence.tar.gz`, 262,161,580 bytes, SHA256
`07ec18faecae109242ad69a4b7d4b5f798609e489bffebedbf5854812658c9d4`,
contains all 16 case identities, sealed results, final accounts and complete
`observations.jsonl.gz` streams. Duplicate per-day envelopes are omitted from
the archive; original local per-day observations are not deleted. Each result
still verifies the exact raw/account file digests after extraction.

C affected checks passed: 30 admission/execution/holding/exit tests, 274 ordinary
allocation/recovery/report/lifecycle/risk tests, 143 proof/risk/reentry tests,
and 10 focused architecture/projection tests after the reviewed docstring
projection correction. Ruff and mypy (317 source files) passed. Independent
review found no code blocker; review is not economic acceptance. An earlier
broad A architecture run was not a completed final verification and is not
counted here. Full L4 was not repeated for failed screening candidates.

## Final stop decision for this candidate

No merge: the user required all other acceptance to pass. Parameter-neighbor
P4 lower / champion also fails wealth retention: changing only the frozen
neighbor override strategic_reversal_max_tech_ret120 from -0.01 to -0.015
produces wealth 5.80319074025768 versus nominal 25.035391084584187,
maximum drawdown 0.1723508887086519 and 19 orders. Its native replay is COMPLETE;
this is an economic failure, not a resource error. The full 64-case robustness
matrix was not completed. The old offset5 comparator identity error remains
unresolved and its original failure records are preserved.

Full pytest/coverage was interrupted after 181 observed passing progress items;
there is no completed full-suite JUnit or coverage verdict. Coverage was
incorrectly applied to architecture scans in the initial invocation; repository
CI limits coverage to application tests. Later resource constraints included
the 20 GiB memory limit and an exhausted 32 GiB root filesystem. No full L4 pass
is claimed. Remaining expensive runs were stopped once independent failures
already made this C ineligible; no thresholds or frozen sources were altered
to turn these failures into passes.

Critical failure evidence is retained in ordinary_recovery_C_failures.tar.gz,
50,784,180 bytes, SHA256
5240d6efa9b964eceed82bb9f1e98a6326ef03c72f6cd36e198a426f9c972fdd.
It includes all 45 Performance cache units and native report/readback,
two unchanged-main diagnostic raw replays, the frozen robustness plan,
P4 lower/champion complete raw replay, the Absolute recovery ERROR manifest,
and resource/full-pytest attempt logs. Persistent file identity:
libfile_ffdca73e765c81918fca44a5327d6bdd.
The earlier v3 checkpoint with the original old-comparator failures remains
retained as libfile_c023d9bf29308191aaee357429506fdf.
A separate 1.085 GB all-Absolute-cache archive was created locally but its
large-file uploads failed; do not claim that archive or its three parts are
persistently saved. Local complete raw cells remain available for validated
reuse, and interrupted attempts must not be counted as complete.

The tested producer remains local 4ecd2296dde76067bea9572d22a3d494b92061bf,
remote code-equivalent 2b97c48aca30741b696039dcf758063619ec3520, shared tree
9051c464eaaa7dffa7722ed1d63f86fc50be6e9b. This follow-up is documentation only;
its own commit is not a new tested producer. Main remains
960539a89408cc7c1fc3937bda19c9f760095012. No live orders or Future Holdout used.


## Continuation diagnosis — 2026-09-09 (no production change)

Main was rechecked at `960539a89408cc7c1fc3937bda19c9f760095012`.
PR56 remains Draft and economically rejected. The following diagnostics do not
replace any frozen comparator or authorize promotion.

### P4 is a shared entry-path failure

An unchanged-main native champion replay with the single preregistered
`strategic_reversal_max_tech_ret120=-0.015` override completed all 869 sessions
(2023-01-03 through 2026-08-05) in approximately 319 seconds. Native
`research.cross_ai_acceptance.read_case` passed. Wealth
5.80319074025768, maximum drawdown 0.1723508887086519 and 19 orders
match C's P4 result. All 869 equity observations and daily decision projections
match after excluding only source-bound epoch_id/grant_id fields. Raw identities
remain distinct; 222 target-day and 11 order-day identity differences are retained.

Main source fingerprint:
`86d3541617b4f3185c94bf0f5ad2bbeedfaddecabdcf1fabd593196223459fdd`.
Main result seal:
`6e6ab4548c2d91e846cec0e6af7cfb163d99f6fddeef8f9b728b3edd5ac6d5ab`.

C nominal versus P4 first diverges on 2023-01-04:
tech_ret120=-0.013866545661801566 passes the -0.01 threshold but fails -0.015.
Nominal requests 0.95 weight in sz300308; P4 requests no target.
P4's first order is not until 2024-02-22. The responsible predicate is in
`uquant/portfolio/strategic/qualification_candidates.py::_witness_owner_routes`.
This establishes shared entry-path sensitivity, not a newly introduced C failure.
An unchanged-main nominal replay was not newly run; no new main nominal ratio is claimed.

### Bull-start absence is upstream of capital allocation

A bounded lossless observation of unchanged main replayed 2025-04-01 through
2025-07-30 with the original three-symbol a/bull universe and production
next-open execution. Every one of 82 canonical decisions exactly matched the
previously saved main a/bull replay. No monkeypatch, strategy changes, new
universe or holdout data were used.

Before the first order, all 81 sessions had zero targets. Opportunity labels
were CHOPPY 26, RECOVERY 23, TREND 16, STRONG_TREND 16; risk labels were
NORMAL 64 and CAUTION 17. There were no READY entries or ready strategic
qualifications. sz300502 was mature on 40 sessions, first on 2025-06-04,
but its independent_core confirmation never exceeded 4 against a required 5.
sz300308 and sz300394 had 20 and 17 mature sessions respectively, with zero
independent_core confirmations. Entry occurred through established STRONG_PAIR
certificates on 2025-07-30.

Candidate admission consumes independent_core confirmation from
`candidate_entry`. That counter requires strict_absolute_owner_quality,
at least one absolute strategic route and independent_market_confirmation;
it is not merely five consecutive mature-stock observations. Thus the observed
block is qualification/confirmation, before budget checks. This does not prove
all earlier rejected entries would have been profitable or safe. Removing the
shared gates without a preregistered executable comparison is not justified;
the previously rejected blanket maturity expansion remains rejected.

### Ownership and Absolute are not mere runner crashes

Ownership run 34339585305, continuity job 102426957008, failed in
`_validate_repeated`: fewer than two actual epochs. Models and other ownership
shards passed. Its source scenario is remove-sz300502.

The existing C Absolute cell cache for remove-sz300502 is COMPLETE and records
one actual epoch, one owner sh688233, active 2025-06-25 and closed 2026-04-28;
wealth 2.0403767562317405 and 11 orders. Absolute historical recovery uses the
same removal and requires at least two closed chains, so its absence error is
consistent with this concrete lack of witness. This does not establish that
every detail of the Ownership replay equals the Absolute replay.
Ownership artifact 10099357664 exists remotely, but its returned download URL
gave HTTP 403 locally; no local readback of that artifact is claimed.

### Recovery and next decision

New evidence archive:
`uquant_continuation_diagnostics_20260909.tar.gz`,
32,013,810 bytes,
SHA256 `7af94b8406b8e1d3b3222dc8585d676012b04b943c3ce1e61f65fb0d15951f7b`,
Library ID `libfile_bb078b0b0cac8191aeefd39fded0cf39`.
Includes full unchanged-main P4 raw/account/identity/result, bounded bull
admission observations and comparison summary. Original failure evidence and
the old dirty workspace remain untouched.

Both new diagnostic processes completed. No new full acceptance matrix was
started; existing remote CI is not claimed complete. Root disk remains full;
new diagnostic output used /dev/shm and is now durably archived.

Recommended responsibility layer is current entry qualification, not another
ordinary-exit or risk-veto variation. A simple executable trend comparator is
still a proposed new design and must be explicitly selected before implementation,
as required by the supplied handoff. No production fix or successful economic
redesign is claimed by this documentation-only checkpoint.
