from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from monitoring.shadow_sampling import ReadOnlySampleGate, SampleStatus


NOW = datetime(2026, 7, 17, 18, 0, tzinfo=timezone.utc)


class ReadOnlySampleGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = ReadOnlySampleGate()

    def evaluate(self, **overrides):
        values = {
            "sample_id": "sample-1",
            "source_updated_at": NOW - timedelta(seconds=1),
            "received_at": NOW,
        }
        values.update(overrides)
        return self.gate.evaluate(**values)

    def test_fresh_sample_is_accepted(self) -> None:
        decision = self.evaluate()
        self.assertEqual(SampleStatus.ACCEPTED, decision.status)
        self.assertEqual(Decimal("1.0"), decision.age_seconds)

    def test_duplicate_sample_is_rejected(self) -> None:
        self.evaluate()
        self.assertEqual("DUPLICATE_SAMPLE", self.evaluate().reason)

    def test_stale_sample_is_rejected(self) -> None:
        decision = self.evaluate(source_updated_at=NOW - timedelta(seconds=11))
        self.assertEqual("STALE_SAMPLE", decision.reason)

    def test_out_of_order_sample_is_rejected(self) -> None:
        self.evaluate(sample_id="newer", source_updated_at=NOW - timedelta(seconds=1))
        decision = self.evaluate(
            sample_id="older", source_updated_at=NOW - timedelta(seconds=2)
        )
        self.assertEqual("OUT_OF_ORDER_SAMPLE", decision.reason)

    def test_missing_source_time_is_rejected(self) -> None:
        self.assertEqual("SOURCE_TIME_UNKNOWN", self.evaluate(source_updated_at=None).reason)

    def test_naive_received_time_is_rejected(self) -> None:
        decision = self.evaluate(received_at=NOW.replace(tzinfo=None))
        self.assertEqual("RECEIVED_TIME_NOT_TIMEZONE_AWARE", decision.reason)

    def test_excessive_future_skew_is_rejected(self) -> None:
        decision = self.evaluate(source_updated_at=NOW + timedelta(seconds=3))
        self.assertEqual("SOURCE_TIME_TOO_FAR_IN_FUTURE", decision.reason)


if __name__ == "__main__":
    unittest.main()

