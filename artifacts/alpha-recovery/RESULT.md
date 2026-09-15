# Alpha recovery: verified comparison and remaining decision boundary

Status: research checkpoint, not joint acceptance. No new production strategy
has been implemented or accepted in this study. Production, risk, eligibility,
data and acceptance contracts are unchanged. PR #67 remains historical and
active evidence; this work is isolated on `research/alpha-recovery`.

## Exact starting identities

- Historical high-return source: `cf8fecff76564fd4ed87faa0da336a06d433fd93`.
  Its replay reproduces the A/E bull values recorded in the promotion champion
  table, whose historical producer label is `9bb58420365b471ee11b4cdfe31793008233ad50`.
  These two commit identities are not claimed identical.
- Main: `df45b4d7d9ea290ae953115afbb73140270537c2`.
- PR #67 at retrieval: `8aaebd20be09ba5d5cdf17f91c18fdf2c01a5d33`, Draft/open.
  Its latest production implementation is `c0d409ae4a82fd2a4692ae674c55da08832a6ccd`.
- Local observer commit: `e2ee33615c3e300d2c639115812b7928fb2b16a7`.
  Remote observer preservation: `b9c26ba775ce0d236cd516aa8d9ae41f8d0ef0fa`.
  Both have tree `30e00555c9b82adf2a272202e744bf3f218096ab`.
  The observer commit changes no production inputs relative to PR #67.

The recovered older workspace had unrelated uncommitted tests and evidence;
it was left unchanged. No original PR synchronize event or duplicate formal
matrix was triggered by this branch.

## Fixed comparison

All six new bull replays use 2025-01-02 through 2026-07-31, the unchanged A or E
pool from `benchmarks/promotion_baseline.json`, identical frozen data bytes and
indices, seed 0, Python 3.12.13, NumPy 2.5.1, pandas 3.0.5 and uv 0.11.33.
The exact same historical observation adapter calls each version's native
`ProductionEngine.backtest`; it does not implement alternative execution.
Two short sentinels completed before the six full-window diagnostics.
The full source/input hashes, complete configuration and raw daily traces are
inside each compressed result. Input hashes are checked before and after replay.

Every shared configuration value is identical. Full configuration schemas are
NOT identical: removed legacy settings and added Sentinel policies are recorded
explicitly in `comparison.json`. This is a historical implementation comparison,
not a claim that one isolated code change explains every economic difference.
No thresholds or configuration values were swept. The fixed promotion champion
is the numerical comparator, not a newly optimized benchmark.

| Pool | Historical | Main | Current PR production | Current / historical |
|---|---:|---:|---:|---:|
| A bull wealth | 13.1664607411x | 9.7349490923x | 11.8706585867x | 90.1583% |
| E bull wealth | 14.9941715605x | 1.0767136269x | 13.8210227236x | 92.1760% |
| A maximum drawdown | 16.3911% | 15.6695% | 16.0048% | — |
| E maximum drawdown | 17.8669% | 16.1808% | 17.2530% | — |
| A economic orders | 10 | 10 | 12 | — |
| E economic orders | 12 | 28 | 13 | — |

The current PR has already recovered much of main's economic damage. A new
strategy must improve on that current source, not claim its existing recovery
as a new experiment. The requested additional alpha candidate remains absent;
main is not mislabeled as the current PR candidate to manufacture a three-arm
alpha result.

## What generated the old return, and what changed?

1. **Discovery/ranking is not the demonstrated bull-window failure.** Current
   and historical A leader rows are equal on all 382 sessions. E rows are equal
   on 379/382; the three differences occur June 24–26, 2026, after their holding
   paths diverged. Both buy sz300308 on April 7, 2025. E also makes the same
   profitable sh688361 January trade. Both add sz300394/sz300502 on May 9.
   Old high returns depend heavily on large, prolonged optical exposure; these
   bull-window results alone do not establish statistically independent alpha.
2. **Allocation and subsequent recovery differ.** On May 9 A the old version
   buys 9,500 sz300394 and 6,600 sz300502 shares; the current version buys 4,300
   and 8,400. The current shared budget also limits resulting gross exposure.
   By October 13 the wealth difference is still only about -0.0179x.
3. **A's main visible gap opens during risk restoration.** Both cut on October
   14. The old version restores on October 17; current restores on October 22.
   On October 21 wealth is 4.9461x versus 4.4974x, with gross exposure about
   94.4% versus 27.9%. Subsequent compounding expands the final difference to
   1.2958021544x. These are path observations, not an isolated counterfactual
   attributing that entire amount to a single rule. Existing H8/sector-risk
   evidence already records genuine risk history; clearing it is not authorized.
4. **E additionally loses a historical theme transition.** The old version
   sells sz300502 and buys sh688498 on November 25, 2025; current does not.
   Both rank sh688498 strongly before that decision. The old recovery
   substitution method still exists, but its production callers were removed
   in `2fdb4e4d57a908a5ba2ab8337e40ab204b6beeaf` (unified allocation).
   This is a real capability difference, not proof that reconnecting the old
   mutating helper is safe. The historical BUY occurs under
   `freeze_new_risk=True`; its warning-state substitution was deliberate in
   the old helper. Current funding requires current authority and settlement,
   and the helper changes anchor rights before fills. Copying that call back
   would not demonstrate compliance with today's frozen obligations.

## Native remove308: a separate, larger generalization weakness

Existing exact-source diagnostic evidence reports wealth 1.1190839397x and
positive total targets on 130 of 869 sessions. This is not the A/E stock-pool
backtest, and its point-in-time universe roles must not be replaced with a
simple user-pool deletion.

The original full replay is not repeated. A new 118-session native prefix,
January 3–June 30, 2023, records previously missing entry/deployment details.
It uses the unchanged native point-in-time role builder with sz300308 absent
from tradable, qualification-reference and risk-reference roles. Its production
fingerprint is `51c230d8ed5e0a8e0f99ce7dcee15987aec9b8aaf2513490bf8208301cda0037`.
This is a prefix diagnostic, not a shortened formal acceptance scenario.

- On February 6 sh688256 already scores 0.83069, but entry is `NOT_MATURE`.
- On February 20 and March 20 it is `CONFIRMATION_INCOMPLETE`, not a READY
  opportunity rejected for lack of cash. March 20 score is 0.97148.
- Before March 29 there are only three unheld ordinary READY observations
  blocked by deployment confirmation, all on March 27. On March 28 the ordinary
  book admits sh688256, sz002281 and sz300502.
- The saved native fills buy sh688256 on March 29 at 116.20. The distinct
  remove502 account bought it on February 7 at 46.90. That contrast is diagnostic;
  different role universes prevent using it as a matched alpha experiment.
- Broad-market/independent confirmation, then the later capital-damage and
  repair path, are the concrete boundaries to investigate. There is no evidence
  here that a small cash-allocation correction could have recovered the missed
  February–March entry while keeping its original eligibility unchanged.

The 118-session prefix closes at equity 2,219,163.36163238. Its entire decision
records and final account are retained in `remove308-prefix.json.gz`.

## Rejected next steps and what remains

- Do not repeat H1–H9, the timing arm, or ordinary-swap experiments. Their
  existing negative evidence remains untouched on the inherited history.
- Do not add another score/filter to solve an entry-authority failure. A
  higher score alone does not establish eligibility or fundability.
- Do not restore the old warning-state rotation by bypassing frozen risk,
  settlement, current capital-book checks or existing recovery rights.
- Do not claim all alpha is broken or all alpha is restored. The bull-window
  comparison and native-removal evidence answer different questions.

No production patch is justified solely by these results. The concrete next
decision is whether a separate ordinary-entry qualification redesign is within
the frozen contract boundary. If it is not, the February entry gap cannot be
repaired by a permitted deployment-only edit at those observed decisions.
Any such redesign needs explicit scope for the affected qualification rules,
while retaining numerical economic acceptance, point-in-time exclusion, all
strategic grant obligations and risk requirements. This study does not grant
that authority or establish that the broader objective is impossible.

Still incomplete: a new alpha candidate, full three-candidate comparison,
cross-window/native-LOO acceptance for that candidate, final engineering and
normal merge. Existing partial PR results and old CI are not promoted to those
claims. This is not a completion or merge recommendation.

## Reproduce and verify

Run `compare.py` from this directory to verify shared inputs, historical wealth
reproduction, daily alignment, configuration differences and all trade events.
`replay.py --help` specifies the historical/current replay interface. Use the
pinned runtime above. `native_prefix.py` reconstructs only the registered prefix.
`SHA256SUMS.json` seals the files. Full raw evidence is retained, including
results that do not support the initial hypothesis.
