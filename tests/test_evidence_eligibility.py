import unittest

from journal.evidence_eligibility import classify_shadow_evidence


class EvidenceEligibilityTests(unittest.TestCase):
    def valid_record(self):
        return {
            "event": "SHADOW_SAMPLE", "status": "COMPLETED", "missing": [], "stale": [], "anomalies": [],
            "governance": {"formal_shadow_authorized": True, "performance_eligibility": "FORMAL_SHADOW_ELIGIBLE"},
            "safety": {"system_mode": "READ_ONLY", "live_trading_enabled": False, "order_tools_enabled": False},
            "rule_violations": {"operational": 0},
        }

    def test_formal_clean_record_is_eligible(self):
        result = classify_shadow_evidence(self.valid_record())
        self.assertTrue(result.eligible)
        self.assertEqual(result.population, "FORMAL_SHADOW")

    def test_pilot_and_stale_are_ineligible(self):
        value = self.valid_record() | {"event": "SHADOW_PILOT_SAMPLE", "stale": [{"code": "STALE"}]}
        result = classify_shadow_evidence(value)
        self.assertFalse(result.eligible)
        self.assertEqual(result.population, "PILOT")
        self.assertIn("STALE_DATA_PRESENT", result.reasons)
