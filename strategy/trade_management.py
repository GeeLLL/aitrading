from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import Enum


class ExitAction(str, Enum):
    HOLD = "HOLD"
    EXIT = "EXIT"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction
    reason: str
    simulated_exit_price: Decimal | None


def calculate_entry_limit(
    bid: Decimal,
    ask: Decimal,
    *,
    tick_size: Decimal = Decimal("0.01"),
) -> Decimal:
    """Return midpoint plus 25% of spread, rounded down to the option tick."""

    if bid < 0 or ask <= 0 or ask < bid or tick_size <= 0:
        raise ValueError("Invalid quote or tick size.")
    midpoint = (bid + ask) / Decimal("2")
    raw_limit = midpoint + (ask - bid) * Decimal("0.25")
    # A buy limit rounds down so tick conversion cannot silently cross to ask.
    ticks = (raw_limit / tick_size).to_integral_value(rounding=ROUND_FLOOR)
    return min(ask, ticks * tick_size)


def shadow_entry_filled(*, observed_ask: Decimal | None, limit_price: Decimal) -> bool:
    """A shadow buy fills only when a real observed ask reaches the limit."""

    return observed_ask is not None and observed_ask > 0 and observed_ask <= limit_price


def evaluate_exit(
    *,
    entry_fill_price: Decimal | None,
    current_bid: Decimal | None,
    current_mark: Decimal | None,
    holding_minutes: int | None,
    underlying_vwap_still_valid: bool | None,
    force_exit_due: bool,
    emergency_exit_due: bool,
    position_and_quote_state_known: bool,
) -> ExitDecision:
    """Evaluate exits conservatively; every simulated exit is valued at bid."""

    if not position_and_quote_state_known:
        return ExitDecision(ExitAction.MANUAL_INTERVENTION, "POSITION_OR_QUOTE_STATE_UNKNOWN", None)
    if entry_fill_price is None or entry_fill_price <= 0:
        return ExitDecision(ExitAction.MANUAL_INTERVENTION, "ENTRY_FILL_UNKNOWN", None)
    if current_bid is None or current_mark is None or current_bid < 0 or current_mark < 0:
        return ExitDecision(ExitAction.MANUAL_INTERVENTION, "EXIT_QUOTE_UNKNOWN", None)
    if holding_minutes is None or holding_minutes < 0:
        return ExitDecision(ExitAction.MANUAL_INTERVENTION, "HOLDING_TIME_UNKNOWN", None)
    if underlying_vwap_still_valid is None:
        return ExitDecision(ExitAction.MANUAL_INTERVENTION, "UNDERLYING_STATE_UNKNOWN", None)

    if emergency_exit_due:
        return ExitDecision(ExitAction.EXIT, "EMERGENCY_TIME_EXIT", current_bid)
    if force_exit_due:
        return ExitDecision(ExitAction.EXIT, "FORCED_TIME_EXIT", current_bid)
    if not underlying_vwap_still_valid:
        return ExitDecision(ExitAction.EXIT, "UNDERLYING_VWAP_INVALIDATION", current_bid)
    if current_mark <= entry_fill_price * Decimal("0.80"):
        return ExitDecision(ExitAction.EXIT, "OPTION_STOP_LOSS", current_bid)
    if current_mark >= entry_fill_price * Decimal("1.30"):
        return ExitDecision(ExitAction.EXIT, "OPTION_PROFIT_TARGET", current_bid)
    if holding_minutes >= 60:
        return ExitDecision(ExitAction.EXIT, "MAX_HOLDING_TIME", current_bid)
    return ExitDecision(ExitAction.HOLD, "NO_EXIT_TRIGGER", None)
