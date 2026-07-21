from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class VolatilityPoint:
    expiration: date
    strike: Decimal
    option_type: str
    implied_volatility: Decimal
    bid: Decimal
    ask: Decimal
    open_interest: int
    volume: int


@dataclass(frozen=True)
class OptionSurfaceAssessment:
    atm_iv: Decimal | None
    put_call_skew: Decimal | None
    term_slope: Decimal | None
    iv_to_realized_ratio: Decimal | None
    exit_liquid: bool
    reasons: tuple[str, ...]


def assess_option_surface(
    points: Sequence[VolatilityPoint], *, underlying: Decimal, realized_volatility: Decimal | None,
    minimum_volume: int, minimum_open_interest: int, maximum_relative_spread: Decimal,
) -> OptionSurfaceAssessment:
    if not points or underlying <= 0:
        return OptionSurfaceAssessment(None, None, None, None, False, ("OPTION_SURFACE_EMPTY",))
    ordered = sorted(points, key=lambda point: (point.expiration, abs(point.strike - underlying)))
    nearest_expiry = ordered[0].expiration
    nearest = [point for point in ordered if point.expiration == nearest_expiry]
    calls = [point for point in nearest if point.option_type == "CALL"]
    puts = [point for point in nearest if point.option_type == "PUT"]
    if not calls or not puts:
        return OptionSurfaceAssessment(None, None, None, None, False, ("CALL_PUT_PAIR_UNKNOWN",))
    call, put = calls[0], puts[0]
    atm_iv = (call.implied_volatility + put.implied_volatility) / Decimal("2")
    skew = put.implied_volatility - call.implied_volatility
    later = next((point for point in ordered if point.expiration > nearest_expiry), None)
    term = later.implied_volatility - atm_iv if later else None
    iv_rv = atm_iv / realized_volatility if realized_volatility is not None and realized_volatility > 0 else None
    reasons: list[str] = []
    for point in (call, put):
        mid = (point.bid + point.ask) / Decimal("2")
        spread = (point.ask - point.bid) / mid if mid > 0 else Decimal("Infinity")
        if point.volume < minimum_volume or point.open_interest < minimum_open_interest:
            reasons.append("EXIT_LIQUIDITY_INSUFFICIENT")
        if spread > maximum_relative_spread:
            reasons.append("EXIT_SPREAD_TOO_WIDE")
    if term is None:
        reasons.append("TERM_STRUCTURE_UNKNOWN")
    if iv_rv is None:
        reasons.append("IV_REALIZED_RATIO_UNKNOWN")
    return OptionSurfaceAssessment(atm_iv, skew, term, iv_rv, not any(reason.startswith("EXIT_") for reason in reasons), tuple(dict.fromkeys(reasons)))
