# Global bottleneck decision — 2026-09-09

## Outcome

The bounded round is complete. Reject the only candidate at qualification
preflight and retain no economic strategy change. This is NOT evidence of
improved profitability, successful generalization or permission to merge C.
PR56 stays draft. Main remains 960539a89408cc7c1fc3937bda19c9f760095012.

The useful decision is to stop uniform first-position downsizing and direct
replacement of risk-anchor coverage as next small patches. Qualification,
available capital and recovery have different bottlenecks; there is no
supported single global relaxation in the inspected evidence.

## Evidence scope

Nine complete native paths were read back using research.cross_ai_acceptance:
four current-main controls, four C controls and the previous rejected H1
candidate. Result seals, source/config/runner identity, role membership,
raw/account hashes, execution dates and native accounting were checked.
Full periods end 2026-08-05; the H1 screen ends 2023-06-30. No protected
Future Holdout was read. These are reused research samples, not fresh
out-of-sample validation. No new economic replay or full CI matrix was run.

Current production source is d8b104b3faea5ffcdc1b0c888fe8844814c99552300ee0b3d2b55d156b4bd303.
The previous round verified the cleanup's neutrality against C in full and
strict-removal paths; this audit does not relabel C's raw manifests as new runs.

| Path | Main wealth | C wealth | Main DD | C DD | Main / C orders |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full | 30.743312 | 30.743312 | .271470 | .271470 | 20 / 20 |
| Remove three | 2.397616 | 2.397616 | .202292 | .202292 | 14 / 14 |
| Also remove sz300666 | .910933 | 2.448595 | .168164 | .158389 | 18 / 32 |
| No optical H1 | 1.447695 | 1.447695 | .244243 | .244243 | 6 / 6 |

C's strict-removal improvement is already real on this particular historical
path. It should not be erased or mistaken for broad acceptance. Existing
Performance, robustness, Absolute and Ownership failures/unfinished gates remain.

## Global attribution and decisions

| C path | Flat days | Flat days with READY unheld stock | Flat, unfrozen, READY days | Frozen days with READY unheld stock |
| --- | ---: | ---: | ---: | ---: |
| Full | 30 | 1 | 1 | 56 |
| Remove three | 329 | 29 | 1 | 61 |
| Also remove sz300666 | 482 | 8 | 3 | 11 |
| No optical H1 | 23 | 1 | 1 | 0 |

These are overlapping day counts, not independent trades or potential profits.
READY is qualification, not capital authorization. In particular, 300/329 and
474/482 flat days in the two deletion paths had no READY stock. Relaxing
deployment alone cannot create a qualified candidate on those observations.
Conversely, some real READY observations are blocked by risk or capital, so
it would also be wrong to call the entire problem insufficient qualification.

1. **Initial capital:** do not revive the rejected entry mechanism with smaller
   sizing. C's no-optical path has no frozen/unheld/READY day; that earlier
   failure chain is not shared there. Several losing C admissions already
   start at 10–20%, whereas important winners start at 40–50%. This does not
   prove the current sizing optimal; it fails to support a uniform reduction.
2. **Coverage and selection:** the March 2023 opportunity was already in the
   pool. At 2023-03-06, sh688256's score is .842872 in remove-three and .843046
   after also removing sz300666, with identical raw price/history fields.
   Broad/tech 120-day returns are .016141/.092581 on both paths. Yet risk
   anchors change from three groups to zero and its qualification disappears.
   Across the two paths, 17 common symbol-days show READY -> unconfirmed with
   the same raw price fields. This demonstrates sensitivity, not an economic
   bug: later account histories and qualification clocks also differ.
3. **Holding/rotation:** C already turns the strict-removal main loss into a
   gain, including positive contributions from sh688300 and sh688498. This
   round has not measured executable post-exit opportunity loss or a better
   rotation policy. Do not justify a fresh holding rewrite from entry counts.
4. **Recovery:** inspect upstream qualification and downstream authorization
   separately. The absence of ordinary READY on prior repair-READY dates is
   consistent with the above table; the denominators are different. A universal
   recovery unlock would not solve the numerous no-candidate dates.
5. **Complexity:** current same-industry cohorts can use independent external
   risk coverage. Replacing it with the cohort's own industry count loses that
   relationship. An additional OR fallback would add another qualification
   exception and is outside this round's fixed candidate. It was not added.

## Candidate, falsifier and restoration

The fixed candidate replaced the dynamic-anchor group count in
strategic_qualification_evidence with the number of distinct known industries
in the route itself, preserving the minimum and other gates. This was a policy
hypothesis, not a presumed bug fix.

Existing tests in test_strategic_grant_observation.py,
test_strategic_universe_quorum.py and test_unified_strategic_selection.py:
**12 failed / 35 passed** with the candidate. Failures include losing a valid
qualification during freeze, absent grants needed for lifecycle validation,
and FULL_COHORT becoming STRONG_PAIR. The issue is a real qualification
regression, not a syntax problem or an assertion updated to force acceptance.

The candidate was reverted immediately. The same 47 tests then all passed
in 1.78 seconds. Restored production fingerprint exactly matches the starting
fingerprint above. No threshold tuning, OR fallback, second candidate, or
economic simulation followed the preflight failure.

| Checkpoint | Original local commit | Published commit |
| --- | --- | --- |
| Preregistered audit/design | c216bb231eef98cd440acd15115c1ecc676cba3a | 481b25a485f1f8d544e5d987eb952d77fac7b642 |
| Rejected coverage candidate | caf663c385fb0a8478ceafcc02a82b2657cb4f3e | 23b02f3da543cb5dd4698c9bbde0dbbcbec317a4 |
| Revert | 7ed767da6a810ef374cdc7257540beb2705db11c | c6ef044746b8b14b9c7e851cae2fba45a197bcee |

Publication uses the authenticated GitHub connector; each published tree was
verified equal to its original local tree. Original SHA mappings are in commit
messages. The preregistration was published before the candidate was changed.

## Next decision, without another automatic patch cycle

Keep the current production policy and C's existing evidence. Do not restart
admission relaxation, uniform sizing reduction or anchor-count replacement
without new distinguishing evidence. The next research deliverable should be
a measured, executable opportunity/capture comparison for the remaining AI
groups, using existing simple baselines first. It must separate a weak pool
from a weak selector and a good selector from costly portfolio interactions.
This audit does not establish the pool's attainable profit ceiling, so it
cannot claim that removing the optical leaders leaves comparable opportunity.

Only a demonstrated gap should select another economic change. If existing
simple comparators fail too, do not keep fitting this same history; preserve
the frozen criteria and move to an explicitly scoped new-sample observation
plan. That is a future decision, not a silently scheduled job or permission
to consume the protected holdout. Full production acceptance has no credible
remaining-duration estimate until the unresolved gates and candidate are fixed.

## Recovery material

global_bottleneck_receipts.json.gz contains all nine audit outputs, original
source seals/hashes, sensitivity and route traces, failed candidate test log
and restored test log. The adjacent manifest binds its compressed and raw
hashes; the archive was decompressed and checked before publication.
It is diagnostic evidence, not the underlying native replay archives. The
previous 80.9 MB raw archive's durable-save problem remains unresolved.

Reproduce the audit with the source directories restored from matching native
archives (their expected identities are checked, not silently replaced):

```sh
python -m research.global_bottleneck_audit --runs /path/to/runs \
  --rejected /path/to/rejected/no_optical_h1 --output /path/to/audit.json
```

No background economic job or new strategy schedule is left running.
