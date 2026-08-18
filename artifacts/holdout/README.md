# Future Holdout evidence

This directory contains report-only, tracked validation evidence for the immutable
`phase2-future-holdout-v1` contract.

- `lane_validation.json` is recomputed from the sealed lane registry and the isolated
  holdout data directory. It preserves zero observations and null formal scores when
  real future data is absent.
- Future deterministic replay artifacts remain separate from historical replay.
- Manual execution is stored only in the append-only execution Journal and never in
  this model-scoring evidence.
- `decision_equivalence.json` records protected-byte equality, the unchanged production
  account code fingerprint, and the Journal/Decision Digest isolation test.
- `diagnostics/` preserves the first production-fingerprint boundary failure and its
  fail-closed resolution instead of deleting failed evidence.

Run `uv run python -m uquant.validation holdout-lanes` to fail closed if the registry,
data identity, observation prefix, milestone policy, or tracked evidence differs.
