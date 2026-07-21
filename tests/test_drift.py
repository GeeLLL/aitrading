import unittest
from decimal import Decimal

from monitoring.drift import evaluate_drift


class DriftTests(unittest.TestCase):
    def test_stable_distribution_passes(self):
        result = evaluate_drift(reference_values=[Decimal("1"), Decimal("1.1")], current_values=[Decimal("1.02"), Decimal("1.08")], no_trade_flags=[False, True], schema_failures=[False, False])
        self.assertFalse(result.drifted)

    def test_no_trade_and_schema_alert(self):
        result = evaluate_drift(reference_values=[Decimal("1")], current_values=[Decimal("2")], no_trade_flags=[True] * 20, schema_failures=[True])
        self.assertTrue(result.drifted)
        self.assertIn("NO_TRADE_RATE_ALERT", result.reasons)
        self.assertIn("SCHEMA_FAILURE_RATE_ALERT", result.reasons)
