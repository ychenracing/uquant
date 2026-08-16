# Phase 2 subsystem ablation conclusions

## Decision

The authenticated evidence supports one deletion, ten retained subsystems, and two
inconclusive experiments. `transition_overlay` was deleted. No other subsystem was
removed or compensated with a new rule.

| Subsystem | Decision | Authenticated evidence |
|---|---|---|
| `sector_guard` | KEEP | Removing it worsens wealth, drawdown, acute return, orders, turnover, and concentration tails in both evidence epochs. |
| `chronic_overlay` | KEEP | Phase 1 is neutral, but its unique observed Generalization protection is `continuous_ai_era/subindustry__pcb` drawdown: removal adds `0.00849898411999983`. |
| `transition_overlay` | DELETE | The carrier genuinely diverges at the risk trace, yet all nine metric deltas are exactly zero across 45 Phase 1 and 191 common-valid Generalization cells, with no status transition. |
| `capital_budget_ladder` | KEEP | Removing it harms wealth, drawdown, orders, turnover, and concentration. Its real `VALID->REPLAY_ERROR` remains an execution failure in the result. |
| `challenger_scout` | INCONCLUSIVE | Both epochs authenticate `invalid_experiment/no_behavior_divergence`; this is not a successful ablation and cannot prove safe deletion. |
| `conviction_weighting` | INCONCLUSIVE | Both epochs authenticate `invalid_experiment/no_behavior_divergence`; this is not a successful ablation and cannot prove safe deletion. |
| `recovery_conviction_weighting` | KEEP | Removing it adds Phase 1 trading cost and loses `6.52588915569515` wealth in Generalization (`bull_crash_2025_2026/remove-one__sz300502`). |
| `tactical_rebound_probe` | KEEP | Removing it loses `8.92638587253313` wealth in the crash tail and worsens acute return, drawdown, and order load. |
| `strategic_trailing` | KEEP | Removing it worsens Phase 1 wealth/drawdown and Generalization turnover, orders, concentration, and tail wealth. |
| `restoration_special_handling` | KEEP | Removing it loses `5.573139099976107` wealth in the continuous optical tail and worsens drawdown/concentration. Its separate frozen-error-to-valid transition does not erase those harms. |
| `add_tranche` | KEEP | Removing it worsens Phase 1 concentration and Generalization wealth, drawdown, orders, turnover, and concentration. |
| `replacement_rotation` | KEEP | Phase 1 is neutral, but removal loses `3.017175631685424` Generalization wealth at `continuous_ai_era/remove-one__sz300394` and worsens order/turnover tails. |
| `dynamic_risk_anchors` | KEEP | Removing it worsens Phase 1 wealth/drawdown and Generalization wealth, drawdown, orders, turnover, and concentration. Its separate frozen-error-to-valid transition does not erase those harms. |

## Method and required dimensions

Every classification was recomputed from authenticated comparison cells, not from a
handwritten summary. Higher is better for `final_wealth` and `acute_return`; lower is
better for `max_drawdown`, `account_orders`, `gross_turnover`, `annual_turnover`,
`top1_concentration`, `top3_concentration`, and `pnl_hhi`. For every subsystem and
contract, `results.json` records comparable/not-applicable counts, nonzero counts, and
the worst ablation harm for all nine dimensions. Status transitions, first causal
divergence, invalid-artifact reason, and `execution_pass` are preserved separately.

The unique-tail rule is deliberately conservative: a mechanism is retained if its
removal harms a tail/generalization cell even when medians or Phase 1 are neutral.
That rule is decisive for `chronic_overlay` and `replacement_rotation`. Conversely,
zero economic deltas alone do not authorize deletion when the experiment contract is
invalid, which is why the two no-divergence carriers remain inconclusive.

## Deletion acceptance and complete rerun

`transition_overlay` was removed alone in commit
`e5e0fa903c9a9b26701063ae01f352af3e246a7d`. Focused tests passed (`196 passed`).
The Phase 1 full gate passed all 45 cells (artifact SHA-256
`4267e620e99ba7a2dbadc99c4acba829530e0763f8f78fb413dc7fe26b4c59b7`). The related
continuous-AI-era matrix preserved all 39 statuses and all 31 comparable valid-cell
economics exactly, including the one known replay error (artifact SHA-256
`b3efd5596d9d3a89e3f728aa22fb8f7f5a9f388f681654895e6274f6dd2ef6a4`). No revert
or compensating rule was required.

The fresh post-deletion registry ran baseline plus all 12 remaining carriers exactly
once. Strict readback authenticates 12/12 coverage: 10 valid divergent experiments,
two authenticated invalid no-divergence experiments, no missing carrier, and
`complete=false` by contract. The capital-budget removal still yields a real
`VALID->REPLAY_ERROR` at `continuous_ai_era/random__05__0001`; it is retained verbatim.
The post-deletion evidence therefore preserves every KEEP/INCONCLUSIVE conclusion.

## Provenance

- Historical Task 7 strict readback SHA-256: `efc4121041dbc9804670a360f8309ec81f22f709e9318aa77824073064c93b04`.
- Post-deletion strict readback SHA-256: `ad3a273a0e24be474021d6c034688a9e4cec6807bd8b1dc1bf8ab375e36c7b00`.
- Post-deletion evidence commit: `aa4b313e000002adae27b32f91b5a84425c78987`.
- Post-deletion trust-anchor commit: `02596b25efef900757f0d3f53599b5dae1c9d4d5`.
- `results.json` SHA-256: `04ad26833bf4780c3a9c64bfd33edd427d39c83216a5d4adc0dfb76bd6ec7ee4`.

The external authenticated archive is
`/tmp/uquant-phase2-task8-aa4b313-checkpoints`; replay remains reproducible from the
tracked registry, runner, and compiled manifest trust root, but the `/tmp` copy itself
is not durable storage.
