from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from strategy.underlying_signal import SignalDirection, UnderlyingSignalDecision


class InvalidUniversePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class RankedUnderlying:
    symbol: str
    direction: SignalDirection
    volume_ratio: Decimal


def load_universe_policy(path: str | Path = "config/universe.toml") -> dict[str, Any]:
    with Path(path).open("rb") as handle:
        policy = tomllib.load(handle)
    validate_universe_policy(policy)
    return policy


def validate_universe_policy(policy: dict[str, Any]) -> None:
    symbols = policy.get("symbols")
    if policy.get("schema_version") != 1:
        raise InvalidUniversePolicyError("Universe schema_version must equal 1.")
    if policy.get("status") != "SHADOW_RESEARCH":
        raise InvalidUniversePolicyError("Universe must remain SHADOW_RESEARCH.")
    if not isinstance(symbols, list) or not symbols or len(symbols) != len(set(symbols)):
        raise InvalidUniversePolicyError("Universe symbols must be a non-empty unique list.")
    if any(not isinstance(symbol, str) or symbol != symbol.upper() for symbol in symbols):
        raise InvalidUniversePolicyError("Universe symbols must be uppercase strings.")
    if policy.get("maximum_full_option_chain_evaluations_per_cycle") != 3:
        raise InvalidUniversePolicyError("At most three option chains may be evaluated per cycle.")
    if policy.get("underlying_bar_interval_minutes") != 5:
        raise InvalidUniversePolicyError("Universe must use completed five-minute bars.")
    if policy.get("completed_bar_only") is not True:
        raise InvalidUniversePolicyError("Incomplete bars are forbidden.")
    if policy.get("allow_llm_to_add_symbols") is not False:
        raise InvalidUniversePolicyError("LLM cannot add universe symbols.")
    if policy.get("allow_price_based_small_cap_bias") is not False:
        raise InvalidUniversePolicyError("Price-based small-cap bias is forbidden.")
    if policy.get("budget_research_bands_usd") != [75, 120, 200, 300]:
        raise InvalidUniversePolicyError("Unexpected budget research bands.")
    if policy.get("stage_1_eligibility_budget_usd") != 75:
        raise InvalidUniversePolicyError("Stage 1 eligibility budget must remain $75.")
    if policy.get("budget_band_must_not_change_underlying_ranking") is not True:
        raise InvalidUniversePolicyError("Budget cannot change underlying ranking.")


def rank_qualified_underlyings(
    decisions: Iterable[tuple[str, UnderlyingSignalDecision]],
    *,
    allowed_symbols: Iterable[str],
    maximum_results: int = 3,
) -> tuple[RankedUnderlying, ...]:
    """Rank only already-qualified signals; option price is intentionally absent."""

    allowed = set(allowed_symbols)
    ranked: list[RankedUnderlying] = []
    for symbol, decision in decisions:
        if symbol not in allowed or decision.direction is SignalDirection.NO_TRADE:
            continue
        if decision.volume_ratio is None:
            continue
        ranked.append(RankedUnderlying(symbol, decision.direction, decision.volume_ratio))
    ranked.sort(key=lambda item: (-item.volume_ratio, item.symbol))
    return tuple(ranked[:maximum_results])

