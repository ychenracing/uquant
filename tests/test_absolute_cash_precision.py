"""Native cash checkpoints from the saved 869-session sh600487 removal replay."""

from uquant.validation.absolute_generalization import _metrics_reconciliation as m


def test_buy_reconstruction_preserves_native_cash_checkpoint():
    lots = {}
    cash = 2_000_000.0
    for date, gross, commission, transfer in (
        ("2023-01-05", 610786.176, 152.696544, 6.1078617600000005),
        ("2023-01-06", 626600.975, 156.65024375, 6.26600975),
    ):
        fill = m._FillEconomics(
            session=date, symbol="owner", side="BUY", shares=100,
            gross=gross, fees=commission + transfer, commission=commission,
            stamp_duty=0.0, transfer_fee=transfer, slippage_cost=0.0,
        )
        cash += m._apply_buy(fill=fill, lots=lots)
    assert cash == 762291.1283407401


def test_sell_reconstruction_preserves_native_cash_checkpoint():
    fill = m._FillEconomics(
        session="2026-07-03", symbol="owner", side="SELL", shares=100,
        gross=13086740.159999998, fees=9945.9225216, commission=3271.68504,
        stamp_duty=6543.37008, transfer_fee=130.8674016, slippage_cost=0.0,
    )
    lot = {"owner": {"tranche": m._Lot(shares=100, unit_cost=1.0)}}
    sold = {
        "tranche_id": "tranche", "shares": 100, "cost": 1.0, "unit_cost": 1.0,
        "avg_cost": 1.0, "cost_basis": 100.0, "commission": fill.commission,
        "stamp_duty": fill.stamp_duty, "transfer_fee": fill.transfer_fee,
        "slippage_cost": 0.0,
    }
    proceeds, _ = m._apply_sell(raw_fill={"sold_tranches": [sold]}, fill=fill, lots=lot)
    assert 58585465.96606582 + proceeds == 71662260.20354421
