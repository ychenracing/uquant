# Daily confirmed trend-entry experiment — preregistered 2026-09-09

Authorized by the user: conduct the simple trend comparison, then decide from evidence whether to replace entry.
This is a research-only fixed experiment, not approval to weaken C acceptance or deploy a new production entry.

## Existing evidence and hypothesis

The monthly MA60/MA120/ret120 comparator already failed (see simple_ordinary_replacement_result.md).
Do not rerun its four long cases merely to obtain new commit labels.
Its removal cases froze after early losses. The new hypothesis is narrowly whether daily access to the
same trend signal, with five consecutive eligible sessions, improves participation and net outcomes
under the unchanged risk/exit/portfolio/execution mechanisms. No unconditional guard release is allowed.

## One fixed variant

- Reuse research.cross_ai_benchmark; preserve the monthly variant and its historical identity.
- Daily variant: current close > MA60 > MA120, trailing 120-session return > 0, existing history,
  liquidity and volume checks; all must hold for five consecutive replay trading sessions.
  Start with zero confirmations, reset on ineligibility or unavailable current bar; observe at most once per date.
- Fill empty slots daily in descending trailing 120-session return, symbol as deterministic tie-break.
  At most three held/pending names. Retain incumbents; do not sell merely because rank changes.
- New entries request existing 0.30 weight. Existing monthly top-ups, between-review held/pending weight retention,
  two closes below MA120 exit, order expiry, lot/fee/slippage rules, risk freezes, account capital state,
  0.90 gross ceiling and 0.75 industry/correlation ceilings remain unchanged.
- Five-session confirmation and daily empty-slot access form ONE declared treatment; no attribution of their
  individual effects. No parameter sweep, second formula or economic revision after observing outcomes.
- Use unchanged main 960539a production for both standalone variants; the producer benchmark source
  is separately bound. The research account never acquires strategic ownership.

## Fixed scenes and decision

All begin 2023-01-03. Full, remove_all_three and remove_all_three minus sz300666 end 2026-08-05;
no_optical H1 ends 2023-06-30. Exactly one daily variant per scene after a tiny native sentinel.
Reuse monthly controls only after native readback and declared reader-only source equivalence.

For a replacement recommendation, the daily variant must satisfy all original prerequisites from
simple_ordinary_replacement_plan.md: removal wealth >= 2.397615989680009, strict-removal wealth >= 1.0,
no-optical H1 wealth >= 1.3753099655116032; corresponding DD ceilings 0.30/0.30/0.2442425185317515
and orders <= 22/22/12. These retain the old comparison's conditions rather than redefining a win.
Separately report results against the later 32-order allowance without calling failure under the
original research screen a pass. Full is descriptive, not proof of strategic-owner preservation.

Report wealth, DD, actual orders, cash/turnover, first/last fill, frozen/flat sessions and rejected eligible
entries. Compare signal/entry timing, capital/risk blocks and realized holding losses. A higher standalone
return alone cannot justify production replacement. If any prerequisite fails, retain the failed evidence,
do not integrate this variant, and identify the next responsible layer from raw. A conditional integration
would still require the original production economic/ownership/Absolute gates; these four are not acceptance.

## Verification and provenance

Validate confirmation chronology/reset, unchanged monthly behavior, freeze safety, next-open fills,
account reconciliation, identity-bound native readback and rejection of the wrong variant.
No Future Holdout, monkeypatch, new dependencies, expanded universe, fee/risk/contract changes.
Save code and raw/checkpoints; old workspace and all previous failures remain untouched.
