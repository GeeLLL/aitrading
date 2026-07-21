from __future__ import annotations

import unittest

from monitoring.shadow_activation import (
    REQUIRED_P0_CHECKS,
    evaluate_shadow_authorization,
    load_p0_qualification,
)


class ShadowActivationTests(unittest.TestCase):
    def test_owner_approval_and_all_p0_checks_are_required(self) -> None:
        checks = {name: True for name in REQUIRED_P0_CHECKS}
        self.assertFalse(evaluate_shadow_authorization(
            strategy_version="strategy_v1.0", p0_checks=checks, owner_approved=False,
        ).authorized)
        self.assertTrue(evaluate_shadow_authorization(
            strategy_version="strategy_v1.0", p0_checks=checks, owner_approved=True,
        ).authorized)

    def test_passing_claim_without_evidence_is_rejected(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        checks = {
            name: {"passed": True, "evidence": ["test evidence"]}
            for name in REQUIRED_P0_CHECKS
        }
        checks["trusted_data_plane"]["evidence"] = []
        payload = {"schema_version": 1, "strategy_version": "strategy_v1.0", "checks": checks}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qualification.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "P0_CHECK_EVIDENCE_MISSING"):
                load_p0_qualification(path, "strategy_v1.0")
