### Task 1: Re-establish the authoritative baseline

**Files:**
- Inspect: `benchmarks/strategic_grant_acceptance_contract.json`
- Inspect: `benchmarks/strategic_ownership_acceptance_contract.json`
- Inspect: `scripts/run_strategic_grant_acceptance.py`
- Inspect: `scripts/run_strategic_ownership_acceptance.py`
- Report only: SDD `task-1-report.md` (ignored; no tracked production edit)

**Interfaces:**
- Consumes: current clean `origin/main`, frozen data, existing grant/ownership runners.
- Produces: exact current champion, report-13, critical-removal, repair, grant, epoch, owner, and provenance facts used to distinguish pre-existing behavior from regressions.

- [ ] **Step 1: Verify the branch identity and frozen inputs**

Run `git status --porcelain=v2 --branch`, `git rev-parse HEAD HEAD^{tree} origin/main`, `git merge-base HEAD origin/main`, frozen-data integrity, and the contract validators. Record exact outputs in the ignored report.

- [ ] **Step 2: Run the focused grant and ownership model baseline**

Run the exact test lists from `.github/workflows/strategic-grant-acceptance.yml` and the `ownership-tests` job. A failure is baseline evidence to diagnose; do not edit production behavior in Task 1.

- [ ] **Step 3: Run the production acceptance sentinels**

Run `scripts/run_strategic_grant_acceptance.py`, then ownership shards `champion` and `critical`. Store output under a temporary directory, not in Git.

- [ ] **Step 4: Reconcile the historical invariants**

Confirm or explicitly report divergence from champion wealth `24.509661802900865`, champion MDD `0.27146973146234554`, remove-`sz300308` wealth `1.2632209078168604` with owner `sh601869` and repair `60/60`, and remove-`sz300394` wealth `7.809998638641716` with owner `sz300502`.

- [ ] **Step 5: Publish the recoverable branch anchor**

If the baseline is coherent, push the branch without fabricating a tracked report and verify `refs/heads/codex/absolute-generalization-acceptance` equals local HEAD. Task 1 itself needs no empty commit.

---

