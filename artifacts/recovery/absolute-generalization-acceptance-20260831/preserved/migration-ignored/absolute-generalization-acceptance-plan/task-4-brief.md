### Task 4: Add causal point-in-time full-removal replay

**Files:**
- Create: `uquant/validation/absolute_generalization/replay.py`
- Modify: `uquant/application/decision.py` only where a public production replay projection is genuinely missing
- Modify: directly related market/universe cache identity modules if required by evidence
- Create: `tests/test_absolute_generalization_replay.py`
- Modify: `tests/test_engine_contracts.py` only for shared role semantics

**Interfaces:**
- Consumes: `AbsoluteGeneralizationScenario`, production `AIUniverse`, point-in-time membership, `ProductionEngine`, frozen data, and next-open execution.
- Produces: `run_absolute_generalization_replay(scenario, *, root, data_dir, cache_dir) -> AbsoluteGeneralizationReplay`, with removed-symbol role absence and full raw production ledgers.

- [ ] **Step 1: Write RED full-removal and PIT tests**

Prove the removed symbol is absent from tradable, qualification-reference, risk-reference, panel loading, role declaration, and cache/data identity only on sessions where it would otherwise be effective. Prove future members do not enter earlier denominators.

- [ ] **Step 2: Write RED negative controls**

Keep a symbol in a role while withholding required data and assert `EXPECTED_BUT_UNAVAILABLE`, fail-closed qualification, and no authorization/grant/Target/Order/Fill/epoch. Distinguish this from intentional `ROLE_ABSENT`.

- [ ] **Step 3: Implement replay orchestration through production authorities**

Use `ProductionEngine.decide` and the existing execution-aware next-open path. Do not import research/scripts, inject events, or create a second economic state. Bind point-in-time role declarations and the absence set into provenance/cache identity.

- [ ] **Step 4: Prove default-path non-regression**

With no removal, verify the champion decision/equity path and identities remain unchanged; with a removal, verify the removed symbol is never loaded or emitted as owner/Target/Order/Fill/epoch.

- [ ] **Step 5: Verify and checkpoint**

Run focused replay tests, directly affected engine/market/account/execution regressions, champion sentinel, contract/source/import gates, Ruff, strict MyPy, `compileall`, and diff check; commit, push, and verify remote SHA.

---

