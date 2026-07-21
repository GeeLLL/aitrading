from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from risk.cash_ledger import CashLedger, UnsettledCashLot


class CashLedgerTests(unittest.TestCase):
    def test_unsettled_cash_is_not_available(self) -> None:
        ledger = CashLedger(
            Decimal("50"), Decimal("0"),
            (UnsettledCashLot(Decimal("250"), date(2026, 7, 20), "OPTION_CLOSE"),),
        )
        self.assertFalse(ledger.can_reserve(Decimal("75")))

    def test_settlement_moves_only_matured_lots(self) -> None:
        ledger = CashLedger(
            Decimal("50"), Decimal("0"),
            (UnsettledCashLot(Decimal("25"), date(2026, 7, 18), "A"),
             UnsettledCashLot(Decimal("30"), date(2026, 7, 21), "B")),
        ).settle_through(date(2026, 7, 18))
        self.assertEqual(Decimal("75"), ledger.settled_cash)
        self.assertEqual(Decimal("30"), ledger.unsettled_cash)
