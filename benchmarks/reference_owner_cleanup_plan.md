# Reference input ownership without economic change

The decision orchestrator exceeds its reviewed dependency budget because dated
industry lookup was added there, although the reference-context owner already
depends on the industry module. Do not increase or waive the frozen fanout cap.

Move the default lookup to build_reference_context: omitted industries means
the exact same decision_industries mapping for that normalized decision date.
Explicit mappings, including empty maps, retain their existing semantics. Remove
the now-redundant caller import and keyword argument. No new wrapper or module.

This preserves dated taxonomy, research inputs, risk/score calculations and all
strategy rules. Verify default versus the former explicit call with current
frozen historical data, explicit custom mappings, industry tests and the exact
fanout check. Record the new economic source identity separately; active research
producers are immutable and unaffected. Main remains unchanged; no new orders.
