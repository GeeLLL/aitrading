from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from risk.cash_ledger import CashLedger


@dataclass(frozen=True)
class OfficialCashView:
    settled_cash: Decimal | None
    unsettled_cash: Decimal | None
    buying_power: Decimal | None
    reserved_cash: Decimal | None


@dataclass(frozen=True)
class CashReconciliation:
    safe: bool
    reasons: tuple[str, ...]


def reconcile_cash_ledger(
    ledger: CashLedger, official: OfficialCashView, *, tolerance: Decimal = Decimal("0.01")
) -> CashReconciliation:
    fields = (official.settled_cash, official.unsettled_cash, official.buying_power, official.reserved_cash)
    if any(value is None for value in fields):
        return CashReconciliation(False, ("OFFICIAL_CASH_STATE_UNKNOWN",))
    assert all(value is not None for value in fields)
    reasons: list[str] = []
    if abs(ledger.settled_cash - official.settled_cash) > tolerance:
        reasons.append("SETTLED_CASH_RECONCILIATION_MISMATCH")
    if abs(ledger.unsettled_cash - official.unsettled_cash) > tolerance:
        reasons.append("UNSETTLED_CASH_RECONCILIATION_MISMATCH")
    if abs(ledger.reserved_cash - official.reserved_cash) > tolerance:
        reasons.append("RESERVED_CASH_RECONCILIATION_MISMATCH")
    if official.buying_power + tolerance < ledger.available_settled_cash:
        reasons.append("BUYING_POWER_BELOW_LOCAL_AVAILABLE_CASH")
    return CashReconciliation(not reasons, tuple(reasons))
