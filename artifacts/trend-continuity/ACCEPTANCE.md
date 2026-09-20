# Trend continuity: preregistered acceptance

Authority: user implementation mandate on 2026-09-20. Baseline is remote main
`7439d43abb8970a1ddff93883c49414e29aecad8`. This is a new bounded task contract,
not a claim to pass the historical Absolute or cross-vintage contracts.
Registered before any economic candidate replay. All windows are previously
observed research data, never fresh holdout.

## Scope and budget

At most two evidence-supported mechanisms, one candidate and at most one
mechanistically justified revision each. Keep failed raw results. No threshold
changes after candidate results. Failed economics cannot be merged as success.
The independently committed shared-market calculation refactor is neutral.

H1: replace the ordinary lifecycle's MFE-dependent MA20/MA60 choice with MA60.
The baseline full/2024 account exit audit includes all ordinary lifecycle exits,
including continued declines. Six of 15 distinct signal/symbol events were
above MA60. Forward returns are diagnosis only, not portfolio benefit.
Do not change eligibility, sizing, risk caps, confirmation count or minimum hold.

## Paired cases and identities

Use existing `run_pairs.run` and strict `replay.py`, frozen 34-symbol universe,
initial cash/account defaults, actual execution constraints and fees. End date
2026-08-05. Cases: full (2023-01-03), 2024-offset0 (2024-01-02),
2025-offset0 (2025-01-02), loo-sz300308, loo-sz300502, remove_all_three
(the latter three start 2023-01-03), and full-cost2.
Development sentinel set: full, 2024-offset0, loo-sz300308, remove_all_three.
Reject a sentinel failure before expanding; successful final candidate needs all
seven cases. Both sides use Python 3.12.13, numpy 2.5.1, pandas 3.0.5,
uv 0.11.33; freeze data/config/universe/start state/cost/runner identity.
Each side records its own committed producer SHA and production tree. Never
label two different implementations with one producer identity.

## Fixed gates

| Dimension | Requirement | Rationale |
| --- | --- | --- |
| Net economics | Geometric mean candidate/baseline final wealth >=1.02 over six standard-cost cases; >=2 cases individually >=1.01 | Material portfolio improvement with more than one witness |
| Worst return trade-off | Every standard-cost case wealth retention >=0.90 | Bound local opportunity cost |
| Risk | Each full-account max drawdown <=30% and increase <=1 percentage point | Preserve recent 30% ceiling; bounded incremental risk |
| Execution burden | Each case distinct durable order count <=max(baseline+2, baseline*1.10); operation days <=baseline+2 | Personal daily operation should not become substantially harder |
| Turnover/cost | Each case daily-equity-normalized turnover <=baseline*1.10; summed fee+slippage/equity <=baseline*1.10 | Compare proportional burden, not fees on different wealth |
| Cost stress | Full-cost2 candidate/baseline wealth >=0.90 and candidate cost2/standard wealth >=0.90 | Preserve benefit under existing doubled-cost scenario |
| Mechanism | Actual changed held/exit path and longer median continuous holding duration; report mean exposure and all affected names | No success from an unreachable edit or isolated hindsight calculation |
| Complexity | Remove >=1 independent branch/rule or state, no new persistent state/config/parallel engine; keep safety checks | Real simplification, not line compression |
| Correctness | Preserve next-session execution, funds/costs, risk caps, causal confirmations, restart state and transactional/input guards | Non-negotiable correctness |

Compute turnover as sum(fill gross / same-day marked equity); cost burden as
sum((commission+stamp duty+transfer fee+slippage)/same-day equity). Operation days
are distinct actual fill dates. Continuous holding duration uses actual positive
shares until flat, including partial exits; same state restart must reproduce,
new accounts start empty. Report concentration of gains, worst deterioration,
and average gross exposure; historical evidence cannot establish generalization.
A candidate failing these gates may be rejected without running remaining cases;
unrun cases are explicitly unverified. No full Absolute pass is claimed.
