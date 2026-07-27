from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.eod_report import (
    reconstruct_trade,
    round_trip_friction_usd,
    load_trajectories,
)


FRICTION = Decimal("1.40")  # 0.15*2 + 0.10 + 1 tick * 0.01 * 100


def event(event_type: str, *, received: str, bid=None, ask=None, reasons=None, labels=None) -> dict:
    return {
        "schema_version": 1,
        "trajectory_id": "t-1",
        "event_type": event_type,
        "policy_labels": labels or ["BASE_25"],
        "underlying": "SPY",
        "option_type": "call",
        "strike": 630.0,
        "expiration_date": "2026-08-07",
        "quote_received_at": received,
        "bid": bid,
        "ask": ask,
        "rejection_reasons": reasons or [],
    }


class FrictionTests(unittest.TestCase):
    def test_matches_shadow_runner_semantics(self):
        config = {"friction_model": {
            "per_contract_fee_usd": 0.15,
            "regulatory_exit_fee_usd": 0.10,
            "exit_latency_slippage_ticks": 1,
            "option_tick_size_usd": 0.01,
        }}
        self.assertEqual(round_trip_friction_usd(config), FRICTION)


class ReconstructTradeTests(unittest.TestCase):
    def test_rejected_candidate_is_no_trade(self):
        events = [event("CANDIDATE", received="T1", ask=2.0, reasons=["PREMIUM_OVER_STAGE1_CAP"])]
        trade = reconstruct_trade(events, FRICTION)
        self.assertEqual(trade["outcome"], "REJECTED_NO_TRADE")
        self.assertIsNone(trade["net_pnl_usd"])

    def test_no_later_quote_at_or_below_limit_means_no_fill(self):
        events = [
            event("CANDIDATE", received="2026-07-27T17:00:00Z", ask=2.00),
            event("QUOTE", received="2026-07-27T17:05:00Z", ask=2.10),
        ]
        trade = reconstruct_trade(events, FRICTION)
        self.assertEqual(trade["outcome"], "NO_FILL")

    def test_filled_and_exited_computes_deterministic_net(self):
        events = [
            event("CANDIDATE", received="2026-07-27T17:00:00Z", ask=2.00),
            event("QUOTE", received="2026-07-27T17:05:00Z", ask=1.95),
            event("HORIZON_CLOSE", received="2026-07-27T17:30:00Z", bid=2.25),
        ]
        trade = reconstruct_trade(events, FRICTION)
        self.assertEqual(trade["outcome"], "FILLED_AND_EXITED")
        self.assertEqual(trade["entry_fill"], 1.95)
        self.assertEqual(trade["exit_bid"], 2.25)
        # gross = (2.25 - 1.95) * 100 = 30.00 ; net = 30.00 - 1.40 = 28.60
        self.assertAlmostEqual(trade["gross_pnl_usd"], 30.00)
        self.assertAlmostEqual(trade["net_pnl_usd"], 28.60)

    def test_losing_trade_subtracts_friction_too(self):
        events = [
            event("CANDIDATE", received="2026-07-27T17:00:00Z", ask=2.00),
            event("QUOTE", received="2026-07-27T17:05:00Z", ask=2.00),
            event("HORIZON_CLOSE", received="2026-07-27T17:30:00Z", bid=1.80),
        ]
        trade = reconstruct_trade(events, FRICTION)
        # gross = -20.00 ; net = -21.40
        self.assertAlmostEqual(trade["gross_pnl_usd"], -20.00)
        self.assertAlmostEqual(trade["net_pnl_usd"], -21.40)

    def test_quote_before_candidate_never_fills(self):
        events = [
            event("QUOTE", received="2026-07-27T16:55:00Z", ask=1.50),
            event("CANDIDATE", received="2026-07-27T17:00:00Z", ask=2.00),
        ]
        trade = reconstruct_trade(events, FRICTION)
        self.assertEqual(trade["outcome"], "NO_FILL")

    def test_fill_without_horizon_close_is_incomplete_not_pnl(self):
        events = [
            event("CANDIDATE", received="2026-07-27T17:00:00Z", ask=2.00),
            event("QUOTE", received="2026-07-27T17:05:00Z", ask=1.90),
        ]
        trade = reconstruct_trade(events, FRICTION)
        self.assertEqual(trade["outcome"], "FILLED_NO_HORIZON_CLOSE")
        self.assertIsNone(trade["net_pnl_usd"])

    def test_null_ask_on_candidate_is_no_limit(self):
        events = [event("CANDIDATE", received="T1", ask=None)]
        trade = reconstruct_trade(events, FRICTION)
        self.assertEqual(trade["outcome"], "NO_LIMIT_PRICE")


class LoadTrajectoriesTests(unittest.TestCase):
    def test_malformed_files_become_warnings_not_crashes(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "good.json").write_text(json.dumps(
                event("CANDIDATE", received="T1", ask=1.0)
            ), encoding="utf-8")
            (directory / "broken.json").write_text("{not json", encoding="utf-8")
            (directory / "noid.json").write_text("{}", encoding="utf-8")
            warnings: list[str] = []
            groups = load_trajectories(directory, warnings)
            self.assertEqual(list(groups.keys()), ["t-1"])
            self.assertEqual(len(warnings), 2)


if __name__ == "__main__":
    unittest.main()
