# Future Holdout evidence

> **权威级别：历史证据** — 本目录保存零观察、null score 的跟踪基线，不代表本地或未来
> 已产生真实观察。当前命令和 no-backfill 合同见 [Future Holdout](../../docs/HOLDOUT.md)，
> 证据目录见[历史证据索引](../README.md)。

This directory contains report-only, tracked validation evidence for the immutable
`phase2-future-holdout-v1` contract.

- `lane_validation.json` is recomputed from the sealed lane registry and the isolated
  holdout data directory. It preserves zero observations and null formal scores when
  real future data is absent.
- Future deterministic replay artifacts remain separate from historical replay.
- Manual execution is stored only in the append-only execution Journal and never in
  this model-scoring evidence. Its default JSONL and external checkpoint are ignored
  by Git; `journal report`, `journal checkpoint`, and `journal verify` provide the
  operator summary and retained-prefix integrity checks.
- `decision_equivalence.json` records protected-byte equality, the unchanged production
  account code fingerprint, and the Journal/Decision Digest isolation test.
- `diagnostics/` preserves the first production-fingerprint boundary failure and its
  fail-closed resolution plus the first full Engineering failure instead of deleting
  failed evidence.

Run `uv run python -m scripts.future_holdout validate-static-lanes` to fail closed if the registry,
data identity, observation prefix, milestone policy, or tracked evidence differs.
