# Research: independent breach episodes after a settled soft exit

Status: preregistered; no production change or success claim.
Control local6bacb85247a51c1e21398e12056d68267ddf10d3 / remote7e139c6368d1ba1ab0f717887bd19e0d7c4cfbd2,
tree46c8a56923118cc5e714228db1ac5645a56bafd4, economic source78e2cc4c35593ce502843ca6fb75995d79b74e7ad4f75d0d5313b1f9b18046c4.
Configadf8c123de75f1df13e16e20793f46f631e35606d1bff20d84ebc3a43dff8e51; pinned uv0.11.33 runtime.

The exact Ownership remove-sz300502 scenario fails repeated-crowning with one
real epoch. Its owner sz300223 was fully sold on2023-05-08, but a peer's900shares
remain through2026-08-05. This is real exposure, not an orphan identity that can
be deleted. The first executed soft ATR sale is intentionally treated as final
for the holding, even after valid structural recovery and a later distinct breach.
Current tests explicitly require that policy. This candidate is therefore an
economic holding-policy hypothesis, not a claimed settlement bug.

Compare alternatives: relabeling/closing an epoch with remaining real shares is
invalid; loosening entry or capital-repair predicates has no new supporting net
opportunity evidence. A distinct observed recovery-to-breach edge supplies new
causal risk evidence and reuses the existing ATR bands, exit step and executor.
Test only that edge; do not change threshold, ranking, sizing, source inputs,
frozen contracts, registry identities or the actual-epoch validator.

Change: an executed band receipt still prevents drift rebalancing and duplicate
cuts within one continuous breach. A newly armed original band after a valid
repair may lower its target again. Missing data cannot rearm; same-day/restart
evaluation is idempotent. No new state, config, dependency or scenario switch.
This branch is separate from CAUTION research and includes no CAUTION exception.

Validation order: native-fill unit fixtures first (including failed old-policy
assertions retained), then this same single continuity scenario. Require at least
two fill-backed epochs/distinct owners, original continuity linkage, unchanged
risk/wealth requirements and40orders. If that passes, check full/champion hard15
and other affected windows before considering composition with CAUTION research.
If it cannot repair actual continuity or sacrifices required wealth/risk, close
this exact hypothesis; do not try different ATR periods/steps/holding thresholds.
It does not by itself address the known no_optical later-window entry shortfall.
No Future Holdout on or after2026-08-06; no live orders or production deployment.
