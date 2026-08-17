# Balanced Code and Documentation Review Design

**Date:** 2026-08-17
**Repository:** `ychenracing/uquant`
**Baseline:** `e2663695fd008fb960b86f33bc36309a2f525b68`

## Objective

Review the complete tracked Python codebase, tests, comments, and project
documentation in repeated, bounded rounds. Fix demonstrated defects and improve
clarity without weakening strategy behavior, evidence integrity, or the economic
acceptance gates. Finish only when no known Critical or Important issue remains
and the final candidate passes every required engineering and economic check.

## Review Level

This review uses the balanced level approved by the user:

- Structural refactoring is allowed when it improves cohesion, naming, reuse, or
  readability.
- Historical decisions, orders, account state, replay output, and economic results
  remain strict compatibility targets.
- A demonstrated logic defect may change behavior only through a test-first,
  minimal fix followed by affected-window and complete economic validation.
- Passing aggregate wealth or drawdown alone is not evidence of behavioral
  equivalence. Decision traces, order paths, replay identity, and sealed contracts
  remain part of the acceptance boundary.

## Protected Constraints

- Preserve `AGENTS.md`. Do not delete or edit it during this review.
- Do not tune strategy parameters or introduce scenario-specific exceptions.
- Do not rewrite frozen historical evidence to make a new result appear older.
- Do not remove migration, compatibility, provenance, or audit code merely because
  it contains words such as `legacy`, `deprecated`, or `historical`.
- Do not weaken fail-closed validation, atomic persistence, execution-journal
  continuity, or future-holdout protections.
- Do not force-push or overwrite a remote branch that has moved.

## Review Architecture

The work is divided into three coordinated review lines.

### Production Code and Comments

Review `uquant/`, `research/`, and `scripts/` for correctness, security,
performance, error handling, typing, duplication, naming, module boundaries, and
comment accuracy. Prefer small responsibility-preserving extractions over broad
rewrites. Comments must explain non-obvious contracts or reasons and must not
repeat, contradict, or outlive the code.

### Tests and Validation Infrastructure

Review `tests/`, workflow definitions, benchmark contracts, and validation
artifacts for missing negative cases, false-positive gates, incomplete provenance,
and assertions coupled to implementation details. Defect fixes follow red-green
TDD. Contract or evidence changes must be regenerated from authentic source data
and sealed through the repository's canonical mechanisms.

### Documentation and Historical Evidence

Treat current documentation and historical evidence differently:

- User documentation: `README.md`, configuration, strategy, operations, and
  performance guides must provide concise navigation, runnable commands, explicit
  limitations, and current behavior.
- Developer documentation: architecture, development, and quality guides must
  match module ownership, verification commands, review rules, and actual CI.
- Historical material: plans, task reports, acceptance reports, and ablation
  conclusions retain their historical decisions and provenance. Only broken links,
  present-tense contradictions, and objectively incorrect descriptions are fixed.

## Iterative Review Loop

Each round follows the same bounded sequence:

1. Run static and repository-wide scans, then inspect relevant files line by line.
2. Classify findings as Critical, Important, or Minor with concrete evidence.
3. Fix Critical and Important findings first. Use a failing regression test for
   behavioral defects and focused checks for behavior-neutral edits.
4. Review the complete round diff for correctness, reuse, comment accuracy, and
   unnecessary scope.
5. Rerun affected tests and static checks, then begin another review round.
6. Stop iterative fixes when a fresh round finds no Critical or Important issue.

Minor findings are fixed only when the change has clear maintenance value and does
not create disproportionate behavioral or evidence risk. The loop is bounded by
the repository's explicit acceptance criteria; it is not an invitation to endless
style churn.

## Defect Handling

For every behavioral defect:

1. Reproduce the incorrect behavior with the smallest deterministic test.
2. Run the test and confirm it fails for the intended reason.
3. Implement the smallest coherent fix.
4. Run the focused test and adjacent suite.
5. Revert or neutralize the fix temporarily when necessary to prove the regression
   test detects the original defect, then restore and rerun it.
6. Expand to full engineering and economic validation when the affected boundary
   includes strategy, account state, execution, replay, data, or evidence.

Documentation-only, comment-only, formatting, and pure rename changes avoid a full
economic replay only after their behavior neutrality is confirmed from the diff
and production-source fingerprint rules.

Task 8 executes authenticated Phase 1/Phase 2 economics, derives any changed
source and holdout identities from committed Git objects, and reruns the affected
contracts. After Task 8's last evidence/ledger metadata commit, Task 9 runs the
zero-deselection complete Engineering gate and final provenance readback against
that exact HEAD.

## Verification Strategy

The final production tree must pass:

- Ruff linting and formatting policy enforced by the repository.
- Strict mypy checking for `uquant`, `scripts`, and `research`.
- Frozen-data manifest validation.
- Complete pytest suite with the configured branch-coverage floor.
- Bytecode compilation and package build.
- Bandit and production dependency audit.
- Phase 1 full AI-era performance validation.
- All six Phase 2 generalization windows and aggregate policy validation.
- Exact contract, source fingerprint, replay, account, and holdout checks required
  by the repository.

For structural strategy refactors, compare pre-change and post-change authenticated
artifacts at the decision, order, account, replay, and economic-summary levels. A
behavioral bug fix may differ only where the regression test and review explicitly
authorize it; all other cells and identities must remain compatible.

## Independent Review and Stop Conditions

After implementation and documentation cleanup, perform a fresh review of the
complete baseline-to-candidate diff. Fix all valid Critical and Important findings
and repeat the focused review after material fixes. The work can stop only when:

- no known Critical or Important correctness, security, data-loss, or material
  economic-regression issue remains;
- comments and current documentation match the final code;
- historical evidence retains accurate provenance;
- all required local gates pass on the final tree;
- the remote tree matches the locally verified tree; and
- Engineering, AI-Era Performance, and AI-Era Generalization complete successfully
  on the published commit.

## Publication

Work on `agent/balanced-review`, keep commits scoped and reviewable, and publish
only after final verification. Preserve remote history and use a non-forced update.
The final GitHub handoff reports the commit, changed areas, validation evidence,
economic-equivalence outcome, and any residual Minor observations.

## Out of Scope

- Strategy tuning, new signals, new thresholds, and portfolio-policy redesign.
- Replacing the data universe or changing official evaluation windows.
- Rewriting historical reports to reflect the current implementation.
- Unbounded cosmetic refactoring without demonstrated clarity or maintenance value.
