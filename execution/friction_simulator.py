from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class FillScenario(str, Enum):
    OPTIMISTIC = "OPTIMISTIC"
    BASE = "BASE"
    STRESS = "STRESS"


@dataclass(frozen=True)
class QuoteLevel:
    bid: Decimal
    ask: Decimal
    bid_size: int | None
    ask_size: int | None


@dataclass(frozen=True)
class SimulatedRoundTrip:
    scenario: FillScenario
    entry_price: Decimal | None
    exit_price: Decimal | None
    filled_quantity: int
    gross_pnl_usd: Decimal | None
    friction_usd: Decimal | None
    reasons: tuple[str, ...]


def simulate_round_trip(
    *,
    entry: QuoteLevel,
    exit: QuoteLevel,
    quantity: int = 1,
    fee_usd: Decimal = Decimal("0"),
) -> tuple[SimulatedRoundTrip, ...]:
    """Conservative three-scenario model; never represents a broker fill."""

    if quantity != 1:
        raise ValueError("Only one contract is supported")
    if entry.bid < 0 or entry.ask <= 0 or entry.ask < entry.bid or exit.bid < 0 or exit.ask <= 0 or exit.ask < exit.bid:
        raise ValueError("Invalid quote")
    mid_entry = (entry.bid + entry.ask) / 2
    mid_exit = (exit.bid + exit.ask) / 2
    spread_entry = entry.ask - entry.bid
    spread_exit = exit.ask - exit.bid
    results: list[SimulatedRoundTrip] = []
    definitions = (
        (FillScenario.OPTIMISTIC, mid_entry, mid_exit, ()),
        (FillScenario.BASE, entry.ask, exit.bid, ()),
        (FillScenario.STRESS, entry.ask + spread_entry, max(Decimal("0"), exit.bid - spread_exit), ("ONE_SPREAD_ADVERSE_SLIPPAGE",)),
    )
    for scenario, buy, sell, base_reasons in definitions:
        reasons = list(base_reasons)
        if entry.ask_size is None or exit.bid_size is None:
            reasons.append("DISPLAYED_SIZE_UNKNOWN")
        elif entry.ask_size < quantity or exit.bid_size < quantity:
            results.append(SimulatedRoundTrip(scenario, None, None, 0, None, None, tuple(reasons + ["INSUFFICIENT_DISPLAYED_SIZE"])))
            continue
        pnl = (sell - buy) * Decimal("100") - fee_usd
        friction = ((buy - mid_entry) + (mid_exit - sell)) * Decimal("100") + fee_usd
        results.append(SimulatedRoundTrip(scenario, buy, sell, quantity, pnl, friction, tuple(reasons)))
    return tuple(results)
