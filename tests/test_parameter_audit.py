from __future__ import annotations

import unittest

from research.parameter_audit import MANDATORY_PARAMETERS, audit_parameters


class ParameterAuditTests(unittest.TestCase):
    def test_every_parameter_needs_value_and_evidence_version(self) -> None:
        values={name: 1 for name in MANDATORY_PARAMETERS}
        result=audit_parameters(values, {name:"baseline-v1" for name in MANDATORY_PARAMETERS})
        self.assertTrue(result.complete)
        missing=audit_parameters({}, {})
        self.assertFalse(missing.complete)
        self.assertEqual(MANDATORY_PARAMETERS, frozenset(missing.missing))
