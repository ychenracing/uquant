# Mechanism comparison and integrated challenger — 2026-09-09

## Decision

Do not replace the ordinary production policy. The one fixed integrated
challenger made money in its first screen but missed the preregistered wealth
floor. It was reverted, with no later long screens, retuning or second candidate.
No production improvement or full acceptance is claimed. PR56 stays draft;
main remains 960539a. The source after revert is exactly the starting source.

## Reused comparison

Eight standalone monthly/daily comparator paths passed native readback again,
under the original main-source workspace and the trusted producer hashes already
recorded in their reports. No old economic replay was repeated. Main/C metrics
are reused from the nine-path sealed audit in global_bottleneck_receipts.json.gz.

| Mechanism | Full wealth | Remove-three wealth | Also-minus-sz300666 wealth | No-optical H1 wealth |
| --- | ---: | ---: | ---: | ---: |
| Main, full strategic/ordinary book | 30.743312 | 2.397616 | .910933 | 1.447695 |
| C, full strategic/ordinary book | 30.743312 | 2.397616 | 2.448595 | 1.447695 |
| Monthly MA standalone | 1.304007 | .884397 | .884397 | .884397 |
| Daily-confirmed MA standalone | 1.162557 | .873263 | .873263 | .873263 |

The standalone rows do not include the strategic-owner path. They cannot be
used as a clean ablation of strategic value. Their identical removal paths
share one early-loss/frozen-cash mechanism, not three independent replications.
The monthly/daily runner calls the shared risk evaluator but not the complete
production allocator's independent-core cash-rearm authorization. Consequently,
their failure does not establish that every integrated simple signal fails.

The surviving evidence supports retaining main as the production control and
preserving C's strict-removal improvement for its outstanding acceptance work.
It does not support deleting strategic ownership or promoting the standalone MA
policy. Repeated rank/confirmation tuning on these same paths was not performed.

## One new integrated policy

As preregistered, the challenger replaced ordinary entry, ranking, initial
sizing and exit decisions together: five historical valid-bar MA60/MA120/ret120
confirmation, ret120 ranking, three total occupied slots, up to 30% fresh weight
within the existing capital budget, and two-close MA120 exit. No discretionary
monthly top-ups or deterioration transfer. Existing data/industry/liquidity
checks, strategic ownership, risk, independent recovery authority, costs and
native next-open execution remained. A simple signal never became a strategic
certificate or independently authorized a frozen-account repair.

This differs from the old standalone in more than recovery integration: risk
budgeting, structural/data checks, historical confirmation startup and holding
behavior also differ. Improvement versus standalone cannot be causally assigned
to the recovery machinery alone. Source diff: 49 production lines added and
103 removed (net -54), with no new state model, configuration or dependency.
This is a local code reduction, not a measured project-wide complexity score.

| No-optical H1 metric | Main / C | Integrated challenger |
| --- | ---: | ---: |
| Wealth including initial capital | 1.4476947005385299 | 1.27222751175821 |
| Net return | 44.7695% | 27.2228% |
| Maximum drawdown | .2442425185317515 | .23553096798090134 |
| Actual account orders | 6 | 8 |
| Cash fees | 2177.92992294 | 2429.36348358 |
| Slippage cost | 4094.6689999999335 | 4728.612999999841 |

The wealth floor 1.3753099655116032 fails. Drawdown and <=12 order gates pass.
Lower drawdown and fewer code lines do not waive the failed wealth gate.
Both native runs passed source/config/data-role/accounting/readback checks:
the nine-session sentinel took 24.22 seconds, and H1's 118 sessions took 76.31
seconds including readback. The sentinel had no trades; actual execution
validation comes from the H1 replay, not the empty sentinel alone.

## Where the economic difference occurred

The challenger really entered sh688256 on the 2023-02-27 signal (next-open
fill on Feb 28), before C's March 6 signal. Discovering it earlier was not
enough to outperform C. Compared with C, net symbol contributions differ by:

| Symbol | Challenger minus C, currency units |
| --- | ---: |
| sh688256 | -211304.53156939975 |
| sh688766 | -120165.2255152799 |
| sh688200 | -43608.85208063995 |
| sz002281 | +24144.23160468007 |
| Total | -350934.3775606395 |

The sum reconciles to the equity difference at 2,000,000 initial capital.
This is realized/open PnL attribution, not an isolated estimate of entry,
sizing or exit causality. The challenger bought sh688200 and sh688766 earlier,
and sh688766 was an additional losing position absent from C in this window.
Both remaining challenger positions were reduced by the unchanged CRISIS path
on the May 5 signal, filled May 8. No guard bypass was used to improve results.

## Verification and known test differences

The initial 52-test run had 48 passes and four failures. One new ranking test
fixture lacked the existing ret60 structural field; that fixture was completed.
Three old policy assertions require the old ordinary-market `confirmed` field,
old peer selection, certificate shape or old .40/.35 weights. They remain
recorded as changed-policy failures, not silently altered to obtain acceptance.
The preceding grant/epoch assertions passed before those policy assertions.

The six new causal/data/exit/ranking/freeze/authority tests plus 13 cash-rearm
safety cases passed (19 total). Ruff and mypy passed for the candidate source.
This supports bounded research execution, not full engineering acceptance.
After revert, all 60 affected existing grant/quorum/selection/cash-rearm tests
passed in 3.70 seconds. No full matrix or manual CI dispatch was started.

| Checkpoint | Local producer | Published equivalent tree |
| --- | --- | --- |
| Preregistration | 7fb48c17302dd4aa7cf1b9ecf129a5b22f91788f | 349c67fb90df411476375a2fe6cc3d3431911795 |
| Challenger | 226ef63b5aabbaf402a0eff0766eca73b32d2e24 | 0476517df2c35cfaec86481ee1e7c64a6f6deb45 |
| Revert | 0d1cd1b8cd312e1e1350a1e973e17e83f04e1ea6 | 04ff27550957a22cbbb4af9779bf0568a714d958 |

Candidate production fingerprint:
b7f54f137576ba15787827103103d4710369d105519c463f4289b1251dbf2942.
H1 result seal: b8c8d73d9c6c86967bacff29a7bf6f76decf81b1dea1d32e8c27e02222825493.
Restored source: d8b104b3faea5ffcdc1b0c888fe8844814c99552300ee0b3d2b55d156b4bd303.

## Resulting direction

This round closes the immediate claim that simpler ordinary rules can be
substituted profitably just by putting them into the native account flow.
This particular integrated candidate is rejected; the broader class is not
statistically disproven. Do not rerun another MA period, confirmation length,
position cap or holding threshold on these observed losses.

Keep the existing profitable core, the known C benefit and all failed evidence.
The next useful decision requires evidence about the capture of available
non-optical opportunities, with explicit separation of stock selection and
portfolio interaction. Earlier entry alone is now directly insufficient on
this comparison. Broad pool quality, attainable opportunity and new-sample
generalization are still unmeasured; this report must not claim those goals
met or automatically launch another modification. Protected Future Holdout
was not read. There is no background research task left running.

## Preserved evidence

Raw archive uquant_simple_policy_evidence_20260909.tar.gz is saved durably:
Library file libfile_0089fab2c0188191b1d344ff42e8f5bf, 14,284,474 bytes,
SHA256 e963353fd0e2095b70ba03424f36882b04e2c6b8467bcaff186b3fce8d9428e0.
All 448 archive members were size/hash checked. It contains both new native
runs, eight standalone controls, main/C H1 controls, scripts, test logs,
comparison and attribution receipts, plus the original local producer bundle.
This does not resolve the separate previous round's 80.9 MB archive-save gap.
