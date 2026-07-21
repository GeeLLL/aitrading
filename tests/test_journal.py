from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from journal.writer import (
    JournalValidationError,
    REQUIRED_TRADE_FIELDS,
    append_trade_record,
    validate_trade_record,
)


def complete_record() -> dict[str, object]:
    record = {field: None for field in REQUIRED_TRADE_FIELDS}
    record.update(
        {
            "trade_id": "shadow-0001",
            "strategy_version": "strategy_v1.0",
            "timestamp": datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc),
            "underlying": "SPY",
            "option_type": "CALL",
            "strike": Decimal("750"),
            "expiration": "2026-07-24",
            "quantity": 1,
            "bid": Decimal("0.60"),
            "ask": Decimal("0.62"),
            "mid": Decimal("0.61"),
            "limit_price": Decimal("0.62"),
            "rule_violations": [],
            "system_errors": [],
        }
    )
    return record


class JournalWriterTests(unittest.TestCase):
    def test_complete_record_is_valid(self) -> None:
        validate_trade_record(complete_record())

    def test_missing_field_is_rejected(self) -> None:
        record = complete_record()
        del record["net_pnl"]
        with self.assertRaises(JournalValidationError):
            validate_trade_record(record)

    def test_account_number_is_rejected_at_any_depth(self) -> None:
        record = complete_record()
        record["market_context"] = {"broker": {"account_number": "secret"}}
        with self.assertRaises(JournalValidationError):
            validate_trade_record(record)

    def test_token_is_rejected(self) -> None:
        record = complete_record()
        record["system_errors"] = [{"oauth_token": "secret"}]
        with self.assertRaises(JournalValidationError):
            validate_trade_record(record)

    def test_append_writes_one_json_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trades.jsonl"
            append_trade_record(complete_record(), path)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines))
            payload = json.loads(lines[0])
            self.assertEqual("shadow-0001", payload["record"]["trade_id"])


if __name__ == "__main__":
    unittest.main()
