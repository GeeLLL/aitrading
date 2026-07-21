import unittest
from datetime import datetime, timezone
from decimal import Decimal

from strategy.virtual_position import VirtualPosition, VirtualPositionState, close_virtual_position, open_virtual_position


class VirtualPositionTests(unittest.TestCase):
    def test_full_lifecycle_uses_ask_entry_bid_exit_and_net_cost(self):
        pending = VirtualPosition("t1", VirtualPositionState.PENDING, 1, Decimal("0.50"))
        opened = open_virtual_position(pending, observed_ask=Decimal("0.49"), observed_at=datetime.now(timezone.utc), entry_fees_usd=Decimal("0.10"))
        closed = close_virtual_position(opened, current_bid=Decimal("0.60"), observed_at=datetime.now(timezone.utc), reason="TARGET", exit_fees_usd=Decimal("0.15"))
        self.assertEqual(closed.state, VirtualPositionState.CLOSED)
        self.assertEqual(closed.gross_pnl_usd, Decimal("11.00"))
        self.assertEqual(closed.friction_usd, Decimal("0.25"))
        self.assertEqual(closed.net_pnl_usd, Decimal("10.75"))

    def test_limit_not_reached_stays_pending(self):
        pending = VirtualPosition("t", VirtualPositionState.PENDING, 1, Decimal("0.50"))
        self.assertEqual(open_virtual_position(pending, observed_ask=Decimal("0.51"), observed_at=datetime.now(timezone.utc)).state, VirtualPositionState.PENDING)

    def test_unknown_exit_requires_manual_review(self):
        opened = VirtualPosition("t", VirtualPositionState.OPEN, 1, Decimal("0.5"), fill_price=Decimal("0.5"), opened_at=datetime.now(timezone.utc))
        self.assertEqual(close_virtual_position(opened, current_bid=None, observed_at=datetime.now(timezone.utc), reason="EXIT").state, VirtualPositionState.MANUAL_REVIEW)
