# Capital/holding attribution and information feasibility — 2026-09-09

User authorized the proposed bounded round: explain existing PnL differences,
at most two fixed native diagnostics, and check one independent information
source. Starting PR56 e7b2e74; current production remains unchanged and draft.

Existing rejected simple producer: 226ef63b5aabbaf402a0eff0766eca73b32d2e24,
source b7f54f137576ba15787827103103d4710369d105519c463f4289b1251dbf2942.
Its H1 no-optical wealth 1.27222751175821; C 1.4476947005385299.
All treatments are research-only, not automatic candidates for promotion.

First reconcile every symbol's native net PnL. For sh688256, both paths have
one buy, one partial CRISIS sale on May 8 and a terminal holding. Decompose
net PnL using total bought quantity, average purchase price, proportion sold,
common sale price, terminal mark, and cash fees. Use exact symmetric Shapley
attribution over quantity/purchase-price/sold-fraction; fees separate, slippage
already in fill prices. This is an accounting identity, not a feasible account
counterfactual. Never double-count slippage or assume capital availability.

Two independent arms, each based on the same rejected simple producer:
S: replace only fresh weight min(.30, budget/n) with
min(existing cfg.core_admission_weight, budget/n). No other change.
H: replace only ordinary exit function with current C's original complete
ordinary exit function and necessary import; keep simple .30 sizing.
No combined S+H arm, no tuning or third arm. Preserve every strategic owner,
risk/capital/recovery constraint, fees, roles and actual next-open/partial fills.
Each arm runs only no_optical Jan 3–Jun 30 2023, native readback. Baseline is
reused under trusted source. Compare full-account differences, not cherry-picked
winner PnL. Research screen: wealth >= 1.3753099655116032, DD <=
.2442425185317515, account orders <=12, preserving original C comparison.
A passing H1 arm is at most a lead; it is not full acceptance or new-sample proof.
If an arm supports a real improvement, assess whether its mechanism is portable
before further replay. If both fail, close these two explanations and continue
information feasibility rather than tuning their constants.

Independent information: quarterly reported revenue year-on-year growth, using
original filings and actual public dates (next-session availability), no latest
restated data retrofitted. Source feasibility only, no economic claim. Use
2023-01-03 as the fixed historical cutoff, so latest eligible quarter is 2022Q3.
Check six alphabetical canonical industries, each first alphabetical symbol
from current manifest: advanced_packaging, compute, design, foundry, materials,
optical. This is deterministic coverage sampling, not winner/loser selection.
For one sampled company check a subsequent 2023Q1 original filing to establish
update feasibility. Do not infer universe membership from revenue statements.
No purchase, input expansion, frozen contract change, or protected holdout access.

Implementation clarification before extracting sampled revenue values: the alphabetical algorithm yields advanced_packaging/sh688498, compute/sh688008, datacenter/sz002281, design/sh688037, foundry/sh688110, materials/sh688019. The earlier illustrative industry list mistakenly omitted datacenter and included optical. Use the algorithmic six, retaining this correction.
