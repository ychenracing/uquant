# Generalization tradeoff evaluator implementation

## Scope

Implemented the standalone evaluator in `scripts/run_generalization_tradeoff_acceptance.py` and focused tests in `tests/test_generalization_tradeoff_acceptance.py`. No strategy, contract, runner, workflow, configuration, or economic replay was changed or run.

## Behavior

- Derives all required public cells from `benchmarks/generalization_tradeoff_acceptance.json`: 45 pool/window pairs, 34 tradable-only LOO pairs, the three full-window cases, and the 12 candidate stress cells.
- Reads immutable gzip JSON artifacts, rejects malformed, incomplete, duplicate-date, nonfinite, nonpositive, mismatched-symbol/window/cost, mixed-source, mixed-input/runtime/config, and unreconciled wealth/drawdown evidence.
- Verifies every declared source digest against the file at the artifact's committed Git revision and every data digest against `data/frozen`.
- Supports the five authorized `native_public_trace_reuse` envelopes only after reading the original absolute path, checking its compressed-file SHA-256, checking original completion and identities, reproducing the input-manifest projection, and exactly reconciling projected metrics, equity curve, fees, and slippage to the original trace. Runner identity is uniform within each evidence method because the native replay and original observer are intentionally distinct programs; both are independently sealed.
- Computes the contract's performance, main-account, LOO, lower-quartile, cross-industry, and per-scenario stress criteria. Raw cell metrics, paired ratios, contract hash, and artifact hashes are included in the JSON report.
- Preserves known economic violations alongside missing evidence. Status precedence is `INVALID`, `INCOMPLETE`, `NOT_MET`, then `COMPLETE`; `passed` is derived solely from `COMPLETE`.
- Refuses to overwrite an existing output.

## Native all-role boundary

The current `--native-summary` interface deliberately reports `BLOCKED` and overall `INCOMPLETE`. A document containing `COMPLETE` or reconciliation booleans cannot prove underlying account integrity. The controller must wire already-validated raw `CellArtifact` objects, or provide a reconstructable raw artifact format and validator, before the 34 frozen all-role cells can become authoritative. The summary SHA-256 is retained for provenance.

## Verification

Using `/workspace/scratch/c24820a73481/canonical-venv/bin/python`:

```text
python -m pytest -q tests/test_generalization_tradeoff_acceptance.py
7 passed

python -m ruff check scripts/run_generalization_tradeoff_acceptance.py tests/test_generalization_tradeoff_acceptance.py
All checks passed!

python -m mypy scripts/run_generalization_tradeoff_acceptance.py tests/test_generalization_tradeoff_acceptance.py
Success: no issues found in 2 source files
```

A CLI smoke read against the concurrently accumulating run directory completed without overwriting evidence. At that partial point it retained 160 missing cells and the already-known main drawdown, main order-count, and cross-industry failures; those are observations of incomplete mutable evidence, not a final economic result.

## Remaining concern

Native all-role physical validation remains intentionally incomplete. The evaluator should be rerun only after the controller finishes the immutable public matrix and supplies a raw-validatable native evidence interface.

## Review remediation

The six evaluator-review findings were addressed after the initial implementation:

1. Every equity date must now exactly equal the frozen `sh000300` session sequence for the cell's inclusive interval. Stress offsets resolve to the contract-specified ordinal before the exact sequence comparison.
2. Input validation reconstructs the complete manifest from every committed `uquant/**/*.py|json`, committed `benchmarks/reference_registry.json`, and every non-cache `data/frozen` file. Exact equality rejects omissions and unknown entries. `source_tree` must equal `git rev-parse source_head:uquant`, and standard cells within a role require identical full configs.
3. Partial paired ratios are built as a name-to-ratio mapping, so missing early or middle cells cannot shift labels.
4. Each cell exposes every scalar raw result metric. Structured output now includes performance aggregates, all available LOO ratios and complete LOO improvement statistics, cross-industry ratios/geometric retention, and each stress scenario's offset/cost/drawdown aggregates.
5. Equivalent numeric fields were added to the contract for the two quantile probabilities, lower-tail count, both primary-improvement alternatives, and cross-industry thresholds. The original prose remains unchanged. Old artifacts may carry only the explicitly recorded audited original hash; output reports both `original_contract_hash` and the current structured-contract hash.
6. Trace reuse requires every trace row to be an object, every `new_fills` value to be a list of objects, and every projected cost to be finite and reconciled. The original manifest is projected exactly and then subjected to the same complete-manifest and exact-session checks as native evidence.

The frozen `no_optical` definition remains the repository contract's five-symbol removal. It is reported as a frozen repository classification because it differs from some real-world industry classifications.

The evaluator now handles the subsequently audited legacy native-runner serialization migration without rewriting raw evidence. For each validated source commit it creates a temporary Git archive, imports that source's `DEFAULT_CONFIG`, and obtains its complete `to_dict()`, 13-field dataclass projection, and `config_fingerprint`. The legacy 13-field representation is accepted only for runner SHA-256 `2e099c...`, exact projection equality, a matching result `effective_config_sha256`, and the already-complete source/data manifest. Full native representations are limited to the legacy runner or audited new runner `435e501...`; reuse evidence must equal its source's complete `to_dict()`. Reports retain the raw representation, raw config hash, reconstructed full config hash, and effective fingerprint.

Raw native Absolute evidence is integrated through `--native-shards`. The evaluator passes the unique already-validated paired candidate `uquant` tree to `evaluate_native`, which independently rechecks the candidate checkout and six raw shard manifests. Missing shards or a candidate-tree mismatch remain `INCOMPLETE`; a complete native `PASS` or `FAIL` enters the shared final gate. `--native-summary` remains provenance-only and cannot pass acceptance.

The post-migration focused evaluator suite has 19 passing tests. A partial CLI smoke run reports `INCOMPLETE` with zero invalid cells, 160 missing public cells, and missing native shards. Its candidate cells disclose both `full_to_dict` and audited `legacy_dataclass_projection` raw forms while sharing the same reconstructed effective configuration.
