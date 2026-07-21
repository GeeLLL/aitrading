from __future__ import annotations

import unittest

from monitoring.shadow_readiness import MONDAY_MARKET_CHECKS, build_shadow_readiness


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


if __name__ == "__main__":
    unittest.main()
