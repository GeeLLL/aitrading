import unittest

from monitoring.emergency_runbook import REQUIRED_STEPS, qualify_emergency_drill


class EmergencyRunbookTests(unittest.TestCase):
    def test_all_steps_required(self):
        self.assertTrue(qualify_emergency_drill({step: True for step in REQUIRED_STEPS}).complete)
        result = qualify_emergency_drill({})
        self.assertFalse(result.complete)
        self.assertEqual(set(result.missing), set(REQUIRED_STEPS))
