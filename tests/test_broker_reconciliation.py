from __future__ import annotations

import tempfile
import unittest

from execution.broker_reconciliation import BrokerOrderView, BrokerPositionView, ReconciliationMode, reconcile_on_startup
from execution.order_state import DurableOrderStore, OrderState


class BrokerReconciliationTests(unittest.TestCase):
    def test_unknown_broker_state_blocks(self) -> None:
        result = reconcile_on_startup(local_orders=(), broker_orders=None, broker_positions=None)
        self.assertEqual(ReconciliationMode.BLOCKED, result.mode)

    def test_matching_open_order_is_safe_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DurableOrderStore(directory)
            local = store.create(intent_id="one", idempotency_key="key", quantity=1)
            result = reconcile_on_startup(
                local_orders=(local,), broker_orders=(BrokerOrderView("key", "queued", 1),), broker_positions=(),
            )
            self.assertEqual(ReconciliationMode.READ_ONLY_SAFE, result.mode)

    def test_unrecognized_position_blocks(self) -> None:
        result = reconcile_on_startup(
            local_orders=(), broker_orders=(), broker_positions=(BrokerPositionView(None, 1, "CALL"),),
        )
        self.assertIn("BROKER_POSITION_IDENTITY_UNKNOWN", result.reasons)

