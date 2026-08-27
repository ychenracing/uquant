# Strategic Handoff and State-Reachability Evidence Closure Design

## Scope and authority

This research subsystem measures strategic-owner portability, qualification-witness
sensitivity, and post-failure state reachability without changing production policy,
configuration, data, dependencies, or economic defaults. Every economic cell calls
`ProductionEngine.decide()`, `ExecutionPlanner.execute_open()`, and the durable
`AccountState`/order/fill ledger. Synthetic paths are diagnostic-only and cannot
support historical-return claims.

The immutable base is commit
`70d66b37edea3cd42ffb19c896b3f318e8bd536e`. The decision window ends on
2026-08-05, one day before the Future Holdout boundary; no observation after that
date may select a scenario, owner, intervention date, or threshold.

## Architecture

`research/strategic_evidence/` owns immutable models, canonical sealing,
production-backed replay, research-only owner intervention, route tracing,
first-divergence analysis, witness ablation, state reachability, graph analysis,
absolute-policy evaluation, and report assembly. `scripts/run_strategic_evidence_closure.py`
is a resumable orchestrator. It writes deterministic JSONL-gzip shards and compact
JSON summaries, validates them by reading them back, and never imports into
`uquant/`.

Economic replay is a close wrapper around the existing `CandidateRunner` pattern:
prepare the official `ReplayHarness`, execute pending orders at the next open, call
the production decision engine at the close, and retain the full account and route
state. The wrapper adds two hooks only: a one-shot, serialized
`StrategicOwnerIntervention` immediately before one production decision, and
research-only evidence/tradability filters. A forced-`sz300308` common-date control
must match the unforced baseline after the intervention provenance is removed.

## Frozen experiment design

The preregistration in `benchmarks/strategic_evidence_closure_contract.json` fixes
the data, source, configuration, window, universes, candidate controls, negative
control selection, removal axes, state/path matrix, health definition, seeds,
required outputs, failure semantics, and raw literal thresholds. A runner defect
does not authorize mutation of v1; an executable replacement must be a separately
sealed v2 with an explicit reason.

Forced-owner common-date cells start from the exact account snapshot immediately
before the first baseline strategic activation. Native-date cells select the first
causal session satisfying every frozen absolute-owner predicate. Intervention
rewrites all symbol-keyed strategic identity and pending-intent fields as one
atomic operation, validates account invariants, and records before/after SHA-256.

Witness ablation separates tradability, evidence, and full removal. Full-removal
cells are economic. Component-only evidence/tradability separation is labeled
`DIAGNOSTIC_ONLY` unless it can traverse the entire production decision and
execution path. First divergence is recorded separately for route, durable state,
and economics. Critical pair/triple search is bounded to the deterministic top
eight single-removal impacts.

Reachability uses historical account checkpoints when available. Synthetic states
must pass the account codec and invariants and are labeled `SYNTHETIC`. Synthetic
OHLCV paths are deterministic and causal. Graph nodes contain the frozen risk,
opportunity, capital, qualification, recovery, target, and position dimensions;
Tarjan SCC analysis identifies terminal components without a positive-position
exit.

## Failure and evidence semantics

`REPLAY_ERROR` and `INSUFFICIENT_SAMPLE` are terminal preserved outcomes. Required
cell absence, malformed accounting, a failed baseline reproduction, trace/readback
mismatch, or an unsealed payload is an engineering failure. A completed experiment
whose literal policy evaluates false is a valid research result and must still be
uploaded.

Every output binds base and experiment commits, production and research source
hashes, config/data/universe/industry/window/scenario hashes, runtime versions,
generation time, and a canonical payload seal. The exact accounting check excludes
cash drag, avoided loss, opportunity cost, and paired counterfactuals.

## Delivery boundary

Tracked artifacts contain the contract, compact summaries, manifest, checksums,
and analysis. Large traces are deterministic compressed workflow artifacts. The
workflow is manual (optionally scheduled), non-blocking, and distinguishes runner
success from strategy capability. No champion, policy gate, production strategy,
account behavior, frozen data, or dependency changes are authorized.
