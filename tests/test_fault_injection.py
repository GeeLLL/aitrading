import unittest

from monitoring.fault_injection import FaultOutcome, expected_fault_matrix, qualify_fault_matrix


class FaultInjectionTests(unittest.TestCase):
    def test_expected_matrix_qualifies(self):
        safe, reasons = qualify_fault_matrix(expected_fault_matrix())
        self.assertTrue(safe)
        self.assertEqual(reasons, ())

    def test_missing_fault_blocks(self):
        safe, reasons = qualify_fault_matrix((FaultOutcome("OAUTH_EXPIRED", "HALT", True),))
        self.assertFalse(safe)
        self.assertTrue(any(reason.startswith("FAULT_NOT_TESTED") for reason in reasons))
