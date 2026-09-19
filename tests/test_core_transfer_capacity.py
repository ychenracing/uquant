"""A full book may rotate only if the settled sale would free a position."""

from copy import deepcopy
from dataclasses import replace

import pytest
from test_core_transfer_feasibility import CHALLENGER, INCUMBENT, WEAK, _scenario

from uquant.types import Position


@pytest.mark.parametrize("weak_shares,can_release_slot", [(20_000, True), (40_000, False)])
def test_full_book_transfer_requires_complete_incumbent_exit(monkeypatch, weak_shares, can_release_slot):
    policy, arguments = _scenario(monkeypatch, "ready")
    account = arguments["account"]
    panel, leaders = arguments["user_panel"], arguments["leaders"]
    account.strategic_cohort_targets.clear()
    account.strategic_restore_weights.clear()
    account.strategic_exit_bands.clear()
    account.protected_weights.clear()
    account.positions[WEAK].shares = weak_shares
    stable_shares = (96_000 - weak_shares) // 5
    for symbol in (INCUMBENT, "sh688008", "sh688019", "sh688041", "sh688082"):
        panel[symbol] = panel[INCUMBENT].copy()
        leaders[symbol] = replace(leaders[INCUMBENT], symbol=symbol)
        account.positions[symbol] = Position(
            symbol, stable_shares, 10.0, account.positions[WEAK].entry_date, 10.0,
        )
        arguments["prices"][symbol] = 10.0
    before = deepcopy(account)
    assert len(account.positions) == policy.cfg.max_positions

    targets = policy.allocate(**arguments)

    observed = {target.symbol: target.weight for target in targets}
    assert observed.get(CHALLENGER, 0.0) == 0.0
    assert account.positions == before.positions and account.cash == before.cash
    if can_release_slot:
        assert observed[WEAK] == 0.0
        assert len(account.rotation_dates) == 1
        assert next(t for t in targets if t.symbol == WEAK).mechanism == "LEADER_ROTATION"
    else:
        assert observed[WEAK] == pytest.approx(0.4)
        assert account.rotation_dates == []
