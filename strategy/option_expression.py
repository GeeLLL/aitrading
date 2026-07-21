from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class OptionRiskSnapshot:
    mark: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    implied_volatility: Decimal | None
    underlying_price: Decimal | None
    expected_underlying_move: Decimal | None
    bid: Decimal | None
    ask: Decimal | None


@dataclass(frozen=True)
class OptionExpressionAssessment:
    eligible: bool
    breakeven_move_from_greeks: Decimal | None
    theta_cost_pct_per_day: Decimal | None
    relative_spread: Decimal | None
    reasons: tuple[str, ...]


def assess_option_expression(snapshot: OptionRiskSnapshot) -> OptionExpressionAssessment:
    """Evaluate whether a directional signal is economically expressible.

    This is a feature/eligibility assessment, not a direction or trade signal.
    """

    required = {
        "MARK_UNKNOWN": snapshot.mark,
        "DELTA_UNKNOWN": snapshot.delta,
        "GAMMA_UNKNOWN": snapshot.gamma,
        "THETA_UNKNOWN": snapshot.theta,
        "VEGA_UNKNOWN": snapshot.vega,
        "IV_UNKNOWN": snapshot.implied_volatility,
        "UNDERLYING_PRICE_UNKNOWN": snapshot.underlying_price,
        "EXPECTED_MOVE_UNKNOWN": snapshot.expected_underlying_move,
        "BID_UNKNOWN": snapshot.bid,
        "ASK_UNKNOWN": snapshot.ask,
    }
    reasons = [reason for reason, value in required.items() if value is None]
    if reasons:
        return OptionExpressionAssessment(False, None, None, None, tuple(reasons))
    assert all(value is not None for value in required.values())
    mark = snapshot.mark
    delta = abs(snapshot.delta)
    bid = snapshot.bid
    ask = snapshot.ask
    assert mark is not None and delta is not None and bid is not None and ask is not None
    if mark <= 0 or delta <= 0 or bid < 0 or ask <= 0 or ask < bid:
        return OptionExpressionAssessment(False, None, None, None, ("OPTION_RISK_INPUT_INVALID",))
    mid = (bid + ask) / Decimal("2")
    spread = (ask - bid) / mid if mid > 0 else None
    theta_cost = abs(snapshot.theta) / mark * Decimal("100")
    # First-order diagnostic only. Gamma/IV remain explicit features and the
    # value must not be interpreted as a guaranteed breakeven.
    breakeven = mark / delta
    if snapshot.expected_underlying_move <= 0:
        reasons.append("EXPECTED_MOVE_NON_POSITIVE")
    if snapshot.expected_underlying_move < breakeven:
        reasons.append("EXPECTED_MOVE_BELOW_FIRST_ORDER_PREMIUM_MOVE")
    return OptionExpressionAssessment(not reasons, breakeven, theta_cost, spread, tuple(reasons))
