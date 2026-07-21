import unittest

from execution.order_event_simulator import BrokerEvent, simulate_order_events


class OrderEventSimulatorTests(unittest.TestCase):
    def test_fill_can_win_cancel_race(self):
        result = simulate_order_events((
            BrokerEvent.SUBMIT_ACK,
            BrokerEvent.CANCEL_REQUESTED,
            BrokerEvent.FULL_FILL,
            BrokerEvent.CANCEL_ACK,
        ))
        self.assertEqual(result.status, "FILLED")
        self.assertEqual(result.filled_quantity, 1)
        self.assertIn("FILL_WON_CANCEL_RACE", result.reasons)
        self.assertIn("LATE_CANCEL_ACK_IGNORED_AFTER_FILL", result.reasons)

    def test_disconnect_fails_closed(self):
        result = simulate_order_events((BrokerEvent.SUBMIT_ACK, BrokerEvent.DISCONNECT))
        self.assertTrue(result.new_entries_blocked)
        self.assertEqual(result.status, "HALTED_UNKNOWN_STATE")

    def test_fill_without_ack_fails_closed(self):
        result = simulate_order_events((BrokerEvent.FULL_FILL,))
        self.assertTrue(result.new_entries_blocked)
        self.assertIn("FILL_WITHOUT_ACK", result.reasons)
