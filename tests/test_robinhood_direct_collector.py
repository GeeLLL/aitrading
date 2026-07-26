from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from execution.mcp_client import HttpResponse, McpClient, StaticTokenProvider
from execution.official_mcp_collector import OfficialCollectorError
from execution.robinhood_direct_collector import (
    batches,
    collect_official_raw_snapshot_direct,
    collect_via_client,
    extract_next_cursor,
    recent_completed_session,
    select_expiration,
    select_instrument_ids,
    session_window_utc,
    symbol_set,
    underlying_last_price,
)

ET = ZoneInfo("America/New_York")


class PureHelperTests(unittest.TestCase):
    def test_symbol_set_dedups_and_orders(self) -> None:
        self.assertEqual(["SPY", "QQQ"], symbol_set("SPY"))
        self.assertEqual(["SPY", "QQQ", "AMD"], symbol_set("AMD"))

    def test_recent_completed_session_uses_prior_day_before_close(self) -> None:
        # Tuesday 2026-07-21 08:00 ET (before close) -> prior session Mon 07-20.
        self.assertEqual(date(2026, 7, 20), recent_completed_session(datetime(2026, 7, 21, 8, 0, tzinfo=ET)))
        # Same day after close -> that day.
        self.assertEqual(date(2026, 7, 21), recent_completed_session(datetime(2026, 7, 21, 16, 30, tzinfo=ET)))

    def test_session_window_is_utc_and_dst_correct(self) -> None:
        # Summer (EDT, UTC-4): 09:30-16:00 ET == 13:30-20:00Z.
        self.assertEqual(("2026-07-20T13:30:00Z", "2026-07-20T20:00:00Z"), session_window_utc(date(2026, 7, 20)))
        # Winter (EST, UTC-5): 09:30-16:00 ET == 14:30-21:00Z.
        self.assertEqual(("2026-01-15T14:30:00Z", "2026-01-15T21:00:00Z"), session_window_utc(date(2026, 1, 15)))

    def test_underlying_last_price_matches_symbol(self) -> None:
        output = {"data": {"results": [
            {"quote": {"symbol": "QQQ", "last_trade_price": "600.00"}},
            {"quote": {"symbol": "SPY", "last_trade_price": "748.51"}, "close": {"symbol": "SPY", "price": "742.09"}},
        ]}}
        self.assertEqual(Decimal("748.51"), underlying_last_price(output, "SPY"))
        # Falls back to close price when last trade is absent.
        output2 = {"data": {"results": [{"quote": {"symbol": "SPY"}, "close": {"symbol": "SPY", "price": "742.09"}}]}}
        self.assertEqual(Decimal("742.09"), underlying_last_price(output2, "SPY"))
        self.assertIsNone(underlying_last_price({"data": {"results": []}}, "SPY"))

    def test_select_expiration_nearest_in_window(self) -> None:
        today = date(2026, 7, 20)
        dates = ["2026-07-21", "2026-07-24", "2026-07-28", "2026-08-14"]  # DTE 1, 4, 8, 25
        self.assertEqual("2026-07-28", select_expiration(dates, today))  # nearest within [7,21]
        self.assertIsNone(select_expiration(["2026-07-21", "2026-08-30"], today))  # none in window

    def test_extract_next_cursor(self) -> None:
        url = "http://internal/options/instruments/?chain_id=abc&cursor=cD03MDkuMDAwMA%3D%3D&state=active"
        self.assertEqual("cD03MDkuMDAwMA==", extract_next_cursor(url))
        self.assertIsNone(extract_next_cursor(None))
        self.assertIsNone(extract_next_cursor(""))

    def test_select_instrument_ids_band_and_cap(self) -> None:
        instruments = [
            {"id": "a", "strike_price": "700.0000"},   # in band (742*0.95=704.9? -> 700 is just out)
            {"id": "b", "strike_price": "740.0000"},   # in band, nearest
            {"id": "c", "strike_price": "760.0000"},   # in band
            {"id": "d", "strike_price": "900.0000"},   # out of band
            {"id": "e"},                                # no strike -> ignored
        ]
        ids = select_instrument_ids(instruments, Decimal("742.09"))
        self.assertEqual(["b", "c"], ids)  # 700 is below 704.99 floor; nearest-first ordering
        # Cap is honored.
        many = [{"id": str(i), "strike_price": f"{742 + i * 0.1:.4f}"} for i in range(200)]
        self.assertEqual(5, len(select_instrument_ids(many, Decimal("742.09"), cap=5)))
        # No underlying price -> empty (caller fails closed).
        self.assertEqual([], select_instrument_ids(instruments, None))

    def test_batches(self) -> None:
        self.assertEqual([["a", "b"], ["c"]], batches(["a", "b", "c"], size=2))
        self.assertEqual([], batches([]))


# --------------------------------------------------------------------------- #
# Canned responses mirroring the real captured shapes.
# --------------------------------------------------------------------------- #

def _canned_outputs():
    chain_id = "c277b118"
    instruments_p1 = {"data": {"instruments": [
        {"id": "i1", "strike_price": "740.0000", "type": "call"},
        {"id": "i2", "strike_price": "745.0000", "type": "put"},
    ], "next": "http://x/?chain_id=%s&cursor=PAGE2&state=active" % chain_id}}
    instruments_p2 = {"data": {"instruments": [
        {"id": "i3", "strike_price": "750.0000", "type": "call"},
        {"id": "i9", "strike_price": "900.0000", "type": "call"},  # out of band
    ], "next": None}}
    return {
        "get_equity_quotes": {"data": {"results": [
            {"quote": {"symbol": "SPY", "last_trade_price": "748.51",
                       "venue_last_trade_time": "2026-07-21T16:48:24.75Z"},
             "close": {"symbol": "SPY", "price": "742.09"}},
        ]}},
        "get_equity_historicals": {"data": {"results": [{"symbol": "SPY", "bars": []}]}},
        "get_option_chains": {"data": {"chains": [
            {"id": chain_id, "symbol": "SPY", "expiration_dates": ["2026-07-21", "2026-07-28", "2026-08-30"]},
        ]}},
        "get_option_instruments": [instruments_p1, instruments_p2],  # paginated
        "get_option_quotes": {"data": {"results": [{"quote": {"bid_price": "1.10", "ask_price": "1.20"}}]}},
        "get_earnings_results": {"data": {"results": []}},
    }


class FakeMcpClient:
    """Returns canned tool outputs; get_option_instruments paginates by call order."""

    def __init__(self, outputs) -> None:
        self._outputs = outputs
        self._instr_page = 0
        self.calls: list[tuple[str, dict]] = []

    def initialize(self):
        return {}

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "get_option_instruments":
            pages = self._outputs[name]
            output = pages[min(self._instr_page, len(pages) - 1)]
            self._instr_page += 1
        else:
            output = self._outputs[name]
        return {"content": [{"type": "text", "text": json.dumps(output)}]}


class OrchestrationTests(unittest.TestCase):
    NOW = datetime(2026, 7, 21, 8, 0, tzinfo=ET)  # prior session = 2026-07-20

    def test_full_sequence_and_pagination(self) -> None:
        client = FakeMcpClient(_canned_outputs())
        requests, responses, texts = collect_via_client(client, "SPY", self.NOW)
        tools = [request["tool"] for request in requests]
        # Every required tool appears; instruments paginated twice; quotes once.
        self.assertEqual(1, tools.count("get_equity_quotes"))
        self.assertEqual(2, tools.count("get_option_instruments"))
        self.assertGreaterEqual(tools.count("get_option_quotes"), 1)
        self.assertIn("get_earnings_results", tools)
        # The second instruments call carried the pagination cursor.
        instr_calls = [args for name, args in client.calls if name == "get_option_instruments"]
        self.assertEqual("PAGE2", instr_calls[1]["cursor"])
        # Selected instrument ids are within the ±5% band (i9 @ 900 excluded).
        quote_call = next(args for name, args in client.calls if name == "get_option_quotes")
        self.assertNotIn("i9", quote_call["instrument_ids"])
        self.assertIn("i1", quote_call["instrument_ids"])
        self.assertEqual(len(requests), len(responses))

    def test_no_expiration_in_window_fails_closed(self) -> None:
        outputs = _canned_outputs()
        outputs["get_option_chains"] = {"data": {"chains": [{"id": "c", "expiration_dates": ["2026-07-21"]}]}}
        client = FakeMcpClient(outputs)
        with self.assertRaisesRegex(OfficialCollectorError, "NO_EXPIRATION_IN_WINDOW"):
            collect_via_client(client, "SPY", self.NOW)

    def test_no_instruments_in_band_fails_closed(self) -> None:
        outputs = _canned_outputs()
        outputs["get_option_instruments"] = [{"data": {"instruments": [{"id": "z", "strike_price": "50.0"}], "next": None}}]
        client = FakeMcpClient(outputs)
        with self.assertRaisesRegex(OfficialCollectorError, "NO_INSTRUMENTS_IN_STRIKE_BAND"):
            collect_via_client(client, "SPY", self.NOW)


class FakeRobinhoodTransport:
    """A transport that drives a real McpClient with canned tool outputs, for an
    end-to-end store test through the actual protocol code."""

    def __init__(self, outputs) -> None:
        self._outputs = outputs
        self._instr_page = 0

    def post(self, url, body, headers) -> HttpResponse:
        payload = json.loads(body.decode("utf-8"))
        method = payload.get("method")
        if method == "notifications/initialized":
            return HttpResponse(status=202, headers={}, text="")
        if method == "initialize":
            return HttpResponse(
                status=200,
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "s1"},
                text=json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {}}),
            )
        # tools/call
        name = payload["params"]["name"]
        if name == "get_option_instruments":
            pages = self._outputs[name]
            output = pages[min(self._instr_page, len(pages) - 1)]
            self._instr_page += 1
        else:
            output = self._outputs[name]
        result = {"content": [{"type": "text", "text": json.dumps(output)}]}
        return HttpResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            text=json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}),
        )


class EndToEndStoreTests(unittest.TestCase):
    def test_direct_snapshot_stores_with_direct_transport_tag(self) -> None:
        transport = FakeRobinhoodTransport(_canned_outputs())
        with tempfile.TemporaryDirectory() as vault:
            receipt = collect_official_raw_snapshot_direct(
                "SPY",
                token_provider=StaticTokenProvider("test-token"),
                transport=transport,
                project_root=".",
                vault_root=Path(vault),
                now=datetime(2026, 7, 21, 8, 0, tzinfo=ET),
            )
            envelope = json.loads(Path(receipt.path).read_text(encoding="utf-8"))
            self.assertEqual("PYTHON_DIRECT_MCP", envelope["request"]["transport"])
            self.assertEqual("SPY", envelope["request"]["symbol"])
            self.assertFalse(envelope["request"]["partial"])
            tools = {tc["tool"] for tc in envelope["request"]["tool_calls"]}
            self.assertIn("get_earnings_results", tools)
            self.assertIn("get_option_quotes", tools)


if __name__ == "__main__":
    unittest.main()
