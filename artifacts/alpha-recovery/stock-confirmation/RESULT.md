# Stock confirmation outcome

Candidate: 1abcdc6eef2a2f92bd7c0c05233504b67d7fffcd.
Status: REJECTED; production changes reverted, source retained in Git history.
No acceptance rule, prior failed evidence, or original PR branch was changed.

The positive regression failed on the parent, then all seven new cases and
44 existing ordinary/repair tests passed. Workflow 34938683986 executed the
candidate under the registered pinned runtime.

| Diagnostic | Parent | Candidate |
| --- | ---: | ---: |
| A bull final wealth | 11.87065858669219 | 11.87065858669219 |
| E bull final wealth | 13.821022723557565 | 1.8976716616758222 |
| E maximum drawdown | 0.1725303673685 | 0.2003465518039731 |
| E economic orders | 13 | 37 |
| Native remove308 118-day final equity | 2219163.36163238 | 2986029.6573927803 |

The native prefix benefit does not compensate for the E failure. Do not
sweep confirmation days or add an E-specific exception. Readback checks and
exact raw diagnostic preservation are recorded separately after completion.

Interpretation: own-stock confirmation changes economically important
decisions, but removing market/deployment dependence is not sufficient for
stable alpha. Trace-level explanation of the E loss is still pending; do not
state an inferred February/freeze chain as verified before reading the fills.

A newly completed original PR67 Absolute report is actually passed=true, with
34 valid metric cells. Its producer is 26aa5d21fa4b5d2d88f402b41ee399bcf990804b,
source fingerprint 51c230d8ed5e0a8e0f99ce7dcee15987aec9b8aaf2513490bf8208301cda0037,
not this rejected candidate. This formal result does not establish robust
returns after removing an economically dominant leader; the low-return
critical-removal diagnostics remain relevant. No merge has occurred.
