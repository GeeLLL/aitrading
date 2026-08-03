from __future__ import annotations

import unittest
from pathlib import Path

from datetime import date

from execution.official_mcp_collector import (
    BARS_PROBE_MAX_SYMBOLS,
    BARS_PROBE_REQUIRED_TOOLS,
    OfficialCollectorError,
    bars_probe_arguments,
    collect_universe_bars_probe,
)

ROOT = Path(__file__).resolve().parents[1]


class BarsProbeValidationTests(unittest.TestCase):
    """The probe exists so the DECISION inputs are hash-anchored rather than
    pulled into the agent's context and reported back. Its argument handling
    must fail closed before any tool call happens."""

    def test_empty_symbol_list_is_rejected(self):
        with self.assertRaises(OfficialCollectorError):
            collect_universe_bars_probe([])

    def test_too_many_symbols_is_rejected(self):
        with self.assertRaises(OfficialCollectorError):
            collect_universe_bars_probe(["SPY"] * (BARS_PROBE_MAX_SYMBOLS + 1))

    def test_malformed_symbols_are_rejected(self):
        for bad in (["not a symbol"], ["SPY", ""], ["SPY", "../etc/passwd"]):
            with self.assertRaises(OfficialCollectorError):
                collect_universe_bars_probe(bad)

    def test_required_tool_set_is_exactly_the_one_call(self):
        self.assertEqual(BARS_PROBE_REQUIRED_TOOLS, frozenset({"get_equity_historicals"}))


class ProbeArgumentTests(unittest.TestCase):
    """The first live attempt failed because the prompt described the call in
    prose and left the agent to invent the arguments. The shape below is taken
    from calls this system has actually made successfully, and is now computed
    in Python and handed to the agent verbatim."""

    def test_arguments_match_the_shape_the_broker_accepts(self):
        args = bars_probe_arguments(["SPY", "QQQ"], date(2026, 7, 31))
        self.assertEqual(set(args), {"bounds", "interval", "start_time", "end_time", "symbols"})
        self.assertEqual(args["bounds"], "regular")
        self.assertEqual(args["interval"], "5minute")
        self.assertEqual(args["symbols"], ["SPY", "QQQ"])

    def test_default_window_is_the_target_session_only(self):
        # VERIFIED live 2026-07-31: this is the only window shape that came back
        # successfully. A two-day window for the same two symbols overflowed the
        # harness tool-output cap ("not valid JSON (possible truncation)"), and
        # all thirteen universe symbols made the tool itself error.
        args = bars_probe_arguments(["SPY"], date(2026, 7, 31))
        self.assertEqual(args["start_time"], "2026-07-31T00:00:00Z")
        self.assertEqual(args["end_time"], "2026-08-01T00:00:00Z")

    def test_prior_sessions_can_be_requested_explicitly(self):
        args = bars_probe_arguments(["SPY"], date(2026, 7, 31), lookback_days=1)
        self.assertEqual(args["start_time"], "2026-07-30T00:00:00Z")

    def test_known_limitation_single_session_starves_the_volume_lookback(self):
        """The frozen policy averages volume over 20 completed five-minute bars.

        A single session supplies that only ~100 minutes after the open, so with
        the default window the earliest slots legitimately report
        INSUFFICIENT_VOLUME_LOOKBACK and produce no signal. That is fail-closed
        and correct, but it is a REAL coverage gap: the fix is to chunk symbols
        across several probes so a multi-session window fits under the payload
        cap, not to widen the window and hope. This test exists so the gap is
        recorded rather than forgotten.
        """
        from research.universe_features import derive_features
        policy = {"breakout_lookback_completed_bars": 6,
                  "volume_average_lookback_bars": 20}
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal
        from research.universe_features import Bar
        open_at = datetime(2026, 7, 31, 13, 30, tzinfo=timezone.utc)
        # 12 bars in = one hour after the open, still short of 20 + 1.
        bars = [
            Bar("SPY", open_at + timedelta(minutes=5 * i), Decimal("1"), Decimal("1"),
                Decimal("1"), Decimal("1"), 1000, "reg")
            for i in range(12)
        ]
        features = derive_features(bars, policy)
        self.assertIn("INSUFFICIENT_VOLUME_LOOKBACK", features["insufficient"])
        self.assertIsNone(features["volume_ratio"])

    def test_timestamps_are_utc_and_second_precision(self):
        args = bars_probe_arguments(["SPY"], date(2026, 7, 31))
        for key in ("start_time", "end_time"):
            self.assertTrue(str(args[key]).endswith("Z"), args[key])
            self.assertNotIn(".", str(args[key]))


class BarsProbePromptTests(unittest.TestCase):
    def test_prompt_carries_the_exact_arguments_and_forbids_analysis(self):
        import json as _json
        prompt = (ROOT / "prompts/robinhood_bars_probe.md").read_text(encoding="utf-8")
        args = bars_probe_arguments(["SPY", "QQQ"], date(2026, 7, 31))
        rendered = prompt.format(arguments=_json.dumps(args, indent=2))
        self.assertIn("exactly once", rendered)
        self.assertIn("EXACTLY these arguments", rendered)
        self.assertIn('"5minute"', rendered)
        self.assertIn("Do not change, widen, or narrow", rendered)
        self.assertIn("Do not split the call per", rendered)
        self.assertIn("Do not call any other tool", rendered)
        self.assertIn("Do not calculate indicators", rendered)
        self.assertIn("do NOT retry", rendered)
        # The agent must not re-encode market data into its own messages.
        self.assertIn("Do not repeat or", rendered)

    def test_prompt_forbids_every_mutating_domain(self):
        prompt = (ROOT / "prompts/robinhood_bars_probe.md").read_text(encoding="utf-8")
        for forbidden in ("order", "cancel", "transfer", "account-mutation"):
            self.assertIn(forbidden, prompt)


class BarsProbeCollectorWiringTests(unittest.TestCase):
    def test_probe_uses_a_single_tool_allowlist(self):
        source = (ROOT / "execution/official_mcp_collector.py").read_text(encoding="utf-8")
        block = source[source.index("def collect_universe_bars_probe"):]
        block = block[:block.index("\ndef ")]
        self.assertIn('f"mcp__{MCP_SERVER_NAME}__get_equity_historicals"', block)
        self.assertIn("resilient=False", block)          # never degrade decision inputs
        self.assertIn('"probe": "UNIVERSE_BARS"', block)  # marker for the adjudicator
        self.assertIn("requested_arguments", block)       # what was actually asked for

    def test_cli_registers_the_command(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('"bars-probe"', source)
        self.assertIn("def bars_probe_command", source)

    def test_default_symbols_come_from_the_universe_config(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        block = source[source.index("def bars_probe_command"):]
        block = block[:block.index("\ndef ")]
        self.assertIn("load_universe_policy", block)
        from strategy.universe import load_universe_policy
        symbols = load_universe_policy(ROOT / "config/universe.toml")["symbols"]
        self.assertLessEqual(len(symbols), BARS_PROBE_MAX_SYMBOLS)


if __name__ == "__main__":
    unittest.main()


class BarsProbeChunkingTests(unittest.TestCase):
    """2026-08-03: every pilot slot FAILED_CLOSED because all thirteen universe
    symbols went into one get_equity_historicals call and the tool errored. The
    universe must be split across payload-sized calls, and the split must never
    silently return a partial universe."""

    def _fake(self, calls, fail_on=None):
        def probe(symbols, **kwargs):
            calls.append((list(symbols), kwargs))
            if fail_on is not None and len(calls) == fail_on:
                raise OfficialCollectorError("TOOL_ERROR")
            return object()
        return probe

    def test_universe_is_split_into_payload_sized_calls(self):
        from unittest.mock import patch
        from execution.official_mcp_collector import (
            BARS_PROBE_CHUNK_SYMBOLS, collect_universe_bars_probes,
        )
        symbols = [f"SY{i}" for i in range(13)]
        calls = []
        with patch("execution.official_mcp_collector.collect_universe_bars_probe",
                   side_effect=self._fake(calls)):
            receipts = collect_universe_bars_probes(symbols)
        self.assertEqual(len(receipts), len(calls))
        self.assertTrue(all(len(c[0]) <= BARS_PROBE_CHUNK_SYMBOLS for c in calls))
        # Every symbol appears exactly once across the chunks.
        flat = [s for c in calls for s in c[0]]
        self.assertEqual(sorted(flat), sorted(symbols))

    def test_one_failed_chunk_fails_the_whole_probe_set(self):
        # A partial universe would quietly change which symbols the frozen
        # strategy could consider, which is worse than collecting nothing.
        from unittest.mock import patch
        from execution.official_mcp_collector import collect_universe_bars_probes
        calls = []
        with patch("execution.official_mcp_collector.collect_universe_bars_probe",
                   side_effect=self._fake(calls, fail_on=2)):
            with self.assertRaises(OfficialCollectorError):
                collect_universe_bars_probes([f"SY{i}" for i in range(13)])

    def test_chunk_timeouts_fit_inside_the_pilot_slot(self):
        from unittest.mock import patch
        from execution.official_mcp_collector import (
            BARS_PROBE_TOTAL_BUDGET_SECONDS, collect_universe_bars_probes,
        )
        calls = []
        with patch("execution.official_mcp_collector.collect_universe_bars_probe",
                   side_effect=self._fake(calls)):
            collect_universe_bars_probes([f"SY{i}" for i in range(13)])
        total = sum(c[1]["timeout_seconds"] for c in calls)
        self.assertLessEqual(total, BARS_PROBE_TOTAL_BUDGET_SECONDS + 60 * len(calls))
        self.assertLess(total, 720)   # the worker's PILOT_TIMEOUT_SECONDS

    def test_duplicate_symbols_do_not_buy_extra_calls(self):
        from unittest.mock import patch
        from execution.official_mcp_collector import collect_universe_bars_probes
        calls = []
        with patch("execution.official_mcp_collector.collect_universe_bars_probe",
                   side_effect=self._fake(calls)):
            collect_universe_bars_probes(["SPY", "spy", "QQQ"])
        self.assertEqual([s for c in calls for s in c[0]], ["SPY", "QQQ"])
