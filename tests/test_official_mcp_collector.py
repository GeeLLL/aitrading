from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.official_mcp_collector import (
    EXPLICITLY_DISALLOWED_TOOLS,
    MCP_SERVER_NAME,
    RAW_COLLECTOR_TOOLS,
    RAW_REQUIRED_TOOLS,
    READ_ONLY_ROBINHOOD_TOOLS,
    OfficialCollectorError,
    _final_json_payload,
    _freshest_source_timestamp,
    _harvest_stream,
    collect_official_raw_snapshot,
    collect_official_shadow_snapshot,
    raw_collector_allowed_tools,
    read_only_allowed_tools,
)


def _runner_stdout(final_message: str) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": final_message})


def _fake_result(returncode: int, stdout: str = "", stderr: str = ""):
    return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()


class ReadOnlyMcpPolicyTests(unittest.TestCase):
    def test_unattended_policy_exposes_only_get_tools(self) -> None:
        self.assertTrue(READ_ONLY_ROBINHOOD_TOOLS)
        self.assertTrue(all(name.startswith("get_") for name in READ_ONLY_ROBINHOOD_TOOLS))
        forbidden = ("place_", "review_", "cancel_", "update_", "remove_", "add_")
        self.assertFalse(any(name.startswith(forbidden) for name in READ_ONLY_ROBINHOOD_TOOLS))

    def test_allowed_tools_are_scoped_to_the_robinhood_server(self) -> None:
        allowed = read_only_allowed_tools().split(",")
        self.assertEqual(len(READ_ONLY_ROBINHOOD_TOOLS), len(allowed))
        for entry in allowed:
            self.assertTrue(entry.startswith(f"mcp__{MCP_SERVER_NAME}__get_"), entry)

    def test_local_mutation_tools_are_explicitly_disallowed(self) -> None:
        for tool in ("Bash", "Write", "Edit", "WebFetch"):
            self.assertIn(tool, EXPLICITLY_DISALLOWED_TOOLS)

    def test_raw_collector_allowlist_is_market_data_only(self) -> None:
        self.assertEqual(RAW_REQUIRED_TOOLS, frozenset(RAW_COLLECTOR_TOOLS))
        for name in RAW_COLLECTOR_TOOLS:
            self.assertTrue(name.startswith("get_"), name)
        for forbidden in (
            "get_accounts", "get_portfolio",
            "get_equity_positions", "get_option_positions",
            "get_equity_orders", "get_option_orders",
        ):
            self.assertNotIn(forbidden, RAW_COLLECTOR_TOOLS)
        allowed = raw_collector_allowed_tools().split(",")
        self.assertEqual(len(RAW_COLLECTOR_TOOLS), len(allowed))
        for entry in allowed:
            self.assertTrue(entry.startswith(f"mcp__{MCP_SERVER_NAME}__get_"), entry)


class FinalJsonPayloadTests(unittest.TestCase):
    def test_plain_json_final_message_is_parsed(self) -> None:
        payload = _final_json_payload(_runner_stdout('{"schema_version": 1}'))
        self.assertEqual({"schema_version": 1}, payload)

    def test_fenced_json_final_message_is_parsed(self) -> None:
        payload = _final_json_payload(_runner_stdout('```json\n{"schema_version": 1}\n```'))
        self.assertEqual({"schema_version": 1}, payload)

    def test_error_result_fails_closed(self) -> None:
        stdout = json.dumps({"type": "result", "is_error": True, "result": "{}"})
        with self.assertRaises(OfficialCollectorError):
            _final_json_payload(stdout)

    def test_prose_final_message_fails_closed(self) -> None:
        with self.assertRaises(OfficialCollectorError):
            _final_json_payload(_runner_stdout("I could not collect the data."))


PREFIX = f"mcp__{MCP_SERVER_NAME}__"


def _stream_lines(pairs, *, terminal=True, terminal_subtype="success", list_content=()):
    """Build fake claude stream-json output from (tool, input, output_text) pairs.

    ``list_content`` marks tool indexes whose result uses the list-of-blocks
    shape instead of a plain string; both occur in real streams.
    """

    lines = [json.dumps({"type": "system", "subtype": "init"})]
    for index, (tool, tool_input, output_text) in enumerate(pairs):
        use_id = f"toolu_{index}"
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "id": use_id, "name": PREFIX + tool, "input": tool_input},
            ]},
        }))
        if index in list_content:
            content: object = [{"type": "text", "text": output_text}]
        else:
            content = output_text
        lines.append(json.dumps({
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": use_id, "content": content},
            ]},
        }))
    if terminal:
        lines.append(json.dumps({
            "type": "result", "subtype": terminal_subtype,
            "is_error": terminal_subtype != "success", "result": "DONE",
        }))
    return "\n".join(lines) + "\n"


def _complete_pairs(timestamp='"2026-07-20T19:59:59.999438083Z"'):
    quote = '{"data":{"results":[{"quote":{"symbol":"SPY","last_trade_price":"742.10","venue_last_trade_time":%s}}]}}' % timestamp
    return [
        ("get_equity_quotes", {"symbols": ["SPY", "QQQ"]}, quote),
        ("get_equity_historicals", {"symbols": ["SPY", "QQQ"]}, '{"bars":[{"close":"742.1"}]}'),
        ("get_option_chains", {"symbol": "SPY"}, '{"chain":{"id":"abc"}}'),
        ("get_option_instruments", {"chain_id": "abc"}, '{"instruments":[{"strike":"742"}]}'),
        ("get_option_quotes", {"ids": ["x"]}, '{"quotes":[{"bid":"1.10","ask":"1.20"}]}'),
        ("get_earnings_results", {"symbol": "SPY"}, '{"earnings":[]}'),
    ]


class HarvestStreamTests(unittest.TestCase):
    def test_complete_stream_harvests_ordered_pairs(self) -> None:
        stdout = _stream_lines(_complete_pairs(), list_content={1})
        requests, responses, texts = _harvest_stream(stdout, PREFIX)
        self.assertEqual(6, len(requests))
        self.assertEqual(6, len(responses))
        self.assertEqual("get_equity_quotes", requests[0]["tool"])
        self.assertEqual({"symbols": ["SPY", "QQQ"]}, requests[0]["input"])
        self.assertEqual("get_earnings_results", responses[-1]["tool"])
        self.assertEqual({"earnings": []}, responses[-1]["output"])
        self.assertEqual(6, len(texts))

    def test_missing_required_tool_fails_closed(self) -> None:
        stdout = _stream_lines(_complete_pairs()[:-1])
        with self.assertRaisesRegex(OfficialCollectorError, "get_earnings_results"):
            _harvest_stream(stdout, PREFIX)

    def test_non_json_tool_result_fails_closed(self) -> None:
        pairs = _complete_pairs()
        pairs[2] = ("get_option_chains", {"symbol": "SPY"}, "[Tool result spilled to disk: too large]")
        with self.assertRaisesRegex(OfficialCollectorError, "not valid JSON"):
            _harvest_stream(_stream_lines(pairs), PREFIX)

    def test_error_tool_result_fails_closed(self) -> None:
        stdout_lines = _stream_lines(_complete_pairs()).splitlines()
        # Flag the second tool_result as an error.
        record = json.loads(stdout_lines[4])
        record["message"]["content"][0]["is_error"] = True
        stdout_lines[4] = json.dumps(record)
        with self.assertRaisesRegex(OfficialCollectorError, "error result"):
            _harvest_stream("\n".join(stdout_lines), PREFIX)

    def test_unsuccessful_terminal_result_fails_closed(self) -> None:
        stdout = _stream_lines(_complete_pairs(), terminal_subtype="error_during_execution")
        with self.assertRaisesRegex(OfficialCollectorError, "terminal result"):
            _harvest_stream(stdout, PREFIX)

    def test_missing_terminal_result_fails_closed(self) -> None:
        stdout = _stream_lines(_complete_pairs(), terminal=False)
        with self.assertRaisesRegex(OfficialCollectorError, "without a terminal result"):
            _harvest_stream(stdout, PREFIX)

    def test_unexpected_mcp_server_tool_fails_closed(self) -> None:
        stdout = _stream_lines(_complete_pairs()).replace(
            PREFIX + "get_option_quotes", "mcp__other-server__get_option_quotes"
        )
        with self.assertRaisesRegex(OfficialCollectorError, "Unexpected MCP tool"):
            _harvest_stream(stdout, PREFIX)

    def test_call_without_result_fails_closed(self) -> None:
        lines = _stream_lines(_complete_pairs()).splitlines()
        del lines[4]  # drop one tool_result
        with self.assertRaisesRegex(OfficialCollectorError, "no result"):
            _harvest_stream("\n".join(lines), PREFIX)

    def test_non_json_stream_line_fails_closed(self) -> None:
        stdout = "garbage line\n" + _stream_lines(_complete_pairs())
        with self.assertRaisesRegex(OfficialCollectorError, "non-JSON line"):
            _harvest_stream(stdout, PREFIX)


class FreshestTimestampTests(unittest.TestCase):
    def test_nanosecond_zulu_timestamp_is_parsed_and_maximum_wins(self) -> None:
        stamp = _freshest_source_timestamp([
            '{"a":"2026-07-20T13:00:00Z"}',
            '{"b":"2026-07-20T19:59:59.999438083Z"}',
            '{"c":"2026-07-20T18:00:00+00:00"}',
        ])
        self.assertEqual(2026, stamp.year)
        self.assertEqual(19, stamp.astimezone().utctimetuple().tm_hour if False else stamp.hour)
        self.assertIsNotNone(stamp.tzinfo)

    def test_no_timestamp_fails_closed(self) -> None:
        with self.assertRaisesRegex(OfficialCollectorError, "source timestamp"):
            _freshest_source_timestamp(['{"expiration_date":"2026-08-07"}', '{"n": 12345}'])

    def test_future_schedule_timestamps_are_ignored(self) -> None:
        from datetime import datetime, timezone
        collected = datetime(2026, 7, 21, 16, 34, 0, tzinfo=timezone.utc)
        stamp = _freshest_source_timestamp(
            [
                '{"venue_last_trade_time":"2026-07-21T16:33:45Z"}',
                # Option expiration/settlement datetimes lie in the future and
                # must never be selected as the observation time.
                '{"expiration_datetime":"2026-07-28T19:45:00+00:00"}',
            ],
            not_after=collected,
        )
        self.assertEqual(2026, stamp.year)
        self.assertEqual((7, 21, 16, 33, 45), (stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second))

    def test_only_future_timestamps_fails_closed(self) -> None:
        from datetime import datetime, timezone
        collected = datetime(2026, 7, 21, 16, 34, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(OfficialCollectorError, "past-or-present"):
            _freshest_source_timestamp(
                ['{"expiration_datetime":"2026-07-28T19:45:00Z"}'], not_after=collected
            )

    def test_bare_dates_do_not_count_as_timestamps(self) -> None:
        with self.assertRaises(OfficialCollectorError):
            _freshest_source_timestamp(['{"d":"2026-07-20"}'])


class OfficialMcpCollectorTests(unittest.TestCase):
    def test_raw_collector_stores_harvested_stream_in_vault(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prompts").mkdir()
            (root / "prompts/robinhood_raw_collector.md").write_text(
                "collect {symbol}", encoding="utf-8"
            )

            def fake_run(command, **kwargs):
                self.assertIn("stream-json", command)
                allowed = command[command.index("--allowedTools") + 1]
                self.assertEqual(raw_collector_allowed_tools(), allowed)
                return _fake_result(0, stdout=_stream_lines(_complete_pairs()))

            with patch("execution.official_mcp_collector.claude_binary", return_value="claude"), \
                    patch("execution.official_mcp_collector.subprocess.run", side_effect=fake_run):
                receipt = collect_official_raw_snapshot(
                    "SPY", project_root=root, vault_root="logs/raw"
                )
            stored = json.loads(receipt.path.read_text(encoding="utf-8"))
            self.assertEqual("CLAUDE_STREAM_JSON_HARVEST", stored["request"]["transport"])
            self.assertEqual("SPY", stored["request"]["symbol"])
            self.assertEqual(6, len(stored["request"]["tool_calls"]))
            self.assertEqual(6, len(stored["response"]["tool_results"]))
            self.assertEqual(
                {"earnings": []}, stored["response"]["tool_results"][-1]["output"]
            )
            self.assertIn("2026-07-20T19:59:59.999438", stored["source_updated_at"])

    def test_raw_collector_rejects_account_identifiers_via_vault(self) -> None:
        pairs = _complete_pairs()
        pairs[0] = (
            "get_equity_quotes",
            {"symbols": ["SPY"]},
            '{"account_number":"12345678","quote":{"updated_at":"2026-07-20T19:00:00Z"}}',
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prompts").mkdir()
            (root / "prompts/robinhood_raw_collector.md").write_text("collect {symbol}", encoding="utf-8")

            def fake_run(command, **kwargs):
                return _fake_result(0, stdout=_stream_lines(pairs))

            with patch("execution.official_mcp_collector.claude_binary", return_value="claude"), \
                    patch("execution.official_mcp_collector.subprocess.run", side_effect=fake_run):
                with self.assertRaises(ValueError):
                    collect_official_raw_snapshot("SPY", project_root=root, vault_root="logs/raw")
            self.assertEqual([], list((root / "logs/raw").glob("*/*.json")))

    def test_invalid_symbol_is_rejected_before_subprocess(self) -> None:
        with self.assertRaises(OfficialCollectorError):
            collect_official_shadow_snapshot("SPY; rm", "unused.json")

    def test_validated_output_is_written_atomically(self) -> None:
        example = Path("config/shadow_input.example.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"

            def fake_run(command, **kwargs):
                return _fake_result(0, stdout=_runner_stdout(example))

            with patch("execution.official_mcp_collector.claude_binary", return_value="claude"), \
                    patch("execution.official_mcp_collector.subprocess.run", side_effect=fake_run) as run:
                result = collect_official_shadow_snapshot("sofi", output)
            self.assertEqual(output, result)
            self.assertEqual(1, json.loads(output.read_text())["schema_version"])
            command = run.call_args.args[0]
            self.assertIn("--allowedTools", command)
            allowed = command[command.index("--allowedTools") + 1]
            self.assertEqual(read_only_allowed_tools(), allowed)
            self.assertIn("--disallowedTools", command)
            self.assertNotIn("--dangerously-skip-permissions", command)

    def test_failed_collector_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            with patch("execution.official_mcp_collector.claude_binary", return_value="claude"), \
                    patch(
                        "execution.official_mcp_collector.subprocess.run",
                        return_value=_fake_result(1),
                    ):
                with self.assertRaises(OfficialCollectorError):
                    collect_official_shadow_snapshot("SPY", output)
            self.assertFalse(output.exists())

    def test_invalid_snapshot_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"

            def fake_run(command, **kwargs):
                return _fake_result(0, stdout=_runner_stdout('{"schema_version": 999}'))

            with patch("execution.official_mcp_collector.claude_binary", return_value="claude"), \
                    patch("execution.official_mcp_collector.subprocess.run", side_effect=fake_run):
                with self.assertRaises(Exception):
                    collect_official_shadow_snapshot("SPY", output)
            self.assertFalse(output.exists())
            self.assertEqual([], list(Path(directory).glob("*.tmp")))


class RawCollectorPromptTests(unittest.TestCase):
    """Guard the collector prompt against the ambiguities that made the
    Claude Code sub-agent refuse (duplicate benchmark symbol, unbounded
    payloads, earnings-for-ETF, and timestamp fabrication)."""

    def _render(self, symbol: str) -> str:
        prompt = Path("prompts/robinhood_raw_collector.md").read_text(encoding="utf-8")
        return prompt.format(symbol=symbol)

    def test_benchmark_symbol_is_not_duplicated(self) -> None:
        rendered = self._render("SPY")
        self.assertNotIn("SPY, QQQ, and SPY", rendered)
        self.assertNotIn("SPY, QQQ, SPY", rendered)

    def test_prompt_renders_for_every_scheduled_symbol(self) -> None:
        for symbol in ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "SOFI", "XOM"):
            rendered = self._render(symbol)
            self.assertIn(symbol, rendered)
            self.assertNotIn("{symbol}", rendered)

    def test_prompt_bounds_payload_and_matches_harvest_contract(self) -> None:
        rendered = self._render("AAPL")
        # DTE window and strike band keep tool results inside the agent context.
        self.assertIn("21 calendar days", rendered)
        self.assertIn("5%", rendered)
        # Earnings must use the calendar tool, never the results tool.
        self.assertIn("`get_earnings_results`", rendered)
        self.assertIn("Do not use\n   `get_earnings_calendar`", rendered)
        # Harvest contract: agent never echoes data; final message is DONE.
        self.assertIn("DONE", rendered)
        self.assertIn("Do NOT repeat, summarize, or re-encode", rendered)
        # Every required tool must be named in the prompt.
        for tool in RAW_REQUIRED_TOOLS:
            self.assertIn(f"`{tool}`", rendered)
        # No account/identifier tools may be requested.
        self.assertNotIn("get_accounts", rendered)
        self.assertNotIn("get_portfolio", rendered)


if __name__ == "__main__":
    unittest.main()
