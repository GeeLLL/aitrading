from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.trajectory_recorder import (
    REQUIRED_FIELDS,
    TrajectoryError,
    candidate_event,
    load_day_events,
    nearest_the_money,
    observation_event,
    open_refresh_targets,
    option_instruments,
    option_quotes_by_instrument,
    underlying_last_trade,
    validate_event,
    write_event,
)

NOW = datetime(2026, 7, 31, 17, 3, tzinfo=timezone.utc)

INSTRUMENT = {
    "id": "abc123def456",
    "chain_symbol": "SPY",
    "type": "call",
    "strike_price": "745.0000",
    "expiration_date": "2026-08-07",
}
QUOTE = {
    "instrument_id": "abc123def456",
    "bid_price": "3.10",
    "ask_price": "3.25",
    "adjusted_mark_price": "3.17",
    "volume": 1200,
    "open_interest": 5400,
    "delta": "0.52",
    "implied_volatility": "0.19",
    "theta": "-0.08",
}


def _candidate(**overrides):
    kwargs = dict(
        instrument=INSTRUMENT,
        quote=QUOTE,
        decision_time=NOW,
        quote_received_at=NOW,
        source_updated_at="2026-07-31T17:02:58Z",
        policy_labels=["BASE_18"],
    )
    kwargs.update(overrides)
    return candidate_event(**kwargs)


class CandidateEventTests(unittest.TestCase):
    def test_candidate_satisfies_schema_and_reader_contract(self) -> None:
        event = _candidate()
        validate_event(event)  # all REQUIRED_FIELDS present
        self.assertEqual("CANDIDATE", event["event_type"])
        self.assertEqual("CALL", event["option_type"])
        # The recorded ask IS the entry limit (reconstruct_trade's rule).
        self.assertEqual(event["ask"], event["limit_price"])
        self.assertEqual(3.25, event["limit_price"])
        deadline = datetime.fromisoformat(event["fill_window_deadline"])
        recorded = datetime.fromisoformat(event["limit_recorded_at"])
        self.assertEqual(60.0, (deadline - recorded).total_seconds())
        self.assertEqual(["BASE_18"], event["policy_labels"])
        self.assertEqual("PILOT_EXCLUDED_FROM_PERFORMANCE", event["evidence_class"])

    def test_candidate_without_instrument_id_fails_closed(self) -> None:
        with self.assertRaisesRegex(TrajectoryError, "WITHOUT_INSTRUMENT_ID"):
            _candidate(instrument={"chain_symbol": "SPY"})

    def test_unknown_numbers_stay_null(self) -> None:
        event = _candidate(quote={"instrument_id": "abc123def456", "ask_price": "3.25"})
        self.assertIsNone(event["bid"])
        self.assertIsNone(event["volume"])
        self.assertEqual(3.25, event["ask"])


class ObservationEventTests(unittest.TestCase):
    def test_quote_event_carries_trajectory_identity(self) -> None:
        candidate = _candidate()
        later = NOW + timedelta(seconds=30)
        event = observation_event(
            event_type="QUOTE", candidate=candidate,
            quote={"bid_price": "3.00", "ask_price": "3.20"},
            quote_received_at=later, source_updated_at=None,
        )
        validate_event(event)
        self.assertEqual(candidate["trajectory_id"], event["trajectory_id"])
        self.assertEqual(candidate["instrument_id"], event["instrument_id"])
        self.assertEqual(3.20, event["ask"])

    def test_unsupported_event_type_rejected(self) -> None:
        with self.assertRaisesRegex(TrajectoryError, "UNSUPPORTED_EVENT_TYPE"):
            observation_event(
                event_type="FILL", candidate=_candidate(), quote={},
                quote_received_at=NOW, source_updated_at=None,
            )


class WriteLoadRefreshTests(unittest.TestCase):
    def test_write_load_roundtrip_and_refresh_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = _candidate()
            write_event(directory, candidate)
            groups = load_day_events(directory)
            self.assertEqual(1, len(groups))

            # Inside the horizon -> needs a QUOTE refresh.
            targets = open_refresh_targets(groups, NOW + timedelta(minutes=5))
            self.assertEqual(1, len(targets))
            self.assertEqual("QUOTE", targets[0]["event_type"])

            # Past the horizon -> needs the HORIZON_CLOSE.
            targets = open_refresh_targets(groups, NOW + timedelta(minutes=61))
            self.assertEqual("HORIZON_CLOSE", targets[0]["event_type"])

            # After the close event is recorded -> nothing more to observe.
            close = observation_event(
                event_type="HORIZON_CLOSE", candidate=candidate,
                quote={"bid_price": "3.40", "ask_price": "3.55"},
                quote_received_at=NOW + timedelta(minutes=61), source_updated_at=None,
            )
            write_event(directory, close)
            groups = load_day_events(directory)
            self.assertEqual([], open_refresh_targets(groups, NOW + timedelta(minutes=90)))

    def test_rejected_candidate_is_never_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rejected = _candidate(rejection_reasons=["SPREAD_TOO_WIDE"])
            write_event(directory, rejected)
            groups = load_day_events(directory)
            self.assertEqual([], open_refresh_targets(groups, NOW + timedelta(minutes=5)))

    def test_write_event_validates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TrajectoryError, "MISSING_FIELDS"):
                write_event(directory, {"trajectory_id": "x", "event_type": "QUOTE"})


class SnapshotExtractorTests(unittest.TestCase):
    ENVELOPE = {
        "received_at": "2026-07-31T17:03:02+00:00",
        "source_updated_at": "2026-07-31T17:02:58+00:00",
        "response": {"tool_results": [
            {"tool": "get_equity_quotes", "output": {"data": {"results": [
                {"quote": {"symbol": "SPY", "last_trade_price": "744.80"}},
            ]}}},
            {"tool": "get_option_instruments", "output": {"data": {"instruments": [
                INSTRUMENT,
                {"id": "far", "chain_symbol": "SPY", "type": "put",
                 "strike_price": "600.0000", "expiration_date": "2026-08-07"},
            ]}}},
            {"tool": "get_option_quotes", "output": {"data": {"results": [
                {"quote": QUOTE},
            ]}}},
            {"tool": "get_option_quotes", "output": None, "truncated": True},
        ]},
    }

    def test_extractors_and_nearest_the_money(self) -> None:
        self.assertEqual(744.80, underlying_last_trade(self.ENVELOPE, "SPY"))
        instruments = option_instruments(self.ENVELOPE)
        self.assertEqual(2, len(instruments))
        quotes = option_quotes_by_instrument(self.ENVELOPE)
        self.assertIn("abc123def456", quotes)
        selection = nearest_the_money(instruments, quotes, 744.80, option_type="CALL")
        self.assertIsNotNone(selection)
        instrument, quote = selection
        # "far" has no quote, so only the quoted near-the-money one qualifies.
        self.assertEqual("abc123def456", instrument["id"])
        self.assertEqual("3.25", quote["ask_price"])

    def test_the_contract_matches_the_signal_direction(self) -> None:
        """Until 2026-08-13 this ranked purely on strike distance and never read
        the contract type. A call and a put at the same strike are equidistant,
        so the venue's listing order decided the side — and BOTH strategy
        positions ever opened came out as PUTs while the frozen strategy had
        decided CALL (AMD 08-03, SOFI 08-13, both BULLISH). Every position took
        the opposite side of its own signal."""
        instruments = [
            {"id": "call-atm", "type": "call", "strike_price": "745.0"},
            {"id": "put-atm", "type": "put", "strike_price": "745.0"},
        ]
        quotes = {"call-atm": {"ask_price": "3.25"}, "put-atm": {"ask_price": "3.30"}}
        for wanted, expected in (("CALL", "call-atm"), ("PUT", "put-atm")):
            selection = nearest_the_money(instruments, quotes, 745.0, option_type=wanted)
            self.assertIsNotNone(selection, wanted)
            self.assertEqual(expected, selection[0]["id"], wanted)

    def test_a_closer_strike_on_the_wrong_side_never_wins(self):
        instruments = [
            {"id": "put-closer", "type": "put", "strike_price": "745.0"},
            {"id": "call-further", "type": "call", "strike_price": "750.0"},
        ]
        quotes = {"put-closer": {"ask_price": "3.30"}, "call-further": {"ask_price": "1.10"}}
        selection = nearest_the_money(instruments, quotes, 745.0, option_type="CALL")
        self.assertEqual("call-further", selection[0]["id"])

    def test_an_unstated_direction_is_refused_rather_than_guessed(self):
        instruments = [{"id": "c", "type": "call", "strike_price": "745.0"}]
        quotes = {"c": {"ask_price": "3.25"}}
        for bad in ("", "LONG", None):
            with self.assertRaises(ValueError):
                nearest_the_money(instruments, quotes, 745.0, option_type=bad)

    def test_no_quoted_instrument_returns_none(self) -> None:
        instruments = [{"id": "x", "strike_price": "740.0"}]
        self.assertIsNone(nearest_the_money(instruments, {}, 744.8, option_type="CALL"))


if __name__ == "__main__":
    unittest.main()
