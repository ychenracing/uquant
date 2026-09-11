# Original tactical cooldown restoration

The authorized research lifecycle sets the original tactical cooldown after
expiry, but the rewritten combined allocator has no reader that decrements it.
The original9bb5842 allocator decremented it and allowed an overheat-only pause
to end on the existing positive reversal/medium-term structure predicates.

Restore this state transition in the current recovery owner, once per observed
trading session. Repeated same-day allocation cannot consume extra days; missing
observations cannot fabricate elapsed confirmations. Keep all existing entry,
freeze, capital and settled-book checks. No clock expiry itself creates a BUY.
Retain the original overheat-only reset, not the ordinary exit cooldown.

This is a separately committed implementation repair atop the retention arm,
not a new signal or parameter trial. Compare native fixed-window fills exactly
with its immutable parent before running any final-candidate matrix. Preserve
both source identities and failures. No production adoption or main merge.
