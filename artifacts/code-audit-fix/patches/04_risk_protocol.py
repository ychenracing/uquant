"""Use stable risk causes for state transitions, preserving reason precedence."""
replace_function('uquant/risk/confirmed_break.py', '_confirmed_break_reason', '''def _confirmed_break_code(ctx: ConfirmedBreakContext) -> str:
    if ctx.held_cohort_break_confirmed:
        return "DYNAMIC_COHORT_BREAK"
    if ctx.terminal_market_backed_restoration_relapse:
        return "INCOMPLETE_RESTORATION_BREAK"
    if ctx.market_backed_restoration_relapse:
        return "MARKET_BACKED_RESTORATION_RELAPSE"
    if ctx.capital_drawdown_relapse:
        return "CAPITAL_RESTORATION_RELAPSE"
    if ctx.incomplete_universe_tail_break:
        return "RESERVE_BACKED_TAIL_GUARD" if ctx.credible_reserve else "UNBACKED_CAPITAL_EXIT"
    return "STRATEGIC_CAPITAL_GUARD" if ctx.strategic_active else "CONCENTRATED_LEADER_BREAK"


def _confirmed_break_reason(ctx: ConfirmedBreakContext) -> str:
    return {
        "DYNAMIC_COHORT_BREAK": "confirmed dynamic cohort structural break",
        "INCOMPLETE_RESTORATION_BREAK": "market-backed portfolio break in incomplete restoration",
        "MARKET_BACKED_RESTORATION_RELAPSE": "market-backed drawdown relapse in restored holdings",
        "CAPITAL_RESTORATION_RELAPSE": "capital drawdown relapse in restored holdings",
        "RESERVE_BACKED_TAIL_GUARD": "reserve-backed incomplete-universe tail guard",
        "UNBACKED_CAPITAL_EXIT": "unbacked incomplete-universe capital exit",
        "STRATEGIC_CAPITAL_GUARD": "confirmed strategic cohort capital guard",
        "CONCENTRATED_LEADER_BREAK": "confirmed concentrated leader break",
    }[_confirmed_break_code(ctx)]''')
replace('uquant/risk/confirmed_break.py', '        "strategic_current_gross": ctx.strategic_current_gross,\n', '''        "strategic_current_gross": ctx.strategic_current_gross,
        "break_reason_code": _confirmed_break_code(ctx),
        "recovery_owner_reset_required": _confirmed_break_code(ctx) in {
            "INCOMPLETE_RESTORATION_BREAK", "CAPITAL_RESTORATION_RELAPSE",
        },
''')
replace('uquant/risk/confirmed_break.py', '            "reasons": [reason],\n', '            "reasons": [reason],\n            "break_reason_code": _confirmed_break_code(ctx),\n')
replace('uquant/risk/transitions.py', '        evidence.pop(key)\n    return RiskAssessment(\n', '        evidence.pop(key)\n    evidence["recovery_owner_reset_required"] = True\n    return RiskAssessment(\n')
replace('uquant/portfolio/pipeline.py', '''    if risk.state is Risk.CRISIS and any(
        marker in risk.reasons for marker in (
            "capital drawdown relapse in restored holdings",
            "market-backed portfolio break in incomplete restoration",
            "capital guard cooldown after failed restoration",
        )
    ):''', '''    if risk.state is Risk.CRISIS and risk.evidence.get("recovery_owner_reset_required") is True:''')
replace_function('uquant/risk/recovery_state.py', '_last_shock_was_market_backed', '''def _last_shock_was_market_backed(ctx: _RecoveryStateContext) -> bool:
    if not ctx.account.last_shock_date:
        return False
    for event in ctx.account.risk_events:
        if event.get("date") != ctx.account.last_shock_date or event.get("to") != Risk.CRISIS.value:
            continue
        if "break_reason_code" in event:
            if event["break_reason_code"] in {
                "MARKET_BACKED_RESTORATION_RELAPSE", "INCOMPLETE_RESTORATION_BREAK",
            }:
                return True
            continue
        # Old persisted events have no structured cause. Decode that wire format
        # here only; current decisions never use display prose as authority.
        if any(reason in {
            "market-backed drawdown relapse in restored holdings",
            "market-backed portfolio break in incomplete restoration",
        } for reason in event.get("reasons", ()) if isinstance(reason, str)):
            return True
    return False''')
