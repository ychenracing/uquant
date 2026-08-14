# Phase 2 AI-Era Generalization Design

**Status:** Approved on 2026-08-14

## Objective

Phase 2 proves that the frozen Phase 1 production champion generalizes across
AI-industry universes, attributes its economic results to stable sources, and
retains no economically valueless strategy complexity. All economic evidence
starts on or after 2023-01-01. Earlier rows remain feature warm-up only.

The frozen champion is `cf8fecff76564fd4ed87faa0da336a06d433fd93`.
Its Engineering Gate and 30 official plus 15 protected AI-Era Performance
cells passed on GitHub Actions before this design was approved. Phase 2 cannot
weaken that gate, its scenario set, its accounting, or its thresholds.

## Constraints

- A-share, cash-only, long-only, daily close decisions and next-session
  execution remain the production model.
- The universe remains within the AI supply chain. Generalization does not
  introduce consumer, liquor, new-energy, or broad-market stocks.
- Scenario-specific parameters, replacement seeds, future-data reuse, and new
  overlays or state machines are prohibited.
- Market and safety rules are not ablation candidates: T+1, price limits,
  suspension and volume constraints, lots, fees, slippage, cash accounting,
  PIT data, determinism, and fail-closed provenance remain invariant.
- Every strategy change or deletion must pass the frozen Phase 1 Performance
  Gate and its affected generalization cells before the next change.

## Frozen Champion

A tracked champion contract records the accepted Phase 1 commit, source,
effective-config, data-manifest, snapshot, numerical environment, lockfile,
and GitHub artifact digest. The contract is immutable validation input, not an
editable score target. Candidate artifacts bind their own exact checked-out
HEAD and compare against this frozen champion without self-signable bypasses.

## AI Universe Contract

One canonical manifest owns all 34 production AI stocks and their PIT industry
membership. Each record contains the symbol, AI-domain assertion, industry,
effective interval, tradability, evidence/review metadata, and content hash.
The stale `sh688205` mapping entry is rejected because it is not a member of
the production reference universe.

Canonical industry names include `optical`, `storage`, `semicap`, `materials`,
and `advanced_packaging`. Industry-only economic validation requires at least
two eligible symbols. Insufficient groups are reported explicitly and never
silently omitted or treated as passing economic samples.

Random pools use base seed `20260810`, immutable seed indexes `0..4`, pool
sizes `5, 9, 15, 20`, canonical symbol sorting, and a versioned deterministic
derivation algorithm. A failing seed is never replaced.

## Generalization Matrix

Every scenario runs independently across all six official Phase 1 windows:

- full AI universe;
- remove `sz300308`, `sz300502`, or `sz300394` individually;
- remove all three core symbols;
- tradable universe without optical stocks;
- industry-balanced universe;
- each sufficiently sampled AI subindustry;
- the 20 fixed random pools.

With the current sufficiently sampled industries this is approximately 32
scenarios per window and 192 economic replays. Pre-window evidence is computed
only from information available before the economic interval. The no-optical
scenario removes optical names from the tradable universe but does not mutate
the production reference-context semantics.

Results preserve every cell and report median, worst case, p10 wealth, p90
drawdown, p90 orders, turnover, Top-1/Top-3 concentration and HHI. Phase 2 uses
intrinsic floors plus frozen-champion non-regression; equality with the frozen
champion is valid. Exact Phase 2 thresholds are written once from the untouched
champion matrix before any strategy modification and cannot later be relaxed.

## Economic Attribution

Stable identifiers propagate from Target through Order, Tranche and Fill:
origin lifecycle, current lifecycle, origin subsystem, mechanism, event ID,
replacement linkage, industry-at-entry and mapping hash. Human-readable reason
text is not an attribution key.

The accounting ledger reports:

- Top-1 and Top-3 positive PnL contribution, signed contribution, PnL HHI and
  absolute-contribution HHI;
- industry contribution and industry HHI;
- CORE, ADD1, ADD2, SATELLITE, RECOVERY and STRATEGIC contribution;
- turnover, trading-session holding period, fees, slippage and all-in costs;
- leader, rotation, recovery and transaction-cost effects;
- cash drag and paired-counterfactual risk avoidance, explicitly separated
  from exact accounting PnL.

Realized and open-lot attribution reconciles to final equity minus initial
cash within tolerance. Post-exit diagnostics cannot read beyond the economic
window.

## Independent Subsystem Ablation

A frozen research registry enumerates each material behavior and its sole
carrier: production configuration where a clean switch already exists, or a
content-addressed research patch where it does not. No permanent production
switch is added merely to make an ablation convenient.

Each of sector guard, chronic and transition overlays, capital ladder, scout,
conviction, recovery, tactical behavior, strategic trailing, restoration, add
tranche, replacement/rotation, dynamic risk anchors, and every other
independent production subsystem is disabled alone from a fresh account. Each
run uses identical data, universe, seeds, environment and champion config, and
must record first divergence, the complete Performance Gate and the complete
Generalization Gate.

An ablation with no behavior divergence is invalid evidence. A subsystem is
deleted only when it improves none of wealth, drawdown, orders, acute defense,
tail behavior or generalization and provides no unique protection. Deletions
are sequential and include code, config, state, serialization, tests, comments
and documentation. The gates are rerun after every accepted deletion.

## Parameter Governance

Every `SystemConfig` field is covered exactly once by a machine-validated
manifest category: `MARKET_RULE`, `SAFETY`, `ECONOMIC`, `DERIVED`, or
`COMPATIBILITY`. Only `ECONOMIC` fields may enter parameter selection, each has
one owning subsystem, derived values are not independently overridden, and
compatibility fields are removed after deterministic equivalence is proven.
Before/after totals and the count of genuinely tunable parameters are reported.

## Future Holdout

The latest market date used by historical optimization, shock cases and
benchmarks is 2026-08-05. The first future holdout session is 2026-08-06.
Future rows are isolated from `data/frozen` so Phase 1 continuous validation
cannot consume them.

The tracked contract freezes the dates and observation policy. A post-checkout
manifest binds the final production commit, source/config/universe/industry
hashes, runtime and lockfile, last in-sample date, first holdout date, and the
2026-08-05 close account, tranche, pending-order and strategy state. The 2026-08-05
decision executing on 2026-08-06 belongs to holdout. With no imported future
sessions, observations and metrics must be null. Parameter changes based on
holdout performance are prohibited; the first review requires 40--60 trading
sessions.

## Manual Execution Journal

An append-only, broker-independent journal records planned price, next open,
actual fill time and price, actual shares, manual skip and derived slippage.
It is observational only and cannot feed production decisions or account state.

## CI and Provenance

Engineering, AI-Era Performance and AI-Era Generalization are independent
blocking checks. Generalization may parallelize by official window, but its
aggregator fails on a missing scenario, changed seed, insufficient provenance,
non-finite metric, stale HEAD or threshold violation. Final artifacts bind the
exact HEAD, production source, effective config, frozen data, universe and
industry manifests, Python/NumPy/Pandas, uv and lockfile. Any final commit after
generation invalidates the artifacts and requires all three gates to rerun.
