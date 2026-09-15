# Full-book transfer routing

Status: implementation test passed; economic effect UNVERIFIED.

Parent: 987ef52098e8246afe76f5eafcdfeee955dee922. Existing canonical baselines remain authoritative. No stock-confirmation relaxation is included.

Hypothesis: pre-sale position-count checks prevent evaluating a legal complete incumbent sale. Evaluate the highest eligible challenger when no slot is available, retaining actual committed-count funding and post-sale-count feasibility. No threshold, risk state, qualification, confirmation, transfer size, settlement or rotation quota changes.

RED before production edit: test_full_book_transfer_requires_complete_incumbent_exit[20000-True] failed with observed weak weight 0.2 rather than 0.0. The 40000-share partial-sale protection passed. After the two-line routing change, these and the existing transfer feasibility/attribution cases passed (18 tests).

This is distinct from the rejected ordinary-swap edge probe: it repairs a routing obstruction only. It does not claim any historical pair meets the unchanged edge, three-session persistence and fundability constraints. No repeat of parameter tuning is authorized by this experiment.

Measure A/E 2025-01-02 through 2026-07-31 and native remove308 2023-01-03 through 2023-06-30 using the existing observers, seed 0, unchanged pools/data/config, Python 3.12.13, numpy 2.5.1, pandas 3.0.5, uv 0.11.33. Save all raw results. If unchanged, classify as correctness-only; do not claim alpha recovery or expand this direction with relaxed thresholds. Full acceptance remains required before merging.
