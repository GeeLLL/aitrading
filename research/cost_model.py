"""Realistic round-trip cost for a long option position.

The frozen ``[friction_model]`` in config/safety.toml charges a flat $1.40 per
contract (two per-contract fees + a regulatory exit fee + one tick of exit
slippage). Measured against real quotes that is a large understatement,
because it omits the two costs that actually dominate:

  * the BID-ASK SPREAD, which for the only contracts this account can afford
    runs 3%-5% of premium (and 9%-14% for the wide names), versus 1.87% for
    the whole flat friction on a $75 position; and
  * TIME DECAY, which for an at-the-money option is approximately

        theta_fraction_per_calendar_day  ~=  1 / (2 * DTE)

    (from the standard ATM approximation premium ~= 0.4 * S * sigma * sqrt(T),
    so P is proportional to sqrt(T) and dP/P per day is 1/(2*DTE)). At the
    configured 7-21 DTE band that is 2.4%-7.1% of premium PER CALENDAR DAY,
    and it is nearly independent of implied volatility.

Structural consequence, worth stating plainly: holding longer amortises the
spread as 1/h but accumulates decay linearly in h, so per-day cost has only a
shallow minimum at a high level. The one lever that fixes spread amortisation
makes decay worse.

This module computes costs; it never decides anything. Selecting an instrument,
a holding period, or an acceptable hurdle is an owner decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

HUNDRED = Decimal("100")

# Fraction of the QUOTED spread paid per leg, relative to the midpoint.
# Entry uses calculate_entry_limit (midpoint + 25% of spread) -> 0.25.
# Exit is adjudicated at the observed bid (a full half-spread below mid) -> 0.5.
# Round trip therefore pays 0.75 of the quoted spread, not 1.0. Crossing at the
# ask on entry instead would make this 1.0.
DEFAULT_ENTRY_SPREAD_FRACTION = Decimal("0.25")
DEFAULT_EXIT_SPREAD_FRACTION = Decimal("0.50")


@dataclass(frozen=True)
class CostBreakdown:
    premium_usd: Decimal
    spread_cost_usd: Decimal
    fee_cost_usd: Decimal
    decay_cost_usd: Decimal
    total_cost_usd: Decimal

    @property
    def total_pct_of_premium(self) -> Decimal:
        if self.premium_usd <= 0:
            return Decimal("0")
        return self.total_cost_usd / self.premium_usd * HUNDRED

    @property
    def breakeven_option_move_pct(self) -> Decimal:
        """The option's mark must rise this much just to return to flat."""
        return self.total_pct_of_premium

    def breakeven_underlying_move_pct(
        self, *, delta: Decimal | None, underlying_price: Decimal | None,
    ) -> Decimal | None:
        """Convert the option hurdle into the underlying move that produces it.

        Uses option elasticity (lambda) = delta * underlying_price / option_price.
        Returns None when either input is unknown — never guesses.
        """
        if delta is None or underlying_price is None:
            return None
        if underlying_price <= 0 or self.premium_usd <= 0:
            return None
        option_price = self.premium_usd / HUNDRED  # per share
        elasticity = abs(delta) * underlying_price / option_price
        if elasticity <= 0:
            return None
        return self.breakeven_option_move_pct / elasticity

    def to_dict(self) -> dict[str, Any]:
        return {
            "premium_usd": float(self.premium_usd),
            "spread_cost_usd": float(self.spread_cost_usd),
            "fee_cost_usd": float(self.fee_cost_usd),
            "decay_cost_usd": float(self.decay_cost_usd),
            "total_cost_usd": float(self.total_cost_usd),
            "total_pct_of_premium": float(round(self.total_pct_of_premium, 4)),
            "breakeven_option_move_pct": float(round(self.breakeven_option_move_pct, 4)),
        }


def decay_fraction_per_day(dte_days: int) -> Decimal:
    """ATM time-decay as a fraction of premium per calendar day: 1 / (2*DTE).

    Raises on a non-positive DTE rather than returning a soft value: an option
    with no time left has no defined fractional decay, and silently returning
    zero would understate cost exactly where it is largest.
    """
    if dte_days <= 0:
        raise ValueError("dte_days must be positive")
    return Decimal(1) / (Decimal(2) * Decimal(dte_days))


def fixed_fees_usd(friction_model: Mapping[str, Any], contracts: int = 1) -> Decimal:
    """Per-contract and regulatory fees from the frozen friction model.

    The frozen model's exit_latency_slippage_ticks is deliberately EXCLUDED
    here: this module models the spread explicitly, and counting a tick of
    slippage as well would double-charge the same effect.
    """
    per_contract = Decimal(str(friction_model["per_contract_fee_usd"]))
    regulatory_exit = Decimal(str(friction_model["regulatory_exit_fee_usd"]))
    return (per_contract * Decimal(2) + regulatory_exit) * Decimal(contracts)


def round_trip_cost(
    *,
    bid: Decimal,
    ask: Decimal,
    dte_days: int,
    holding_days: Decimal | int,
    friction_model: Mapping[str, Any],
    contracts: int = 1,
    entry_spread_fraction: Decimal = DEFAULT_ENTRY_SPREAD_FRACTION,
    exit_spread_fraction: Decimal = DEFAULT_EXIT_SPREAD_FRACTION,
) -> CostBreakdown:
    """Full round-trip cost of one long option position, in dollars.

    ``holding_days`` is calendar days (decay accrues on weekends too). A zero
    holding period is legitimate — an intraday round trip pays spread and fees
    but no full day of decay.
    """
    if bid < 0 or ask <= 0 or ask < bid:
        raise ValueError("Invalid quote")
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    holding = Decimal(str(holding_days))
    if holding < 0:
        raise ValueError("holding_days cannot be negative")

    midpoint = (bid + ask) / Decimal(2)
    spread = ask - bid
    premium = midpoint * HUNDRED * Decimal(contracts)

    spread_cost = (
        spread * (entry_spread_fraction + exit_spread_fraction) * HUNDRED * Decimal(contracts)
    )
    fees = fixed_fees_usd(friction_model, contracts)
    decay_cost = premium * decay_fraction_per_day(dte_days) * holding

    return CostBreakdown(
        premium_usd=premium,
        spread_cost_usd=spread_cost,
        fee_cost_usd=fees,
        decay_cost_usd=decay_cost,
        total_cost_usd=spread_cost + fees + decay_cost,
    )


def frozen_friction_usd(friction_model: Mapping[str, Any], contracts: int = 1) -> Decimal:
    """The flat cost the frozen config charges, for side-by-side comparison."""
    per_contract = Decimal(str(friction_model["per_contract_fee_usd"]))
    regulatory_exit = Decimal(str(friction_model["regulatory_exit_fee_usd"]))
    slippage_ticks = Decimal(str(friction_model["exit_latency_slippage_ticks"]))
    tick_size = Decimal(str(friction_model["option_tick_size_usd"]))
    single = (
        per_contract * Decimal(2)
        + regulatory_exit
        + slippage_ticks * tick_size * HUNDRED
    )
    return single * Decimal(contracts)


def understatement_ratio(
    realistic: CostBreakdown, friction_model: Mapping[str, Any], contracts: int = 1,
) -> Decimal | None:
    """How many times larger the realistic cost is than the frozen constant."""
    frozen = frozen_friction_usd(friction_model, contracts)
    if frozen <= 0:
        return None
    return realistic.total_cost_usd / frozen
