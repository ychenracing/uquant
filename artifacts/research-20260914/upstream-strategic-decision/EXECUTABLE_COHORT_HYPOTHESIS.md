# Executable cohort commitment hypothesis

The timing candidate f5bec508 is rejected: E acute +4.6108107936%, E H2
1.5121423856x, native remove308 0.8757181088x and remove502 2.4588111454x
(both 2 epochs/owners). Full/champion matrices were not run for that failure.
Its exact code and four raw outputs remain in Git history and this directory.

A 14-session diagnostic using the existing native point-in-time replay seam,
Jan 3–20 2023, resolved the missing first-commitment detail. It is a prefix
diagnostic, not full-window acceptance. On Jan 17 the selected owner was
sh688072; Jan 18 the selected quorum was invalid; Jan 19 the owner became
sz300223. Therefore whole-certificate persistence was not a valid protection
for the Jan 19 path. No confirmation count will be tuned around those dates.

## Distinct mechanism and economic rationale

The Jan 19 certificate had TWO currently executable members: sh688072 and
sz300223 passed the existing shared-certificate confirmation, structure and
liquidity checks. The losing July 22 E certificate had only sh688268 READY;
sz300223 failed structure and sh688361 was not ready. The Sep 27 E cohort
likewise had only sh688200 READY, while stronger ordinary candidates competed
for capital. A full-cohort label can therefore create a weak single-member
capital commitment rather than executable diversified cohort exposure.

Keep qualification and all its thresholds unchanged. For a new nondecisive
reversal FULL_COHORT with both MARKET_CONFIRMATION and OWNER_ABSOLUTE_QUALITY
failed, proceed with prospective grant creation only if another member of
the same current signature/evidence hash passes existing candidate_entry.
Otherwise retain READY and defer capital, with exact-certificate cross-entry
arbitration. No new numerical threshold, symbol/date rule or parameter is added.
Existing grants, partial fills, pending orders, owners and duties are protected
by their existing lifecycle paths. This is a prospective allocation decision;
it does not turn an unqualified security into an eligible one.

The new native behavior test first failed because the timing rule blocked a
cohort with an executable peer. After the change, supported and unsupported
cases pass. Restart and same/later-session checks prove time alone cannot
authorize a lone weak owner. Decisive and nondecisive partial-grant lifecycle
cases are covered. The rejected timing state field is no longer introduced.

Two pre-existing architecture failures were also addressed without updating
contracts: remove the accidentally public H9 reason constant, and split
capital-deferral classification from admission funding without changing its
precedence. Remaining old mature-market tests are not yet resolved; all
engineering gates must pass before merge.

Replay the same exact remotely preserved candidate in the authorized order.
No economic result is claimed for this hypothesis until generated.
