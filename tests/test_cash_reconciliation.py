from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from risk.cash_ledger import CashLedger, UnsettledCashLot
from risk.cash_reconciliation import OfficialCashView, reconcile_cash_ledger


class CashReconciliationTests(unittest.TestCase):
    def test_all_cash_fields_are_required(self) -> None:
        result = reconcile_cash_ledger(CashLedger(Decimal("300"), Decimal("0")), OfficialCashView(Decimal("300"), None, Decimal("300"), Decimal("0")))
        self.assertFalse(result.safe)

    def test_matching_cash_view_is_safe(self) -> None:
        ledger = CashLedger(Decimal("250"), Decimal("25"), (UnsettledCashLot(Decimal("50"), date(2026, 7, 22), "exit"),))
        result = reconcile_cash_ledger(ledger, OfficialCashView(Decimal("250"), Decimal("50"), Decimal("225"), Decimal("25")))
        self.assertTrue(result.safe)

