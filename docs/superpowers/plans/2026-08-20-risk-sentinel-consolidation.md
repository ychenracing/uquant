# Risk Sentinel Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the rejected Phase 7 work into durable evidence, validation, reporting, and audit assets while preserving every production economic output.

**Architecture:** Analyze the existing immutable Phase 6 market timeline in a research-only module, render its already-computed summary through the sole Daily Report, and archive only compact Phase 7 rejection facts. Candidate authority, candidate configuration, and trading-path changes are excluded by both file inventory and tests.

**Tech Stack:** Python 3.12, immutable dataclasses, pandas, pytest, uv, Ruff, mypy, canonical JSON.

**Spec:** `docs/superpowers/specs/2026-08-20-risk-sentinel-consolidation-design.md`

## Global Constraints

- Start from remote `main` at `711af1179aa72ce48ca3a6af58ecddb3a029a7ce`; do not merge or whole-cherry-pick Phase 7.
- Keep `risk_sentinel_causal_confirmation_enabled=false`; do not add Sentinel-exclusive authority.
- Do not modify strategy, portfolio, execution, account economics, gross-cap behavior, symbol caps, SELL authority, or account fields.
- Only `SHADOW` and `FREEZE_ONLY` are supported modes; rejected legacy values raise explicit errors.
- Preserve Decision Digest, RiskAssessment, targets, orders, fills, account economics, wealth, drawdown, Sharpe, turnover, account orders, acute return, and trade count exactly.

---

### Task 1: Audit and lock the Phase 7 recovery boundary

**Files:**
- Create: `docs/reviews/phase7_artifact_review.md`
- Create: `docs/reviews/phase7_rejection_summary.md`
- Test: `tests/test_phase8_consolidation_artifacts.py`

**Interfaces:**
- Consumes: Git range `711af117..c559c00` and Phase 7 final evidence.
- Produces: a complete per-path merge/rewrite/archive/discard inventory and compact rejection record.

- [ ] **Step 1: Write the failing artifact-boundary test**

```python
def test_phase7_review_classifies_every_changed_path():
    payload = json.loads(Path("artifacts/sentinel/evidence_closure/phase7_recovery_inventory.json").read_text())
    assert {row["path"] for row in payload["files"]} == PHASE7_CHANGED_PATHS
    assert payload["production_causal_confirmation_enabled"] is False
    assert not Path("artifacts/sentinel/exclusive_freeze/candidate_lock.json").exists()
```

- [ ] **Step 2: Run the focused test and verify it fails because the review files are absent**

Run: `uv run pytest -q tests/test_phase8_consolidation_artifacts.py`

- [ ] **Step 3: Write the two audit documents**

Record every one of the 27 Phase 7 changed paths with one disposition. Preserve
the exact rejection facts: one trusted non-severe event on 2024-06-25,
zero blocked new-risk actions, zero direct sells/gross-cap events/healthy
reductions, and no promotion, holdout lane, tag, or main merge.

- [ ] **Step 4: Run the artifact test and commit the audit milestone**

```bash
uv run pytest -q tests/test_phase8_consolidation_artifacts.py
git add docs/reviews tests/test_phase8_consolidation_artifacts.py
git commit -m "docs: archive phase7 sentinel rejection"
```

### Task 2: Add the point-in-time Sentinel Evidence Closure analyzer

**Files:**
- Create: `research/sentinel_evidence_closure.py`
- Create: `tests/test_sentinel_evidence_closure.py`
- Create: `artifacts/sentinel/evidence_closure/README.md`
- Create: `artifacts/sentinel/evidence_closure/evidence_closure.json`

**Interfaces:**
- Consumes: `RiskEvidenceTimeline` and an optional mapping of trigger date to 5/10/20-session tech-index returns.
- Produces: `analyze_evidence_closure(timeline, forward_returns)` and `run_evidence_closure(data_dir, as_of, output)` returning canonical JSON-compatible evidence.

- [ ] **Step 1: Write literal fixture tests for each classification**

```python
@pytest.mark.parametrize(
    ("base_dates", "sentinel_dates", "expected"),
    [
        ({"market_velocity": "2026-01-02"}, {"market_velocity": "2026-01-02"}, "DUPLICATE"),
        ({"breadth_structure": "2026-01-06"}, {"breadth_structure": "2026-01-02"}, "EARLIER"),
        ({}, {"covariance_stress": "2026-01-02"}, "INCREMENTAL"),
    ],
)
def test_classifies_first_family_relationship(base_dates, sentinel_dates, expected):
    result = analyze_evidence_closure(timeline(base_dates, sentinel_dates), RETURNS)
    assert result["events"][0]["relationship"] == expected
```

Add independent tests that positive 20-session outcomes are
`FALSE_POSITIVE`, missing 20-session outcomes remain `DATA_NOT_READY`, current
account families cannot enter the analyzer, and output order is deterministic.

- [ ] **Step 2: Run tests and verify RED failures name the missing module/API**

Run: `uv run pytest -q tests/test_sentinel_evidence_closure.py`

- [ ] **Step 3: Implement the minimal pure analyzer**

Use only `market_velocity`, `breadth_structure`, and `covariance_stress` from
the timeline. Do not copy or alter thresholds. Calculate relation from the two
first-date maps and the aligned base row on the Sentinel trigger date.

- [ ] **Step 4: Add the production-shaped research runner**

Load the canonical universe and index data through `ProductionEngine`, obtain
the immutable account-free timeline, compute tech-index forward returns from
the already-loaded close series, and atomically write canonical JSON. The
runner must not call `decide`, mutate an account, or override configuration.

- [ ] **Step 5: Verify focused tests and generate the reviewed artifact**

```bash
uv run pytest -q tests/test_sentinel_evidence_closure.py
uv run python -m research.sentinel_evidence_closure \
  --data-dir data/frozen --as-of 2026-08-05 \
  --output artifacts/sentinel/evidence_closure/evidence_closure.json
```

- [ ] **Step 6: Commit the evidence-closure milestone**

```bash
git add research/sentinel_evidence_closure.py tests/test_sentinel_evidence_closure.py artifacts/sentinel/evidence_closure
git commit -m "research: close sentinel evidence comparison"
```

### Task 3: Finalize the single Daily Report Sentinel section

**Files:**
- Modify: `uquant/report.py`
- Create: `tests/test_sentinel_report.py`
- Create: `artifacts/sentinel/evidence_closure/daily_report_example.md`

**Interfaces:**
- Consumes: the existing `Decision.risk_summary`; no Sentinel service call.
- Produces: one compact `## Risk Sentinel` section from `render_daily_report()`.

- [ ] **Step 1: Write report behavior tests before changing production code**

```python
@pytest.mark.parametrize(
    ("coverage", "base", "sentinel", "owner"),
    [
        ("NOT_READY", False, False, "DATA_NOT_READY"),
        ("READY", False, False, "NONE"),
        ("READY", True, False, "BASE_RISK"),
        ("READY", False, True, "SENTINEL"),
        ("READY", True, True, "BOTH"),
    ],
)
def test_daily_report_owner_and_bounded_conclusion(coverage, base, sentinel, owner):
    report = render_daily_report(decision(summary(coverage, base, sentinel)), account())
    assert f"Owner: **{owner}**" in report
    assert not any(term in report for term in ("sell", "reduce position", "single-stock"))
```

Also assert Mode, Level, Coverage, Confidence, Risk Families, AI Industry Risk,
and Conclusion are present for a ready observational assessment.

- [ ] **Step 2: Run focused tests and verify RED on the new compact labels**

Run: `uv run pytest -q tests/test_sentinel_report.py`

- [ ] **Step 3: Implement the compact renderer without touching decisions**

Read only existing summary keys. Conclusions are exactly one of normal
execution, no new risk, or check data. Never derive a target, order, sell,
gross cap, or symbol instruction.

- [ ] **Step 4: Verify report tests and generate a sample**

```bash
uv run pytest -q tests/test_sentinel_report.py tests/test_cli_and_report.py tests/test_engine_contracts.py
```

- [ ] **Step 5: Commit the report milestone**

```bash
git add uquant/report.py tests/test_sentinel_report.py artifacts/sentinel/evidence_closure/daily_report_example.md
git commit -m "feat: consolidate sentinel daily reporting"
```

### Task 4: Unify production documentation and guard rejected modes

**Files:**
- Modify: `README.md`
- Modify: `docs/RISK_SENTINEL.md`
- Modify: `docs/PERFORMANCE.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/CONFIGURATION.md`
- Modify: `tests/test_phase8_consolidation_artifacts.py`

**Interfaces:**
- Consumes: existing `SystemConfig` validation and Phase 6/7 evidence.
- Produces: one consistent production statement and executable mode-contract tests.

- [ ] **Step 1: Add failing behavioral mode tests**

Instantiate `SystemConfig` from mappings with `LIMITED_GROSS_CAP` and
`SENTINEL_EXCLUSIVE_FREEZE`; assert explicit `ValueError`, and assert default
mode/authority remain `FREEZE_ONLY`/`false`.

- [ ] **Step 2: Run focused tests and confirm current code already enforces the contract**

If the behavior already passes, keep `uquant/config.py` byte-identical and
record that its Phase 6 guard was retained rather than rewritten.

- [ ] **Step 3: Rewrite contradictory docs**

State consistently: production mode is `FREEZE_ONLY`; Phase 6 causal history
is diagnostic because authority is disabled; Phase 7 was rejected; Sentinel
has no direct sell or gross-cap capability; daily operation runs only
`uquant daily`.

- [ ] **Step 4: Verify focused contracts and commit documentation**

```bash
uv run pytest -q tests/test_config_contracts.py tests/test_phase8_consolidation_artifacts.py
git add README.md docs tests/test_phase8_consolidation_artifacts.py
git commit -m "docs: close sentinel production state"
```

### Task 5: Prove engineering and economic equivalence, migrate code identity, and publish

**Files:**
- Create: `artifacts/sentinel/evidence_closure/account_code_identity_migration.json`
- Create: `artifacts/sentinel/evidence_closure/economic_equivalence.json`
- Create: `docs/reviews/2026-08-20-risk-sentinel-consolidation.md`

**Interfaces:**
- Consumes: committed final tree, frozen data, clean frozen champion checkout, and a baseline account.
- Produces: final engineering evidence, exact Phase 1 decision/account digest equality, code-only account migration, and review report.

- [ ] **Step 1: Run the complete Engineering gate on the final candidate tree**

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run pytest --cov=uquant --cov-report=term-missing --cov-report=xml
uv run python -m compileall -q uquant scripts research tests
uv run python -m build
uv run bandit -q -r uquant research scripts
uv export --extra dev --format requirements-txt --no-hashes --output-file /tmp/uquant-phase8-requirements.txt
uv run pip-audit --cache-dir /tmp/uquant-phase8-pip-audit-cache --requirement /tmp/uquant-phase8-requirements.txt
```

- [ ] **Step 2: Commit the candidate and run exact cross-commit equivalence**

Use `scripts/verify_phase1_decision_equivalence.py` with a clean checkout of the
frozen champion and the clean Stage 8 worktree. Require 45/45 identical
decision-payload and economic-account hashes. Record the exact digests and
metrics in `economic_equivalence.json`.

- [ ] **Step 3: Execute explicit account code-identity migration**

Create a baseline account bound to the pre-Stage-8 code fingerprint, run
`uquant account-code-migrate --acknowledge-code-change`, and prove every
economic field is byte-identical after excluding only `code_hash` and the new
migration event.

- [ ] **Step 4: Request independent read-only review and fix all Critical/Important findings**

Review the complete `711af117..HEAD` range against this plan and the user
requirements. Re-run affected checks after any fix.

- [ ] **Step 5: Write the final review and rerun fresh completion checks**

Record recovery inventory, closure counts/dates/outcomes, report sample,
engineering results, equivalence hashes, migration hashes, commits, and remote
main/branch state. Run `git diff --check`, full pytest, and focused artifact
readback on the exact tree that will be pushed.

- [ ] **Step 6: Commit and push the Stage 8 branch**

```bash
git add artifacts/sentinel/evidence_closure docs/reviews/2026-08-20-risk-sentinel-consolidation.md
git commit -m "test: seal sentinel consolidation evidence"
git push -u origin codex/uquant-phase-8-risk-sentinel-consolidation
```
