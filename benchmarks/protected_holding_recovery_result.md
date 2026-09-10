# Protected holding recovery — rejected, 2026-09-10

The implemented candidate is rejected, not promoted. Native 382-session
no_optical/later replay completed with sealed raw/account/role/source/config,
next-open fills, economic attribution and frozen input readback PASS.
Producer local bb9f1fa3e0fdce9e8e638f6fcd05fcea054c7177, remote equivalent
1cee9a34c72fa2a4d31bf67ed49066b0e0cb951f; economic source
eac89fcd6fd386d8ca123e8d32ca3e7846c0955404908e9c78d8b301c20584e8.

| Metric | Selected control a39a9542 | New candidate |
| --- | ---: | ---: |
| Final wealth | 1.1253125939953692 | 1.0508007573618494 |
| Maximum drawdown | .17351438505134742 | .20616200375560245 |
| Account orders | 12 | 14 |
| Fees | 4662.949909262 | 5477.655776302 |
| Slippage | 9018.162099999632 | 10688.129499999499 |

Both original and authorized later-window wealth gates FAIL. The current floor
is 1.5854100500632815; this is not a minor acceptance miss. All net difference
is sh688233: -149023.67326703994 at 2m initial capital. The Nov12 restoration
signal bought 12700 shares next open, followed by Nov24/25 crisis reductions.
The policy actually operated; merely unfreezing an existing holding did not
improve economic capture. No counter-length/cap/structure retuning, no full matrix.

45 unique focused tests passed, but focused read-only review found a P1 scope
leak: a recognized pending restoration remainder leaves other eligible symbols
able to newly reach five observations, and a higher healthy cap can expand the
same-symbol target beyond that remainder. In-memory partial-fill reproduction
replaces O000000003 target .5 with O000000004 target .5994525241568182.
Do not reuse this rejected implementation without fixing and testing both bounds.
No live deployment or main merge occurred. Original failed producer is preserved.

Result seal b2536b39b60aff3f57805f856180e5383ffcb9a1117a316f59f719797e33c6dd;
raw SHA256 5e5df93770ce2bc04acb08e38560e85efa05f82363a648c93a7a188dad12477a.

## Authorized contingent continuation

Evaluate the already fixed integrated simple ordinary challenger, without adding
this failed holding policy, changing MA periods/ranking/caps or retuning losses.
Original source b7f54f137576ba15787827103103d4710369d105519c463f4289b1251dbf2942,
local producer 226ef63b5aabbaf402a0eff0766eca73b32d2e24 has been restored exactly;
11 missing Git blobs were recovered and checked against their original object IDs.
Archive SHA and all 448 manifested members verified. Original H1 sealed native
readback PASS; preserve its old 1.3753-floor rejection. Original H1 runtime/data
match current frozen inputs, so do not rerun those 118 sessions.

One new screen: no_optical 2025-01-02 through 2026-07-31, same original producer,
runner, config, universe and frozen uv0.11.33 runtime. Evaluate against current
cross-AI contract/v5 using the current reader separately from original producer;
retain original gate judgment too. Require later wealth 1.5854100500632815,
DD <= .30, account orders <=40. If it materially fails, stop this fixed challenger
without variants. If viable, then principal/removal and affected gates determine
whether integration is warranted. No additional 29-fold full retention target.
Minor misses may be considered once under latest user authorization, with named
old/new limits; neither failed economic hypothesis justifies widening a large gap.
