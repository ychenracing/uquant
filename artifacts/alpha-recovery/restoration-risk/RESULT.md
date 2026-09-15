# Resolved recovery permission: no economic improvement

Reject this candidate as an alpha recovery result. All four completed matched comparisons have identical daily equity and fill economics to their controls. The proposed permission change therefore has no demonstrated economic benefit in these observations. Do not promote this arm, expand it to a full matrix, or sweep its parameters. This conclusion does not rule out other core strategy or risk redesigns.

On 2026-09-15 the user explicitly accepted drawdown exceeding the original limit by up to and including **1 percentage point**. Apply that authorized tolerance to the current acceptance assessment; the reported E H2 excess of approximately **0.762 percentage points is accepted and is not a remaining blocker**. Preserve original measurements and their producer identities. The rejection here is for zero alpha improvement, not for that accepted drawdown difference. This amendment does not by itself establish full joint acceptance or supply missing evidence for other windows.

Candidate producer: `01b324491f13ed63209954279b5b7ece1ce165a9`. Canonical control producer: `6f8de5c02ff1161b78ef42e63e91f36d35c6bfc0`. Candidate production fingerprint recorded by the native prefix: `8730bb8b6cb1f09d50cdc0a954a2c4b2a74dd2d234a670289c4d27bbd4a47cb2`. These results belong to those producer identities and do not validate a later candidate or current HEAD.

The sole changed production file is `uquant/portfolio/pipeline.py`. The candidate lets existing ordinary restoration rights use a CAUTION state already resolved as RECOVERY, ROTATION_RECOVERY, or FAST_V_RECOVERY instead of separately vetoing on raw votes alone. It retains continuity and confirmation requirements, transition damage, Sentinel blocking, reduction limits, cash, concentration, target cap, and restrictions on new rights. The preserved test XML records **70 tests, 0 failures, 0 errors, 0 skips**; no tests or economic replays were rerun for this report. The preregistered experiment, RED output, tests, and exact `source.bundle` remain preserved.

`compare.py` is complete for the four registered comparisons. Readback verified completion, producer commits, configuration, interval, seed, runtime, symbol/contract identity, adapter and observer hashes, expected source changes, and each changed file's bytes at both producer commits. Runtime is Python 3.12.13, NumPy 2.5.1, pandas 3.0.5, and uv 0.11.33. The prefix runtime lock digest is unchanged. Its two observer hashes differ only because the root/output arguments were resolved differently; exact script comparison confirms identical replay and observation logic. The control and candidate hashes are retained in `comparison.json`.

| Observation | Metric | Canonical control | Candidate |
|---|---|---:|---:|
| A, 2025-01-02 to 2026-07-31 | Final wealth, including principal | 11.87065858669219 | 11.87065858669219 |
| A | Maximum drawdown | 16.004817592256193% | 16.004817592256193% |
| A | Account orders | 12 | 12 |
| A | Gross turnover | 18.4081591135 | 18.4081591135 |
| A | Annual turnover | 11.661713365096857 | 11.661713365096857 |
| E, 2025-01-02 to 2026-07-31 | Final wealth, including principal | 13.821022723557565 | 13.821022723557565 |
| E | Maximum drawdown | 17.253036736852323% | 17.253036736852323% |
| E | Account orders | 13 | 13 |
| E | Gross turnover | 18.432130123500002 | 18.432130123500002 |
| E | Annual turnover | 11.676899188185864 | 11.676899188185864 |
| Remove 308 prefix, 2023-01-03 to 2023-06-30 | Final equity | 2219163.36163238 | 2219163.36163238 |
| E H2, 2024-07-01 to 2024-12-31 | Final wealth, including principal | 1.8168767799091217 | 1.8168767799091217 |
| E H2 | Maximum drawdown | 9.854024217858659% | 9.854024217858659% |
| E H2 | Account orders | 7 | 7 |
| E H2 | Gross turnover | 2.70976798415 | 2.70976798415 |
| E H2 | Annual turnover | 5.246110817314401 | 5.246110817314401 |

All **382 A, 382 E, 118 prefix, and 125 H2** daily equity observations match exactly. Every daily fill matches economically after excluding only source-derived `epoch_id` and `grant_id` recursively. Raw evidence is not byte-identical: code/account/repair/grant/epoch identities and their derived digests reflect the changed source. Every differing leaf path and count remains in `comparison.json`; raw values have not been rewritten. No additional prefix statistic is inferred beyond its reported final equity.

The E H2 control is the separately preserved `risk-repair/current-e-h2.json.gz` from the canonical producer and matching runtime. The candidate has no newly run acute or full removal acceptance result. Both report and comparison remain **DIAGNOSTIC_ONLY_NOT_JOINT_ACCEPTANCE**. The separate sector repair arm's `RESULT.md`, `compare.py`, and `comparison.json` are complete and report its own zero improvement; neither failed arm is evidence of recovered alpha.

Candidate raw SHA-256 digests:

| File | SHA-256 |
|---|---|
| `a.json.gz` | `75211cb3a6e909311888c1ca093ed6894b61974177c001157ee180ca3349f5a8` |
| `e.json.gz` | `3fb07935632fa01504e0f60a40eed21c814c11b91df7e181e6f7dc4738eddab2` |
| `remove308-prefix.json.gz` | `b6fc62b3442c1f619a8e90bfb905d15a9d2d287919935c7531b2a18ac1c897ee` |
| `e-h2.json.gz` | `a3a01f4316ee5dbed155d09af64ec63ee2a3ae39db12e0790c7f72382754afba` |
| `source.bundle` | `f6d670da653ee0d3a1681325fee8f232eeba9d78925a09b89b23f49566ddfd38` |

Readback reproduced both arms' existing `comparison.json` content exactly without writing either comparison file. Before/after SHA-256 checks of all 26 raw, source-bundle, and test/log files across both arms were unchanged. To reproduce the ordinary readback, run `python artifacts/alpha-recovery/restoration-risk/compare.py` from a checkout containing both producer commits, canonical controls, and `risk-repair/current-e-h2.json.gz`. Use `--h2-control PATH` when the H2 control is in a separate checkout. That script only writes `comparison.json` and runs no economic replay.
