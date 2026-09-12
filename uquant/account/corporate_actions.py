"""Apply verified distributions to the existing durable account, in session order."""
from __future__ import annotations

import calendar
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date as Date
from typing import Any

from ..models.account import AccountState
from ..models.corporate_action import (
    CorporateAction,
    CorporateActionState,
    DividendTaxDebit,
    DividendTaxLot,
    corporate_action_share_award,
)
from ..models.trading import Position, Tranche


def _validate_distribution_terms(action: CorporateAction) -> None:
    if action.bonus_tax_per_share:
        raise ValueError("taxable bonus shares require source-bound bonus tax allocations")
    if action.tax_category != "cn_individual_dividend":
        raise ValueError("unsupported corporate action account tax category")
    if action.payment_phase not in {"open", "close"}:
        raise ValueError("cash distribution requires source-bound payment phase")
    if action.cash_per_share and (not action.payment_date or action.payment_date < action.ex_date):
        raise ValueError("cash distribution requires payment date on/after ex date")
    if action.share_ratio and action.share_tax_acquisition_rule not in {"original_acquisition", "explicit_date"}:
        raise ValueError("share distribution lacks source-verified statutory acquisition rule")
    if action.share_ratio and action.share_tax_acquisition_rule == "explicit_date" and not action.share_tax_acquired_date:
        raise ValueError("share distribution lacks source-verified statutory acquisition date")
    if action.share_ratio and (not action.share_available_date or action.share_available_date < action.ex_date):
        raise ValueError("share distribution requires availability date on/after ex date")


def validate_action(action: CorporateAction) -> None:
    dates = (action.disclosed_date, action.record_date, action.ex_date,
             action.payment_date, action.share_available_date, action.share_tax_acquired_date)
    try:
        for date_value in dates:
            if date_value and Date.fromisoformat(date_value).isoformat() != date_value:
                raise ValueError("noncanonical corporate action date")
    except (TypeError, ValueError) as exc:
        raise ValueError("corporate action requires ISO source dates") from exc
    if not (action.disclosed_date and action.disclosed_date <= action.record_date < action.ex_date):
        raise ValueError("corporate action disclosure/record/ex chronology differs")
    if not action.action_id or not re.fullmatch(r"(?:sh|sz)\d{6}", action.symbol):
        raise ValueError("corporate action identity differs")
    if not action.source_url.startswith("https://") or not re.fullmatch(r"[a-f0-9]{64}", action.source_sha256):
        raise ValueError("corporate action lacks source URL/SHA-256")
    for value in (action.cash_per_share, action.cash_adjustment_per_share,
                  action.share_ratio, action.reference_share_ratio, action.bonus_tax_per_share):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("corporate action requires explicit nonnegative cash/share terms")
    _validate_distribution_terms(action)


def _fifo_events(account: AccountState, symbol: str, through: str) -> list[tuple[str, int, int, str, int, str]]:
    events: list[tuple[str, int, int, str, int, str]] = []
    for index, fill in enumerate(account.fills):
        if fill.symbol == symbol and fill.fill_date <= through:
            events.append((fill.fill_date, 1, index, fill.side, fill.shares, f"fill:{index}"))
    for index, state in enumerate(account.corporate_actions):
        if state.action.symbol == symbol and state.distributed_date and state.distributed_date <= through:
            for offset, lot in enumerate(state.tax_lots):
                value = lot.shares * state.action.share_ratio
                if not math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-8):
                    raise ValueError("fractional statutory share award requires broker allocation")
                quantity = round(value)
                if quantity:
                    events.append((state.distributed_date, 0, index * 100000 + offset,
                                   "BUY", quantity, f"action:{state.action.action_id}:{offset}"))
    return events


def corporate_action_fifo_inventory(account: AccountState, symbol: str, through: str = "9999-12-31", sales: list[tuple[str, str, int]] | None = None) -> list[DividendTaxLot]:
    """Reconstruct statutory FIFO independently of strategy tranche reduction order."""
    events = _fifo_events(account, symbol, through)
    distribution_dates = {f"action:{state.action.action_id}:{index}": (
                              lot.acquired_date if state.action.share_tax_acquisition_rule == "original_acquisition"
                              else state.action.share_tax_acquired_date)
                          for state in account.corporate_actions for index, lot in enumerate(state.tax_lots)}
    inventory: list[DividendTaxLot] = []
    for day, _, _, side, quantity, lot_id in sorted(events):
        if side == "BUY":
            inventory.append(DividendTaxLot(lot_id, distribution_dates.get(lot_id, day), quantity, quantity))
            inventory.sort(key=lambda lot: (lot.acquired_date, lot.lot_id))
        else:
            remaining = quantity
            for lot in inventory:
                consumed = min(remaining, lot.remaining_shares)
                lot.remaining_shares -= consumed
                remaining -= consumed
                if consumed and sales is not None:
                    sales.append((lot.lot_id, day, consumed))
            if remaining:
                raise ValueError(f"{symbol}: tax FIFO lacks originating acquisition evidence")
    return [lot for lot in inventory if lot.remaining_shares]


def _anniversary(value: Date, *, months: int) -> Date:
    number = value.year * 12 + value.month - 1 + months
    year, month = divmod(number, 12)
    month += 1
    return Date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def dividend_tax_rate(acquired_date: str, sold_date: str) -> float:
    acquired, sold = Date.fromisoformat(acquired_date), Date.fromisoformat(sold_date)
    if sold < acquired:
        raise ValueError("dividend tax sale precedes acquisition")
    if sold <= _anniversary(acquired, months=1):
        return 0.2
    return 0.1 if sold <= _anniversary(acquired, months=12) else 0.0


def settle_dividend_tax(account: AccountState, *, symbol: str, shares: int, date: str) -> float:
    """Recognize sale-triggered liability; broker cash debits need dated evidence."""
    states = [state for state in account.corporate_actions
              if state.action.symbol == symbol and state.recorded_date and state.action.ex_date <= date]
    if not states:
        return 0.0
    remaining = shares
    allocations: dict[str, int] = {}
    for lot in corporate_action_fifo_inventory(account, symbol):
        consumed = min(remaining, lot.remaining_shares)
        allocations[lot.lot_id] = consumed
        remaining -= consumed
    if remaining:
        raise ValueError(f"{symbol}: sale exceeds statutory FIFO inventory")
    total = 0.0
    for state in states:
        taxable = state.action.cash_per_share + state.action.bonus_tax_per_share
        tax = 0.0
        for lot in state.tax_lots:
            consumed = min(lot.remaining_shares, allocations.get(lot.lot_id, 0))
            tax += consumed * taxable * dividend_tax_rate(lot.acquired_date, date)
            lot.remaining_shares -= consumed
        state.tax_assessed += tax
        total += tax
    return total


def corporate_action_receivable(account: AccountState, marks: Mapping[str, float] | None = None) -> float:
    """Cash plus marked unsupplied share rights; receivables are never spendable cash."""
    result = 0.0
    for state in account.corporate_actions:
        if not state.ex_processed_date:
            continue
        action = state.action
        result -= state.tax_assessed - state.tax_cash
        if not state.paid_date:
            result += sum(lot.shares for lot in state.entitled_lots) * action.cash_per_share
        if action.share_ratio and not state.distributed_date:
            if marks is None or action.symbol not in marks:
                raise ValueError(f"{action.symbol}: outstanding share rights require current raw mark")
            result += sum(corporate_action_share_award(lot, action) for lot in state.entitled_lots) * marks[action.symbol]
    return result


def _validate_tax_debit(debit: DividendTaxDebit) -> None:
    """Validate every supplied debit, including events after the current session."""
    if any(not isinstance(value, str) or not value.strip()
           for value in (debit.debit_id, debit.action_id, debit.date, debit.phase,
                         debit.source_url, debit.source_sha256)):
        raise ValueError("dividend tax debit requires complete source-bound fields")
    try:
        if Date.fromisoformat(debit.date).isoformat() != debit.date:
            raise ValueError("noncanonical debit date")
    except (TypeError, ValueError) as exc:
        raise ValueError("dividend tax debit date must be YYYY-MM-DD") from exc
    if debit.phase not in {"open", "close"}:
        raise ValueError("dividend tax debit requires source-bound session phase")
    if (isinstance(debit.amount, bool) or not isinstance(debit.amount, (int, float))
            or not math.isfinite(debit.amount) or debit.amount <= 0):
        raise ValueError("dividend tax debit requires a finite positive amount")
    if (not debit.source_url.startswith("https://")
            or len(debit.source_url.removeprefix("https://").split("/", 1)[0]) == 0
            or not re.fullmatch(r"[a-f0-9]{64}", debit.source_sha256)):
        raise ValueError("dividend tax debit lacks source URL/SHA-256")


def _validate_due_tax_cash(account: AccountState, due: Sequence[DividendTaxDebit], states: dict[str, CorporateActionState], *, date: str, phase: str) -> None:
    due_by_action: dict[str, float] = {}
    for debit in due:
        due_by_action[debit.action_id] = due_by_action.get(debit.action_id, 0.0) + debit.amount
    for action_id, amount in due_by_action.items():
        state = states.get(action_id)
        if state is None or amount > state.tax_assessed - state.tax_cash + 1e-8:
            raise ValueError("dividend tax debit lacks source-bound outstanding liability")
    # Corporate cash receipts at this same boundary are posted before debits.
    # Count only already recorded entitlements, not assumed future holdings.
    available = account.cash + sum(
        sum(lot.shares for lot in state.entitled_lots) * state.action.cash_per_share
        for state in account.corporate_actions
        if state.recorded_date and not state.paid_date
        and state.action.payment_date == date and state.action.payment_phase == phase
    )
    if sum(debit.amount for debit in due) > available + 1e-8:
        raise ValueError("dividend tax debit exceeds cash")


def _due_tax_debits(
    account: AccountState,
    actions: Sequence[CorporateAction],
    tax_debits: Sequence[DividendTaxDebit],
    *,
    date: str,
    phase: str,
    cursor: str,
) -> list[DividendTaxDebit]:
    """Preflight the complete input and all due liabilities before any mutation."""
    action_ids = {action.action_id for action in actions}
    states = {state.action.action_id: state for state in account.corporate_actions}
    posted = {debit.debit_id: debit for debit in account.dividend_tax_debits}
    if len(posted) != len(account.dividend_tax_debits):
        raise ValueError("duplicate durable dividend tax debit identity")
    seen: set[str] = set()
    due: list[DividendTaxDebit] = []
    for debit in tax_debits:
        _validate_tax_debit(debit)
        if debit.debit_id in seen:
            raise ValueError("duplicate dividend tax debit input identity")
        seen.add(debit.debit_id)
        if debit.action_id not in action_ids:
            raise ValueError("dividend tax debit has no matching source-bound corporate action")
        prior = posted.get(debit.debit_id)
        if prior is not None and prior != debit:
            raise ValueError("dividend tax debit source changed across restart")
        debit_cursor = debit.date + (":0" if debit.phase == "open" else ":1")
        if prior is None:
            if debit_cursor < cursor:
                raise ValueError(f"{debit.debit_id}: missing dividend tax debit processing at {debit.date} {debit.phase}")
            if debit_cursor == cursor:
                due.append(debit)
    if not set(posted).issubset(seen):
        raise ValueError("restart omitted durable dividend tax debit source")
    _validate_due_tax_cash(account, due, states, date=date, phase=phase)
    return due


def _record_action(account: AccountState, action: CorporateAction, known: dict[str, CorporateActionState], *, date: str, phase: str) -> CorporateActionState | None:
    if date < action.record_date:
        return None
    state = known.get(action.action_id)
    if state is None:
        if date > action.record_date:
            prior_fills = any(fill.symbol == action.symbol and fill.fill_date <= action.record_date
                              for fill in account.fills)
            position = account.positions.get(action.symbol)
            prior_holding = position is not None and any(
                lot.entry_date <= action.record_date for lot in position.tranches
            )
            if not prior_fills and not prior_holding:
                return None
            raise ValueError(f"{action.symbol} {date}: missing record-date account entitlement")
        state = CorporateActionState(action)
        account.corporate_actions.append(state)
        known[action.action_id] = state
    if date == action.record_date and phase == "close" and not state.recorded_date:
        position = account.positions.get(action.symbol)
        lots = [replace(lot) for lot in position.tranches] if position else []
        for lot in lots:
            corporate_action_share_award(lot, action)
        tax_lots = corporate_action_fifo_inventory(account, action.symbol)
        if sum(lot.remaining_shares for lot in tax_lots) != sum(lot.shares for lot in lots):
            raise ValueError(f"{action.symbol}: record-date holdings lack FIFO fill evidence")
        state.entitled_lots = lots
        state.tax_lots = [replace(lot, shares=lot.remaining_shares) for lot in tax_lots]
        state.recorded_date = date
    return state


def _recognize_action(account: AccountState, state: CorporateActionState, *, date: str) -> None:
    action = state.action
    if not state.ex_processed_date:
        if date != action.ex_date:
            raise ValueError(f"{action.symbol}: missing ex-date processing")
        position = account.positions.get(action.symbol)
        if position:
            for lot in position.tranches:
                lot.avg_cost /= 1 + action.share_ratio
                lot.highest_close = (lot.highest_close - action.cash_adjustment_per_share) / (1 + action.reference_share_ratio)
                if lot.lowest_close:
                    lot.lowest_close = (lot.lowest_close - action.cash_adjustment_per_share) / (1 + action.reference_share_ratio)
            position.avg_cost /= 1 + action.share_ratio
            position.highest_close = (position.highest_close - action.cash_adjustment_per_share) / (1 + action.reference_share_ratio)
        state.ex_processed_date = date
        state.income_cash = sum(lot.shares for lot in state.entitled_lots) * action.cash_per_share


def _deliver_action(account: AccountState, state: CorporateActionState, *, date: str) -> None:
    action = state.action
    if action.share_ratio and date >= action.share_available_date and not state.distributed_date:
        if date != action.share_available_date:
            raise ValueError(f"{action.symbol}: missing share-availability processing")
        from ..execution.tranches import rebuild_position_from_tranches
        position = account.positions.get(action.symbol, Position(symbol=action.symbol))
        for index, lot in enumerate(state.entitled_lots):
            quantity = corporate_action_share_award(lot, action)
            if quantity:
                position.tranches.append(replace(
                    lot, tranche_id=f"ca:{action.action_id}:{index}", shares=quantity,
                    avg_cost=lot.avg_cost / (1 + action.share_ratio),
                    highest_close=(lot.highest_close - action.cash_adjustment_per_share) / (1 + action.reference_share_ratio),
                    lowest_close=(lot.lowest_close - action.cash_adjustment_per_share) / (1 + action.reference_share_ratio) if lot.lowest_close else 0,
                    sellable_date=date,
                ))
                state.distributed_shares += quantity
        if position.tranches:
            rebuild_position_from_tranches(position)
            account.positions[action.symbol] = position
        state.distributed_date = date


def _settle_action(account: AccountState, state: CorporateActionState, *, date: str, phase: str) -> None:
    action = state.action
    if date < action.ex_date:
        return
    if phase == "close":
        if not state.ex_processed_date:
            raise ValueError(f"{action.symbol}: missing ex-date open processing")
        if action.payment_phase == "close" and date == action.payment_date and not state.paid_date:
            if not state.ex_processed_date:
                raise ValueError("cash payment precedes ex recognition")
            account.cash += state.income_cash
            state.paid_date = date
        return
    if not state.recorded_date:
        raise ValueError(f"{action.symbol}: missing record-date account entitlement")
    if action.payment_date and date > action.payment_date and not state.paid_date:
        raise ValueError(f"{action.symbol}: missing payment-date processing")
    _recognize_action(account, state, date=date)
    if action.payment_phase == "open" and action.payment_date and date >= action.payment_date and not state.paid_date:
        if date != action.payment_date:
            raise ValueError(f"{action.symbol}: missing payment-date processing")
        account.cash += state.income_cash
        state.paid_date = date
    _deliver_action(account, state, date=date)


def apply_corporate_actions(account: AccountState, actions: Sequence[CorporateAction], *, date: str, phase: str, tax_debits: Sequence[DividendTaxDebit] = ()) -> None:
    """Capture at record close; recognize, pay, and deliver before open execution."""
    if phase not in {"open", "close"}:
        raise ValueError("corporate action phase must be open or close")
    if Date.fromisoformat(date).isoformat() != date:
        raise ValueError("corporate action session must be YYYY-MM-DD")
    cursor = date + (":0" if phase == "open" else ":1")
    if account.corporate_action_cursor and cursor < account.corporate_action_cursor:
        raise ValueError("corporate action replay must be chronological")
    known = {state.action.action_id: state for state in account.corporate_actions}
    if not set(known).issubset({action.action_id for action in actions}):
        raise ValueError("restart omitted durable corporate action source")
    if len({(action.symbol, action.ex_date) for action in actions}) != len(actions):
        raise ValueError("multiple corporate actions for one symbol/ex date require consolidated source terms")
    if len({action.action_id for action in actions}) != len(actions):
        raise ValueError("duplicate corporate action input identity")
    for action in actions:
        validate_action(action)
        if action.action_id in known and known[action.action_id].action != action:
            raise ValueError("corporate action source terms changed across restart")
    due_debits = _due_tax_debits(account, actions, tax_debits, date=date, phase=phase, cursor=cursor)
    for action in actions:
        state = _record_action(account, action, known, date=date, phase=phase)
        if state is not None:
            _settle_action(account, state, date=date, phase=phase)
    for debit in due_debits:
        state = known[debit.action_id]
        account.cash -= debit.amount
        state.tax_cash += debit.amount
        account.dividend_tax_debits.append(debit)
    account.corporate_action_cursor = cursor


def corporate_action_state_from_payload(payload: Mapping[str, Any]) -> CorporateActionState:
    fields = dict(payload)
    fields["action"] = CorporateAction(**fields["action"])
    fields["entitled_lots"] = [Tranche(**item) for item in fields["entitled_lots"]]
    fields["tax_lots"] = [DividendTaxLot(**item) for item in fields["tax_lots"]]
    return CorporateActionState(**fields)


def _record_inventory(account: AccountState, action: CorporateAction) -> dict[tuple[str, str], int]:
    expected: dict[tuple[str, str], int] = {}
    for fill in account.fills:
        if fill.symbol != action.symbol or fill.fill_date > action.record_date:
            continue
        if fill.side == "BUY":
            key = (fill.event_id, fill.fill_date)
            expected[key] = expected.get(key, 0) + fill.shares
        else:
            for allocation in fill.sold_tranches:
                key = (str(allocation["event_id"]), str(allocation["entry_date"]))
                expected[key] = expected.get(key, 0) - int(allocation["shares"])
    for prior in account.corporate_actions:
        if prior.action.symbol == action.symbol and prior.distributed_date and prior.distributed_date <= action.record_date:
            for lot in prior.entitled_lots:
                key = (lot.event_id, lot.entry_date)
                expected[key] = expected.get(key, 0) + corporate_action_share_award(lot, prior.action)
    return expected


def _validate_recorded_rights(account: AccountState, state: CorporateActionState) -> None:
    action = state.action
    if state.recorded_date:
        expected = _record_inventory(account, action)
        actual: dict[tuple[str, str], int] = {}
        for lot in state.entitled_lots:
            if type(lot.shares) is not int or lot.shares <= 0:
                raise ValueError("corporate action record-date lot shares differ")
            key = (lot.event_id, lot.entry_date)
            actual[key] = actual.get(key, 0) + lot.shares
        if {key: value for key, value in expected.items() if value} != actual:
            raise ValueError("corporate action record-date lots differ from native fill inventory")
        fifo = corporate_action_fifo_inventory(account, action.symbol, action.record_date)
        if [(lot.lot_id, lot.acquired_date, lot.remaining_shares) for lot in fifo] != [
            (lot.lot_id, lot.acquired_date, lot.shares) for lot in state.tax_lots
        ]:
            raise ValueError("corporate action record-date FIFO entitlement differs")
    elif state.entitled_lots or state.tax_lots or state.ex_processed_date:
        raise ValueError("corporate action rights lack record-date processing")


def _validate_assessed_tax(account: AccountState, state: CorporateActionState) -> None:
    action = state.action
    for tax_lot in state.tax_lots:
        if type(tax_lot.shares) is not int or type(tax_lot.remaining_shares) is not int or not 0 <= tax_lot.remaining_shares <= tax_lot.shares:
            raise ValueError("corporate action tax shares differ")
    sales: list[tuple[str, str, int]] = []
    live = {tax_lot.lot_id: tax_lot.remaining_shares for tax_lot in corporate_action_fifo_inventory(account, action.symbol, sales=sales)}
    expected_tax = 0.0
    for tax_lot in state.tax_lots:
        if tax_lot.remaining_shares != min(tax_lot.shares, live.get(tax_lot.lot_id, 0)):
            raise ValueError("corporate action outstanding tax entitlement differs from native FIFO")
        expected_tax += sum(quantity * action.cash_per_share * dividend_tax_rate(tax_lot.acquired_date, day)
                            for lot_id, day, quantity in sales
                            if lot_id == tax_lot.lot_id and day > action.record_date)
    if not math.isclose(state.tax_assessed, expected_tax, rel_tol=0.0, abs_tol=1e-8):
        raise ValueError("corporate action assessed tax differs from native FIFO sales")


def _validate_action_economics(account: AccountState, state: CorporateActionState) -> None:
    action = state.action
    entitled = sum(lot.shares for lot in state.entitled_lots)
    if sum(lot.shares for lot in state.tax_lots) != entitled:
        raise ValueError("corporate action tax entitlement differs")
    _validate_assessed_tax(account, state)
    expected_income = entitled * action.cash_per_share if state.ex_processed_date else 0.0
    if not math.isclose(state.income_cash, expected_income, rel_tol=0.0, abs_tol=1e-8):
        raise ValueError("corporate action income differs from source entitlement")
    if not math.isfinite(state.tax_assessed) or not 0 <= state.tax_cash <= state.tax_assessed <= entitled * (action.cash_per_share + action.bonus_tax_per_share) * 0.2 + 1e-8:
        raise ValueError("corporate action tax amount differs")
    expected_shares = sum(corporate_action_share_award(lot, action) for lot in state.entitled_lots) if state.distributed_date else 0
    if state.distributed_shares != expected_shares:
        raise ValueError("corporate action share award differs")


def validate_corporate_action_state(account: AccountState) -> None:
    seen: set[str] = set()
    for state in account.corporate_actions:
        action = state.action
        validate_action(action)
        if action.action_id in seen:
            raise ValueError("duplicate durable corporate action")
        seen.add(action.action_id)
        for actual_date, expected_date in ((state.recorded_date, action.record_date),
                                 (state.ex_processed_date, action.ex_date),
                                 (state.paid_date, action.payment_date),
                                 (state.distributed_date, action.share_available_date)):
            if actual_date and actual_date != expected_date:
                raise ValueError("corporate action processing date differs")
        _validate_recorded_rights(account, state)
        if (state.paid_date or state.distributed_date) and not state.ex_processed_date:
            raise ValueError("corporate action settlement precedes recognition")
        _validate_action_economics(account, state)

    debit_ids: set[str] = set()
    for debit in account.dividend_tax_debits:
        _validate_tax_debit(debit)
        if debit.debit_id in debit_ids or debit.action_id not in seen:
            raise ValueError("invalid durable dividend tax debit")
        Date.fromisoformat(debit.date)
        debit_ids.add(debit.debit_id)
    for state in account.corporate_actions:
        paid = sum(debit.amount for debit in account.dividend_tax_debits if debit.action_id == state.action.action_id)
        if not math.isclose(state.tax_cash, paid, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError("dividend tax cash differs from source-bound debits")
