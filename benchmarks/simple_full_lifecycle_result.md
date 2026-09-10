# Complete E nominal result — not promoted

Immutable LOCAL29971d4a767cfddd2dcbce7deb172b9ebe36afe6 / REMOTE997b6bc8293a66c8bde164eef6e5a4b2c45892d9,
economic sourcee4eb4dff7566ca41f1216abd055a5e63dfeb6d9551dc16e06be77772b90fe72a.
All14 native cases completed and source/config/frozen data/universe/runtime/raw/
account/PnL readbacks PASS. Current nominal11PASS/3FAIL, cross-window improvement
requirements PASS. Fixed protected a/bull11.6582546524/DD.156694888/10orders
currentPASS, native cache readback PASS, original wealth failures retained.

| Case | Wealth | DD | Orders | Current |
| --- | ---: | ---: | ---: | --- |
| champion continuous | 25.3412815016 | .2714697315 | 14 | PASS |
| full continuous | 28.2117904934 | .2712307181 | 20 | PASS |
| no_optical continuous | 1.3599681624 | .2512890227 | 16 | PASS including cost/turnover |
| remove_all_three continuous | 1.3929914234 | .2467109849 | 15 | PASS including cost/turnover |
| no_optical later | 5.7216773192 | .2026171074 | 30 | PASS |
| remove_all_three later | 1.8354108228 | .1124261537 | 20 | PASS |
| remove_all_three H1-2023 | 1.2525906143 | .2337412508 | 10 | FAIL DD.2170542996 |
| no_optical H2-2024 | 1.2803683381 | .2040348619 | 7 | FAIL wealth1.5444534766/DD.1058304885 |
| remove_all_three H2-2024 | 1.2429965062 | .2040348619 | 7 | FAIL wealth1.5444534766/DD.1058304885 |

Remaining five half-year rows PASS. Full JSON keeps all original/current verdicts.
H2-2024 misses are material, not comparable small margins; no extra tolerance or
broad robustness run for this rejected whole strategy. No-optical continuous is
also close to its floor; a nominal pass does not establish robustness.

First H2-2024 decision divergence2024-07-16: selectedcontrol no BUY; E buys688256,
688766,002409 at.2666667 each, broadret120=.06519455,techret120=.00279584.
Later first added buys2025-02-05: broadret120=.10990943,techret120=.43585566.
Both old impulse confirmations were false. Restoring that entire impulse shortcut
would also suppress the observed useful later entry; do not claim it solves both.
These dates are mechanism evidence, not proof of an unrun filter's profitability.

Independent validation-only F(adb97cbe / remote3b8ed503b363bfcb0195c01b127c0310e073a667)
updated current Absolute source/registry/config/seal, retaining every frozen policy
field.35 contract tests and focused review PASS; economic fingerprint unchanged.
131 ownership models,56 focused lifecycle tests, whole Ruff and mypy336 PASS.
Full application/architecture attempts found failures and were interruptedexit130,
not completed acceptance. First causes:267 absent historical Git blobs restored
from immutablecd3551, each hash verified and fullgitarchive/first governance test
then PASS; obsolete ordinary_market.confirmed assertion; Absolute successful
fixture still loads immutable739 raw while current source isE. Never relabel raw.
These broader engineering issues remain, and complete L4 has not passed.
