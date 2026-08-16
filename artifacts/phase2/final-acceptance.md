# Phase 2 final acceptance

## Decision

Task 11 is **ACCEPTED** under the authorized Generalization policy-v2
clarification. The literal thresholds remain visible as diagnostics, while the
blocking decision uses authenticated champion-relative effective bounds. Exact
champion equality and benign improvement pass; any genuine per-cell,
common-support tail, attribution, coverage, provenance, safety, or replay
degradation still fails closed.

The baseline, champion payload, seeds, windows, market rules, safety controls,
PIT/T+1, fees, cash/position constraints, data, and scenario identities were
not changed. The authentic 2026-08-05 prior-close account is taken from the
same deterministic `continuous_ai_era/full` production replay used by the
matrix and is pinned by its canonical digest in the reviewed contract, so the
zero-session holdout manifest contains null scores without fabricated state.
Observed-session score files are rejected until scores can be recomputed from
deterministic replay evidence. No generated self-binding benchmark artifact is
tracked.

## Exact evidence candidate

- Acceptance checkpoint before this summary: `da7c554c560c3eaf20cacbccebca9bd772f732a3`
- Last byte-exact economic replay HEAD: `fb4b78362dda05282023d1fa504c1bb6cbdcddac`
- Branch: `codex/phase2-ai-era-generalization`
- Remote branch at start and at evidence capture:
  `a8e9cc0a66f198eabb1b56bf22f46b0cdaf77ca3`
- Tracked tree before the runs: clean
- Full logs: `/tmp/task11-fb4b78362dda05282023d1fa504c1bb6cbdcddac/`
- Runtime: CPython 3.12.13, NumPy 2.5.1, pandas 3.0.5, uv 0.11.33,
  Linux 6.18.35 x86_64/glibc 2.39, `TZ=America/New_York`, locale
  `C.UTF-8`, `PYTHONHASHSEED` unset
- `uv.lock`: `4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61`
- Frozen snapshot: `20260809T094222Z-causal-tech-index-rebase`, 36 files,
  manifest `343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d`,
  checksums `ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29`

The economic payload below was captured before the policy-v2 clarification;
focused policy, holdout, ablation, and provenance regressions then passed at the
acceptance checkpoint. Per the plan, the documentation-only summary commit is
followed by one complete exact-HEAD Engineering, Phase 1, Phase 2, holdout, and
determinism rerun before publication.

## Engineering gate

All engineering commands used `UV_CACHE_DIR=/tmp/task11-uv-cache`.

| Check | Exact command | Result |
|---|---|---|
| Environment lock | `uv sync --frozen --all-groups` | exit 0 |
| Ruff | `uv run ruff check .` | exit 0 |
| Strict mypy | `uv run mypy uquant scripts research` | exit 0; 65 files |
| Frozen manifest | `uv run python -m uquant.validation data-manifest --data-dir data/frozen` | exit 0; 36 files |
| Full tests and branch coverage | `uv run pytest --cov=uquant --cov-branch --cov-report=term-missing --cov-report=xml:/tmp/task11-fb4b78362dda05282023d1fa504c1bb6cbdcddac/coverage.xml --cov-report=json:/tmp/task11-fb4b78362dda05282023d1fa504c1bb6cbdcddac/coverage.json --cov-fail-under=85` | exit 0; 1167 passed in 433.94s; 85.21% |
| Compile | `uv run python -m compileall -q uquant scripts research tests` | exit 0 |
| Build | `uv build --out-dir /tmp/task11-fb4b78362dda05282023d1fa504c1bb6cbdcddac/dist` | exit 0 |
| Bandit | `uv run bandit -q -r uquant research scripts` | exit 0 |
| Locked production export | `uv export --frozen --no-dev --no-emit-project --no-hashes --output-file /tmp/task11-fb4b78362dda05282023d1fa504c1bb6cbdcddac/production-requirements.txt` | exit 0 |
| Dependency audit | `uv run pip-audit --cache-dir /tmp/task11-fb4b78362dda05282023d1fa504c1bb6cbdcddac/pip-audit-cache --requirement /tmp/task11-fb4b78362dda05282023d1fa504c1bb6cbdcddac/production-requirements.txt` | exit 0; no known vulnerabilities |

Coverage counted 12,648 statements with 1,519 missing and 5,226 branches with
1,124 missing branches (994 partial). Wheel SHA-256 is
`bfa831bbf7d8446e3ccc48bce285971a9ff557171bdf84a92549c91f599cf5ec`;
sdist SHA-256 is
`354a183bc524b62959511594bc5d99dfefacffe9405edc2c80f11f3cd35dc1cb`.
The exported production requirement set has SHA-256
`81e06a296c6c13f08bcc53e0fbad1553e3a48ded70f1b34f841927c4ee6b6ba0`.

## Phase 1 Performance

Command:

```text
uv run python -m uquant.validation promotion --data-dir data/frozen --profile full --output benchmarks/ai_era_performance.json
```

Both runs exited 0. Authoritative `uquant.validation.ci_artifacts phase1`
readback passed twice with no failures and exact candidate HEAD/source/data/
config/runtime provenance. The artifact contains exactly 30 official records
(five pools × six official windows) and 15 protected records (five pools ×
`year_2023`, `year_2024`, and `bull`), for exactly 45 records. Summary metrics:
median final wealth `4.14785416600156`, worst max drawdown
`0.2786861829563525`, total account orders `340`.

- First raw SHA-256:
  `03c767df54ee81a267f7fa3f3331cc9dbfb322d85d4b9ac05886915a21eeb83b`
- Repeat raw SHA-256:
  `4f880fe7233ee63bd18d080be609dd5f20908f925ce42dcae6ef685242e64174`
- First/repeat canonical SHA-256 after removing only the contract-allowed
  `provenance.generated_at` and `provenance.binding.generated_at`:
  `74a92643245d0c5dc856ff1b735e14ac6f6e8358cebdf3371ed01bd7d01aac0b`
- Production source SHA-256:
  `858fb18e79b79444dc2bb2229f53c60555c428264693d8d99c26313cd16204e5`
- Effective config SHA-256:
  `ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13`

## Phase 2 Generalization

Command, run twice:

```text
uv run python -m uquant.validation generalization-matrix --data-dir data/frozen --output benchmarks/ai_era_generalization.json
```

Both matrix executions exited 0. The raw artifacts are byte-identical with
SHA-256 `2171eb487735b95ecb7b93524ff3a16d7e72accc594478a0ebd7d14f7504401c`.
The contract permits no timestamp removal, and `cmp` exited 0.

Authoritative readback through `load_generalization_baseline()`,
`load_generalization_policy()`, and
`evaluate_generalization_policy_artifact(..., data_dir="data/frozen")` proves:

- exact HEAD `fb4b78362dda05282023d1fa504c1bb6cbdcddac` and production source
  `64d8272a983c92a4190d1d6b905128753056a0a270bd1dfddc7d6e9d34be448e`;
- exactly 234 records: 192 `READY`/economic/`VALID` attribution and 42
  `INSUFFICIENT_SAMPLE`; zero replay errors and zero attribution
  reconciliation failures;
- six exact 39-record windows: `h1_2023` 2023-01-03..2023-06-30,
  `h2_2023` 2023-07-03..2023-12-29, `h1_2024`
  2024-01-02..2024-07-01, `h2_2024` 2024-07-01..2024-12-31,
  `bull_crash_2025_2026` 2025-01-02..2026-07-31, and
  `continuous_ai_era` 2023-01-03..2026-08-05;
- exact random base seed `20260810`, indexes `0..4`, sizes `5,9,15,20`,
  and all 39 scenario identities per window;
- family counts: full 6, industry-balanced 6, random 120, remove-all-core 6,
  remove-one 18, subindustry 72 (30 economic + 42 insufficient), and
  tradable-no-optical 6;
- all 30 directional/remove-one intrinsic results pass; and
- all 192 economic cells carry valid, reconciled economic attribution and
  attached concentration. Overall medians are wealth `1.0956720592566103`,
  orders `6`, top-1 concentration `0.637210818247079`, top-3
  `1.0`, and PnL HHI `0.49652276352790725`.

### Every random-tail result

Policy schema/id/seal are `2`, `ai-era-generalization-policy-v2`, and
`46cf95d26d04186824f181266da68e5a2d98814b65371c0b358c7cacfa8ef8fc`.
Literal bounds are positive-return fraction ≥ 0.6, p10 wealth ≥ 0.8, p90
drawdown ≤ 0.3, p90 orders ≤ 20; five cells are required in every group.

| Window | Size | Positive fraction | p10 wealth | p90 drawdown | p90 orders | Literal status |
|---|---:|---:|---:|---:|---:|---|
| bull_crash_2025_2026 | 5 | 0.6 | 0.9728391400857042 | 0.13420141642853176 | 12.6 | PASS |
| bull_crash_2025_2026 | 9 | 1.0 | 1.0622480872790956 | 0.17919239079210614 | 28.4 | FAIL: orders |
| bull_crash_2025_2026 | 15 | 1.0 | 1.591292227237585 | 0.20285142356149827 | 41.6 | FAIL: orders |
| bull_crash_2025_2026 | 20 | 1.0 | 2.309422567774183 | 0.21628036681339444 | 46.2 | FAIL: orders |
| continuous_ai_era | 5 | 0.4 | 0.8310192035788272 | 0.2059597758774645 | 10.2 | FAIL: positive fraction |
| continuous_ai_era | 9 | 0.6 | 0.8310192035788272 | 0.2059597758774645 | 28.2 | FAIL: orders |
| continuous_ai_era | 15 | 0.8 | 0.9945765016521941 | 0.34411972502114335 | 32.8 | FAIL: drawdown, orders |
| continuous_ai_era | 20 | 0.6 | 0.9775056848143062 | 0.3794176865485904 | 23.6 | FAIL: drawdown, orders |
| h1_2023 | 5 | 0.0 | 1.0 | 0.0 | 0.0 | FAIL: positive fraction |
| h1_2023 | 9 | 0.2 | 1.0 | 0.06714557765104358 | 3.6 | FAIL: positive fraction |
| h1_2023 | 15 | 1.0 | 1.1854461757103558 | 0.21732930824625923 | 9.2 | PASS |
| h1_2023 | 20 | 0.8 | 1.099366030844274 | 0.21076320828845932 | 12.2 | PASS |
| h1_2024 | 5 | 0.4 | 1.0 | 0.06668484776142725 | 2.0 | FAIL: positive fraction |
| h1_2024 | 9 | 0.0 | 0.9490942040706558 | 0.15217228081231027 | 9.0 | FAIL: positive fraction |
| h1_2024 | 15 | 1.0 | 1.0404867238126294 | 0.1550911563888399 | 13.4 | PASS |
| h1_2024 | 20 | 0.8 | 1.0131556212015775 | 0.19092266608603425 | 11.8 | PASS |
| h2_2023 | 5 | 0.0 | 1.0 | 0.0 | 0.0 | FAIL: positive fraction |
| h2_2023 | 9 | 0.2 | 1.0 | 0.07131829598981979 | 2.4 | FAIL: positive fraction |
| h2_2023 | 15 | 0.4 | 1.0 | 0.11886382664969963 | 4.0 | FAIL: positive fraction |
| h2_2023 | 20 | 0.8 | 1.016957580344848 | 0.11886382664969963 | 4.0 | PASS |
| h2_2024 | 5 | 0.8 | 1.1304930520246472 | 0.12038184541250302 | 6.0 | PASS |
| h2_2024 | 9 | 0.4 | 1.0 | 0.07314024734745717 | 6.8 | FAIL: positive fraction |
| h2_2024 | 15 | 0.8 | 1.0645818958363624 | 0.11989062766789041 | 9.0 | PASS |
| h2_2024 | 20 | 1.0 | 1.3376351784740914 | 0.08583048851864583 | 10.0 | PASS |

The policy-v2 result is **PASS** with zero effective failures. All 17 literal
threshold reasons across 15 groups remain in the artifact as diagnostics; 9
groups also pass the literal bounds. The other 15 pass only because they equal
or improve on the authenticated champion support. Tests require champion
equality and benign improvement to pass, while real worsening of a
grandfathered tail or any per-cell regression fails.

Seven positive-fraction groups have 3–5 frozen zero-turnover cells:
`h1_2023/5` (5), `h1_2023/9` (4), `h1_2024/5` (3), `h2_2023/5`
(5), `h2_2023/9` (4), `h2_2023/15` (3), and `h2_2024/9` (3).
Per-cell policy requires zero baseline turnover to remain exactly zero. Starting
from fresh cash, those cells therefore remain at wealth exactly `1.0`, which
cannot satisfy the tail definition `final_wealth > 1.0` in 60% of five cells.
This is why the literal floor is diagnostic for these authenticated cells rather
than an impossible blocking requirement. The zero-turnover invariant itself is
not relaxed.

### Frozen replay repair

The frozen identity `continuous_ai_era/random__20__0000` was not omitted,
renamed, or replaced. Before the repair it failed on 2024-04-26 because the
strategic restoration vector combined live winner drift with saved loser
weights to gross `1.006521330959`, bypassing the hard cap before the outer risk
reducer could apply its `0.82` target. A fail-first test reproduced gross
`1.005`; commit `388125839a196560e0d4d67d55ea8ad794652289` caps only this
pre-risk vector to immutable `max_gross`, after which the existing reducer owns
the risk cap. The focused test passed, the full engineering gate passed, and
the exact frozen cell is now `READY`: wealth `1.4426537804641812`, drawdown
`0.35326861883016647`, 24 orders. This is a production safety repair, not a
baseline, seed, window, policy, or strategy-compensation change.

## Holdout

The authoritative contract/layout/readback passes and proves:

- sealed contract SHA-256:
  `64f22aaf33bc709b2a46767b5fabfd20d43514cc19c20d2b48b218fa8cadcf0c`;
- last in-sample `2026-08-05`, start `2026-08-06`, observation-driven
  parameter changes prohibited, 40/60-session review milestones;
- zero holdout sessions and empty-data SHA-256
  `4308b714db46527214f6bbc47f46e904dbdc5f747144da5a67766495934ac17b`;
- exact production HEAD and source/config/universe/industry/runtime/lock
  binding; and
- immutable strategy anchor commit
  `c63a2645992bda1b9aa6d0231ebf35a785b0158c`, strategy source
  `6a131e8b3a64738955f0dd9c295c5092f6ea59fcf923e86940a645de0498fe8e`,
  and account-code hash
  `f43e1e93859169df056051ad1963b761e35143be31b321bf11883726218c5dc7`.

The prior-close account is the exact final account from the deterministic
`continuous_ai_era/full` replay: last successful run and data-hash date are both
`2026-08-05`, account code SHA is
`f43e1e93859169df056051ad1963b761e35143be31b321bf11883726218c5dc7`,
frozen-prefix data SHA is
`9a73ed7e19d34ab8876c7ddb9e974147e1c43d8dcfcaa73abe85c7f9a3ee492e`,
and canonical account SHA is
`2404eb5cd1e0ccfc68ab4663778288dd3a17f607baeb3f8104583443673273f1`.
That account digest is now part of the reviewed contract: mutations to cash,
positions, pending orders, fills, or strategy state fail even when code/data
bindings are retained. With no future sessions, all seven scores and the
metrics SHA remain null. Once sessions exist, detached score JSON is rejected;
only a future deterministic holdout replay/readback implementation may populate
the scores.

## Ablations and configuration

No Task 8 ablation was rerun. Authenticated evidence records 13 original
experiments and decisions `KEEP=10`, `INCONCLUSIVE=2`, `DELETE=1`.
Only `transition_overlay` was deleted after zero deltas across all 236
common-valid cells and passing the focused/Phase 1/related Generalization
deletion gate. The post-deletion 12-carrier coverage contains 10 valid and 2
honestly inconclusive carriers. Pre/post evidence commits are
`9592fcca3860d1901a7009d799d29d20959d1699` and
`aa4b313e000002adae27b32f91b5a84425c78987`; strict readback hashes are
`efc4121041dbc9804670a360f8309ec81f22f709e9318aa77824073064c93b04`
and `ad3a273a0e24be474021d6c034688a9e4cec6807bd8b1dc1bf8ab375e36c7b00`.

Later source changes are fail-closed and content-addressed, not allowed by path
name. The post-Task8 contract binds base `e5e0fa903c9a9b26701063ae01f352af3e246a7d`,
reviewed endpoint `97788451181419f5e4bb804c68684e264b7a68b4`, exact per-path
before/after hashes, reviewed production source
`7717b0f0537f4bd75d8765d67acefdf1430297ffd338c0ee245651732e1cbe0b`,
and canonical seal
`e5da89f0ec9457261f8c2b09d79d11b1c8244fa702416c8be643266409d8f59f`.
The mutation-rejection tests pass.

Configuration governance records total fields `285 → 278 → 275`; economic
fields remain exactly `164 → 164 → 164`. Seven dead compatibility fields were
removed; neither economic freedom nor safety/market-rule configuration was
weakened.

## Consistency scan and known risks

A scan of source, tests, config, comments, README, public docs, and workflows
found no tracked contradiction with Tasks 1–10. Public material has no obsolete
smoke guidance, no optional/nonblocking Performance or Generalization claim, no
numeric unobserved holdout result, and the dates/seeds/windows agree. The
remaining `research/generalization_smoke.py` is explicitly a compatibility
adapter that delegates one exact official shard; it cannot define alternate
inputs. Generated artifacts are ignored and absent from `git ls-files`.

Remaining risks are explicit rather than blocking: 15 groups remain
literal-red, median top-3 concentration is `1.0`, worst drawdown is
`0.396850398360873`, two ablations remain inconclusive, and the future holdout
still has zero observed sessions. The score path intentionally fails closed
until deterministic holdout replay evidence is implemented. Any deterioration
beyond authenticated champion support, observed holdout manipulation, or
provenance mismatch remains blocking.
