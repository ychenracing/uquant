# Crisis cap deduplication implementation plan

> Execute inline with superpowers:executing-plans under the existing autonomous authorization.

**Goal:** Remove one duplicate crisis-cap decision implementation without changing decisions or ledger semantics.

**Architecture:** Keep `protected_recovery.persistent_crisis_cap` as the existing implementation used by the protected-book call chain. Keep the public and private recovery-state interfaces and function identity through a forwarding function. No new module or dependency.

**Tech Stack:** Python 3.12.13, existing frozen runtime and pytest, Ruff, pinned mypy.

**Spec:** User continuation decision plan dated 2026-09-11, route E. Research routes and failed candidate remain separate from this branch.

## Global constraints

- Original main cf07402a98997470621fa8ffdc519e5bec156028, tree e8d35dce096926f146236a69c956c24cc414a9c7.
- No threshold, configuration, universe, execution, recovery permission, or frozen artifact changes.
- Identical severity branches and reserve-backed distinction; unknown severity fallback remains unchanged.
- Preserve compatibility imports and the recovery-state wrapper owner.
- Reuse economic results with original producer identities only after equivalence proof. No earnings claim.
- Normal PR and actual required gates; old source-bound failures remain failures.

## Task 1: Replace duplicate implementation

Files: `uquant/risk/recovery_state.py`, `uquant/risk/protected_recovery.py` (move explanatory comment only).

Interfaces: `_persistent_crisis_cap(severity: str, cfg: SystemConfig, *, reserve_backed: bool = False) -> float`; public alias `persistent_crisis_cap` stays intact.

1. Run existing risk/ownership/architecture tests against the unmodified baseline. They exercise risk semantics; do not invent an implementation-mirroring failing test for a neutral refactor.
2. Parse both baseline function ASTs and assert identical argument definitions and executable bodies. Record independent global dependency resolution.
3. Add `from .protected_recovery import persistent_crisis_cap as _protected_crisis_cap`; replace only the duplicate wrapper body with `return _protected_crisis_cap(severity, cfg, reserve_backed=reserve_backed)`. Move the reserve explanatory comment to the retained implementation.
4. Prove the retained executable function AST is unchanged; prove the wrapper forwards all arguments exactly and the rest of both module ASTs is unchanged after removing this import/body. Compare both function outputs for every severity and both reserve states under default and varied configurations; snapshot configuration immutability.
5. Repeat affected tests, full lint/type and required PR engineering gates. Review final diff independently. Failure is diagnosed, never masked.

## Task 2: Evidence and delivery

Files: `docs/crisis-cap-cleanup-20260911.md`, structured equivalence receipt and reproducible verifier in `research/`.

1. Record changed lines, original/new source identities, tests and no economic increment.
2. Retain separate A/C/D research and B rejection evidence; prepare honest Future Holdout identity/account boundary without future data ingestion.
3. Commit, push normal branch, create PR, resolve actual required gates and merge only when accepted. Verify remote main SHA. No reset, clean, rebase, or force push.
