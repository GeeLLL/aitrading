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
from strategy.shadow_pipeline import OptionCandidate, PipelineStatus
from strategy.shadow_runner import PositionObservation, ShadowRunner, ShadowSnapshot
from strategy.trade_management import ExitAction
from strategy.underlying_signal import UnderlyingSignalSnapshot


NOW = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)


class ShadowRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        recorder = ShadowSessionRecorder(
            strategy_version="strategy_v1.0",
            session_date=NOW.date(),
            root=Path(self.temp.name),
        )
        self.runner = ShadowRunner(
            recorder=recorder,
            safety_config=load_safety_config("config/safety.toml"),
            strategy_policy=load_strategy_policy(),
            pilot_mode=True,
        )
        bars = tuple(
            CompletedMarketBar(
                symbol, Decimal(close), Decimal(vwap), Decimal(fast), Decimal(slow),
                5,
                NOW - timedelta(minutes=15 if index % 2 == 0 else 10),
                NOW - timedelta(minutes=10 if index % 2 == 0 else 5),
                NOW - timedelta(minutes=10 if index % 2 == 0 else 5) + timedelta(seconds=1),
                NOW - timedelta(minutes=10 if index % 2 == 0 else 5) + timedelta(seconds=2),
                True,
            )
            for index, (symbol, close, vwap, fast, slow) in enumerate((
                ("SPY", "101", "100", "101", "100"),
                ("SPY", "102", "100", "101", "100"),
                ("QQQ", "201", "200", "201", "200"),
                ("QQQ", "202", "200", "201", "200"),
            ))
        )
        underlying = UnderlyingSignalSnapshot(
            "SOFI", Decimal("17.60"), Decimal("17.30"), Decimal("17.50"),
            Decimal("17.35"), Decimal("17.55"), Decimal("17.00"), 1800,
            Decimal("1000"),
        )
        option = OptionCandidate(
            "SOFI", OptionType.CALL, Decimal("17.5"), date(2026, 7, 24),
            Decimal("0.47"), Decimal("0.48"), Decimal("0.47"),
            NOW - timedelta(seconds=2), 6500, 5300, date(2026, 7, 29),
            NOW - timedelta(seconds=1),
        )
        account = AccountSnapshot(
            "cash", Decimal("300"), Decimal("300"), 0, 0, 0, 0, 0, 0,
            Decimal("300"), Decimal("0"), Decimal("0")
        )
        self.snapshot = ShadowSnapshot(
            NOW, bars, underlying, option, account, 0, True, True, False
        )
        self.runner.start()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_approved_decision_creates_simulated_pending_entry(self) -> None:
        decision = self.runner.evaluate(self.snapshot)
        self.assertEqual(PipelineStatus.PILOT_SIMULATED_ENTRY, decision.status)
        self.assertEqual(ShadowSessionState.ENTRY_PENDING, self.runner.recorder.state)
        self.assertEqual(Decimal("0.47"), self.runner.pending_limit_price)

    def test_observed_ask_can_fill_simulation(self) -> None:
        self.runner.evaluate(self.snapshot)
        self.assertTrue(self.runner.observe_entry(observed_at=NOW, observed_ask=Decimal("0.47"), timed_out=False))
        self.assertEqual(ShadowSessionState.POSITION_OPEN, self.runner.recorder.state)

    def test_timeout_never_assumes_fill(self) -> None:
        self.runner.evaluate(self.snapshot)
        self.assertFalse(self.runner.observe_entry(observed_at=NOW, observed_ask=Decimal("0.48"), timed_out=True))
        self.assertEqual(ShadowSessionState.COMPLETE, self.runner.recorder.state)

    def test_no_pending_entry_cannot_be_observed(self) -> None:
        with self.assertRaises(RuntimeError):
            self.runner.observe_entry(observed_at=NOW, observed_ask=Decimal("0.47"), timed_out=False)

    def open_position(self) -> None:
        self.runner.evaluate(self.snapshot)
        self.runner.observe_entry(observed_at=NOW, observed_ask=Decimal("0.47"), timed_out=False)

    def observation(self, **overrides) -> PositionObservation:
        values = {
            "observed_at": NOW + timedelta(minutes=10),
            "quote_updated_at": NOW + timedelta(minutes=10, seconds=-1),
            "current_bid": Decimal("0.46"),
            "current_mark": Decimal("0.47"),
            "underlying_vwap_still_valid": True,
            "force_exit_due": False,
            "emergency_exit_due": False,
        }
        values.update(overrides)
        return PositionObservation(**values)

    def test_open_position_can_be_held(self) -> None:
        self.open_position()
        decision = self.runner.observe_position(self.observation())
        self.assertEqual(ExitAction.HOLD, decision.action)
        self.assertEqual(ShadowSessionState.POSITION_OPEN, self.runner.recorder.state)

    def test_profit_target_exits_and_records_complete_session(self) -> None:
        self.open_position()
        decision = self.runner.observe_position(
            self.observation(current_bid=Decimal("0.61"), current_mark=Decimal("0.62"))
        )
        self.assertEqual("OPTION_PROFIT_TARGET", decision.reason)
        self.assertEqual(ShadowSessionState.COMPLETE, self.runner.recorder.state)
        self.assertIsNone(self.runner.entry_fill_price)

    def test_forced_exit_requires_an_existing_position(self) -> None:
        with self.assertRaises(RuntimeError):
            self.runner.observe_position(self.observation(force_exit_due=True))

    def test_stale_quote_enters_error_state_instead_of_exiting(self) -> None:
        self.open_position()
        decision = self.runner.observe_position(
            self.observation(quote_updated_at=NOW - timedelta(seconds=30), force_exit_due=True)
        )
        self.assertEqual(ExitAction.MANUAL_INTERVENTION, decision.action)
        self.assertEqual(ShadowSessionState.ERROR, self.runner.recorder.state)


if __name__ == "__main__":
    unittest.main()
