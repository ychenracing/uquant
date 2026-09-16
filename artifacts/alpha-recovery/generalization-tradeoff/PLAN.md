# Generalization tradeoff implementation

User authorized revised acceptance and normal merge of original PR67 on success. Latest spec: benchmarks/generalization_tradeoff_acceptance.json.

1. Freeze economic preference budget and historical source before missing comparisons; preserve all old criteria and results.
2. Restore 9e988979 economic implementation on PR head; reuse exact matching existing evidence. Compare historical and candidate public production backtests under identical windows/universes/data/runtime. All-role native removals are separately evaluated, never compared to historical tradable-only outputs.
3. Complete required robustness/cost/start tests for the fixed candidate; if it fails, diagnose sufficient economic cause before modifying strategy. No threshold search or post-result relaxation.
4. Integrate revised evaluator without bypassing factual provenance/accounting checks; run affected engineering and final branch review.
5. Save small coherent source/evidence milestones via connector, programmatically preserve large originals, and normally merge original PR only on verified success.

## Decisions
- Latest explicit user authorization supersedes old prohibitions on changing economic acceptance. Physical correctness and real repository protections remain.
- Order cap remains 44 on nominal main, pressure-case counts are disclosed and reviewed. This scope correction is intentional and is not changing order definitions.
- Historical implementation lacks all-role native replay: comparing its public backtest to modern all-role replay would be invalid. Use paired public paths plus modern all-role stress.

## Status
Contract fixed. Paired public screens now reject readiness on main drawdown/orders and original PR on no_optical profitability. See SCREEN_STATUS.json for exact source/metrics. Two existing, independent variants are being screened under the revised policy; no new strategy parameters are being searched. Evaluator audit corrections and authentic native raw-artifact integration are in progress. No merge.
