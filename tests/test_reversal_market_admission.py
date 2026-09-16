"""Current market permission for native synchronized-reversal formation."""
from __future__ import annotations

import numpy as np
import pandas as pd
from test_lifecycle_and_risk import _leader, _trend_frame

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio_core import strategic_dominant_symbol
from uquant.types import AccountState, Opportunity, Risk, RiskAssessment


def _native_reversal(*, decisive: bool, opportunity: Opportunity):
    # Reuse the native decisive-opening price construction; changing relative
    # evidence creates a real nondecisive quorum, never an injected certificate.
    dates = pd.bdate_range("2023-01-02", periods=251)
    panel = {}
    for symbol, base in (("sz300308", .69), ("sz300394", .725), ("sz300502", .73)):
        close = np.concatenate((np.linspace(1., .68, len(dates) - 5), np.linspace(.69, .74, 5)))
        close[-61:-5] = np.linspace(base, .68, 56)
        panel[symbol] = _trend_frame(dates, close=close, ma20=.70, ma60=.72, ret20=.08, ret60=.07)
        panel[symbol]["atr"] = .02
    leaders = {s: _leader(s, score, industry="optical", mature=False)
               for s, score in (("sz300308", .70), ("sz300394", .60 if decisive else .69), ("sz300502", .20))}
    if decisive:
        leaders["sz300394"].components["trend_persistence"] = 1 / 3
    risk = RiskAssessment(Risk.NORMAL, 1., 1, {
        "tech_ret120": -.10, "risk_anchor_symbols": [],
        "risk_anchor_group_count": 0, "configured_user_universe_size": 3,
    }, (), "NONE")
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity, account.code_hash = "account:reversal-native", "code:reversal-native"
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    targets = ()
    for date in dates[-2:]:
        targets = policy.allocate(
            date=date, opportunity=opportunity, risk=risk, user_panel=panel,
            leaders=leaders, account=account,
            prices={s: float(panel[s].loc[date, "close"]) for s in panel},
        )
    observed = account.strategic_qualification
    assert observed.qualification_ready
    assert observed.qualification_route == "reversal_industry"
    assert observed.qualification_quorum == "FULL_COHORT"
    return policy, account, dates, panel, leaders, risk, targets


def test_nondecisive_synchronized_full_does_not_create_grant_in_choppy():
    _, account, _, _, _, _, targets = _native_reversal(
        decisive=False, opportunity=Opportunity.CHOPPY,
    )
    assert strategic_dominant_symbol(account) is None
    assert account.strategic_grant is None
    assert not account.strategic_epochs
    assert not any(target.weight > 0 for target in targets)


def test_nondecisive_synchronized_full_may_deploy_in_open_trend():
    _, account, _, _, _, _, targets = _native_reversal(
        decisive=False, opportunity=Opportunity.TREND,
    )
    assert account.strategic_grant is not None
    assert strategic_dominant_symbol(account) is None
    assert any(target.weight > 0 and target.epoch_id for target in targets)


def test_decisive_native_two_member_opening_keeps_choppy_permission():
    _, account, _, _, _, _, targets = _native_reversal(
        decisive=True, opportunity=Opportunity.CHOPPY,
    )
    assert account.strategic_grant is not None
    assert strategic_dominant_symbol(account) == "sz300308"
    assert account.strategic_cohort_targets == {"sz300308": DEFAULT_CONFIG.strategic_dominant_max_weight}
    assert any(target.symbol == "sz300308" and target.weight > 0 for target in targets)


def test_native_partial_grant_keeps_identity_after_restart_in_choppy(tmp_path):
    from hashlib import sha256

    from uquant.account import load_account, save_account
    from uquant.application.target_attribution import attach_target_attribution
    from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
    from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

    policy, account, dates, panel, leaders, risk, targets = _native_reversal(
        decisive=False, opportunity=Opportunity.TREND,
    )
    grant = account.strategic_grant
    assert grant is not None
    original_grant = (grant.grant_id, grant.epoch_id, grant.qualification_signature)
    signal = str(dates[-1].date())
    price = float(panel[grant.candidate_symbol].iloc[-1]['close'])
    account.data_hash = sha256(''.join(
        symbol + frame.to_csv() for symbol, frame in sorted(panel.items())
    ).encode()).hexdigest()
    attributed = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=signal, targets=targets,
    )
    orders = plan_orders(signal_date=signal, targets=attributed, account=account,
                        prices={s: price for s in panel}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=signal,
    ))
    next_dates = pd.bdate_range(dates[-1], periods=3)[1:]
    execution = {s: pd.DataFrame({
        "open": price, "high": price * 1.01, "low": price * .99, "close": price,
        "volume": 1_000_000., "amount": price * 1_000_000.,
    }, index=dates[-1:].append(next_dates)) for s in panel}
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=next_dates[0], account=account, panel=execution,
    )
    assert fills and all(f.side == 'BUY' and f.shares > 0 for f in fills)
    pending = next(o for o in account.pending_orders if o.symbol == grant.candidate_symbol)
    original_order = (pending.order_id, pending.event_id, pending.grant_id, pending.epoch_id)
    assert pending.remaining_shares > 0
    save_account(account, tmp_path / 'partial.json')
    restored = load_account(tmp_path / 'partial.json')
    extended = {s: pd.concat((frame, pd.DataFrame(
        [frame.iloc[-1].to_dict()], index=next_dates[:1],
    ))) for s, frame in panel.items()}
    resumed = policy.allocate(
        date=next_dates[0], opportunity=Opportunity.CHOPPY, risk=risk,
        user_panel=extended, leaders=leaders, account=restored,
        prices={s: price for s in panel},
    )
    assert restored.strategic_grant is not None
    assert (restored.strategic_grant.grant_id, restored.strategic_grant.epoch_id,
            restored.strategic_grant.qualification_signature) == original_grant
    assert not restored.strategic_qualification.deployment_blocked
    assert any(t.symbol == grant.candidate_symbol and t.weight > 0 for t in resumed)
    retained = next(o for o in restored.pending_orders if o.symbol == grant.candidate_symbol)
    assert (retained.order_id, retained.event_id, retained.grant_id, retained.epoch_id) == original_order
    next_signal = str(next_dates[0].date())
    resumed_attributed = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=next_signal,
        targets=resumed, retained_orders=restored.pending_orders,
    )
    planned = plan_orders(
        signal_date=next_signal, targets=resumed_attributed, account=restored,
        prices={s: price for s in panel}, cfg=DEFAULT_CONFIG,
    )
    restored.pending_orders = list(reconcile_account_orders(
        account=restored, previous=restored.pending_orders, current=planned,
        submitted_date=next_signal,
    ))
    retained = next(o for o in restored.pending_orders if o.symbol == grant.candidate_symbol)
    assert (retained.order_id, retained.event_id, retained.grant_id, retained.epoch_id) == original_order
    assert retained.remaining_shares == pending.remaining_shares
    next_fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=next_dates[1], account=restored, panel=execution,
    )
    assert any(f.order_id == retained.order_id and f.side == 'BUY' and f.shares > 0 for f in next_fills)
