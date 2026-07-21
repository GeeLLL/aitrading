from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.official_mcp_collector import (
    READ_ONLY_ROBINHOOD_TOOLS,
    _read_only_mcp_overrides,
    OfficialCollectorError,
    collect_official_raw_snapshot,
    collect_official_shadow_snapshot,
)


class ReadOnlyMcpPolicyTests(unittest.TestCase):
    def test_unattended_policy_exposes_only_get_tools(self) -> None:
        self.assertTrue(READ_ONLY_ROBINHOOD_TOOLS)
        self.assertTrue(all(name.startswith("get_") for name in READ_ONLY_ROBINHOOD_TOOLS))
        forbidden = ("place_", "review_", "cancel_", "update_", "remove_", "add_")
        self.assertFalse(any(name.startswith(forbidden) for name in READ_ONLY_ROBINHOOD_TOOLS))

    def test_every_visible_tool_has_explicit_approval_override(self) -> None:
        serialized = " ".join(_read_only_mcp_overrides())
        self.assertIn("enabled_tools=", serialized)
        for name in READ_ONLY_ROBINHOOD_TOOLS:
            self.assertIn(
                f"tools.{name}.approval_mode=\"approve\"",
                serialized,
            )


class OfficialMcpCollectorTests(unittest.TestCase):
    def test_raw_collector_parses_json_strings_before_vault_storage(self) -> None:
        envelope = {
            "schema_version": 1,
            "source_updated_at": "2026-07-20T17:00:00Z",
            "request": json.dumps({"symbol": "SPY"}),
            "response": json.dumps({"quote": {"symbol": "SPY"}}),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "prompts").mkdir()
            (root / "config").mkdir()
            (root / "prompts/robinhood_raw_collector.md").write_text(
                "collect {symbol}", encoding="utf-8"
            )
            (root / "config/raw_mcp_snapshot.schema.json").write_text(
                "{}", encoding="utf-8"
            )

            def fake_run(command, **kwargs):
                result_path = Path(command[command.index("--output-last-message") + 1])
                result_path.write_text(json.dumps(envelope), encoding="utf-8")
                return type("Result", (), {"returncode": 0, "stderr": ""})()

            with patch("execution.official_mcp_collector.subprocess.run", side_effect=fake_run):
                receipt = collect_official_raw_snapshot(
                    "SPY", project_root=root, vault_root="logs/raw"
                )
            stored = json.loads(receipt.path.read_text(encoding="utf-8"))
            self.assertEqual({"symbol": "SPY"}, stored["request"])
            self.assertEqual("SPY", stored["response"]["quote"]["symbol"])

    def test_invalid_symbol_is_rejected_before_subprocess(self) -> None:
        with self.assertRaises(OfficialCollectorError):
            collect_official_shadow_snapshot("SPY; rm", "unused.json")

    def test_validated_output_is_written_atomically(self) -> None:
        example = Path("config/shadow_input.example.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"

            def fake_run(command, **kwargs):
                result_path = Path(command[command.index("--output-last-message") + 1])
                result_path.write_text(example, encoding="utf-8")
                return type("Result", (), {"returncode": 0})()

            with patch("execution.official_mcp_collector.subprocess.run", side_effect=fake_run) as run:
                result = collect_official_shadow_snapshot("sofi", output)
            self.assertEqual(output, result)
            self.assertEqual(1, json.loads(output.read_text())["schema_version"])
            command = run.call_args.args[0]
            self.assertIn("read-only", command)
            self.assertNotIn("dangerously-bypass-approvals-and-sandbox", command)

    def test_failed_collector_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            with patch(
                "execution.official_mcp_collector.subprocess.run",
                return_value=type("Result", (), {"returncode": 1})(),
            ):
                with self.assertRaises(OfficialCollectorError):
                    collect_official_shadow_snapshot("SPY", output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
