from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from execution.raw_data_vault import RawDataVault


class RawDataVaultTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = RawDataVault(Path(directory)).store(
                source="ROBINHOOD_OFFICIAL_MCP",
                request={"symbol": "SPY"},
                response={"bid": "1.00", "ask": "1.01"},
                source_updated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
            )
            self.assertTrue(receipt.path.exists())
            verified = RawDataVault.verify(receipt.path, receipt.content_sha256)
            self.assertEqual(receipt.content_sha256, verified.content_sha256)

    def test_modified_snapshot_fails_expected_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = RawDataVault(directory).store(
                source="MCP", request={"symbol": "SPY"}, response={"bid": "1"},
                source_updated_at=datetime.now(timezone.utc),
            )
            envelope = json.loads(receipt.path.read_text(encoding="utf-8"))
            envelope["response"]["bid"] = "2"
            receipt.path.write_text(
                json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "RAW_SNAPSHOT_HASH_MISMATCH"):
                RawDataVault.verify(receipt.path, receipt.content_sha256)
            self.assertEqual(64, len(receipt.content_sha256))

    def test_sensitive_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                RawDataVault(Path(directory)).store(
                    source="MCP", request={}, response={"account_number": "forbidden"},
                    source_updated_at=datetime.now(timezone.utc),
                )

    def test_store_appends_hash_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = RawDataVault(Path(directory))
            receipt = vault.store(
                source="MCP", request={"symbol": "SPY"}, response={"bid": "1"},
                source_updated_at=datetime.now(timezone.utc),
            )
            index_lines = (Path(directory) / "vault_index.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(index_lines))
            entry = json.loads(index_lines[0])
            self.assertEqual(receipt.snapshot_id, entry["snapshot_id"])
            self.assertEqual(receipt.content_sha256, entry["content_sha256"])

    def test_in_place_rewrite_is_detected_without_operator_supplied_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = RawDataVault(Path(directory)).store(
                source="MCP", request={"symbol": "SPY"}, response={"bid": "1"},
                source_updated_at=datetime.now(timezone.utc),
            )
            envelope = json.loads(receipt.path.read_text(encoding="utf-8"))
            envelope["response"]["bid"] = "2"
            # Rewrite in canonical form so only the ledger can expose the edit.
            receipt.path.write_bytes(
                json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            )
            with self.assertRaisesRegex(ValueError, "RAW_SNAPSHOT_INDEX_MISMATCH"):
                RawDataVault.verify(receipt.path)
