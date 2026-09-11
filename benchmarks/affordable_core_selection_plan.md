# AB: affordable admission count, unchanged candidate rank

AA nominal2.570823/cost2.561829 pass with doubled costs36066.33 below unchanged40000, but offset20noopt1.623149 fails paired wealth retention. All three outcomes and raw evidence are preserved. No threshold change or acceptance claim.

An independently reproducible allocator defect appears when the remaining purchase allowance can fund one minimum-sized order but is split among three qualified candidates. Every slice falls below min_trade_weight, so all READY candidates get zero. The real repair/certificate/fill fixture at threefold post-fill appreciation reproduces this failure; sixfold appreciation is the unaffected multi-name control.

AB filters candidates whose own allowed capital cannot fund a minimum order, then retains the longest affordable prefix within existing position slots and candidate rank. Equal division is kept among selected candidates; no score, route, stock or date preference is added. Cash, commitment, concentration, risk and original repair-principal guards still apply. Budget-limited exclusions have a distinct diagnostic from exhausted position slots.

45focused tests pass. Two native repair/entry regressions fail on AA and pass AB; one legacy weak-market test explicitly expected the same starvation and now requires one highest-ranked affordable order while preserving held shares and the exact total allowance. Partial fills, current proof loss, restart, losses, normal accounts and shared qualification controls remain tested.

Freeze this source and run only nominal/highcost/offset20 noopt. No full matrix expansion on screening failure; original financial and input contracts remain binding. Engineering/CI secondary, no merge or future holdout use.
