from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from execution.raw_data_vault import RawDataVault
from monitoring.market_checks import (
    CheckStatus,
    to_evidence_document,
    verify_market_checks,
)
from monitoring.shadow_readiness import MONDAY_MARKET_CHECKS


def _store_snapshot(root: Path, *, received: datetime, quote_ts: str | None) -> Path:
    option_output = {"quotes": [{"bid": "1.10", "ask": "1.20"}]}
    if quote_ts is not None:
        option_output["quotes"][0]["updated_at"] = quote_ts
    tool_results = [
        {"tool": "get_equity_quotes", "output": {"results": [{"symbol": "SPY"}]}},
        {"tool": "get_equity_historicals", "output": {"bars": [{"close": "742.1"}]}},
        {"tool": "get_option_chains", "output": {"chain": {"id": "abc"}}},
        {"tool": "get_option_instruments", "output": {"instruments": [{"strike": "742"}]}},
        {"tool": "get_option_quotes", "output": option_output},
        {"tool": "get_earnings_results", "output": {"earnings": []}},
    ]
    receipt = RawDataVault(root).store(
        source="ROBINHOOD_OFFICIAL_MCP",
        request={"schema_version": 1, "transport": "CLAUDE_STREAM_JSON_HARVEST", "symbol": "SPY", "tool_calls": []},
        response={"tool_results": tool_results},
        source_updated_at=received - timedelta(seconds=1),
        received_at=received,
    )
    return receipt.path


class MarketChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 7, 21, 17, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fresh_quote_snapshot_passes_market_data_checks(self) -> None:
        fresh_ts = (self.now - timedelta(seconds=3)).isoformat()
        path = _store_snapshot(self.root, received=self.now, quote_ts=fresh_ts)
        results = verify_market_checks(path)
        self.assertEqual(set(results), set(MONDAY_MARKET_CHECKS))
        self.assertEqual(results["official_raw_mcp_snapshot"].status, CheckStatus.PASS)
        self.assertEqual(results["raw_to_feature_reproducibility"].status, CheckStatus.PASS)
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.PASS)

    def test_account_and_session_are_unknown_not_pass(self) -> None:
        fresh_ts = (self.now - timedelta(seconds=3)).isoformat()
        path = _store_snapshot(self.root, received=self.now, quote_ts=fresh_ts)
        results = verify_market_checks(path)
        for name in (
            "official_instrument_session",
            "official_account_cash_reconciliation",
            "official_orders_positions_reconciliation",
        ):
            self.assertEqual(results[name].status, CheckStatus.UNKNOWN, name)
            self.assertFalse(results[name].passed)

    def test_stale_quote_fails_closed(self) -> None:
        stale_ts = (self.now - timedelta(seconds=90)).isoformat()
        path = _store_snapshot(self.root, received=self.now, quote_ts=stale_ts)
        results = verify_market_checks(path, maximum_option_quote_age_seconds=10)
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)
        self.assertIn("QUOTE_STALE", results["fresh_option_quote"].reason or "")

    def test_missing_quote_timestamp_is_unknown(self) -> None:
        path = _store_snapshot(self.root, received=self.now, quote_ts=None)
        results = verify_market_checks(path)
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.UNKNOWN)

    def test_tampered_snapshot_fails_raw_check(self) -> None:
        fresh_ts = (self.now - timedelta(seconds=3)).isoformat()
        path = _store_snapshot(self.root, received=self.now, quote_ts=fresh_ts)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["response"]["tool_results"][0]["output"]["results"][0]["symbol"] = "QQQ"
        path.write_bytes(
            json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
        results = verify_market_checks(path)
        self.assertEqual(results["official_raw_mcp_snapshot"].status, CheckStatus.FAIL)

    def test_supplied_account_reconciliation_can_pass(self) -> None:
        fresh_ts = (self.now - timedelta(seconds=3)).isoformat()
        path = _store_snapshot(self.root, received=self.now, quote_ts=fresh_ts)
        results = verify_market_checks(
            path,
            account_reconciliation={"reconciled": True, "evidence": ["settled=300; buying_power=300"]},
        )
        self.assertEqual(results["official_account_cash_reconciliation"].status, CheckStatus.PASS)

    def test_evidence_document_only_marks_pass_checks_satisfied(self) -> None:
        fresh_ts = (self.now - timedelta(seconds=3)).isoformat()
        path = _store_snapshot(self.root, received=self.now, quote_ts=fresh_ts)
        document = to_evidence_document(verify_market_checks(path))
        self.assertEqual(document["schema_version"], 1)
        checks = document["checks"]
        self.assertTrue(checks["official_raw_mcp_snapshot"]["passed"])
        self.assertFalse(checks["official_instrument_session"]["passed"])


def _store_probe(root: Path, *, received: datetime, quote_ts: str | None) -> Path:
    output = {"quotes": [{"bid": "1.10", "ask": "1.20"}]}
    if quote_ts is not None:
        output["quotes"][0]["updated_at"] = quote_ts
    receipt = RawDataVault(root).store(
        source="ROBINHOOD_OFFICIAL_MCP",
        request={
            "schema_version": 1,
            "transport": "CLAUDE_STREAM_JSON_HARVEST",
            "probe": "FRESH_OPTION_QUOTE",
            "instrument_ids": ["43103818-5340-4bbd-811c-e22e4641662e"],
            "tool_calls": [],
        },
        response={"tool_results": [{"tool": "get_option_quotes", "output": output}]},
        source_updated_at=received - timedelta(seconds=1),
        received_at=received,
    )
    return receipt.path


class InstrumentSessionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
        fresh = (self.now - timedelta(seconds=3)).isoformat()
        self.snapshot = _store_snapshot(self.root, received=self.now, quote_ts=fresh)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_valid_tradability_evidence_passes(self) -> None:
        results = verify_market_checks(self.snapshot, instrument_session={
            "tool": "get_equity_tradability",
            "symbol": "SPY",
            "active": True,
            "evidence": ["tradability=tradable; state=active"],
        })
        self.assertEqual(results["official_instrument_session"].status, CheckStatus.PASS)

    def test_wrong_tool_fails_closed(self) -> None:
        results = verify_market_checks(self.snapshot, instrument_session={
            "tool": "get_equity_quotes", "symbol": "SPY", "active": True, "evidence": ["x"],
        })
        self.assertEqual(results["official_instrument_session"].status, CheckStatus.FAIL)
        self.assertIn("WRONG_TOOL", results["official_instrument_session"].reason or "")

    def test_inactive_session_fails(self) -> None:
        results = verify_market_checks(self.snapshot, instrument_session={
            "tool": "get_equity_tradability", "symbol": "SPY", "active": False, "evidence": ["halted"],
        })
        self.assertEqual(results["official_instrument_session"].status, CheckStatus.FAIL)

    def test_missing_evidence_fails(self) -> None:
        results = verify_market_checks(self.snapshot, instrument_session={
            "tool": "get_equity_tradability", "symbol": "SPY", "active": True, "evidence": [],
        })
        self.assertEqual(results["official_instrument_session"].status, CheckStatus.FAIL)

    def test_absent_stays_unknown(self) -> None:
        results = verify_market_checks(self.snapshot)
        self.assertEqual(results["official_instrument_session"].status, CheckStatus.UNKNOWN)

    def test_symbol_mismatch_fails_closed(self) -> None:
        results = verify_market_checks(self.snapshot, instrument_session={
            "tool": "get_equity_tradability", "symbol": "QQQ", "active": True, "evidence": ["x"],
        })
        self.assertEqual(results["official_instrument_session"].status, CheckStatus.FAIL)
        self.assertIn("SYMBOL_MISMATCH", results["official_instrument_session"].reason or "")

    def test_malformed_evidence_shapes_fail_not_crash(self) -> None:
        results = verify_market_checks(
            self.snapshot,
            instrument_session="yes",
            account_reconciliation=["reconciled"],
        )
        self.assertEqual(results["official_instrument_session"].status, CheckStatus.FAIL)
        self.assertEqual(
            results["official_account_cash_reconciliation"].status, CheckStatus.FAIL
        )


class FreshQuoteProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
        # Main snapshot's quotes are 90s old — stale by the legacy measurement.
        stale = (self.now - timedelta(seconds=90)).isoformat()
        self.snapshot = _store_snapshot(self.root, received=self.now, quote_ts=stale)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _probe(self, *, quote_age_seconds: int = 2) -> tuple[Path, "datetime"]:
        probe_received = self.now + timedelta(seconds=120)
        probe = _store_probe(
            self.root / "probe", received=probe_received,
            quote_ts=(probe_received - timedelta(seconds=quote_age_seconds)).isoformat(),
        )
        return probe, probe_received

    def test_fresh_probe_overrides_stale_main_snapshot_measurement(self) -> None:
        probe, probe_received = self._probe()
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=probe,
            adjudicated_at=probe_received + timedelta(seconds=30),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.PASS)
        self.assertTrue(
            any("probe_snapshot_id=" in item for item in results["fresh_option_quote"].evidence)
        )

    def test_stale_probe_still_fails(self) -> None:
        probe, probe_received = self._probe(quote_age_seconds=45)
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=probe,
            adjudicated_at=probe_received + timedelta(seconds=30),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)
        self.assertIn("QUOTE_STALE", results["fresh_option_quote"].reason or "")

    def test_replayed_old_probe_fails_contemporaneity(self) -> None:
        # THE replay attack: a genuine, internally-fresh historical probe must
        # never adjudicate freshness for a later gate run.
        probe, probe_received = self._probe()
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=probe,
            adjudicated_at=probe_received + timedelta(minutes=30),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)
        self.assertIn("PROBE_NOT_CONTEMPORANEOUS", results["fresh_option_quote"].reason or "")

    def test_probe_predating_snapshot_fails(self) -> None:
        probe_received = self.now - timedelta(seconds=120)  # before snapshot receipt
        probe = _store_probe(
            self.root / "probe", received=probe_received,
            quote_ts=(probe_received - timedelta(seconds=2)).isoformat(),
        )
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=probe,
            adjudicated_at=probe_received + timedelta(seconds=30),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)
        self.assertIn("PROBE_PREDATES_SNAPSHOT", results["fresh_option_quote"].reason or "")

    def test_envelope_without_probe_marker_fails(self) -> None:
        # An arbitrary vault snapshot containing a get_option_quotes result is
        # NOT a probe; the marker is mandatory.
        plain = _store_snapshot(
            self.root / "plain",
            received=self.now + timedelta(seconds=120),
            quote_ts=(self.now + timedelta(seconds=118)).isoformat(),
        )
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=plain,
            adjudicated_at=self.now + timedelta(seconds=150),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)
        self.assertIn("PROBE_MARKER_MISSING", results["fresh_option_quote"].reason or "")

    def test_unindexed_probe_envelope_fails_closed(self) -> None:
        # Canonically-encoded but never store()d: wholesale fabrication.
        probe, probe_received = self._probe()
        index = self.root / "probe" / "vault_index.jsonl"
        index.write_text("", encoding="utf-8")
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=probe,
            adjudicated_at=probe_received + timedelta(seconds=30),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)
        self.assertIn("PROBE_VERIFY_FAILED", results["fresh_option_quote"].reason or "")

    def test_tampered_probe_fails_closed_never_falls_back(self) -> None:
        probe, probe_received = self._probe()
        envelope = json.loads(probe.read_text(encoding="utf-8"))
        envelope["response"]["tool_results"][0]["output"]["quotes"][0]["ask"] = "9.99"
        probe.write_bytes(
            json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=probe,
            adjudicated_at=probe_received + timedelta(seconds=30),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)
        self.assertIn("PROBE_VERIFY_FAILED", results["fresh_option_quote"].reason or "")

    def test_missing_probe_file_fails_closed(self) -> None:
        results = verify_market_checks(
            self.snapshot, fresh_quote_snapshot=self.root / "nope.json",
            adjudicated_at=self.now + timedelta(seconds=150),
        )
        self.assertEqual(results["fresh_option_quote"].status, CheckStatus.FAIL)


class ProbeCollectorValidationTests(unittest.TestCase):
    def test_rejects_bad_instrument_ids(self) -> None:
        from execution.official_mcp_collector import (
            OfficialCollectorError,
            collect_fresh_option_quote_probe,
        )
        for bad in ([], ["not-a-uuid"], ["43103818-5340-4bbd-811c-e22e4641662e"] * 7):
            with self.assertRaises(OfficialCollectorError):
                collect_fresh_option_quote_probe(bad)


class MarketChecksReadinessIntegrationTests(unittest.TestCase):
    def test_document_feeds_load_market_check_evidence(self) -> None:
        from monitoring.shadow_readiness import load_market_check_evidence

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fresh = datetime(2026, 7, 21, 17, 0, tzinfo=timezone.utc)
            path = _store_snapshot(root, received=fresh, quote_ts=(fresh - timedelta(seconds=2)).isoformat())
            document = to_evidence_document(verify_market_checks(path))
            evidence_path = root / "market_checks.json"
            evidence_path.write_text(json.dumps(document), encoding="utf-8")
            loaded = load_market_check_evidence(evidence_path)
            # Market-data checks satisfied; account/session still pending.
            self.assertTrue(loaded["official_raw_mcp_snapshot"])
            self.assertFalse(loaded["official_account_cash_reconciliation"])


if __name__ == "__main__":
    unittest.main()


def _store_session_probe(root: Path, *, received: datetime, symbol: str = "SPY",
                         tradability: str = "tradable", state: str = "active") -> Path:
    receipt = RawDataVault(root).store(
        source="ROBINHOOD_OFFICIAL_MCP",
        request={
            "schema_version": 1,
            "transport": "CLAUDE_STREAM_JSON_HARVEST",
            "probe": "INSTRUMENT_TRADABILITY",
            "symbol": symbol,
            "tool_calls": [],
        },
        response={"tool_results": [{
            "tool": "get_equity_tradability",
            "output": {"data": {"tradability": tradability, "state": state}},
        }]},
        source_updated_at=received - timedelta(seconds=1),
        received_at=received,
    )
    return receipt.path


class SessionProbeTests(unittest.TestCase):
    """The instrument-session check used to PASS on a string the agent typed.
    Harvested evidence must be required and replay-proof, like the quote probe."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
        fresh = (self.now - timedelta(seconds=3)).isoformat()
        self.snapshot = _store_snapshot(self.root, received=self.now, quote_ts=fresh)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _verify(self, probe, at_offset_seconds: int = 30):
        return verify_market_checks(
            self.snapshot, session_snapshot=probe,
            adjudicated_at=self.now + timedelta(seconds=60 + at_offset_seconds),
        )["official_instrument_session"]

    def test_harvested_tradable_probe_passes(self) -> None:
        probe = _store_session_probe(self.root / "sp", received=self.now + timedelta(seconds=60))
        result = self._verify(probe)
        self.assertEqual(result.status, CheckStatus.PASS)
        self.assertTrue(any("probe_snapshot_id=" in item for item in result.evidence))

    def test_not_tradable_fails(self) -> None:
        probe = _store_session_probe(
            self.root / "sp", received=self.now + timedelta(seconds=60), tradability="untradable",
        )
        result = self._verify(probe)
        self.assertEqual(result.status, CheckStatus.FAIL)
        self.assertEqual(result.reason, "INSTRUMENT_NOT_TRADABLE")

    def test_inactive_state_fails(self) -> None:
        probe = _store_session_probe(
            self.root / "sp", received=self.now + timedelta(seconds=60), state="halted",
        )
        result = self._verify(probe)
        self.assertEqual(result.status, CheckStatus.FAIL)
        self.assertEqual(result.reason, "INSTRUMENT_STATE_NOT_ACTIVE")

    def test_symbol_mismatch_fails(self) -> None:
        probe = _store_session_probe(
            self.root / "sp", received=self.now + timedelta(seconds=60), symbol="QQQ",
        )
        result = self._verify(probe)
        self.assertEqual(result.status, CheckStatus.FAIL)
        self.assertEqual(result.reason, "SESSION_SYMBOL_MISMATCH")

    def test_replayed_probe_fails_contemporaneity(self) -> None:
        probe = _store_session_probe(self.root / "sp", received=self.now + timedelta(seconds=60))
        result = self._verify(probe, at_offset_seconds=3600)
        self.assertEqual(result.status, CheckStatus.FAIL)
        self.assertIn("PROBE_NOT_CONTEMPORANEOUS", result.reason or "")

    def test_envelope_without_probe_marker_fails(self) -> None:
        plain = _store_snapshot(
            self.root / "plain", received=self.now + timedelta(seconds=60),
            quote_ts=(self.now + timedelta(seconds=58)).isoformat(),
        )
        result = self._verify(plain)
        self.assertEqual(result.status, CheckStatus.FAIL)
        self.assertEqual(result.reason, "PROBE_MARKER_MISSING")

    def test_probe_supersedes_supplied_evidence(self) -> None:
        # A typed claim must not be able to override a failing harvested probe.
        probe = _store_session_probe(
            self.root / "sp", received=self.now + timedelta(seconds=60), tradability="untradable",
        )
        result = verify_market_checks(
            self.snapshot,
            session_snapshot=probe,
            instrument_session={
                "tool": "get_equity_tradability", "symbol": "SPY",
                "active": True, "evidence": ["state=active"],
            },
            adjudicated_at=self.now + timedelta(seconds=90),
        )["official_instrument_session"]
        self.assertEqual(result.status, CheckStatus.FAIL)
