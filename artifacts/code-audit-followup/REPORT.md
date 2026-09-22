# Follow-up repository audit — 2026-09-22

Baseline: `ccb7be8907f8ec547c573d1e032a59ee5d4e2468`. Published code:
`9be5f3bbebb34ed8e04cf099b6848b82eba91f96`, tree
`1d449ba3f2f92e230cbdf5d1e6550d71ec32fca0`. This is exactly the tree tested locally
as `1a6e569aee68542cfddfe93bcdfe70a511def9f5`; the connector created a new commit
identity after native Git push lacked authentication. All 32 changed blob IDs,
the complete tree and remote main ref were checked; native fetch also confirmed
zero tree differences. No force push, frozen baseline changes or budget relaxation.

## Scope and confirmed fixes

`source-inventory.tsv` records the bytes and SHA-256 of 710 tracked Python files:
249 production, 63 research, 19 scripts, 11 cloud tooling, 366 tests and two root
build/setup files. All were parsed; executable code/tests received Ruff checks,
and mypy covered 331 configured source files. Manual review concentrated on the
changed paths, input validation, economic state mutation, compatibility and
evidence loading. This is not a claim of manual line-by-line inspection of every
file or proof that no defect remains. Frozen artifact producers are historical
evidence, not current implementation to rewrite.

- Broker identities: reject null, booleans, numbers, containers and blank IDs
  before mutating account state. Previously string coercion manufactured IDs.
  Added 21 malformed-identity cases asserting unchanged account state.
- Market data: reject missing dates, non-finite OHLC/volume/turnover and negative
  turnover. Preserve existing optional-turnover fallback and valid data behavior.
  Added 13 malformed-data cases.
- Legacy execution journal: replace runtime rewriting of class names, modules,
  annotations and dataclass metadata with ordinary definitions in the public
  compatibility module. Remove three unused internal copies; preserve public
  v1 read contracts and the existing prohibition on v1 writes.
- Split rearm predicates and candidate qualification helpers into existing owners;
  retain original public imports and signatures. Remove duplicate leader filtering
  and simplify regime lookup. Extract daily invocation and run-identity validation
  from production observation while preserving validation order and transactions.
- Consolidate research evidence decoders, identity-path validators and canonical
  JSON hashing into existing implementations. Preserve distinct strict and
  `default=str` encodings and envelope-hash exclusions.
- Historical evidence loading: stream Git archive output through a temporary file
  instead of keeping duplicate archive bytes in memory; clean failed extraction.
  Keep the immutable source identity and unsafe archive-member rejection.

Retained compatibility has concrete consumers: public import/reflection/pickle
identities, old account/journal readers, and exact schema-binding cache facades.
Tiny validators for genuinely different schemas were not merged. Historical
frozen evidence and its producers were not edited to resemble current code.

## GitHub Actions diagnosis

Baseline Engineering run `35689082521` had seven failing tests across jobs
`106622034060` and `106622034064`; complete original logs are in `ci-baseline/`.
Old gates counted deferred/type-only dependencies as initialization cycles and
schema-bound cache delegation as duplicated implementation despite the newer
exact proofs already in the repository. They now use those same fail-closed
proofs. Actual module/function growth was fixed in code rather than allowed.

The seven complexity/import-cycle checks passed after repair. The reverse-owner
risk check passed in the 38-test final runtime selection. One larger historical
risk relocation test reached the 600-second local supervisor limit without a
verdict. It is **unverified**, not passed; its confirmed new 121-line function
debt was removed and all current complexity budgets pass. Existing negative
fixtures covering real eager cycles and mutated cache delegation were retained
and passed in the targeted boundary selections.

New Engineering `35695668278`, Grant `35695668276`, Ownership `35695668281` and
Absolute `35695668285` runs were triggered on the published code and were queued
at inspection. No claim of all-green current CI is made. Baseline Grant,
Ownership, foundation, quality, Windows, security and application-right passed;
these are baseline results, not results on the new revision. Long-running jobs
were not awaited, as requested. Workflow timeouts and acceptance criteria remain
unchanged.

## Validation and evidence reuse

Complete commands, stdout/stderr, failures and checkpoints are preserved in the
multipart validation archive described by `validation-manifest.json`.

| Selection | Result | Scope |
| --- | --- | --- |
| Final Ruff | pass | production, research, scripts, tools, tests |
| Final mypy | pass | 331 configured files |
| Broker/journal/rearm/runtime boundaries | 144 passed | initial coherent repair |
| Broker transaction/rearm integration | 155 passed | includes malformed-ID regressions |
| Data/API/runtime boundaries | 44 passed | includes malformed-data regressions |
| Risk transitions and holdings | 222 passed | unchanged risk behavior |
| Research consolidation | 120 passed | codecs, provenance, runners, window comparisons |
| Final core selection | 102 passed, 1 environment failure | wrong host uv; corrected below |
| Final runtime/observation/reverse-owner | 38 passed | Python 3.12.13, uv 0.11.33 |
| Final complexity/import-cycle selection | 7 passed | no budget relaxation |
| Portfolio relocation selection | 7 passed, 1 API failure | explicit reexports corrected; API passed in final core selection |
| Historical risk relocation | 600-second timeout | unverified; not retried indefinitely |

Counts overlap and must not be summed as unique tests. Earlier failures also
include an invalid test path, dirty-checkout refusal and runtime mismatch; all
are retained. Final runtime and API checks resolve those respective defects.
Earlier targeted successes are reused only where later changes did not affect
their behavior; the final observation extraction received the final runtime
selection. No full-suite or full frozen economic acceptance claim is made.

`paired-replay/RESULT.json` compares baseline and candidate over 59 early-trend
and 47 recent-risk sessions. Exact economic projections match for all 106 days:
cash, equity, holdings, targets, orders, fills and risk controls. Configuration
hash is unchanged. Full original decisions/accounts retain distinct source-derived
identities; these identities are not falsely compared as economic differences.
The two windows are diagnostic coverage, not a full generalization matrix.

## Reconstructing preserved originals

Uploads use bounded binary parts. Each manifest records ordered offsets, lengths
and SHA-256 values. Concatenate parts in manifest order to restore the exact
original. For example, from this directory:

```sh
cat validation-logs.tar.gz.part* > validation-logs.tar.gz
cat paired-replay/baseline.json.gz.part* > paired-replay/baseline.json.gz
cat paired-replay/candidate.json.gz.part* > paired-replay/candidate.json.gz
```

Verify reconstructed hashes against the manifests before extraction. The replay
producer is copied from the existing bounded audit replay with only baseline and
output locations updated; `GITHUB_SHA` records its actual local tested revision.
