# Active anchor memory and bounded cleanup — 2026-09-11

Current economic source `db7a71375453347e812b3af2c0eba7ea82381fb151a3594d1683d6ec09aec31e`. Active rank-only re-anchoring preserves armed/break memory; original ordered admission confirmation is unchanged. First bounded cleanup removes four unused helpers and one parser alias, net 58 production lines. Final historical verification: 55 native accounts, 55 economically identical to Z, 221 focused safety tests and 63 cleanup-related tests passed. Full historical/generalization contract remains NOT_MET; known test/CI failures remain disclosed. See docs/active-anchor-memory-20260911.md and benchmarks/active_anchor_memory_result_20260911.json. Resolve mutable PR/merge state from PR58.

The broader admission-identity change was rejected for an 18.29% later-window wealth decline beyond the preauthorized 10% tolerance. Preserve benchmarks/anchor_correctness_rejected_20260911.json and its original plan; do not reuse it as accepted evidence. No further candidate, parameter search, future holdout or live rollout is pending in this bounded delivery.

## Previous finite Z delivery

Z source18912bdf remains the historical baseline, delivered in PR56 under its explicit finite scope. See docs/z-finite-delivery-20260911.md and benchmarks/z_finite_delivery_contract_20260911.json. Original reports and prior task history are preserved.
