### Task 2: Freeze the absolute contract and canonical 34 scenarios

**Files:**
- Create: `benchmarks/absolute_generalization_acceptance_contract.json`
- Create: `uquant/validation/absolute_generalization/__init__.py`
- Create: `uquant/validation/absolute_generalization/contract.py`
- Create: `uquant/validation/absolute_generalization/scenarios.py`
- Modify: `benchmarks/source_surface_registry.json`
- Modify: directly affected public/reference governance files if the current registry requires them
- Create: `tests/test_absolute_generalization_contract.py`
- Create: `tests/test_absolute_generalization_scenarios.py`

**Interfaces:**
- Consumes: canonical `AIUniverse`, `benchmarks/strategic_ownership_acceptance_contract.json`, strict/canonical JSON helpers, frozen source/config/data identities.
- Produces: `AbsoluteGeneralizationContract`, `AbsoluteGeneralizationScenario`, `load_absolute_generalization_contract(path)`, and `build_leave_one_out_scenarios(contract) -> tuple[AbsoluteGeneralizationScenario, ...]`.

- [ ] **Step 1: Write RED contract tests**

Test exact field sets, duplicate-key rejection, NaN/Infinity rejection, tamper rejection, compiled seal equality, `baseline_can_relax_absolute_limits is False`, exact window/components/thresholds/critical/witness/shards, and equality with the canonical 34-member production/ownership universe.

- [ ] **Step 2: Write RED scenario tests**

Assert exactly 34 sorted unique cells; each removes one and only one canonical symbol; static shards are disjoint and complete; critical/witness flags use fixed membership; no runtime selection or random subdivision exists.

- [ ] **Step 3: Implement strict immutable contract loading**

Use duplicate-detecting JSON parsing, finite-number validation, canonical serialization, a compiled `ABSOLUTE_GENERALIZATION_CONTRACT_SHA256`, and exact identity verification. Expose no writer or auto-accept path.

- [ ] **Step 4: Implement canonical scenario construction**

Define a frozen scenario record carrying `cell_id`, `removed_symbol`, window, shard, critical/witness flags, and contract identity. Construct from the validated contract only, then independently check 34/34 coverage.

- [ ] **Step 5: Register the reviewed source surface**

Update the current registry according to its canonical rules and recompute only the candidate registry seal; do not alter frozen baseline/absolute thresholds.

- [ ] **Step 6: Verify and checkpoint**

Run the two focused test files, affected contract/source/public-API gates, Ruff and strict MyPy for touched modules, `compileall`, and `git diff --check`; commit a coherent Task 2 checkpoint, push, and verify remote SHA.

---

