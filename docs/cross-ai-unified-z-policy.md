# Unified Z: consume each continuous ATR breach once

Precommitted scope, 2026-09-08. This is an experimental holding-policy change,
not an acceptance result. All cross-AI, Grant, Ownership and other frozen gates
remain unchanged. Y is the comparison source (`d428dfcc0c5af9a7d96eb2d321e716a53884e8c557746204313de2cb2368c789`).

## Evidence and decision

The native Y remove-all-three H2 observations reproduce every exit-band change.
For one held position, all five ATR thresholds remained breached for sixteen
consecutive sessions, generating three actual sales in that interval. Across
the two holdings, 120 of 143 and 98 of 133 threshold decrements respectively
consumed already-true signals. These are mechanical counts, not a counterfactual
profit estimate. A native pending-order test also reproduces same-session
restart compounding: the target falls from .116 to .112 without new market data.

Z consumes each threshold's false-to-true transition once. It reuses
`strategic_active_bands` to retain the last valid signal; it adds no setting,
threshold, account field, special security/date rule or market-entry condition.
Each finite false observation rearms that threshold. This is an ATR threshold
edge, **not** proof of structural recovery: volatility can move the threshold
while the underlying trend remains damaged. Missing ATR, fast moving average
or fast return cannot rearm it. The existing accumulated target is still
executed, including after cancellation or restart.

The original step sizes, profit-arm threshold, disaster stop, portfolio risk
caps, minimum trade sizes, FIFO receipts and prohibition on recapturing exit
capital remain active. The impulse all-threshold liquidation remains active.
No restored capital or new entry qualification is granted by a false signal.

## Risks and validation boundary

Repeated daily trimming formerly reduced more capital during sustained damage.
Z may retain too much risk; five new thresholds can reduce only the configured
single step. Lower order count alone is insufficient. Validate exact native
execution/restart cases and affected lifecycle modules, then only the existing
H2 remove-all-three sentinel and champion. If H2 improves sufficiently, run the
previously failed H1 no-optical sentinel before wider acceptance. Do not run
the full matrix for this experiment or borrow Y's champion pass.

Historical account booleans mean "ever triggered", whereas Z records the last
valid signal. A retained old true value is conservatively treated as already
consumed until a valid false is observed. Deserialization is not evidence of
economic equivalence or authorization to adopt the account under a new source
identity. Operator cutover must acknowledge this boundary and preserve the old
account/code evidence; do not manufacture missing intervening observations.

The current API snapshot repair is separate and changes no economic source.

## Observed result (after source freeze)

Source `42c9e896cb98e48303fe8c2b9c06b9cff8a6df8ad82ca73dfb71e71ab2a02b95`,
remote commit `4e6f20d1c18f549567c85eb2411313c7732892ee`:

| Native case | Sessions | Wealth | Maximum drawdown | Orders | Frozen result |
| --- | ---: | ---: | ---: | ---: | --- |
| Champion | 869 | 24.509661802900865 | 0.27146973146234554 | 12 | PASS |
| Remove all three, H2 2024 | 125 | 1.267772159717437 | 0.1460155678818953 | 4 | FAIL wealth and drawdown retention |

Both complete native accounts, observations, source/config/data/runtime seals,
and original `read_case`/`check_metrics` readbacks were verified. H2 improvement
over Y does not satisfy the contract; Z is not accepted. The original H1 Y
path never had a strategic grant, cohort target or exit band, so this change
does not address that failure; no redundant H1 run was launched.

A startup attempt used July 2 rather than the frozen July 1 H2 start. Its
identity check rejected it, it was interrupted with exit 130, and its prefix
is explicitly marked invalid. The accepted diagnostic above uses all 125
frozen sessions, July 1 through December 31. No prefix result was promoted.

Native archive: `uquant-unified-z-native-evidence-20260908.zip`, 18,703,598 bytes,
SHA256 `91b42c33674f66adcc7247127c2fefb482d75114f16b2d26957f061ec0fbe496`,
persistent file `libfile_5100ae904c9481919216753a2c5cc505`.
Archive member integrity and original raw accounting were verified before
saving. Full Grant, Ownership, other nominal cells and robustness are not
claimed by these two diagnostics.
