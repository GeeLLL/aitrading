"""What a given number of observations can and cannot establish.

The frozen gate asks for 40 eligible shadow days and 30 completed trades. It is
worth being precise about what that buys, because the arithmetic is not close:

    t = (per-trade Sharpe) * sqrt(N)          and       t ~= SR_annual * sqrt(years)

40 trading days is 0.159 years, so t = 0.40 * SR_annual. Reaching even t = 2
would require a realised annualised Sharpe of 5.0; the multiple-testing bar
commonly cited for a new factor (Harvey, Liu & Zhu, RFS 2016) is t > 3, i.e.
SR_annual = 7.5. Strategies at that level exist — they are colocated
market-making books — and they are not discovered by a shadow experiment.

Equivalently, at N = 30 the 95% confidence interval on an observed win rate is
about +/-18 percentage points: an observed 60% is statistically indistinguishable
from a losing process.

None of this makes a 40-day shadow period useless. It makes it a different
instrument than a hypothesis test:

  * INFRASTRUCTURE VALIDATION — does the pipeline fire, are fills recorded, do
    the risk limits bind, does the scheduler survive holidays?
  * FALSIFICATION — rejection is far cheaper than confirmation. A process that
    loses on 35 of 40 days is dead on a sample that could never have proven it
    alive.
  * COST CALIBRATION — slippage per trade has much lower relative noise than
    P&L per trade, so a few dozen real fills give a usable cost estimate long
    before they give a usable edge estimate.

This module computes those bounds. It does not judge any strategy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

TRADING_DAYS_PER_YEAR = 252

# Standard normal quantiles used below (avoids a scipy dependency).
Z_95 = 1.959963984540054   # two-sided 95%
Z_POWER_80 = 0.8416212335729143
Z_POWER_90 = 1.2815515655446004


@dataclass(frozen=True)
class PowerReport:
    observations: int
    win_rate_ci_halfwidth_pct: float
    detectable_edge_at_80_power_pct: float
    required_n_for_5pp_edge_80_power: int
    required_n_for_5pp_edge_90_power: int
    required_per_trade_sharpe_for_t2: float
    required_annual_sharpe_for_t2: float | None
    required_annual_sharpe_for_t3: float | None
    trading_days: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "win_rate_ci_halfwidth_pct": round(self.win_rate_ci_halfwidth_pct, 2),
            "detectable_edge_at_80_power_pct": round(self.detectable_edge_at_80_power_pct, 2),
            "required_n_for_5pp_edge_80_power": self.required_n_for_5pp_edge_80_power,
            "required_n_for_5pp_edge_90_power": self.required_n_for_5pp_edge_90_power,
            "required_per_trade_sharpe_for_t2": round(self.required_per_trade_sharpe_for_t2, 4),
            "required_annual_sharpe_for_t2": (
                None if self.required_annual_sharpe_for_t2 is None
                else round(self.required_annual_sharpe_for_t2, 2)
            ),
            "required_annual_sharpe_for_t3": (
                None if self.required_annual_sharpe_for_t3 is None
                else round(self.required_annual_sharpe_for_t3, 2)
            ),
            "trading_days": self.trading_days,
            "verdict": self.verdict,
        }

    @property
    def verdict(self) -> str:
        """A one-line, non-negotiable statement of what this sample supports."""
        return (
            f"At N={self.observations} the 95% CI on a win rate is "
            f"+/-{self.win_rate_ci_halfwidth_pct:.1f}pp. Detecting a 5pp edge at 80% "
            f"power needs N={self.required_n_for_5pp_edge_80_power}. This sample can "
            f"falsify a badly losing process and calibrate costs; it cannot validate "
            f"a small edge."
        )


def win_rate_ci_halfwidth(observations: int, *, z: float = Z_95, p: float = 0.5) -> float:
    """Half-width, in percentage points, of the CI on an observed win rate."""
    if observations <= 0:
        raise ValueError("observations must be positive")
    return z * math.sqrt(p * (1 - p) / observations) * 100


def required_observations(
    edge_pct: float, *, power_z: float = Z_POWER_80, z: float = Z_95, p: float = 0.5,
) -> int:
    """Two-proportion sample size for detecting ``edge_pct`` points of win-rate edge."""
    if edge_pct <= 0:
        raise ValueError("edge_pct must be positive")
    delta = edge_pct / 100
    return math.ceil((z + power_z) ** 2 * p * (1 - p) / delta**2)


def detectable_edge(observations: int, *, power_z: float = Z_POWER_80,
                    z: float = Z_95, p: float = 0.5) -> float:
    """The smallest win-rate edge (in points) this N can detect at the given power."""
    if observations <= 0:
        raise ValueError("observations must be positive")
    return math.sqrt((z + power_z) ** 2 * p * (1 - p) / observations) * 100


def required_annual_sharpe(t_target: float, trading_days: int) -> float:
    """Annualised Sharpe implied by demanding ``t_target`` over ``trading_days``."""
    if trading_days <= 0:
        raise ValueError("trading_days must be positive")
    years = trading_days / TRADING_DAYS_PER_YEAR
    return t_target / math.sqrt(years)


def assess(observations: int, *, trading_days: int | None = None) -> PowerReport:
    """Bound what a sample of this size can establish."""
    return PowerReport(
        observations=observations,
        win_rate_ci_halfwidth_pct=win_rate_ci_halfwidth(observations),
        detectable_edge_at_80_power_pct=detectable_edge(observations),
        required_n_for_5pp_edge_80_power=required_observations(5.0),
        required_n_for_5pp_edge_90_power=required_observations(5.0, power_z=Z_POWER_90),
        required_per_trade_sharpe_for_t2=2.0 / math.sqrt(observations),
        required_annual_sharpe_for_t2=(
            None if trading_days is None else required_annual_sharpe(2.0, trading_days)
        ),
        required_annual_sharpe_for_t3=(
            None if trading_days is None else required_annual_sharpe(3.0, trading_days)
        ),
        trading_days=trading_days,
    )
