import unittest
from decimal import Decimal

from research.sensitivity import parameter_grid, run_sensitivity


class SensitivityTests(unittest.TestCase):
    def test_grid_and_results(self):
        grid = parameter_grid({"dte": [7, 14], "budget": [75, 120]})
        self.assertEqual(len(grid), 4)
        results = run_sensitivity(grid, lambda p: [Decimal(str(p["budget"])), Decimal("-1")])
        self.assertEqual(len(results), 4)
        self.assertEqual(results[0].observations, 2)

    def test_empty_grid_rejected(self):
        with self.assertRaisesRegex(ValueError, "PARAMETER_GRID_EMPTY"):
            parameter_grid({})
