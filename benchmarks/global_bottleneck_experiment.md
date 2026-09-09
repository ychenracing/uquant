# Global bottleneck round — 2026-09-09

Authorization: one bounded 2–3 hour round, at most one candidate, same draft
PR56, no main merge. Starting remote HEAD 4b87373; main 960539a. C is still
unaccepted. The preceding market-confirmation candidate remains rejected.

## Evidence and choice, before implementation

Native readback of four main paths, four C paths and the rejected H1 path
completed. The main/C full and remove-three paths have identical scalar
economics. Strict-removal C improves wealth 0.91093 -> 2.44859 over main,
but this does not repair its outstanding acceptance failures.

The rejected candidate's two 40% early positions are not representative of
current C admissions. Current substantial allocations include major winners;
many losing C admissions already start at 10–20%. Do not rescue the rejected
candidate through a new sizing rule. No uniform initial-size reduction chosen.

On 2023-03-06, sh688256 is READY in remove-three and unconfirmed after also
removing sz300666. Its raw price history is unchanged and its leader score
slightly increases. Broad/technology index confirmation also persists, while
risk_anchor_group_count goes from 3 to 0. The qualification calculation depends
on this separate dynamic-anchor formation state. Seventeen common symbol-days
show READY -> CONFIRMATION_INCOMPLETE with identical raw price fields across
the two paths; later observations also contain divergent account histories.
These are sensitivity observations, not 17 causal missed trades.

## One fixed candidate

In strategic_qualification_evidence, replace coverage from the dynamic risk
anchor count with the existing distinct known industries of the actual route
witnesses. Keep the same strategic_cohort_min_size, market predicate,
confirmation clocks, route quality, accounting, risk and execution code.
No new parameter, state, alternative entry route or security/date exception.
This changes qualification policy, and is NOT claimed to be a bug fix.

Important falsifier: same-industry cohorts can currently be supported by
independent risk anchors. The replacement may wrongly eliminate that valid
relationship. First run existing grant-observation, quorum and selection
tests. Reject at preflight if real qualification/authority contracts regress;
do not rewrite their assertions to accommodate the candidate, add an OR
fallback or proceed to expensive economics after that failure.

If preflight passes: short native sentinel, then no_optical H1 first, with
wealth >= 1.3753099655116032, DD <= .2442425185317515 and <=12 orders.
On pass, run remove-three, strict-removal and full once, with respective
wealth floors 2.397615989680009, 2.4485949990159668, 29.206146144512992,
DD <= .30 and orders <=32; reuse verified main/C controls. No screen replaces
full promotion acceptance. Stop on first failed screen without retuning.

If no candidate survives, retain the diagnostic evidence and explicit rejected
directions. Do not manufacture a production change to fill the time budget.
Future Holdout stays unread; frozen criteria and existing failures stay intact.
