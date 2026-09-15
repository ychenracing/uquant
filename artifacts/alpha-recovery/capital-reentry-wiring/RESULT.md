# Recovered capital reentry results

The five previously running diagnostics completed on exact producer
`1aaf83d12117d341626d82a87a87d9d9e263bbc9`. Their original bytes, runtime,
source identity and failed requirements are retained. No replay was repeated
to recover this checkpoint. The source bundle already preserved at `3470123`
was verified and restored; the economic fingerprint is
`ae0f82f502a9f2c5a21e24b3482cf13364d4fed1e48ef0d31c1adffce3e58c10`.

| Scenario | Control wealth | Candidate wealth | Candidate drawdown | Orders |
|---|---:|---:|---:|---:|
| A bull | 11.8706585867 | 11.8706585867 | 16.0048% | 12 |
| E bull | 13.8210227236 | 13.8210227236 | 17.2530% | 13 |
| E 2024 H2 | 1.8168767799 | 1.8168767799 | 9.8540% | 7 |
| Native remove308 | 1.1190839397 | 2.3254337685 | 27.3421% | 64 |
| Native remove502 | 2.3851418754 | 13.1290200829 | 26.3413% | 42 |

Both native cases use the complete 869-session window and match their paired
control configuration, data, point-in-time roles, execution contract, runner
and canonical runtime. The candidate first changes their equity on 2024-07-23.
The recovery counter now reaches its required count in production, unlike the
earlier dormant implementation. A/E/H2 retain exactly the control equity paths.

This is a meaningful mechanism result, not joint acceptance. The native cases
exceed the current 40-order requirement and retain only one realized strategic
epoch/owner. The historical two-epoch/two-owner requirement therefore also
fails. Do not relabel ordinary holdings as realized strategic epochs. Costs,
the full candidate account, all windows and final engineering remain to verify.
The previously authorized one-percentage-point H2 drawdown margin remains an
acceptance adjustment, not a return improvement.

## Next causal question

The additional remove308 turnover is mainly ordinary leader entry and lifecycle
exit, not duplicate orders. Of 23 lifecycle exit fills, 11 occur on signals with
close still above MA60. Current ordinary exit logic gives an unproven holding
the faster MA20 horizon, while protecting a holding with historical MFE >=20%
with MA60. The proposed distinct mechanism is one medium-trend holding horizon
for ordinary trend capital, retaining independent risk reductions, mature-hold
protection, relative-return evidence and distinct-session confirmation. This
replaces a conditional exit horizon; it introduces no new threshold or symbol,
date or scenario switch. Its economic effect and drawdown cost remain unknown.

The user's latest instruction authorizes core strategy and risk redesign and
accepts risk adjustment. Research should concentrate on this now-observed
recovery/holding interaction, preserve the existing successful paths, and avoid
another confirmation/deployment/capacity sweep. Continue on the same recovery
line. No main merge is justified by this checkpoint.
