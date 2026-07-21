import unittest
from decimal import Decimal

from execution.broker_reconciliation import BrokerOrderView
from execution.order_state import DurableOrder, OrderState
from monitoring.startup_reconciliation import evaluate_startup_gate
from risk.cash_ledger import CashLedger
from risk.cash_reconciliation import OfficialCashView


class StartupGateTests(unittest.TestCase):
    def setUp(self):
        self.local_order = DurableOrder("i", "key", OrderState.INTENT_CREATED, 1, 0, "now")
        self.cash = CashLedger(Decimal("300"), Decimal("75"))
        self.official = OfficialCashView(
            Decimal("300"), Decimal("0"), Decimal("225"), Decimal("75")
        )

    def test_read_only_observation_is_safe_but_entry_is_blocked(self):
        report = evaluate_startup_gate(
            local_orders=[self.local_order],
            broker_orders=[BrokerOrderView("key", "queued", 1)],
            broker_positions=[],
            local_cash=self.cash,
            official_cash=self.official,
            system_read_only=True,
            live_trading_enabled=False,
            order_tools_enabled=False,
            kill_switch_engaged=True,
        )
        self.assertTrue(report.safe_for_observation)
        self.assertFalse(report.safe_for_new_entry)
        self.assertIn("SYSTEM_READ_ONLY", report.reasons)

    def test_unknown_broker_state_blocks_observation_and_entry(self):
        report = evaluate_startup_gate(
            local_orders=[], broker_orders=None, broker_positions=None,
            local_cash=CashLedger(Decimal("300"), Decimal("0")),
            official_cash=OfficialCashView(Decimal("300"), Decimal("0"), Decimal("300"), Decimal("0")),
            system_read_only=False, live_trading_enabled=True,
            order_tools_enabled=True, kill_switch_engaged=False,
        )
        self.assertFalse(report.safe_for_observation)
        self.assertFalse(report.safe_for_new_entry)
        self.assertIn("BROKER_STATE_UNKNOWN", report.reasons)

    def test_cash_mismatch_blocks_entry(self):
        report = evaluate_startup_gate(
            local_orders=[], broker_orders=[], broker_positions=[],
            local_cash=CashLedger(Decimal("300"), Decimal("0")),
            official_cash=OfficialCashView(Decimal("299"), Decimal("0"), Decimal("299"), Decimal("0")),
            system_read_only=False, live_trading_enabled=True,
            order_tools_enabled=True, kill_switch_engaged=False,
        )
        self.assertFalse(report.safe_for_new_entry)
        self.assertIn("SETTLED_CASH_RECONCILIATION_MISMATCH", report.reasons)
