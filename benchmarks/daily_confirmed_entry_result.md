# Daily confirmed entry: completed experiment and replacement decision

2026-09-09. **DO NOT REPLACE production entry with this variant.**
All four prescribed native replays and readbacks completed (2,725 sessions total).
All three replacement prerequisites fail on wealth. This completes the authorized
comparison, not the overall cross-AI profitability objective; C remains unaccepted.

## Results

The unchanged-main standalone monthly control was reused after native readback.
Daily treatment means five consecutive eligible sessions and daily empty-slot access,
with the same signal formula, incumbent retention, monthly top-ups, exits, risk
evaluator, costs and next-open execution. No economic rule was tuned after results.

| Scene | Monthly wealth | Daily wealth | Monthly max DD | Daily max DD | Monthly / daily orders |
| --- | ---: | ---: | ---: | ---: | ---: |
| full | 1.30400698590262 | 1.1625565824378101 | 0.13881266356876165 | 0.22243988810834137 | 12 / 13 |
| remove_all_three | 0.8843966427651402 | 0.8732628252235902 | 0.13353804370757427 | 0.13934180493890513 | 7 / 7 |
| remove_all_three minus sz300666 | 0.8843966427651402 | 0.8732628252235902 | 0.13353804370757427 | 0.13934180493890513 | 7 / 7 |
| no_optical H1-2023 | 0.8843966427651402 | 0.8732628252235902 | 0.13353804370757427 | 0.13934180493890513 | 7 / 7 |

Wealth includes initial capital; it is neither profit nor CAGR. All begin 2023-01-03.
The first three end 2026-08-05; H1 ends 2023-06-30.
Removal wealth requirements remain 2.397615989680009, 1.0 and 1.3753099655116032.
Original DD/order prerequisites pass, but do not compensate for wealth failure.
The later 32-order allowance cannot change the conclusion: orders are not the blocker.
Full is descriptive, not an integrated strategic-owner preservation test.

## Actual failure path

The removal treatment still bought sh688008, sh688200 and sh688766. Compared with
monthly entry, signal dates changed from Jan 3 / Jan 3 / Feb 1 to Jan 9 / Jan 13 /
Jan 20 respectively. Their daily-treatment net PnLs were -84,106.04, -52,388.82 and
-116,979.49. Thus this combined daily/confirmation treatment did not select a
better initial portfolio and worsened its loss. It does not separately identify
the effect of review frequency versus confirmation.

The last removal fill was 2023-04-26. Including that session, all 794 remaining
sessions were flat with capital level at least one. Terminal cash was
1,746,525.6504471805 against a preserved capital peak of 2,029,290.67598456:
capital drawdown 13.934180493890513%. Level-one release requires drawdown below
the unchanged 8% threshold. Cash without holdings cannot repair that loss.
There were 737 dates with blocked eligible candidates in remove_all_three
(8,228 candidate-session risk-freeze rejections, not independent opportunities).

The strict-removal path has the same complete equity curve. H1 contains the same
initial loss and then ends sooner. These are a shared failure mechanism, not three
independent statistical replications.

Full treatment remained dependent on sz300502: +660,484.12 net PnL, while the other
four traded names lost money. Last fill was 2023-08-22; the final 715 sessions were
flat, terminal capital level three. No cross-theme recovery improvement is shown.

## Scope and next responsible layer

Do not infer a universal production deadlock from the standalone comparator.
Production has an explicit `authorize_ordinary_cash_rearm` path in
`uquant/portfolio/strategic/rearm.py`, consumed through the allocator; the standalone
benchmark calls the shared risk evaluator but never that complete recovery authority.
The monthly/daily comparison is fair to each other, but does not isolate the value
of a simple entry signal inside the full production allocator after damage.

The next useful investigation is the connection between existing flat-book repair
readiness, independent-core qualification, and the single authorized recovery order.
Reuse that path and its identity/account/position safeguards before considering
new risk logic. Trace where an otherwise ready recovery episode lacks a valid
entry certificate; do not fabricate one from a trend score or bypass a freeze.

Do not run another MA/confirmation search, re-enable rejected D/E risk-veto changes,
or interpret this failure as proof that every trend strategy fails.
No production entry replacement is justified by this experiment. A new integrated
economic mechanism needs its own explicit design and original acceptance; this
report neither proposes a threshold waiver nor silently starts another candidate.

## Verification and reproducibility

- 19 affected tests passed: existing benchmark safety/readback suite plus causal
  five-session/reset/repeated-date/freeze checks and the daily native sentinel.
- Ruff and mypy passed for the two affected Python files; git diff check passed.
- All four new cases passed native `read_benchmark_case(daily_confirmed=True)`.
  Wrong-variant readback is rejected. All four old controls separately passed their
  trusted reader-only producer readback; no old economic replay was repeated.
- No production file, runtime lock, fee, risk threshold, stock role or Future Holdout
  was changed. No full acceptance matrix or manual CI dispatch was started.
- Local producer: `806ded527e296e52b49aeb4462f8b542a8776332`.
  Remote equivalent-tree producer: `be859843de5bd040d79bfef307d70b79f3afee34`.
  Shared tree: `d268dfdf4139939230d9b1b78ad57c84a3db9f35`.
  This remote producer is retained as an ancestor of the report checkpoint.
- Production base: `960539a89408cc7c1fc3937bda19c9f760095012`.
  Production fingerprint: `86d3541617b4f3185c94bf0f5ad2bbeedfaddecabdcf1fabd593196223459fdd`.
  Benchmark SHA256: `1ba498a0a73c759101dbdfb835f16bdb11975a2b3f69319ec0665d1303e271a8`.
  Config SHA256: `ff491f722c3f84211eda9953cce1309392f7a89bb86bcc1e2cb33232580d4a26`.
- Preregistration was pushed as `5d0c219d131638502276f61ab41637861c30b3c8` before replay.
  PR research-code checkpoint `26fa88814be594bf01a46a8ef30b805ab5bebea4` preserves
  the existing C production branch; it is not relabeled as the unchanged-main producer.

Archive: `uquant_daily_entry_experiment_20260909.tar.gz`, 1,940,535 bytes,
SHA256 `96bc74b3ca5928e5a53108a4b6a72af8c27942fb84181f5431270b56869b83d4`,
Library ID `libfile_2901e37ffe208191826661f42fe81804`.
Contains all four raw/account/state/result directories, summary and provenance,
including reused-control seals. Archive members were read back and byte-compared.
All four replays completed within one sequential controller, which exited zero.
Old workspace modifications and prior failure evidence remain untouched.

| Daily scene | Native result seal |
| --- | --- |
| full | `748e50754f4bbf04779aec6ec233f0cbe5a2b197778f9bb59c0935f432c67ffa` |
| remove_all_three | `4b518337c0252de249a1f1313cba76b3324c2c4a7f026809bc2539e64ba8c782` |
| minus_sz300666 | `8abc048d96395d1524698db6161dca8b7543f09ddc291b6d6e3ee93f5737de26` |
| no_optical_h1 | `a28f82485f5f4143051b3efbdaa369f5e705fb122e036f11255d6b26ad00d019` |
