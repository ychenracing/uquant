# Confirm persistence of a real recovery breakout

Base9e988979a2942938b6d5d10cf71e6abe32a4e4a6; independent of rejected f5d7ba1 depth relaxation. Unchanged revised acceptance from holding-basis/ACCEPTANCE.md. At most this additional distinct confirmation design before reassessing the two-result continuation.

Observed evidence: remove308 current risk opens2025-05-06; sz300394 has an actual10-session breakout2025-05-08 at37.878, prior high35.878, ma20=33.6804, ret120=-.43036. OnMay9 andMay12 close36.704/37.857 remains above35.878 andma20, but is not a fresh high; existing scan removes the member and resets pending confirmation. Ordinary trades then occupy the portfolio. The same lost-continuity issue can affect any symbol/period.

Single mechanism: a breakout within the existing recovery_member_confirm_days remains valid only while every subsequent observed close stays at/above its original10-session high and fast MA. Expire it at that unchanged confirmation horizon; after a break require a new breakout. Use only data through the decision date. Existing candidate depth thresholds, cohort breadth, confirmation counts, weak-market guards, risk caps, liabilities and funding are unchanged. No new parameter and no symbol/date special case.

Test-first: plateau confirmation test fails on unchanged base; broken level, broken fast MA, expired breakout and future-data cases retain exclusion. First full economic screens are the same869-session critical removals and original native runner/runtime. Reject if fixed wealth/DD/order rules fail; do not stack the earlier rejected patch or tune nearby thresholds after observing returns.
