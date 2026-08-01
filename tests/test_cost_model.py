from __future__ import annotations

import unittest
from decimal import Decimal

from research.cost_model import (
    CostBreakdown,
    decay_fraction_per_day,
    frozen_friction_usd,
    round_trip_cost,
    understatement_ratio,
)

FRICTION = {
    "per_contract_fee_usd": 0.15,
    "regulatory_exit_fee_usd": 0.10,
    "exit_latency_slippage_ticks": 1,
    "option_tick_size_usd": 0.01,
}


class DecayTests(unittest.TestCase):
    def test_atm_decay_is_one_over_twice_dte(self):
        self.assertAlmostEqual(float(decay_fraction_per_day(7)), 1 / 14, places=10)
        self.assertAlmostEqual(float(decay_fraction_per_day(14)), 1 / 28, places=10)
        self.assertAlmostEqual(float(decay_fraction_per_day(21)), 1 / 42, places=10)

    def test_shorter_dated_decays_faster(self):
        self.assertGreater(decay_fraction_per_day(7), decay_fraction_per_day(21))

    def test_non_positive_dte_raises_rather_than_returning_zero(self):
        # Returning 0 would understate cost exactly where it is largest.
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                decay_fraction_per_day(bad)


class RoundTripCostTests(unittest.TestCase):
    def test_components_are_separated_and_sum(self):
        cost = round_trip_cost(
            bid=Decimal("0.66"), ask=Decimal("0.70"), dte_days=14,
            holding_days=1, friction_model=FRICTION,
        )
        self.assertEqual(
            cost.total_cost_usd,
            cost.spread_cost_usd + cost.fee_cost_usd + cost.decay_cost_usd,
        )
        # mid 0.68 -> premium $68; spread 0.04 * 0.75 * 100 = $3.00
        self.assertEqual(cost.premium_usd, Decimal("68.00"))
        self.assertEqual(cost.spread_cost_usd, Decimal("3.00"))
        # fees exclude the slippage tick (spread is modelled explicitly)
        self.assertEqual(cost.fee_cost_usd, Decimal("0.40"))
        # decay: 68 * (1/28) * 1 day
        self.assertAlmostEqual(float(cost.decay_cost_usd), 68 / 28, places=6)

    def test_round_trip_pays_075_of_quoted_spread_not_full(self):
        # Entry limit is mid + 25% of spread; exit is at the bid (mid - 50%).
        cost = round_trip_cost(
            bid=Decimal("1.00"), ask=Decimal("1.20"), dte_days=14,
            holding_days=0, friction_model=FRICTION,
        )
        self.assertEqual(cost.spread_cost_usd, Decimal("15.00"))  # 0.20 * 0.75 * 100

    def test_crossing_at_the_ask_costs_the_full_spread(self):
        cost = round_trip_cost(
            bid=Decimal("1.00"), ask=Decimal("1.20"), dte_days=14, holding_days=0,
            friction_model=FRICTION, entry_spread_fraction=Decimal("0.5"),
        )
        self.assertEqual(cost.spread_cost_usd, Decimal("20.00"))

    def test_zero_holding_period_has_no_decay_but_still_pays_spread(self):
        cost = round_trip_cost(
            bid=Decimal("0.66"), ask=Decimal("0.70"), dte_days=14,
            holding_days=0, friction_model=FRICTION,
        )
        self.assertEqual(cost.decay_cost_usd, Decimal("0"))
        self.assertGreater(cost.spread_cost_usd, 0)

    def test_holding_longer_amortises_spread_but_accumulates_decay(self):
        # The structural trap: per-day cost has only a shallow minimum.
        per_day = []
        for days in (1, 2, 5, 10):
            cost = round_trip_cost(
                bid=Decimal("0.66"), ask=Decimal("0.70"), dte_days=14,
                holding_days=days, friction_model=FRICTION,
            )
            per_day.append(float(cost.total_pct_of_premium) / days)
        self.assertLess(per_day[-1], per_day[0])      # amortisation helps
        self.assertGreater(per_day[-1], 4.0)          # but the floor stays high

    def test_invalid_quotes_are_rejected(self):
        for bid, ask in ((Decimal("-1"), Decimal("1")), (Decimal("1"), Decimal("0")),
                         (Decimal("2"), Decimal("1"))):
            with self.assertRaises(ValueError):
                round_trip_cost(bid=bid, ask=ask, dte_days=14, holding_days=1,
                                friction_model=FRICTION)

    def test_negative_holding_is_rejected(self):
        with self.assertRaises(ValueError):
            round_trip_cost(bid=Decimal("1"), ask=Decimal("1.1"), dte_days=14,
                            holding_days=-1, friction_model=FRICTION)


class BreakevenTests(unittest.TestCase):
    def test_breakeven_option_move_equals_total_cost_pct(self):
        cost = round_trip_cost(
            bid=Decimal("0.66"), ask=Decimal("0.70"), dte_days=14,
            holding_days=1, friction_model=FRICTION,
        )
        self.assertEqual(cost.breakeven_option_move_pct, cost.total_pct_of_premium)

    def test_underlying_move_uses_elasticity(self):
        cost = round_trip_cost(
            bid=Decimal("0.66"), ask=Decimal("0.70"), dte_days=14,
            holding_days=0, friction_model=FRICTION,
        )
        # elasticity = 0.5 * 16 / 0.68 = 11.76; hurdle 5.0% -> ~0.43% underlying
        move = cost.breakeven_underlying_move_pct(
            delta=Decimal("0.5"), underlying_price=Decimal("16"),
        )
        self.assertIsNotNone(move)
        self.assertLess(float(move), float(cost.breakeven_option_move_pct))
        self.assertAlmostEqual(float(move), 0.425, places=2)

    def test_unknown_inputs_return_none_never_a_guess(self):
        cost = round_trip_cost(
            bid=Decimal("0.66"), ask=Decimal("0.70"), dte_days=14,
            holding_days=0, friction_model=FRICTION,
        )
        self.assertIsNone(cost.breakeven_underlying_move_pct(
            delta=None, underlying_price=Decimal("16")))
        self.assertIsNone(cost.breakeven_underlying_move_pct(
            delta=Decimal("0.5"), underlying_price=None))


class FrozenComparisonTests(unittest.TestCase):
    def test_frozen_constant_matches_the_configured_140(self):
        self.assertEqual(frozen_friction_usd(FRICTION), Decimal("1.40"))

    def test_real_calibration_trade_shows_the_understatement(self):
        # The actual 2026-07-28 IWM calibration trade.
        cost = round_trip_cost(
            bid=Decimal("2.54"), ask=Decimal("2.61"), dte_days=14,
            holding_days=Decimal("0.038"), friction_model=FRICTION,
        )
        ratio = understatement_ratio(cost, FRICTION)
        self.assertGreater(float(ratio), 4.0)
        self.assertLess(float(cost.total_pct_of_premium), 3.0)

    def test_wide_spread_cheap_contract_is_the_worst_case(self):
        # A $0.75 contract with a $0.05 tick spread: the cap-affordable corner.
        cheap = round_trip_cost(
            bid=Decimal("0.70"), ask=Decimal("0.75"), dte_days=7,
            holding_days=1, friction_model=FRICTION,
        )
        self.assertGreater(float(cheap.total_pct_of_premium), 10.0)


if __name__ == "__main__":
    unittest.main()
