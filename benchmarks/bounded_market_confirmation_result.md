# Bounded improvement round — 2026-09-09

## Decision

Reject the integrated ordinary market-confirmation candidate. Its first fixed
screen failed; no later long economic screens, retuning or second strategy
candidate were started. Revert commit d4fddc4 preserves producer 4c6dac8 and its
failure evidence. The only retained production change reuses already-computed
candidate eligibility, without changing a decision predicate, clock or limit.
The cross-AI profitability objective remains NOT_MET; C's existing economic
acceptance failures are not cured or waived by this refactor.

## Fixed economic comparison

Both sides use the complete production allocator, risk/account-repair path,
fees, stock roles, causal daily data and next-open native execution. C control
was reused through native readback. No Future Holdout was read.
Window: no_optical, 2023-01-03 to 2023-06-30, 118 sessions.

| Metric | C control | Rejected candidate |
| --- | ---: | ---: |
| Wealth including initial capital | 1.4476947005385299 | 0.877189640574585 |
| Maximum drawdown | 0.2442425185317515 | 0.1275032608431621 |
| Actual account orders | 6 | 7 |

Wealth floor 1.3753099655116032 fails; unchanged DD and order screens pass.
A smaller drawdown does not compensate for losing the required profitability.
The nine-session sentinel and 118-session screen both completed and passed
native readback; the separate C control readback also passed.
Candidate source: 76805e2f703e98eeafbee9ee05f26bce9e9fb7c78ded52dff43b3a8d53b25d7f.
Candidate producer: 4c6dac80201c839dffa3f1d73849c7457eaea864.
Candidate result seal: 58ce67210cc3e521636d798f552ba5500063b9d750faca4fe4965069f08f23b0.
Control result seal: 0353d2f5d7656e170aba8145b05524161b9ce7637c63e54115434ac7884e832e.
The preregistration was committed locally as 8d8da4d before economics; remote
publication was blocked, so it must not be described as a pushed preregistration.

## What actually changed

First target divergence: 2023-01-18. Candidate requested 40% each in sh688200
and sh688766; C requested neither. Actual buys began next open, including
volume-limited partial fills. Those two names ultimately contributed
-104,146.38 and -146,464.06; sh688300 contributed +4,989.73.
Candidate first froze new risk on 2023-02-17 at capital level 1, capital DD
8.8467%. On 2023-03-06 sh688256 had a real READY established certificate,
but its allocation was denied NEW_RISK_FROZEN. C actually bought that name
and it contributed +953,222.02 in this window. This is a path-dependent
combination of initial selection, initial capital exposure and subsequent
risk restrictions, not proof that every rejected entry was profitable.

The opportunity already existed in the pool, and a later real certificate
existed. Expanding the pool or relaxing more admission tests does not directly
address this observed failure. A possible next independent hypothesis is to
bound initial capital committed to less-established ordinary signals using
existing probe sizing, and increase exposure only through existing validated
holding/promotion logic. This has NOT been implemented, evaluated or shown
profitable; this round does not launch it or promise it will meet the target.

## Retained simplification and verification

Return the eligibility computed by _observe_resolved_strategic_candidates to
its public caller, instead of evaluating the same routes and quality again.
The internal helper returns snapshots plus eligibility; its production callers
and one test fixture unpack them. No new state, cache, dependency or setting.
Two files, 8 additions / 11 deletions before identity-binding metadata.

10 public observations (five sessions, repeated same-day calls): internal
eligibility calls 20 -> 10. The complete returned observations AND account
payload have identical SHA256 0891a54b781a21184edca88f48b883d797c43b7c53febaf4a7a03f17f89041f6.
This is a 50% reduction inside that observer call, NOT a measured 50% reduction
in total replay time or a claim of better trading profitability.

71 affected cleanup tests passed after updating the private-helper test caller;
the initial 3 tuple-shape test failures and their cause are retained in this
report, not presented as economic failures. Ruff and mypy passed for affected
cleanup source. The earlier rejected candidate separately passed 71 affected
tests; engineering validity did not make its economics acceptable.

| Paired C / cleanup scenario | Wealth (both) | DD (both) | Orders (both) | Daily equity |
| --- | ---: | ---: | ---: | --- |
| minus_sz300666 | 2.4485949990159668 | 0.15838911160983737 | 32 | 869 / 869 exact |
| full | 30.74331173106631 | 0.27146973146234554 | 20 | 869 / 869 exact |

Both cleanup runs and reused C controls passed native source/config/roles/raw/
account readback. All scalar metrics and all daily equity values are exactly
equal. All daily decisions, fills and ledgers match after excluding only the
six explicitly listed derived identity fields in cleanup_summary.json;
raw byte identity is NOT claimed. No full matrix or new manual CI dispatch.
Cleanup native producer: 6572a44fa99fc6c4b267b418298c3b0219a3bc20.
Cleanup production fingerprint: d8b104b3faea5ffcdc1b0c888fe8844814c99552300ee0b3d2b55d156b4bd303.

## Delivery boundary

The main branch was not modified or merged. PR56 remains an unaccepted draft.
Normal pushes to ychenracing/uquant, codex/ordinary-recovery-20260909 were
rejected by automatic approval review, including a retry after checking the
handoff's original push authorization and the matching remote repository/PR.
The review explicitly requires direct publication authorization; no alternate
push route was used. Local commits and raw evidence are preserved. Publishing
these commits to the same draft PR is the remaining external action; it would
not authorize merging C or restarting strategy experiments.

Existing Performance/robustness/Absolute/Ownership failures and unfinished
acceptance remain separately outstanding. This round delivered a negative
strategy decision plus a small verified simplification, not a profitability
improvement or complete acceptance.

Final identity check: the Absolute contract candidate source and its seal were
rebound to the cleanup fingerprint using the existing projection mechanism.
All thresholds, frozen inputs and historical baseline fields are unchanged;
the metadata edit does not change the economic source fingerprint or relabel
old manifests as new evidence. All 35 existing Absolute contract tests passed.
