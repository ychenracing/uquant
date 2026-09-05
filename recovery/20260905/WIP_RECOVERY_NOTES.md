# Recorded post-58d WIP and next-session recovery

These notes preserve the last known work. They are not original missing file bytes. No implementation or validation was continued after the stop request.

## Allocator patch retained separately

1. Parameterize existing _candidate_entry confirmation_days. Fresh core admission still uses leader_tenure_days (5); an already partly filled ordinary entry uses one current valid route and current maturity/market evidence, without repeating the initial five-session wait.
2. A failed ordinary pending BUY cannot be funded again later in the same decision by ordinary restoration. Genuine POST_SHOCK_RESTORATION orders remain in restoration.
3. Fully exited ordinary protected names use normal fresh qualification; old rights do not resurrect them.
4. Fresh ordinary core admission retires only that symbol's stale ordinary protected_weights entry after successful budget funding.
5. Restoration requires causal linkage to the current uninterrupted holding across last_shock_date. Position.entry_date alone is insufficient after FIFO removes original lots. The recorded helper works backwards through literal fill quantities to find the uninterrupted holding's start.
6. Legitimate continuous held restoration can remain NOT_MATURE under new-entry criteria; holdings and new capital entry criteria are distinct.
7. No numerical economic threshold, fee, symbol/date rule, engine or account schema change was added by this recorded patch.

## tests/test_unified_core_book.py: missing original WIP

At feature baseline, replace the old retained partial-core test with:
- test_retained_partial_core_intent_uses_current_quality_without_repeating_confirmation(monkeypatch, evidence, protected)
- evidence: ready, routes_lost, not_mature, missing_leader, missing_panel, missing_session; protected False/True.
- _inputs(), symbol sh600001, account total 1,000,000, cash900,000, holding10,000 shares at10, pending ordinary BUY target.2.
- Current established eligibility streak1 (0 if routes_lost), current observation session=date ordinal. Optional old protected_weights[symbol]=.4. not_mature sets leader.mature=False.
- Stub strategic observer only to isolate allocator behavior. READY target.2, all failures retain actual .1. No repeated fresh five-day confirmation; cash/shares unchanged.

Add test_fully_exited_ordinary_restore_rights_require_a_new_core_admission(monkeypatch, confirmed):
- Empty 1m account, old protection.4, last_shock_date2023-04-27; optionally confirmed existing route streak5; isolate strategic observer.
- Unconfirmed: no target and old .4 remains.
- Confirmed: ordinary LEADER_SELECTION target.2 and stale protection for this fresh admission removed.
- No physical cash/share mutation.

Add test_rejected_new_entry_cannot_reopen_old_restoration_after_restart(tmp_path):
- Real attribution/planner/reconciliation/execution/save/load path, symbolsh688008 from _inputs panel, leader matureFalse.
- Default initial cash; code/data fixture hashes; old protection.4; last_shock_date2023-04-27.
- Establish an ordinary Target.2 with LEADER_SELECTION, semiconductor / REQUIRED_AI_UNIVERSE_SHA256 attribution at dates[-3].
- Execution panel open10/high10.1/low9.9/close10, volume1m, amount100m over dates[-3:].
- Actual next-open dates[-2] fills5,000, leaving a pending remainder.
- Current allocator retains actual weight, planner/reconcile cancels invalid remainder.
- Save/load; on next decision it still retains actual weight and must not turn stale old restoration rights into a new BUY.
- Cash/shares do not change in allocation; old .4 remains until a genuine fresh admission retires it.

Adjust existing test_partial_ordinary_restore_survives_restart_without_new_entry_qualification:
- After the actual initial fill and protected_weights[symbol]=.6, set last_shock_date=signal.
- Completed marker equals pd.Timestamp(signal).toordinal() (old fixture expected0).

Missing agent test test_ordinary_restore_keeps_continuous_holding_after_fifo_retires_original_lots:
- Actual initial BUY39,900 on July24; shockJuly25 with saved.6.
- Actual restoration BUY79,900 on July28.
- RISK_OFF cap.3, actual FIFO sellJuly29 leaves60,000, all from post-shock restoration lots.
- Holding never became zero; normalJuly30 should allow .6 restoration, not .3003847654.
- The date-only guard went RED; the final reverse-fill helper and five focused positive/negative cases were reported PASS before workspace loss.
- Rebuild against actual fixture dates and current executor APIs; do not invent original test bytes or treat synthetic unit fixtures as economic proof.

## Report and docs: missing original WIP

Owned paths were uquant/report.py, tests/test_daily_report_explanation.py, docs/STRATEGY.md, docs/OPERATIONS.md.
- _core_allocation_report reads recorded pending_entry evidence; explains continuation with label 'pending current quality'.
- PENDING_CORE_BUY_ALREADY_EVALUATED is routing information, not a reason to hide the actual quality rejection.
- Preserve final Sentinel freeze / CAPITAL_LIMIT explanation precedence.
- Map RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING faithfully.
- Explain ordinary pending current-route1 vs fresh5, eligible continuously held restoration even when not mature, fresh admission clearing stale ordinary rights, causal continuous holding proven by actual fills.
- Agent reported four RED cases, then all eight report tests PASS, scoped Ruff and mypy PASS. Raw patch/logs are unavailable after workspace loss.

## Lifecycle / reflection adapter: missing original WIP

tests/_lifecycle_protected_repair_cases.py:
- Two positive restoration fixtures now have real held positions: single20 shares + cash80; pair20 shares each + cash60.
- Entry date=dates[0], last shock=dates[-3].
- Frozen decision retains actual weights; repaired budget restores .6 single or .4+.35 pair.
- No unfilled future sell may fund a buy.
- Reported lifecycle154 PASS then two corrected exact nodes PASS.

tests/architecture/test_portfolio_boundaries.py:
- Current reflection literal update had exactly14 leaf differences:2 obsolete docstrings,4 instance-pickle hash/size pairs,4 mode digests.
- Pickle sizes were each4 bytes below prior1948/1937/1951/1953 values.
- Public signatures, MRO, class pickle, descriptors and import behavior unchanged.
- Exact reflection node, current API and Ruff reported PASS.
- Actual new hash values are missing: regenerate current snapshots after source stabilizes; never rewrite immutable historical fixtures.

## Config/compatibility adapter: status unknown

Agent had begun a bounded159-to157 validation-clause projection for two removed controls. No final diff or pass/fail report was received.
Potential paths:
tests/test_config_contracts.py, tests/test_config_governance.py,
tests/test_config_model_characterization.py, tests/test_modular_config_models.py,
tests/architecture/test_compatibility_contracts.py, tests/architecture/_config_transport.py,
tests/architecture/test_config_boundaries.py, tests/architecture/test_config_public_owners.py.
Preserve historical compatibility_config_validation_contract.json; project only the two legitimately deleted controls and preserve remaining157 clauses, ordering and adversarial checks. No production/config threshold changes were part of this bounded task.

## Economic diagnosis behind pending-quality correction

Full pool July16,2024: sh688766 established streak46, matureTrue, ret20+4.5041%.
July17: matureTrue, close63.252, ma20 66.35275, ma60 60.79125, ret20-5.9827%; all five route predicatesFALSE/streak0.
July18: matureTrue, ret20-8.1559%, all routes0. July19 matureFalse.
_candidate_market_block alone stayed READY. Existing established ret20 floor-.05 was lost when a held partial entry was retained without current route quality.
Unified-c cancelled after8,700 shares. Unified-d continued +11,400 July18 and +8,200 July19 (28,300 total).
July25 drawdown20.0699% versus18.10995% crossed unchanged BaseRisk20% CRISIS; d sold34,500 sz300308 versus17,300, leaving3,000 versus20,200. Do not change that risk threshold.
Lost scratch full-regression.json:459,284 bytes, SHA2569e9c77206cd5919b9792258d96fce9f34676a5c698b2b12853bdeb6cf7b5d05f. Reconstruct if needed from preserved c/d raw streams.

## Stale flat restoration evidence

remove_all_three:
- 2025-02-17 sh688200 NOT_MATURE -> Feb18 BUY4,800, last flat2023-04-28.
- 2025-06-24 sh688200 NOT_MATURE -> Jun25 BUY4,300.
- 2025-06-25 sz300054 NOT_MATURE -> Jun26 BUY10,600.
no_optical:
- 2025-02-17 sh688200 -> BUY4,900.
- 2026-05-14 sh688200 NOT_MATURE -> BUY1,800; sh688300 confirmation3<5 -> BUY4,900.
full d:
- 2026-04-10 sh688766 NOT_MATURE -> Apr13 BUY7,600; no comparable old-c flat restore.

Do not assume every unwanted order is a raw persistent/reversal route:
- Removal20 new ordinary origin orders (19 filled):18 established +1 transition +1 impulse.
- No-optical9:7 established +1 transition +1 impulse.
- Old established cohort median persistent_ret240>=1.0 is not the ordinary per-stock gate;10/18 removal and4/7 no-optical established names were individually below1.0.
- Replacing a cohort median with a per-stock threshold is NOT an equivalent bugfix. No new durability threshold had been added.

## Simplification inventory: missing scratch file

cross-ai-simplification-inventory.json:69,616 bytes, SHA2569a0ed6d7ca3c43783030fa74faa37e7f2f3f6c282c5aedc35d1711941a632112.
Counts were version-bound before the final helper/report changes:
- economic tunables164->162; all config277->275.
- AccountState84->84, schema8.
- named state-key families73->77.
- portfolio/core branch sites675->658; broad production2506->2523.
- Removed7 modules:5 allocation wrappers (45 functions), context, successor (6 functions); added capital.py.
- Ten-way initial founding-grant first-match chain STILL PRESENT and called.
- Old leader cycle/target helpers are disconnected but compatibility-exported; persistent state backwards compatibility remains.
- p2/p4/p7/p8/confirmation controls all live:zero deleted-control exclusions, all30 neighbor cases required.
Refresh counts after stabilization; do not claim a complete removal of first-match design or fewer persistent states.

## Next diagnostic and final work

unified-e was prepared/planned on a clean snapshot named uquant-economic-quality-58d06fe. Root approved copying only final pipeline after focused checks/review passed. No launched process ID, snapshot SHA, completed metric or persisted artifact was reported. No E archive appears in the fresh Library inventory; status UNKNOWN.

Check for a persisted newer artifact first. If absent, restore code/fixtures, run only affected focused checks, freeze a new immutable candidate, then the four principal cases with true raw evidence. Reuse c/d for diagnosis, not final acceptance of changed economics.
Remaining obligations:14 nominal cells,64 prescribed robustness specs including all30 neighbor cases; final Performance,8 Absolute shards with34LOO, Grant/Ownership, engineering, focused review, refreshed simplification inventory and operator cutover.
Final absolute candidate source binding is stale; current registry changes must not overwrite the immutable historical registry authority.
