# Protected holding recovery implementation plan

Goal: evaluate the user's authorized narrow recovery policy on the preserved
selected CAUTION economic control a39a9542, then continue according to evidence.
Architecture: reuse the single allocation book, protected live-holding episode,
existing structural checks and POST_SHOCK_RESTORATION execution. One separate
per-symbol consecutive observation count lives in existing candidate_tenure;
it does not change the capital tier or its old1/2/5day permission consumers.
Tech stack: existing Python3.12, pandas, pytest; no new dependency or state model.
Spec: latest acceptance_small_gaps_20260910_assessment.md and user implementation
authorization following that reassessment. Execute inline without approval pauses.

## Scope and decision rules

- Only nonstrategic, positive, continuously held protected positions in a valid
  current shock episode. No flat-member reentry, new selection or larger cap.
- Current NORMAL/CAUTION, reduction<=1, capital tier1/chronic0, votes<=1,
  transition_damage<=the existing repair threshold and held_damage_ratio<.5;
  no independent/sector/strategic/acute damage or Sentinel freeze.
- Confirm current holding structure and health for the existing5session
  capital_budget_repair_days. Repeated days do not count; missing observations,
  episode change and damaged/closed holdings reset their count.
- No new authorization with unresolved execution. Preserve only an already
  authorized, correctly bound POST_SHOCK_RESTORATION partial remainder if current
  health/structure and holding still qualify. Other pending or late-fill rights
  block the new permission. Cash/reservations/fees/concentration remain binding.
- Keep actual losses, high-water marks, original capital repair streak and tier;
  emit only scoped restoration targets through the existing final freeze filter.
- Principal wealth15 and orders40 remain explicit anchors. User also authorizes
  comparable minor acceptance margins: review named wealth comparisons around1%
  and DD differences around1.5pp case by case, record both limits and old failures,
  never compound repeated tolerances or treat the present40.89% gap as minor.
- Original raw identities, frozen inputs and Future Holdout boundary remain.

## Task A: real bounded restoration through the allocator

Files: uquant/portfolio/pipeline.py; tests/test_held_recovery_research.py.
Reuse tests/test_core_bounded_risk_restoration.py's actual buy/risk-sale fixture.

- [x] Add failing native-order tests: no restoration on days1–4 or duplicate days;
  day5 POST_SHOCK_RESTORATION BUY fills next open; restart preserves authority;
  peaks and the old tier/streak are unchanged; no repeated restoration after fill.
- [x] Test damage/Sentinel/structure/episode/settlement denials and observation gaps.
  Check genuine partial remainder continuation without granting another entry.
- [x] Run `python -m pytest tests/test_held_recovery_research.py -q` and retain RED.
- [x] Implement `_held_repair_symbols(book) -> set[str]` and scope
  `_restore_ordinary_holdings(book, permitted_symbols=...)` plus final freeze
  authorization to exactly those symbols. Existing permissions remain separate.
- [ ] Run the new tests plus existing bounded restoration and tactical control
  modules; Ruff/mypy only affected sources; commit immutable candidate.

## Task B: economic decision, then contingent continuation

- [ ] Run existing research.cross_ai_strategy no_optical2025-01-02..2026-07-31
  under frozen uv0.11.33/Python3.12.13 and unchanged configuration/data; read_case
  verifies real raw/attribution. Compare source-bound selected control, first
  actual differing target/fill, added/restored losses and net wealth/DD/orders.
- [ ] If promising, verify full/champion>=15, fixedbull, removalcontinuous and
  affected H1/later cases before broad matrices. Reuse unaffected control evidence.
- [ ] If no real effect, follow the actual blocking condition; if executed but
  uneconomic, assess the saved integrated simple ordinary challenger under current
  legitimate gates/inputs, without retuning its MA periods/ranking or old evidence.
- [ ] If an economic candidate is viable, integrate independently verified shared
  exit/participation work, affected cases first, then required final acceptance.
- [ ] Save code, source-bound results, raw receipts, failures and latest decisions;
  update original PR56. A checkpoint or partial pass does not complete the goal.
