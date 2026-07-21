from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from execution.shadow_input import InvalidShadowInputError, load_shadow_input


EXAMPLE = Path("config/shadow_input.example.json")


class ShadowInputTests(unittest.TestCase):
    def load_raw(self) -> dict:
        return json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def write(self, raw: dict) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "input.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_example_is_valid(self) -> None:
        sample_id, source_time, snapshot = load_shadow_input(EXAMPLE)
        self.assertTrue(sample_id)
        self.assertIsNotNone(source_time.utcoffset())
        self.assertEqual("SOFI", snapshot.underlying.symbol)

    def test_account_number_is_rejected(self) -> None:
        raw = self.load_raw()
        raw["account"]["account_number"] = "forbidden"
        with self.assertRaises(InvalidShadowInputError):
            load_shadow_input(self.write(raw))

    def test_missing_required_field_is_rejected(self) -> None:
        raw = self.load_raw()
        del raw["option"]["bid"]
        with self.assertRaises(InvalidShadowInputError):
            load_shadow_input(self.write(raw))

    def test_naive_timestamp_is_rejected(self) -> None:
        raw = self.load_raw()
        raw["received_at"] = "2026-07-17T15:00:00"
        with self.assertRaises(InvalidShadowInputError):
            load_shadow_input(self.write(raw))

    def test_non_boolean_market_state_is_rejected(self) -> None:
        raw = self.load_raw()
        raw["market"]["market_open"] = "true"
        with self.assertRaises(InvalidShadowInputError):
            load_shadow_input(self.write(raw))


if __name__ == "__main__":
    unittest.main()
