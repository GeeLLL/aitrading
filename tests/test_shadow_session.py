from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from journal.shadow_recorder import ShadowSessionRecorder, ShadowSessionState
from risk.models import AccountSnapshot, OptionType
from risk.startup_guard import load_safety_config
from strategy.market_regime import CompletedMarketBar
from strategy.policy import load_strategy_policy
from strategy.shadow_pipeline import OptionCandidate
from strategy.shadow_runner import PositionObservation, ShadowRunner, ShadowSnapshot
from strategy.shadow_session import ShadowSessionController
from strategy.underlying_signal import UnderlyingSignalSnapshot


NOW = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)


def build_snapshot() -> ShadowSnapshot:
    bars = tuple(
        CompletedMarketBar(
            s, Decimal(c), Decimal(v), Decimal(f), Decimal(w), 5,
            NOW - timedelta(minutes=15 if index % 2 == 0 else 10),
            NOW - timedelta(minutes=10 if index % 2 == 0 else 5),
            NOW - timedelta(minutes=10 if index % 2 == 0 else 5) + timedelta(seconds=1),
            NOW - timedelta(minutes=10 if index % 2 == 0 else 5) + timedelta(seconds=2),
            True,
        )
        for index, (s, c, v, f, w) in enumerate((
            ("SPY", "101", "100", "101", "100"),
            ("SPY", "102", "100", "101", "100"),
            ("QQQ", "201", "200", "201", "200"),
            ("QQQ", "202", "200", "201", "200"),
        ))
    )
    underlying = UnderlyingSignalSnapshot(
        "SOFI", Decimal("17.60"), Decimal("17.30"), Decimal("17.50"),
        Decimal("17.35"), Decimal("17.55"), Decimal("17.00"), 1800, Decimal("1000")
    )
    option = OptionCandidate(
        "SOFI", OptionType.CALL, Decimal("17.5"), date(2026, 7, 24),
        Decimal("0.47"), Decimal("0.48"), Decimal("0.47"), NOW - timedelta(seconds=1),
        6500, 5300, date(2026, 7, 29), NOW,
    )
    account = AccountSnapshot(
        "cash", Decimal("300"), Decimal("300"), 0, 0, 0, 0, 0, 0,
        Decimal("300"), Decimal("0"), Decimal("0")
    )
    return ShadowSnapshot(NOW, bars, underlying, option, account, 0, True, True, False)


class ShadowSessionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        recorder = ShadowSessionRecorder(
            "strategy_v1.0", NOW.date(), root=Path(self.temp.name)
        )
        runner = ShadowRunner(
            recorder=recorder,
            safety_config=load_safety_config("config/safety.toml"),
            strategy_policy=load_strategy_policy(),
            pilot_mode=True,
        )
        self.controller = ShadowSessionController(runner)
        self.controller.start()
        self.snapshot = build_snapshot()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def decide(self) -> None:
        result = self.controller.process_decision_snapshot(
            sample_id="decision-1",
            source_updated_at=NOW - timedelta(seconds=1),
            received_at=NOW,
            snapshot=self.snapshot,
        )
        self.assertIsNotNone(result)

    def fill(self) -> None:
        self.decide()
        result = self.controller.process_entry_quote(
            sample_id="entry-1",
            quote_updated_at=NOW,
            received_at=NOW + timedelta(seconds=1),
            observed_ask=Decimal("0.47"),
            timed_out=False,
        )
        self.assertTrue(result)

    def test_no_trade_day_can_finish_cleanly(self) -> None:
        self.controller.finish_day()
        self.assertEqual(ShadowSessionState.COMPLETE, self.controller.runner.recorder.state)

    def test_unfilled_entry_is_closed_at_session_end(self) -> None:
        self.decide()
        self.controller.finish_day()
        self.assertEqual(ShadowSessionState.COMPLETE, self.controller.runner.recorder.state)

    def test_stale_decision_sample_stops_session(self) -> None:
        result = self.controller.process_decision_snapshot(
            sample_id="stale",
            source_updated_at=NOW - timedelta(seconds=11),
            received_at=NOW,
            snapshot=self.snapshot,
        )
        self.assertIsNone(result)
        self.assertEqual(ShadowSessionState.ERROR, self.controller.runner.recorder.state)

    def test_open_position_prevents_false_day_completion(self) -> None:
        self.fill()
        with self.assertRaises(RuntimeError):
            self.controller.finish_day()

    def test_fresh_position_sample_can_complete_exit(self) -> None:
        self.fill()
        observation = PositionObservation(
            NOW + timedelta(minutes=10), NOW + timedelta(minutes=10, seconds=-1),
            Decimal("0.61"), Decimal("0.62"), True, False, False,
        )
        result = self.controller.process_position_observation(
            sample_id="position-1", observation=observation
        )
        self.assertEqual("OPTION_PROFIT_TARGET", result.reason)
        self.assertEqual(ShadowSessionState.COMPLETE, self.controller.runner.recorder.state)


if __name__ == "__main__":
    unittest.main()
