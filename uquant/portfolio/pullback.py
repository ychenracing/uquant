"""Consume explicit BaseRisk permission through the one actual capital book."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import config_fingerprint
from ..models.ordinary_entry import REASON, pullback_order_entry
from ..models.trading import late_strategic_fill_allowed
from ..ordinary_pullback import current_pullback_proof
from ..risk.pullback import pullback_book_settled, pullback_risk_open

if TYPE_CHECKING:
    from ..types import PendingOrder
    from .pipeline import _AllocationBook


def admit_pullback(book: _AllocationBook) -> set[str]:
    permission = book.risk.evidence.get("ordinary_pullback_permission")
    if (not isinstance(permission, dict) or permission.get("as_of") != str(book.date.date())
            or not pullback_risk_open(book.risk, book.account) or not pullback_book_settled(book.account)
            or any(weight > 0 for weight in book.committed.values())
            or any(weight > 0 for weight in book.proposed.values())):
        return set()
    cfg = book.policy.cfg
    maximum = min(permission["maximum_weight"], cfg.core_admission_weight, book.gross_cap)
    candidates = []
    for symbol, original in permission["proofs"].items():
        if symbol not in book.user_panel or symbol not in book.leaders or symbol in book.owned:
            continue
        proof = current_pullback_proof(symbol=symbol, date=book.date, frame=book.user_panel[symbol],
                                      leader=book.leaders[symbol], cfg=cfg)
        if proof == original and proof["block"] == "READY":
            candidates.append(symbol)
    selected = sorted(candidates, key=lambda s: (-book.leaders[s].score, s))[
        :min(cfg.max_positions, int((maximum + 1e-12) / cfg.min_trade_weight))]
    allowed = set()
    for symbol in selected:
        if book.fund(symbol, maximum / len(selected), phase="PULLBACK_CORE", minimum=cfg.min_trade_weight):
            book.reasons[symbol] = "bounded ordinary long-pullback entry"
            book.record(symbol).update(pullback_entry=permission["proofs"][symbol],
                                       entry_gate="BOUNDED_PULLBACK_AUTHORIZED")
            allowed.add(symbol)
    return allowed


def pending_pullback_open(book: _AllocationBook, order: PendingOrder) -> bool:
    """Only the original unfilled quantity survives; a historical proof is not cash."""
    entry = pullback_order_entry(book.account, order)
    ledger = next((item for item in book.account.order_ledger if item.order_id == order.order_id), None)
    if (entry is None or order.reason_code != REASON or ledger is None
            or ledger.status not in {"SUBMITTED", "OPEN", "PARTIALLY_FILLED"}
            or ledger.remaining_shares <= 0 or ledger.event_id != order.event_id
            or entry["code_hash"] != book.account.code_hash
            or entry["config_sha256"] != config_fingerprint(book.policy.cfg)
            or order.target_weight > ledger.target_weight + 1e-12
            or not pullback_risk_open(book.risk, book.account)
            or any(late_strategic_fill_allowed(item) or (
                item.order_id != order.order_id and item.status not in {"FILLED", "CANCELLED", "REPLACED"}
                and item.reason_code != REASON) for item in book.account.order_ledger)
            or order.symbol not in book.leaders or order.symbol not in book.user_panel):
        return False
    proof = current_pullback_proof(symbol=order.symbol, date=book.date, frame=book.user_panel[order.symbol],
                                  leader=book.leaders[order.symbol], cfg=book.policy.cfg)
    book.record(order.symbol)["pending_entry"] = proof
    return bool(proof["block"] == "READY")
