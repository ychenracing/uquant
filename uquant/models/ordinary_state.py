"""Typed views of ordinary-account state, preserving its persisted wire keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..types import AccountState


@dataclass(frozen=True, slots=True)
class CoreTransfer:
    incumbent: str
    challenger: str

    @property
    def key(self) -> str:
        return f"core_transfer:{self.incumbent}->{self.challenger}"

    @property
    def session_key(self) -> str:
        return f"core_transfer_session:{self.incumbent}->{self.challenger}"

    @classmethod
    def from_key(cls, key: str) -> CoreTransfer:
        prefix, separator, pair = key.partition(":")
        incumbent, arrow, challenger = pair.partition("->")
        if (
            prefix != "core_transfer"
            or not separator
            or not arrow
            or not incumbent
            or not challenger
            or ":" in pair
            or "->" in challenger
        ):
            raise ValueError("invalid ordinary transfer identity")
        return cls(incumbent, challenger)

    def confirmations(self, account: AccountState) -> int:
        return account.replacement_tenure.get(self.key, 0)

    def observed_session(self, account: AccountState) -> int:
        return account.candidate_tenure.get(self.session_key, 0)

    def observe(self, account: AccountState, *, session: int, previous_session: int, qualified: bool) -> None:
        last = self.observed_session(account)
        if last > session:
            raise RuntimeError("transfer observation session moved backwards")
        if last != session:
            streak = self.confirmations(account) if last == previous_session else 0
            account.replacement_tenure[self.key] = streak + 1 if qualified else 0
            account.candidate_tenure[self.session_key] = session

    def record_request(self, account: AccountState, session: int) -> None:
        account.candidate_tenure[self.session_key] = session
        for other in transfers(account):
            if other != self:
                other.consume(account)

    def consume(self, account: AccountState) -> None:
        account.replacement_tenure[self.key] = 0


def transfers(account: AccountState) -> tuple[CoreTransfer, ...]:
    return tuple(
        CoreTransfer.from_key(key) for key in account.replacement_tenure if key.startswith("core_transfer:")
    )


def _repair_origin_key(order_id: str, event_id: str) -> str:
    return f"ordinary_repair_origin:{order_id}:{event_id}"


def repair_origin_recorded(account: AccountState, order_id: str, event_id: str) -> bool:
    return account.candidate_tenure.get(_repair_origin_key(order_id, event_id)) == 1


def record_repair_origin(account: AccountState, order_id: str, event_id: str) -> None:
    account.candidate_tenure["ordinary_repair_capital_active"] = 1
    account.candidate_tenure[_repair_origin_key(order_id, event_id)] = 1


def clear_repair_origins(account: AccountState) -> None:
    account.candidate_tenure.pop("ordinary_repair_capital_active", None)
    for key in tuple(account.candidate_tenure):
        if key.startswith("ordinary_repair_origin:"):
            account.candidate_tenure.pop(key)
