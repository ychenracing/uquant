# Sector repair trend: no economic improvement

Reject this candidate as an alpha recovery result. Four matched economic comparisons from five completed new raw runs show zero improvement. Do not promote the candidate, expand it to a full matrix, or tune this repair horizon further. This conclusion applies to the observed comparisons; it is not proof that every risk redesign is ineffective.

Candidate producer: `0080f419a35b7c8067a5d4c337fe86ff11533ddf`; production fingerprint recorded by the native prefix: `e56139fe31898539a685003424d4177d00fa41dc6c13fa154da5029cb8291d93`. Control producer: `6f8de5c02ff1161b78ef42e63e91f36d35c6bfc0`. The `source.bundle`, preregistered experiment, probe, and test records remain preserved. These values retain their real producer identities and do not validate any subsequent candidate or current HEAD.

The hypothesis replaced daily-positive sector repair observations with cumulative cohort return over the existing confirmation horizon. The fixed-cohort price probe changed a possible repair date, but the actual accounts did not change their economic paths. The probe therefore supplies no demonstrated account alpha.

`compare.py` read every raw result back and verified completion, configuration, interval, seed, data/input manifests, runtime, and observer identity. The only recorded source changes are `uquant/risk_sector.py`, `uquant/risk/assessment.py`, and `uquant/risk/transitions.py`; their hashes were also checked against bytes at each producer commit. Runtime is Python 3.12.13, NumPy 2.5.1, pandas 3.0.5, and uv 0.11.33. The native prefix lock digest is unchanged. Its original runner hashes differ; exact script bytes verify that only root/output argument resolution changes, with replay and observation code identical. Both hashes remain in `comparison.json`.

All reported account metrics are shown below. Wealth includes principal; turnover is the original runner's ratio. No metric has been inferred for the native prefix beyond its reported final equity.

| Observation | Metric | Canonical control | Sector repair candidate |
|---|---|---:|---:|
| A, 2025-01-02 to 2026-07-31 | Final wealth | 11.87065858669219 | 11.87065858669219 |
| A | Max drawdown | 0.16004817592256193 | 0.16004817592256193 |
| A | Account orders | 12 | 12 |
| A | Gross turnover | 18.4081591135 | 18.4081591135 |
| A | Annual turnover | 11.661713365096857 | 11.661713365096857 |
| E, 2025-01-02 to 2026-07-31 | Final wealth | 13.821022723557565 | 13.821022723557565 |
| E | Max drawdown | 0.17253036736852323 | 0.17253036736852323 |
| E | Account orders | 13 | 13 |
| E | Gross turnover | 18.432130123500002 | 18.432130123500002 |
| E | Annual turnover | 11.676899188185864 | 11.676899188185864 |
| Remove 308 native prefix, 2023-01-03 to 2023-06-30 | Final equity | 2219163.36163238 | 2219163.36163238 |
| E H2, 2024-07-01 to 2024-12-31 | Final wealth | 1.8168767799091217 | 1.8168767799091217 |
| E H2 | Max drawdown | 0.09854024217858659 | 0.09854024217858659 |
| E H2 | Account orders | 7 | 7 |
| E H2 | Gross turnover | 2.70976798415 | 2.70976798415 |
| E H2 | Annual turnover | 5.246110817314401 | 5.246110817314401 |

All 382 A, 382 E, 118 prefix, and 125 H2 daily equity observations match exactly. Fill economics also match on every session after excluding only source-derived `epoch_id` and `grant_id` fields recursively. Raw traces are not byte-identical: the candidate adds sector repair diagnostics, and changed source fingerprints propagate to code/account/repair/grant/epoch identities and their digests. In the prefix, three fill rows carry different derived identities. All differing leaf paths and occurrence counts are retained in `comparison.json`; the immutable originals retain the actual values. No identity has been overwritten or relabeled.

The new H2 control was run at the matching canonical runtime and is preserved as `current-e-h2.json.gz`; it is compared only with `candidate-e-h2.json.gz`. Earlier H2/acute diagnostic evidence must retain its own identity and does not validate this candidate's unrun acute window. This is diagnostic rejection, not full joint acceptance. Main promotion is unsupported by these results.

Reproduce the readback with `python artifacts/alpha-recovery/risk-repair/compare.py` from a checkout containing the preserved evidence and both producer commits. This writes only `comparison.json` and runs no replay.
