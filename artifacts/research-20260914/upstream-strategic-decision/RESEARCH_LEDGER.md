# Upstream strategic decision ledger

## H9 — reject unconditional weak-certificate deferral

Source commit `2be82f589d389013518d341080a8f9d09b335a7b`, tree
`c3d58002f95f5fcbed1b884c0cf36f0fa19d8dc4`, based on the exact preserved H8
tree through commits `cbaead21d8610fcd5b025ed5f0776ce60fd760ab` and
`db5635f895a5a534c62d6edd8f07b924e6d5fb01`.

Hypothesis: keep a nondecisive `reversal_industry` `FULL_COHORT` eligible and
observable, but defer the prospective grant when both market confirmation and
owner absolute quality fail. Prevent the same certificate from bypassing that
capital decision through ordinary CORE admission. Existing grants, positions,
orders, fills, duties, risk conditions, eligibility rules, and thresholds were
not changed.

The focused behavior/regression suite passed 34 tests. Ruff and mypy passed for
the changed modules. E acute replayed from 2024-07-01, retained the original
2024-08-01 through 2024-09-02 measurement interval, and improved from H8's
-2.8553764161% to +4.6108107936%. E 2024-H2 improved from 1.3955925424x to
1.8168767799x. A, D, and E bull were unchanged from H8.

H9 is rejected as an integrated candidate. Native point-in-time remove-308
fell from H8's 1.1190839397x to 0.8949076697x and changed the protected epoch
path. Native remove-502 reached 2.4868745924x but retained only one realized
strategic epoch and one distinct owner instead of the required protected 2/2
path. The rule also deferred the lawful 2023-01-19 initial `sz300223`
`FULL_COHORT` commitment in both removals; H8 had a four-session confirmation
streak there, while E's losing 2024-07-22 certificate first became ready at the
existing three-session minimum and disappeared on the next session. Therefore
the unconditional deferral is too broad. The raw traces below remain diagnostic
evidence and must not be relabeled as canonical acceptance.

| Cell | Wealth / return | Max drawdown | Orders | Result |
|---|---:|---:|---:|---|
| E acute | +4.6108107936% | 0.8887256946% prefix MDD | 2 | PASS |
| E 2024-H2 | 1.8168767799x | 9.8540242179% | 7 | PASS |
| A bull | 11.8706585867x | 16.0048175923% | 12 | PASS |
| D bull | 8.8774550752x | 24.2806120730% | 18 | PASS |
| E bull | 13.8210227236x | 17.2530367369% | 13 | PASS |
| Native remove308 | 0.8949076697x | 20.9613969697% | 13 | FAIL |
| Native remove502 | 2.4868745924x | 23.5117797862% | 16 | FAIL: 1 epoch / 1 owner |

