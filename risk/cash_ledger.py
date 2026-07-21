from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class UnsettledCashLot:
    amount: Decimal
    settlement_date: date
    source: str


@dataclass(frozen=True)
class CashLedger:
    settled_cash: Decimal
    reserved_cash: Decimal
    unsettled_lots: tuple[UnsettledCashLot, ...] = ()

    def __post_init__(self) -> None:
        if self.settled_cash < 0 or self.reserved_cash < 0:
            raise ValueError("Cash balances cannot be negative.")
        if any(lot.amount < 0 for lot in self.unsettled_lots):
            raise ValueError("Unsettled cash lots cannot be negative.")

    @property
    def available_settled_cash(self) -> Decimal:
        return self.settled_cash - self.reserved_cash

    @property
    def unsettled_cash(self) -> Decimal:
        return sum((lot.amount for lot in self.unsettled_lots), Decimal("0"))

    def can_reserve(self, amount: Decimal) -> bool:
        return amount > 0 and self.available_settled_cash >= amount

    def reserve(self, amount: Decimal) -> "CashLedger":
        if not self.can_reserve(amount):
            raise ValueError("Insufficient settled cash")
        return CashLedger(self.settled_cash, self.reserved_cash + amount, self.unsettled_lots)

    def release(self, amount: Decimal) -> "CashLedger":
        if amount <= 0 or amount > self.reserved_cash:
            raise ValueError("Invalid reservation release")
        return CashLedger(self.settled_cash, self.reserved_cash - amount, self.unsettled_lots)

    def settle_through(self, as_of: date) -> "CashLedger":
        matured = sum((lot.amount for lot in self.unsettled_lots if lot.settlement_date <= as_of), Decimal("0"))
        remaining = tuple(lot for lot in self.unsettled_lots if lot.settlement_date > as_of)
        return CashLedger(self.settled_cash + matured, self.reserved_cash, remaining)

    def reconcile(self, *, official_settled_cash: Decimal | None, tolerance: Decimal = Decimal("0.01")) -> tuple[str, ...]:
        if official_settled_cash is None:
            return ("OFFICIAL_SETTLED_CASH_UNKNOWN",)
        if abs(self.settled_cash - official_settled_cash) > tolerance:
            return ("SETTLED_CASH_RECONCILIATION_MISMATCH",)
        return ()
