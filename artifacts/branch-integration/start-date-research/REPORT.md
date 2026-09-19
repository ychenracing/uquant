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

### C7 full development and contract screen (not accepted)

The four development replays completed at the exact unified source above:
W 32.316992795022415 / 32.31090501796239 / 31.653480772240975 /
31.72720416499964 for offsets 0/1/5/10, with DD 27.1339% / 27.1471% /
27.2328% / 27.1197%. Offset5/full is 97.95%; full retains 99.20% of B0.
The six pool/cost replays also completed. `../C7_ROBUSTNESS_RESULT.json` is
NOT_MET: weak-pool geometric retention is 0.9653384704227385 (required 1.20),
and offset5 cost-stress retention is 0.580883683800839 (required 0.90).
Strong pools and absolute drawdown pass among these ten completed cells.
The thirteen confirmation cells remain NOT_RUN; no reserve dates were consumed.

The first cost-stress control-path divergence is July 20, 2023: normal cost
reduces the strategic holding to 0.5 through CAPITAL_BUDGET, whereas doubled
cost invokes CRISIS at 0.2. This is not explained by fees alone. The weak pools
all take the same January 4 sh688200 strategic entry, sourced from the December
7 market observation, and converge to W 1.0417438648145532. Investigate these
causal paths before changing the candidate or consuming confirmation dates.

Final same-base mechanism pairs completed and passed payload/identity validation;
see `../UNIFIED_MECHANISM_COMPARISON.json`. Entry weighting produces W
5.679903233990157 versus 5.524467185719514 without it in the registered 2025
window. Expanded tactical eligibility produces W 1.1239075851579703 versus
1.0296647183898 in the registered Q3 2023 window. Keep both provisionally;
the second fresh-funding score tilt remains removed. These are portfolio-level
comparisons with all window events included, not attribution-tag profit claims.
Integrated architecture/runner/screen/boundary checks and updated membership
anchor regressions passed. This does not turn the failed economic screen into
acceptance or authorize merging this candidate.

### C8 causal counterfactuals and next bounded hypothesis

`../C8_COUNTERFACTUALS.json` preserves separate experiments. Requiring current
close at or above the observed formation close (while preserving its original
swing-low invalidation) restores loo308 W 1.8633650614478565 / DD
0.27108751240098516. It does not create account history or require the account
to have witnessed the original impulse. Keep provisionally; other weak cells
are not yet verified under this revision.

Unifying generic strategic crisis severity with the existing strategic route
leaves normal offset5 unchanged but worsens double-cost to W 9.602531732519818 /
DD 0.30931143242650383. Reject this change; do not keep it merely for apparent
state-machine uniformity. Its exact source and raw failure remain preserved.

C9 instead tests execution granularity in profit protection: C7 emits April24
targets 0.70646/0.71107, then the remaining reduction to the policy's 0.70
floor is smaller than the existing 0.05 minimum trade weight. Complete that
sub-minimum protective remainder with the current sale, while retaining the
one-ATR gradual transition outside the final minimum-sized band. This introduces
at most the existing minimum trade-size step and never increases exposure.
No crisis severity, capital ladder, loss history or restoration budget is reset.
Test offsets0/5/10 plus double-cost5 only through the known November1 risk trough
before any new full-window run or confirmation dates. C9 includes the supported
current-price confirmation, excludes rejected crisis reclassification.

The new market-observation helper is moved to a dedicated application module
because the unchanged architecture budget rejects growth of decision.py beyond
its limit. This preserves its calculation and cache identity; no budget is
weakened. C7 raw archive3 has been byte-verified remotely (REMOTE_RECEIPT_3.json).

C9's bounded counterfactual passes the known risk trough: W at offsets0/5/10/
double-cost5 is 3.8759035747777597 / 3.78644793330406 / 3.78644793330406 /
3.77851160822668; DD is 27.1339% / 27.1222% / 27.1222% / 27.2185%.
All four retain the July20 capital reduction then July21 concentrated crisis.
See `../EXECUTABLE_PROTECTION_PREFIX.json`. This is not full-window acceptance.

Frozen full-window producer: `33e310ec6aee04b50b2fa907c3e5935e1c88c557`,
uquant tree `27e436e2be97d2bf82bb29eab95450f976e091e0`. The economic/full-package
source surfaces now explicitly include market_observations.py and their seal
is recomputed; the prefix producer's missing new-module registry entry is not
carried into final runs. New full development/pool/cost runs use this exact source
and fresh accounts. Same-base mechanism ablations use independent commits.

Thirty-six source-identity, runner, reason/screen and protection-boundary tests
pass. The unchanged complexity budget passes after moving market observation
out of the decision allocation function. Real January4/10/19 observations are
identical with a cold/warm cache and with all future rows removed; every retained
owner meets the current formation-price test. See the current verification JSON.
No confirmation or reserve date has yet run. This candidate remains unaccepted
until complete economic results, raw preservation and repository gates pass.

Same-C9-base mechanism pairs retain the previously observed economics exactly;
`../C9_MECHANISM_COMPARISON.json` also records instruction-set displacement and
portfolio costs/cash. Entry weighting W5.679903233990157 versus5.524467185719514;
expanded tactical W1.1239075851579703 versus1.0296647183898. The legacy raw field
`peak_to_recovery_days` actually counts sessions after the trough and returns
remaining window sessions when unrecovered. Preserve it, but report actual
peak/trough/recovery dates and censoring separately: expanded tactical recovers
July24's trough on August31 (28 sessions); control's August25 trough is still
unrecovered on September28. Its raw24 is not proof of successful recovery.

C9 actual-account checkpoints at July19 and July21, under both normal and doubled
costs, reproduce every subsequent account field and decision through July24.
No account or risk state is fabricated for this equivalence test.
`REMOTE_RECEIPT_4.json` records another byte-verified archive:5,897,141 bytes,
205 incremental entries. It includes rejected C8 originals, C9 prefixes and
mechanism pairs, causal/resume traces and the first six completed full C9 cells.
The four remaining development/strong cells and confirmation dates need later
preservation. The original no_optical five-name tradable exclusion is also being
observed on C9; this does not exclude all optical reference roles and is not a
new acceptance blocker.

### C9 fixed-candidate confirmation in progress

All ten development/pool/cost cells complete at the frozen producer above;
`../C9_DEVELOPMENT_RESULT.json` has no failed available gate and remains INCOMPLETE
only for thirteen confirmation dates. Weak-pool geometric retention is
1.588688947412487; normal/double-cost wealth retention is 0.9946152513091567
for full and 0.9939220681735514 for offset5. Thirteen confirmation replays began
2026-09-19 19:01 UTC. These dates are now consumed; reserve dates remain unrun.

The four native mechanism checkpoint resumes pass. Both treatment and control
start from the same actual C9 prefix account (May7 2025 / July4 2023). Explicit
code migration preserves the economic-state digest. Every subsequent cash,
position and equity observation matches the corresponding full native paired
replay; all final accounts pass strict decoding. See
`MECHANISM_CHECKPOINT_VERIFICATION.json`. No favorable state is hand-injected.

C9's original no_optical observation is W1.6697556792437505, DD22.561481451191534%,
19 account instructions, 20 fill records and 16 operation days. It retains the
old limited five-name tradable exclusion and reference roles. This is not evidence
of full optical-industry independence and does not enter new weak-pool/risk gates.

The original complete PR71 archive is now independently saved and byte-read back:
6,951,866 bytes, SHA256 aa66970d04e31b73743501b91eb4c4d999d02f11b7b4c76318c3e852f3db4ab0.
See ORIGINAL_PR71_REMOTE_RECEIPT.json (263 members including MANIFEST).
The C9 GitHub Grant job passed 92 safety/identity tests, then its serial native
replay was cancelled at the existing 25-minute job limit. No economic conclusion
was produced. The workflow budget is raised to 60 minutes and its existing
identity-bound per-case cache is preserved as an artifact. No assertion or
acceptance threshold is removed. A matching native local acceptance is running.

### Fixed C9 complete economic matrix

All 23 core cells passed the strict source/input/runtime/payload screen:
ECONOMIC_MATRIX_MET, failures=[], NOT_RUN=[]. Cluster R_common is
0.966103551803152 / 0.8631011864156226 / 0.9998176598235909 for 2023/2024/2025.
Offset5/full W is 0.977031; worst core DD is 27.2412%. Confirmation results
were not used to modify C9; all reserve dates remain unrun. The 2024 cluster
includes real participation and losses, not a claim of universal profitability.
See C9_DELIVERY_REPORT.md, ../C9_FINAL_RESULT.json and C9_RAW_MANIFEST.json.
All remaining matrix originals and shared-checkpoint mechanism states are
byte-verified in REMOTE_RECEIPT_5.json (8,503,968 bytes,193 incremental entries).
This supersedes earlier in-progress status, not historical failed candidates.
Grant acceptance and final publication reconciliation remain pending.
