# Simple ordinary replacement: current-input result

2026-09-09. Decision: **do not replace production ordinary strategy**.
The three prerequisites in `simple_ordinary_replacement_plan.md` failed.
This change delivers comparison/exclusion support and trustworthy readback,
not an improvement in production profitability, generalization or complexity.
Production, risk configuration, strategic ownership and Future Holdout are unchanged.

## One frozen comparator, four complete cases

| Case | Sessions | Wealth | Maximum drawdown | Orders | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| remove_all_three | 869 | 0.8843966427651402 | 0.13353804370757427 | 7 | fails wealth floor 2.397615989680009 |
| remove_all_three minus sz300666 | 869 | 0.8843966427651402 | 0.13353804370757427 | 7 | fails wealth floor 1.0 |
| no_optical H1-2023 | 118 | 0.8843966427651402 | 0.13353804370757427 | 7 | fails wealth floor 1.3753099655116032 |
| full standalone control | 869 | 1.30400698590262 | 0.13881266356876165 | 12 | descriptive only; no strategic-owner integration claim |

All cases start 2023-01-03 and end 2026-08-05 except H1, ending 2023-06-30.
DD/order limits pass, but do not compensate for failed wealth prerequisites.
The identical removal paths are one shared failure mechanism, not independent
evidence of success or independent out-of-sample tests. No parameter search,
new data or Future Holdout was used. No integrated strategy was constructed.

## What actually failed

The removal path lost money on sh688008 (-75,970.22), sh688200 (-52,230.44)
and sh688766 (-103,006.05), including cash fees. Last fill: 2023-04-26.
From that date to the long replay end, all 794 sessions were flat and had
capital_budget_level >= 1. Forty monthly reviews reported eligible candidates
blocked by `risk_freeze`; for example 2023-03-01 had sh688256, sh688300 and sz002281.
Thus neither opportunity absence nor universal signal failure is established.

`uquant/risk/capital.py` preserves the lifetime capital peak when flat, although
it resets the operating peak. Releasing level 1 requires capital and operating
drawdown below 8%; capital level >= 1 freezes new risk. This path ended with
cash 1,768,793.2855302803, capital peak 2,041,397.5162844001 and capital drawdown
13.3538%. With no holdings, external flows or separate entry authority, cash
cannot generate the recovery needed by that release condition. This explains
non-participation in this standalone path; it does not prove a universal
production deadlock or establish that bypassing the guard would be profitable.

Full standalone earned 829,093.20 on sz300502 while the other three names lost
money. Its last fill was 2023-08-22. It does not demonstrate cross-theme
generalization or preservation of the production strategic owner path.

The next economic hypothesis, if separately approved, should isolate recoverable
ordinary risk budgeting under unchanged loss limits. Do not tune selection on
these failures or silently remove the capital guard to manufacture a pass.

## Provenance and readback

Producer local commit: `f2279eff145ccd74e102ec617cc2e0347b63c9a1`.
Public equivalent-tree commit: `f342808ce8b0d449385cfa6cd9774e26b54c8a03`;
both have tree `c8b199b307ae03c9c074e3f86fbbf581e03d7e82`.
Original evidence is not rewritten with later commit/source identities.

- Producer benchmark SHA-256: `55839e691b244959b728e2002039cc9f114ecf83aa6cf37ddf4bfcc2d773828b`
- Production SHA-256: `86d3541617b4f3185c94bf0f5ad2bbeedfaddecabdcf1fabd593196223459fdd`
- Config SHA-256: `ff491f722c3f84211eda9953cce1309392f7a89bb86bcc1e2cb33232580d4a26`
- Runner SHA-256: `0c3f9138ddfd51de500aa7c4a78483fdf7555d949cd30cb3d0c1db1c62906b59`

Each result also records exact data/universe/runtime identities and raw/account/state
hashes. Prior production candidate raw was not relabeled as current baseline:
its source identity differs. The floors above are frozen requirements, not a
claim that a paired current-production replay was performed in this change.

| Case directory | Canonical result SHA-256 |
| --- | --- |
| remove_all_three | `28795966db9420c4fd9872d7ee071fd94170bd5aca886def8a7c6f4238f4d13b` |
| minus_sz300666 | `d0483fe1cfbfc0dba5bf054c72b27ff52cbd987f1e3310b04b19d8e5f8a52adb` |
| no_optical_h1 | `4a1d9a9ab6cc226fbaf588fef85d671846d0449ac41bd5f68a939778ed2edd45` |
| full | `6651494367a0014389673e86269a9c4e6fe7e12945413e0be1364de0da273f20` |

All four passed `read_benchmark_case`: seals, current non-benchmark inputs,
session schedule, roles/exclusions, next-session fill chronology, account fills,
terminal state, per-session equity/PnL, wealth/DD/orders/costs and symbol attribution.
The final reader-only fix changed the benchmark file hash, not economic code:
module AST excluding `read_benchmark_case` was compared against the producer and
was identical (also checked independently in review). Readback explicitly supplies
the above trusted `producer_sha256`; it never learns its trust anchor from the
result under validation. Default readback still requires the current benchmark hash.

Regression tests first reproduced acceptance of compensating daily PnL mutations
and ledger-equity tampering, then passed after the per-session checks. Resealed
role/chronology/state mutations and producer override identity checks are covered.
Only the small representative fixture was rerun for validation; the four valid
economic replays were reused unchanged. Complete raw/account/state/results are
preserved in the companion `simple_ordinary_current_evidence.tar.gz`.
