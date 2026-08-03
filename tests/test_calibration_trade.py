from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.calibration_trade import (
    entry_allowed,
    entry_record,
    exit_due,
    exit_record,
    ranked_symbols,
    select_calibration_contract,
    write_once,
)

NOW = datetime(2026, 8, 3, 17, 3, tzinfo=timezone.utc)


def _inst(ident, strike, otype="call", exp="2026-08-17"):
    return {"id": ident, "chain_symbol": "SPY", "type": otype,
            "strike_price": str(strike), "expiration_date": exp}


def _quote(ident, delta, mark, vol, oi, bid="1.00", ask="1.10"):
    return {"instrument_id": ident, "delta": str(delta),
            "adjusted_mark_price": str(mark), "volume": vol, "open_interest": oi,
            "bid_price": bid, "ask_price": ask, "implied_volatility": "0.2"}


def _envelope(pairs):
    return {"response": {"tool_results": [
        {"tool": "get_option_instruments",
         "output": {"data": {"instruments": [p[0] for p in pairs]}}},
        {"tool": "get_option_quotes",
         "output": {"data": {"results": [{"quote": p[1]} for p in pairs]}}},
    ]}}


class SelectionTests(unittest.TestCase):
    def test_prefers_lowest_band_then_delta_then_oi(self) -> None:
        pairs = [
            (_inst("a", 740), _quote("a", 0.50, "0.70", 500, 500)),   # band 75, |d-0.5|=0
            (_inst("b", 745), _quote("b", 0.48, "1.00", 500, 500)),   # band 120
            (_inst("c", 750), _quote("c", 0.52, "0.70", 500, 900)),   # band 75, |d-0.5|=0.02
        ]
        selection = select_calibration_contract(_envelope(pairs))
        self.assertIsNotNone(selection)
        instrument, _quote_row, band = selection
        self.assertEqual("a", instrument["id"])
        self.assertEqual(75, band)

    def test_liquidity_floor_is_hard(self) -> None:
        pairs = [(_inst("z", 740), _quote("z", 0.50, "0.70", 0, 0))]  # the IWM lesson
        self.assertIsNone(select_calibration_contract(_envelope(pairs)))

    def test_delta_band_and_premium_cap(self) -> None:
        pairs = [
            (_inst("lo", 740), _quote("lo", 0.10, "0.70", 500, 500)),   # delta out
            (_inst("hi", 745), _quote("hi", 0.50, "4.00", 500, 500)),   # $400 > $300
        ]
        self.assertIsNone(select_calibration_contract(_envelope(pairs)))

    def test_put_uses_abs_delta(self) -> None:
        pairs = [(_inst("p", 740, "put"), _quote("p", -0.45, "0.70", 500, 500))]
        self.assertIsNotNone(select_calibration_contract(_envelope(pairs)))


class LifecycleTests(unittest.TestCase):
    def test_ranked_symbols_by_volume_ratio(self) -> None:
        decision = {"symbols": {"SPY": {"volume_ratio": 1.2},
                                "NVDA": {"volume_ratio": 2.4},
                                "AMD": {"volume_ratio": None}}}
        self.assertEqual(["NVDA", "SPY"], ranked_symbols(decision))

    def test_entry_window_and_exit_due(self) -> None:
        self.assertTrue(entry_allowed((7, 3)))
        self.assertTrue(entry_allowed((11, 3)))
        self.assertFalse(entry_allowed((11, 23)))
        entry = {"entry_observed_at": NOW.isoformat()}
        self.assertIsNone(exit_due(entry, NOW + timedelta(minutes=20), (10, 3)))
        self.assertEqual("HORIZON_40_MIN", exit_due(entry, NOW + timedelta(minutes=41), (10, 3)))
        self.assertEqual("FORCED_LAST_PILOT_SLOT", exit_due(entry, NOW + timedelta(minutes=20), (11, 23)))

    def test_records_have_required_fields_and_write_once(self) -> None:
        pairs = [(_inst("a", 740), _quote("a", 0.50, "0.70", 500, 500))]
        instrument, quote, band = select_calibration_contract(_envelope(pairs))
        entry = entry_record(run_id="r1", symbol="SPY", instrument=instrument,
                             quote=quote, premium_band=band, observed_at=NOW,
                             source_updated_at="2026-08-03T17:02:58Z")
        for field in ("instrument_id", "entry_ask", "entry_bid", "premium_band",
                      "delta", "volume", "open_interest", "evidence_class"):
            self.assertIn(field, entry)
        self.assertEqual("CALIBRATION_EXCLUDED_FROM_PERFORMANCE", entry["evidence_class"])
        exit_rec = exit_record(run_id="r2", entry=entry,
                               quote=_quote("a", 0.5, "0.80", 400, 500, bid="0.75"),
                               observed_at=NOW + timedelta(minutes=41),
                               exit_reason="HORIZON_40_MIN")
        self.assertEqual(0.75, exit_rec["exit_bid"])
        self.assertEqual(41.0, exit_rec["holding_minutes"])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "entry.json"
            self.assertTrue(write_once(path, entry))
            self.assertFalse(write_once(path, entry))  # never overwrites


if __name__ == "__main__":
    unittest.main()
