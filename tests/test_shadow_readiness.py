from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from monitoring.shadow_readiness import (
    MONDAY_MARKET_CHECKS,
    build_shadow_readiness,
    load_market_check_evidence,
)


class ShadowReadinessTests(unittest.TestCase):
    def test_repository_is_offline_ready_but_not_authorized(self) -> None:
        report = build_shadow_readiness(root=".")
        self.assertTrue(report.offline_ready)
        self.assertFalse(report.formal_shadow_authorized)
        self.assertFalse(report.monday_go)
        self.assertIn("FORMAL_SHADOW_NOT_AUTHORIZED", report.blockers)

    def test_market_checks_do_not_bypass_owner_authorization(self) -> None:
        report = build_shadow_readiness(
            root=".", market_checks={check: True for check in MONDAY_MARKET_CHECKS}
        )
        self.assertEqual((), report.pending_market_checks)
        self.assertFalse(report.monday_go)


class MarketCheckEvidenceTests(unittest.TestCase):
    def evidence_file(self, directory: str, checks: dict) -> Path:
        path = Path(directory) / "market_checks.json"
        path.write_text(
            json.dumps({"schema_version": 1, "checks": checks}), encoding="utf-8"
        )
        return path

    def complete_checks(self, **overrides) -> dict:
        checks = {
            name: {"passed": True, "evidence": ["logs/raw/example.json sha256:abc"]}
            for name in MONDAY_MARKET_CHECKS
        }
        checks.update(overrides)
        return checks

    def test_valid_evidence_marks_all_checks_satisfied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.evidence_file(directory, self.complete_checks())
            result = load_market_check_evidence(path)
            self.assertTrue(all(result.values()))
            report = build_shadow_readiness(root=".", market_checks=result)
            self.assertEqual((), report.pending_market_checks)

    def test_passed_without_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.evidence_file(
                directory,
                self.complete_checks(fresh_option_quote={"passed": True, "evidence": []}),
            )
            with self.assertRaisesRegex(ValueError, "MARKET_CHECK_EVIDENCE_MISSING"):
                load_market_check_evidence(path)

    def test_incomplete_check_set_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checks = self.complete_checks()
            checks.pop("fresh_option_quote")
            path = self.evidence_file(directory, checks)
            with self.assertRaisesRegex(ValueError, "MARKET_CHECK_SET_INVALID"):
                load_market_check_evidence(path)

    def test_unpassed_check_stays_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.evidence_file(
                directory,
                self.complete_checks(fresh_option_quote={"passed": False, "evidence": []}),
            )
            result = load_market_check_evidence(path)
            report = build_shadow_readiness(root=".", market_checks=result)
            self.assertEqual(("fresh_option_quote",), report.pending_market_checks)


if __name__ == "__main__":
    unittest.main()
