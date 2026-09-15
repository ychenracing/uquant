# Deployment-only result: rejected

Exact producer: `7b000bc9170e6c9233dd82fd38bc34613be0fafd`.
Production fingerprint: `eb066ef7d0b81dec36ea2c86a511c71f536670507d6d843cabebe95e678699bb`.
All three previously unrun diagnostics completed with exit 0. Existing 88 tests were reused, not rerun.

| Diagnostic | Canonical current | Deployment-only |
|---|---:|---:|
| A bull final wealth | 11.87065858669219 | 11.87065858669219 |
| E bull final wealth | 13.821022723557565 | 2.0787587597870383 |
| E maximum drawdown | 0.17253036736852323 | 0.18523439864176305 |
| E account orders | 13 | 34 |
| Native remove308 prefix final equity | 2219163.36163238 | 2290629.2902634004 |

The prefix gains 71465.9286310204 (3.2204% relative equity), but E wealth loses about 84.96%. Reject this component as a production candidate. No wider matrix is justified for it. Capacity's zero-alpha result remains unchanged; no combination or production promotion was performed.

## Verified comparison identity

`compare.py` reads raw gzip files, checks complete observations, exact producer, configuration, seed, interval, runtime, inputs and session alignment. The only changed recorded source is `uquant/portfolio/pipeline.py`; all frozen data, contracts and other recorded source inputs match canonical current. A/E runner and adapter hashes match exactly.

The first strict prefix comparison failed on runner hash; retained here as a verification finding. Examination of both exact scripts found only root/output CLI argument resolution differs. `compare.py` verifies each original hash and exact byte equality after those two explicit substitutions. Replay and observation code are identical, and runtime/config/contract/session/input identities match. This is an explicit runner bridge, not an identical-hash claim or a new economic replay. `comparison.json` retains daily equity differences and original raw hashes.

## Failure mechanism and corrected attribution

`path-diagnosis.json` extracts the original E observations. Both baseline and deployment-only still reject sh603986 as CONFIRMATION_INCOMPLETE on 2025-02-05. On February 7 it is READY under the original qualification in both arms, but baseline deployment confirmation blocks it. Removing that block admits three ordinary targets, first filled February 10, then grows the ordinary book.

By February 28 the deployment book has gross exposure 0.960765 and activates SECTOR_GUARD; baseline remains cash without that holding-dependent state. March 3 sale leaves 2208095.5524728964, above the January cash level 2197772.75318636. Thus the collapse is not simply a realized loss on the February trades. The ensuing legitimate risk state persists through April 3 and May 8 and prevents the historical recovery path. Later June ordinary positions also compete with July leaders for slots.

This corrects the earlier hypothesis that retaining stock confirmation would avoid the failed joint candidate's major path problem: it delays initial admission by two sessions, but does not prevent it. Neither clearing risk history nor adding a date/name-specific gate is supported or authorized. The whole subsequent wealth loss is path-dependent and cannot be attributed additively to one gate.

## Continuation

Deployment-only is rejected; do not tune or expand it. Next inspect the already recorded October restoration decision/funding path in the unchanged current candidate to distinguish discretionary funding loss from frozen risk constraints. Do not reconnect historical warning substitution or change eligibility, risk repair, existing rights or thresholds. Only a independently evidenced admissible change warrants another candidate. Main and PR67 remain untouched. Joint acceptance remains NOT_MET.
