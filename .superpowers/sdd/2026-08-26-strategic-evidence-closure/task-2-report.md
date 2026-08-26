# Task 2 report — production-backed trace, provenance, and intervention

## Scope delivered

- Added deterministic mtime-0 JSONL-gzip shards with a separately sealed header and row digest; readback rejects altered/unsealed rows.
- Added required compact provenance construction and canonical payload sealing helpers.
- Added `StrategicOwnerIntervention` with one-shot, atomic owner replacement on a shadow account, native account-codec validation, and before/after economic hashes.
- Added the production close/open replay wrapper. It uses `ProductionEngine`, `ReplayHarness`, production execution, and the durable account model; the only hook is immediately before the selected close decision.
- Added causal route rows, fixed first-divergence layer order, accounting reconstruction, and baseline-derived common activation/target-gross detection.

## TDD evidence

- RED: the initial focused suite failed with missing `provenance`, `intervention`, and `replay` imports.
- RED: the shard test then failed with one JSON record where deterministic JSONL required a header plus one line per row.
- GREEN: focused suite passed after the minimal implementations and JSONL rewrite.

## Verification

- Focused suite: `7 passed`.
- Adjacent existing suite: `82 passed` (`test_research`, `test_first_divergence`, `test_market_replay`, and `test_account_schema_v3_integrity`, plus Task 2 tests).
- Ruff: clean; strict MyPy: clean for the four new modules.
- Full frozen five-symbol reproduction used baseline-derived activation `2023-01-04` and target gross `0.95`. Baseline and forced-`sz300308` traces had no divergence after stripping intervention provenance; metrics were identical: total return `23.509661802900865`, max drawdown `0.27146973146234554`, account orders `12`.

## Scope boundaries

No files under `uquant/`, `data/frozen/`, the sealed v1 contract, production configuration/policies, or `uv.lock` were modified.

## Review-fix addendum

Root cause: the initial pre-decision account rewrite was overwritten by the production activation decision, and the replay request trusted a caller-supplied holdout boundary. The repair adds one explicit, one-time research activation boundary: it records the production decision, replaces only that activation's strategic target/intent and durable strategic identity atomically, and then resumes unmodified production next-open execution and later decisions. The same-owner control remains exact, while `sz300502` reaches the next-session fill in its regression test.

- The Future Holdout boundary is now fixed at `2026-08-06`; attempted overrides fail closed.
- Final accounting now rejects fractional/non-integral durable shares without `int()` coercion.
- Strategic map collisions fail before mutation; no key is silently overwritten.
- Runtime terminal outcomes are retained as `REPLAY_ERROR` or `INSUFFICIENT_SAMPLE` `ReplayResult` rows with empty/null-equivalent metrics rather than deleted cells.
- The deterministic full-window command recorded above remains the sealed reproduction verifier: it derives activation and target gross causally, validates both accounts, compares full route traces after provenance stripping, and asserts equal metrics.

## Provenance closure

- Shard write and readback now require the exact preregistered provenance field set, validate both Git identities, every SHA-256 identity, runtime/generated-at text fields, the envelope `payload_sha256`, and the JSONL row digest.
- Direct tests cover missing, empty, malformed, and tampered shard inputs plus a valid deterministic sealed shard.
- Commands: `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run pytest tests/test_strategic_evidence_provenance.py`; `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run ruff check research/strategic_evidence/provenance.py tests/test_strategic_evidence_provenance.py`; `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run mypy --strict research/strategic_evidence/provenance.py` — all passed (4 tests).

## Committed full-window reproduction

Command: `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run python -c "from research.strategic_evidence.checkpoint2_verifier import write_checkpoint2_summary; p=write_checkpoint2_summary('.'); print(p['payload_sha256'])"`.

The verifier completed against current frozen data in approximately 263 seconds and read back its own canonical seal. Compact evidence is `artifacts/strategic_evidence_closure/checkpoint2_forced_zhongji_reproduction.json`; its payload SHA-256 is `ab4a66bd261b6fb332bda4324dd87e82c591cce19cfb2e2363290c9ed3534402`. It binds the full window, five-symbol universe, current experiment commit and preregistered identities, causal activation, one intervention, trace/account/metrics hashes and equality checks. Large traces are explicitly not committed.

## Controller verification import-order fix

The controller found Ruff I001 in `checkpoint2_verifier.py`. The only code change reorders the local `StrategicOwnerIntervention` import before `.models`. Re-ran: `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run pytest tests/test_strategic_evidence_provenance.py tests/test_strategic_owner_intervention.py tests/test_strategic_evidence_trace.py tests/test_strategic_evidence_contract.py` (`17 passed in 11.65s`); `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run ruff check research/strategic_evidence tests/test_strategic_evidence_provenance.py tests/test_strategic_owner_intervention.py tests/test_strategic_evidence_trace.py tests/test_strategic_evidence_contract.py`; and `UV_CACHE_DIR=/tmp/uquant-uv-cache uv run mypy --strict research/strategic_evidence` (both clean).
