# Settled tactical lifecycle, native execution parity

The real archived Apr7 tactical BUY15300 was fully sold by the native executor
on Apr10 under an imposed CRISIS invariant fixture. Cash/fills/positions reconcile,
and no anchor/protection/pending owner remains, but tactical_active=1 and its
anchor symbol remain. That stale label can permanently prevent another entry.
This fixture is not an economic performance replay.

Broker sync already retires an unowned tactical lifecycle and starts the original
cooldown. Add the corresponding settlement check to the current allocation owner,
after actual execution and before tactical holding/admission. Require a bound
tactical BUY and actual zero net shares, no live positions, no pending/unsettled
orders and no retained positive restoration/anchor rights. Never retire a pending
BUY, partial holding or protected owner. Set original cooldown; no forced reentry.

This is a separately source-bound readiness repair of the authorized research
mechanism, not an economic parameter experiment. Preserve parent83732c4 native
result and prove boundary behavior before any final candidate matrix.
