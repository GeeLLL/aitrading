from __future__ import annotations

import unittest

from research.validation_power import (
    assess,
    detectable_edge,
    required_annual_sharpe,
    required_observations,
    win_rate_ci_halfwidth,
)


class WinRateBoundsTests(unittest.TestCase):
    def test_ci_halfwidth_matches_the_textbook_arithmetic(self):
        # 1.96 * 0.5 / sqrt(n) * 100
        self.assertAlmostEqual(win_rate_ci_halfwidth(40), 15.50, places=1)
        self.assertAlmostEqual(win_rate_ci_halfwidth(100), 9.80, places=1)
        self.assertAlmostEqual(win_rate_ci_halfwidth(30), 17.89, places=1)

    def test_ci_shrinks_as_root_n(self):
        self.assertAlmostEqual(
            win_rate_ci_halfwidth(100) / win_rate_ci_halfwidth(400), 2.0, places=6,
        )

    def test_non_positive_n_is_rejected(self):
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                win_rate_ci_halfwidth(bad)


class SampleSizeTests(unittest.TestCase):
    def test_five_point_edge_needs_about_780_trades_at_80_power(self):
        self.assertEqual(required_observations(5.0), 785)

    def test_ninety_percent_power_costs_more(self):
        from research.validation_power import Z_POWER_90
        self.assertGreater(required_observations(5.0, power_z=Z_POWER_90),
                           required_observations(5.0))

    def test_bigger_edges_need_fewer_observations(self):
        self.assertLess(required_observations(20.0), required_observations(5.0))

    def test_detectable_edge_is_the_inverse_of_required_n(self):
        n = required_observations(5.0)
        self.assertAlmostEqual(detectable_edge(n), 5.0, places=1)

    def test_invalid_edge_is_rejected(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                required_observations(bad)


class SharpeBarTests(unittest.TestCase):
    def test_forty_days_demands_an_implausible_sharpe(self):
        # t = SR * sqrt(years); 40/252 = 0.1587 yr
        self.assertAlmostEqual(required_annual_sharpe(2.0, 40), 5.02, places=1)
        self.assertAlmostEqual(required_annual_sharpe(3.0, 40), 7.53, places=1)

    def test_a_full_year_is_far_more_forgiving(self):
        self.assertAlmostEqual(required_annual_sharpe(2.0, 252), 2.0, places=6)

    def test_non_positive_horizon_is_rejected(self):
        with self.assertRaises(ValueError):
            required_annual_sharpe(2.0, 0)


class FrozenGateAssessmentTests(unittest.TestCase):
    def test_the_configured_gate_cannot_validate_a_small_edge(self):
        # strategy_v1.0: 30 completed trades over 40 shadow days.
        report = assess(30, trading_days=40)
        self.assertGreater(report.win_rate_ci_halfwidth_pct, 15.0)
        self.assertGreater(report.detectable_edge_at_80_power_pct, 20.0)
        self.assertEqual(report.required_n_for_5pp_edge_80_power, 785)
        self.assertGreater(report.required_annual_sharpe_for_t2, 5.0)
        self.assertIn("cannot validate a small edge", report.verdict)

    def test_report_serialises_with_a_verdict(self):
        payload = assess(30, trading_days=40).to_dict()
        self.assertIn("verdict", payload)
        self.assertEqual(payload["observations"], 30)

    def test_trading_days_are_optional(self):
        report = assess(30)
        self.assertIsNone(report.required_annual_sharpe_for_t2)
        self.assertIsNone(report.required_annual_sharpe_for_t3)


if __name__ == "__main__":
    unittest.main()
