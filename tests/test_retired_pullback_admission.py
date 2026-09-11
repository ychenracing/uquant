"""Retirement cannot mint new orders or discard real pre-retirement liabilities."""
from test_ordinary_pullback_execution import _submitted

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity


def test_current_pullback_proof_cannot_create_a_new_allocation():
    _, original, date, _, panel, leaders, risk = _submitted()
    account = AccountState.empty(original.initial_cash)
    account.account_identity = original.account_identity
    account.code_hash, account.data_hash = original.code_hash, original.data_hash
    assert risk.evidence['ordinary_pullback_permission']['proofs']
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date, opportunity=Opportunity.CHOPPY, risk=risk, user_panel=panel,
        leaders=leaders, account=account,
        prices={s: float(f.loc[date, 'close']) for s, f in panel.items()})
    assert not any(t.weight > 0 for t in targets)
    assert not account.pending_orders and not account.fills
