# Completed confirmation diagnostic — not accepted

Producer1e6951381df315146600139adbea175654484886; economic fingerprint653c9f15b73e0cca7adcd0cf723cc08aeb50bdca8904609e8e698df7faa4bbc9. Both native869-session runs finished with statusCOMPLETE, no replay errors and exact matched runtime/scenario/runner. Summary and attribution reconcile full net wealth, drawdown, fees, slippage and terminal symbol P&L.

|Scenario|Net wealth|Maximum drawdown|Orders|Versus readiness control|
|---|---:|---:|---:|---|
|remove308|2.391637764907622|22.8889194976%|31|Wealth lower16.12%; orders68→31|
|remove502|14.413293907703471|27.6551680943%|54|Unchanged full economic path|

Both are NOT jointly accepted: remove308 benchmark requirement≈7.050777x, deficit≈4.65914x; remove502 exceeds44-order condition by10. Do not lower the table after seeing results. No main/full, final34LOO, stress/start/tail matrices for this rejected candidate, and no merge.

The causal mechanism operated: in remove308 sz300394 became a funded60% recovery target2025-05-12 and actual buy2025-05-13. However, portfolio crisis sold it2025-08-11; restoration did not execute until2026-01-13, then another crisis sale2026-03-04. No other new fills after those dates in the remaining window. Therefore earlier confirmation alone does not restore sustained profitable participation. This evidence points to the economic interaction of real portfolio risk reduction and restoration, not another confirmation-count/depth threshold adjustment. It does not establish that holding through those episodes would meet the risk bound. A useful next investigation must quantify that tradeoff under the same prices, costs and chronology before selecting a new design.

Engineering:45 initial synthetic/config checks passed. Follow-on recovery suite75pass/1fail (the5 synthetic tests overlap); retained failure in test_real_partial_recovery_keeps_fills_and_cancels_lost_current_proof expects canceling sz300394 on lack of a fresh high, while the changed confirmation rule retains its still-intact breakout. No test expectations were rewritten to manufacture acceptance. Since economic candidate is rejected, this source is not promoted and no final engineeringPASS is claimed. Scoped Ruff passed.

Both full raw files saved through the authorized program-managed file channel, independently materialized and byte-length/SHA256 verified. raw-preservation.json contains exact IDs and source paths. This is a complete raw backup, not a summary pretending to be an original. Source restoration patch remains relative to live PRcffea663; production PR/main are untouched.
