# Startup-date repair: research checkpoint, NOT COMPLETE

Authorized contract: `../START_DATE_CONTRACT.json`, dated 2026-09-20 Asia/Tokyo.
B0 is live main `28a4d4ead1e4dd3c98119eec0c57b9f294edf9ef` (verified at task recovery).
Draft integration PR: https://github.com/ychenracing/uquant/pull/73.
No strategy candidate in this checkpoint meets the contract. Do not merge this checkpoint as completion.

## Verified engineering work

The runner propagates child failure, validates identity and payload before reuse,
preserves rejected bytes and failed attempt logs, uses independent attempt identities,
inherits its lock into children, and atomically publishes only validated results.
Seven focused tests passed, covering exit 7, empty/invalid existing artifacts,
same-label retry, successful reuse, outer failure, and actual allocator gate states.
Four native C1 payloads were reused successfully after daily cash, positions and
marked equity reconciliation was added. Reuse did not rerun their economics.
The recovery-only allocation block now reports `RECOVERY_ALLOCATION_ACTIVE`;
freeze, unsettled liabilities and actual settlement protections remain separate.

## Causal findings and bounded experiments

Indicators are prepared from full available price history before account inception;
the observed gap is not explained by clipping their history to the account start.
The original account qualifies a decisive industry reversal on 2023-01-04 and
submits a 95% strategic target, filled over subsequent sessions. On 2023-01-10,
the five-session impulse is no longer present. The new account does not receive
that formation certificate. It later enters other opportunities and finally the
same leader through an ordinary route, with different sizing and holding rights.
Missing the earlier price move is a legitimate difference; a transient market
certificate controlling years of account eligibility is the structural issue under
investigation. Later actual profit-protection state must never be copied backward.

C1 integrates membership-based risk-anchor confirmation from the historical
combined candidate onto B0, retaining current fill attribution and validation fixes.
Fresh native results: W = 32.579245830951976 / 32.56160358866472 /
5.622466803311666 / 1.8984387124959399 for offsets 0 / 1 / 5 / 10.
C1 fails startup robustness; reproducing it is not task completion.

C2 removes an account-claim dependency from ordinary market maturity and aligns
rearm with all current market-entry paths. Its first-half-2023 delayed-start fills
are identical to C1. C3 additionally removes the broad/technology 120-session
positive-return requirement from otherwise confirmed leader-cycle evidence.
It adds a later trade but does not advance the first target leader entry.
Full-window development results reject C3: original W 13.17488044924954,
offset5 W 1.82981679956659, offset10 W 5.429822630637547.
These related relaxations are not being tuned further.

C4 tests market-only observation continuity: recent decisive reversal evidence
can remain observable while its contemporaneous swing low remains unbroken.
It does not simulate pre-inception holdings, orders, high-water marks or grants.
An initial implementation selected an older unrelated setup before the current
setup. A generic recency ordering corrects that prefix, preserving original
January 4 selection and allowing January 19 delayed participation. This is still
experimental: full economics, safety, performance, resume equivalence and all
held-out date clusters remain unverified. Later funding/holding differences are
not presumed to be errors merely because they reduce return.

## Recovery and tactical paired evidence

Window 2025-04-01 through 2025-12-31, three core symbols, was registered from
known native path triggers before reading experiment profits. Each run starts
from real cash and replays its complete prefix. All events in the window are kept.
There are actual TACTICAL_REBOUND, RECOVERY_COHORT and POST_SHOCK_RESTORATION fills;
STRATEGIC_RESTORATION is not substituted for these paths.

Entry leadership weighting raises portfolio W from 5.576242029802855 to
5.679903233990157 and reduces DD from 15.51597624494696% to 15.365546220578596%.
Both have 8 account instructions, 8 fill records, 4 operation days, peak 3
instructions/day. Fees increase from 6236.39649429 to 6348.043319686.
This supports retention within this sample, not a claim of universal benefit.
Removing extra fresh-budget score tilt or reverting expanded tactical eligibility
has exactly zero portfolio effect in this window. The underlying paths trigger;
these incremental rules remain unproven. Their final disposition and verification
must be made on the same final production candidate.
See `../MECHANISM_COMPARISON.json` for turnover, cash, recovery and paired metrics.

## Preservation and resume

`PROVENANCE.json` maps exact local research commits and diagnostic sources.
`source.bundle` contains those exact commits with B0 as its sole prerequisite.
`REMOTE_RECEIPT.json` records the byte-verified 8,373,679-byte raw research archive,
including 250 hashed entries. Original PR71 archive was separately verified against
the user-provided length and SHA-256; all 262 business/log members match its manifest.
Neither archive is replaced by this summary.

Current account and economic state: NOT_MET. Reserved confirmation dates are
unrun. No protected merge has been attempted. Continue the existing PR, retain
failures, and run the complete contract only on a coherent fixed candidate.

## Subsequent evidence (same task, thresholds unchanged)

C4 full development runs preserve W 32.579245830951976 / 32.56160358866472
at offsets 0 / 1 and raise offsets 5 / 10 to 25.077568530797446 /
24.50763140920257. Delayed drawdowns are 31.62023038633569% /
31.833123929567997%, so C4 is explicitly NOT_MET. Their peak-to-trough episode
is 2023-06-20 through 2023-11-01. Full account histories, costs and peaks are
not reset. Only the four development dates have run.

The delayed account's qualified 95% target falls to a 75% funding ceiling after
another small ordinary holding enters during partial deployment. This leaves
room for unrelated entries and changes May/July risk and restoration history.
C6 tests retaining the existing qualified cohort concentration allowance during
continuation, while preserving current cash, gross, symbol, freeze and liability
checks. This is a hypothesis being tested against a C5 control through the
observed drawdown trough, not a declaration that every funding difference is wrong.

C5 removes the unproven second leadership tilt from fresh recovery funding;
entry leadership weighting remains. It caches market-only setup observations,
with today's invalidation check outside the cache. Cold/warm observations and
observations recomputed after truncating future rows agree on the tested dates.
The same-account checkpoint experiment at January 18 and January 20 matches
every subsequent account and decision through January 31. These are bounded
invariant checks, not full economic acceptance.

The native July 2023 tactical test supplied another legitimate trigger window,
registered before its paired results: 2023-07-03 through the frozen calendar's
last Q3 session, 2023-09-28. Expanded tactical eligibility produces W
1.1239075851579703 / DD 6.823342110531982%, versus W 1.0296647183898 /
DD 11.852709509868908% for the original bounded-CAUTION route. Expanded/original
submitted instructions are 10/5, fill records 10/2, operation days 6/3, daily peak
3/3, fees 2401.28768406/437.1582204. The original route has three pending final-day
instructions; expanded has one partly filled pending instruction. No next-day
fill beyond the window is assumed. Expanded eligibility is retained provisionally.
Proportional fresh funding again produces identical portfolio economics in this
second window. See `../NORMAL_TACTICAL_COMPARISON.json` for complete paired rows.

Operational reporting now counts the durable account order ledger, including
unfilled submissions, separately from actual fill records. The replay's compact
executed-order ledger is not a complete submission count.

An additional real durability defect was found in C1 offset5: a prior rearm
authorization can become INVALIDATED after the capital budget reaches zero.
The writer emits a rejected, non-authorized terminal observation; the loader
formerly accepted zero only for OBSERVING. The narrow validator correction
accepts this rejected terminal state only without authorization or consumer
identities. An actual state-machine regression round-trips it and rejects a
forged zero-budget authorization. The runner now invokes the production strict
account decoder and reconciles its final cash/positions against daily evidence.
All four C1 payloads pass that stronger reuse check after the decoder correction;
their historical source identities and economic results are unchanged.

C6's controlled funding change fixes the offset10 prefix drawdown (27.1183%) but
leaves offset5 at 31.3491%. Its remaining divergence is the dominant profit-lock
threshold: real January entry costs place April 24's close on opposite sides of
the same 220% MFE threshold. Offset5 waits until April 28, then follows a different
May reduction/restoration path. No account price or high-water mark is fabricated.

C7 replaces that allocation discontinuity with a one-observed-ATR transition up
to the existing threshold and retained-gross target. Protection can only lower
the existing target; real cost, MFE, cash, shares and account peaks are unchanged.
The matched prefix through November 1 has W 3.8759035747777597 /
3.7966546453884997 / 3.8067454270302092 and DD 27.133916084354315% /
27.23275942565919% / 27.119679209784264% at offsets 0 / 5 / 10.
This is a successful bounded counterfactual, not full acceptance.

Unified candidate `0dc8b02af99a33a18924810e310808035e6171f0`, production tree
`c240eaaf0f62089693b8d88c11decfe403d6a884`, is now running full development dates
0/1/5/10 and same-base entry-weight/tactical ablations in the two registered
mechanism windows. Its production tree equals the tested prefix producer
`1bce3c9e18429ce452f6ee830455bb65055996a0`; the following commit adds only a
boundary regression test. No held-out date has yet been consumed. Do not modify
these running research worktrees. Preserve failed C3/C4/C6 evidence.

`REMOTE_RECEIPT_2.json` records another 4,354,712 byte-verified raw evidence archive
with 256 additional/changed manifest entries, linked to the first checkpoint.
Research source is preserved separately in the updated Git bundle.
