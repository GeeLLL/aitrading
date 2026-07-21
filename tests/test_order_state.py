from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from execution.order_state import DurableOrderStore, OrderState, reconcile_order_state


class DurableOrderStateTests(unittest.TestCase):
    def test_state_is_durable_and_duplicate_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DurableOrderStore(Path(directory))
            store.create(intent_id="intent-1", idempotency_key="key-1", quantity=1)
            store.transition("intent-1", OrderState.VALIDATED)
            reloaded = DurableOrderStore(Path(directory)).load("intent-1")
            self.assertEqual(OrderState.VALIDATED, reloaded.state)
            with self.assertRaises(ValueError):
                store.create(intent_id="intent-2", idempotency_key="key-1", quantity=1)

    def test_cancel_fill_race_is_representable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DurableOrderStore(Path(directory))
            store.create(intent_id="i", idempotency_key="k", quantity=1)
            store.transition("i", OrderState.VALIDATED)
            store.transition("i", OrderState.SUBMITTING)
            store.transition("i", OrderState.ACKNOWLEDGED)
            store.transition("i", OrderState.CANCEL_PENDING)
            filled = store.transition("i", OrderState.FILLED, filled_quantity=1)
            self.assertEqual(OrderState.FILLED, filled.state)

    def test_reconciliation_fails_closed(self) -> None:
        decision = reconcile_order_state((), broker_open_idempotency_keys=None, broker_position_count=None)
        self.assertFalse(decision.safe)
        self.assertIn("BROKER_STATE_UNKNOWN", decision.reasons)
