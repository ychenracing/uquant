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
