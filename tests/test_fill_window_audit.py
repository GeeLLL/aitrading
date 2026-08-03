from __future__ import annotations

import unittest

from scripts.eod_report import fill_window_dual_clock_audit


def _candidate(**overrides):
    base = {
        "event_type": "CANDIDATE",
        "trajectory_id": "T1",
        "rejection_reasons": [],
        "limit_price": 3.25,
        "ask": 3.25,
        "limit_recorded_at": "2026-07-31T17:03:00+00:00",
        "fill_window_deadline": "2026-07-31T17:04:00+00:00",
        "quote_received_at": "2026-07-31T17:03:00+00:00",
        "source_updated_at": "2026-07-31T17:02:50+00:00",
    }
    base.update(overrides)
    return base


def _quote(received: str, source: str | None, ask: float):
    return {
        "event_type": "QUOTE",
        "trajectory_id": "T1",
        "quote_received_at": received,
        "source_updated_at": source,
        "ask": ask,
    }


class DualClockAuditTests(unittest.TestCase):
    def test_divergence_flagged_when_venue_clock_would_fill(self) -> None:
        # Receipt clock: 75s elapsed (outside 60s+5 window). Venue clock: the
        # quote's update is only 40s after the candidate's -> inside the window.
        # Ask <= limit, so the venue clock WOULD have filled: divergence.
        groups = {"T1": [
            _candidate(),
            _quote("2026-07-31T17:04:15+00:00", "2026-07-31T17:03:30+00:00", 3.20),
        ]}
        audit = fill_window_dual_clock_audit(groups)
        self.assertEqual(1, audit["clock_divergence_count"])
        window = audit["windows"][0]
        self.assertFalse(window["in_window_receipt_clock"])
        self.assertTrue(window["in_window_venue_clock"])
        self.assertTrue(window["clock_divergence_changes_outcome"])

    def test_no_divergence_when_both_clocks_agree(self) -> None:
        groups = {"T1": [
            _candidate(),
            _quote("2026-07-31T17:03:30+00:00", "2026-07-31T17:03:20+00:00", 3.20),
        ]}
        audit = fill_window_dual_clock_audit(groups)
        self.assertEqual(1, len(audit["windows"]))
        self.assertEqual(0, audit["clock_divergence_count"])
        self.assertTrue(audit["windows"][0]["in_window_receipt_clock"])

    def test_above_limit_never_diverges(self) -> None:
        groups = {"T1": [
            _candidate(),
            _quote("2026-07-31T17:04:15+00:00", "2026-07-31T17:03:30+00:00", 3.40),
        ]}
        audit = fill_window_dual_clock_audit(groups)
        self.assertEqual(0, audit["clock_divergence_count"])
        self.assertFalse(audit["windows"][0]["ask_at_or_below_limit"])

    def test_rejected_and_missing_fields_skipped(self) -> None:
        groups = {
            "T1": [_candidate(rejection_reasons=["SPREAD"]),
                   _quote("2026-07-31T17:03:30+00:00", None, 3.20)],
            "T2": [_candidate(trajectory_id="T2", limit_price=None, ask=None)],
        }
        audit = fill_window_dual_clock_audit(groups)
        self.assertEqual([], audit["windows"])

    def test_missing_venue_timestamps_stay_unknown_not_invented(self) -> None:
        groups = {"T1": [
            _candidate(source_updated_at=None),
            _quote("2026-07-31T17:03:30+00:00", None, 3.20),
        ]}
        audit = fill_window_dual_clock_audit(groups)
        window = audit["windows"][0]
        self.assertIsNone(window["venue_elapsed_seconds"])
        self.assertFalse(window["in_window_venue_clock"])
        self.assertFalse(window["clock_divergence_changes_outcome"])


if __name__ == "__main__":
    unittest.main()
