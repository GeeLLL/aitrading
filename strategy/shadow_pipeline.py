from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from risk.models import (
    AccountSnapshot,
    OptionType,
    OrderAction,
    OrderIntent,
    OrderType,
    PositionEffect,
)
from risk.validator import evaluate_opening_order
from strategy.market_regime import CompletedMarketBar, determine_market_regime, validate_bar_set
from strategy.trade_management import calculate_entry_limit
from strategy.underlying_signal import (
    SignalDirection,
    UnderlyingSignalSnapshot,
    evaluate_underlying_signal,
)


class PipelineStatus(str, Enum):
    PILOT_SIMULATED_ENTRY = "PILOT_SIMULATED_ENTRY"
    NO_TRADE = "NO_TRADE"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class OptionCandidate:
    underlying: str
    option_type: OptionType
    strike: Decimal
    expiration: date
    bid: Decimal | None
    ask: Decimal | None
    delta: Decimal | None
    quote_updated_at: datetime | None
    volume: int | None
    open_interest: int | None
    earnings_date: date | None
    quote_received_at: datetime | None = None


@dataclass(frozen=True)
class ShadowPipelineDecision:
    status: PipelineStatus
    reasons: tuple[str, ...]
    market_regime: str
    signal_direction: str
    proposed_limit_price: Decimal | None
    estimated_premium_usd: Decimal | None
    hard_rule_rejection: bool = False


def evaluate_shadow_candidate(
    *,
    market_bars: Iterable[CompletedMarketBar],
    underlying_snapshot: UnderlyingSignalSnapshot,
    option: OptionCandidate,
    account: AccountSnapshot,
    safety_config: dict[str, Any],
    strategy_policy: dict[str, Any],
    now: datetime,
    completed_live_trades: int | None,
    market_open: bool | None,
    within_entry_window: bool | None,
    near_forced_exit: bool | None,
    pilot_mode: bool = False,
    shadow_authorized: bool = False,
) -> ShadowPipelineDecision:
    """Run the read-only decision chain without any broker execution client."""

    regime_policy = strategy_policy["market_regime"]
    bars = tuple(market_bars)
    integrity = strategy_policy.get("data_integrity", {})
    if integrity.get("require_trusted_bar_metadata", True):
        timeline_reasons = validate_bar_set(
            bars,
            decision_time=now,
            expected_interval_minutes=int(regime_policy["bar_interval_minutes"]),
            maximum_receipt_delay_seconds=int(integrity.get("maximum_bar_receipt_delay_seconds", 10)),
            maximum_latest_bar_lag_seconds=int(
                integrity.get("maximum_latest_completed_bar_lag_seconds", 420)
            ),
        )
        if timeline_reasons:
            return ShadowPipelineDecision(
                PipelineStatus.REJECTED,
                timeline_reasons,
                "UNKNOWN",
                SignalDirection.NO_TRADE.value,
                None,
                None,
                True,
            )
    regime = determine_market_regime(
        bars,
        reference_symbols=tuple(regime_policy["reference_symbols"]),
        confirmation_bars=regime_policy["confirmation_completed_bars"],
    )
    if regime.regime.value == "NO_TRADE":
        return ShadowPipelineDecision(
            PipelineStatus.NO_TRADE,
            regime.reasons,
            regime.regime.value,
            SignalDirection.NO_TRADE.value,
            None,
            None,
        )

    signal_policy = strategy_policy["underlying_signal"]
    signal = evaluate_underlying_signal(
        underlying_snapshot,
        regime.regime,
        minimum_volume_ratio=Decimal(str(signal_policy["minimum_volume_ratio"])),
    )
    if signal.direction is SignalDirection.NO_TRADE:
        return ShadowPipelineDecision(
            PipelineStatus.NO_TRADE,
            signal.reasons,
            regime.regime.value,
            signal.direction.value,
            None,
            None,
        )

    expected_type = OptionType.CALL if signal.direction is SignalDirection.CALL else OptionType.PUT
    reasons: list[str] = []
    if option.underlying != underlying_snapshot.symbol:
        reasons.append("OPTION_UNDERLYING_MISMATCH")
    if option.option_type is not expected_type:
        reasons.append("OPTION_DIRECTION_MISMATCH")

    eligibility = strategy_policy["contract_eligibility"]
    maximum_quote_age = int(eligibility["maximum_quote_age_seconds"])
    if option.quote_received_at is None:
        reasons.append("FINAL_OPTION_QUOTE_RECEIPT_TIME_MISSING")
    elif (
        option.quote_received_at.tzinfo is None
        or option.quote_received_at.utcoffset() is None
    ):
        reasons.append("FINAL_OPTION_QUOTE_RECEIPT_TIME_NOT_AWARE")
    elif now.tzinfo is None or now.utcoffset() is None:
        reasons.append("CURRENT_TIME_NOT_TIMEZONE_AWARE")
    else:
        decision_lag = (now - option.quote_received_at).total_seconds()
        if decision_lag < 0:
            reasons.append("FINAL_OPTION_QUOTE_RECEIPT_FROM_FUTURE")
        elif decision_lag > maximum_quote_age:
            reasons.append("FINAL_OPTION_QUOTE_NOT_REFRESHED")
    if option.delta is None:
        reasons.append("OPTION_DELTA_UNKNOWN")
    else:
        absolute_delta = abs(option.delta)
        if absolute_delta < Decimal(str(eligibility["minimum_absolute_delta"])):
            reasons.append("OPTION_DELTA_BELOW_MINIMUM")
        if absolute_delta > Decimal(str(eligibility["maximum_absolute_delta"])):
            reasons.append("OPTION_DELTA_ABOVE_MAXIMUM")

    if now.tzinfo is None or now.utcoffset() is None:
        reasons.append("CURRENT_TIME_NOT_TIMEZONE_AWARE")
    elif option.earnings_date is None:
        reasons.append("EARNINGS_DATE_UNKNOWN")
    else:
        # Use the exchange calendar date, matching the validator's DTE basis, so
        # the blackout window cannot shift by a day around UTC midnight.
        market_date = now.astimezone(ZoneInfo("America/New_York")).date()
        days_to_earnings = (option.earnings_date - market_date).days
        if 0 <= days_to_earnings <= int(eligibility["earnings_blackout_calendar_days"]):
            reasons.append("EARNINGS_BLACKOUT")

    if option.bid is None or option.ask is None:
        reasons.append("OPTION_QUOTE_MISSING")
        limit_price = None
    else:
        try:
            limit_price = calculate_entry_limit(option.bid, option.ask)
        except ValueError:
            reasons.append("OPTION_QUOTE_INVALID")
            limit_price = None

    if reasons or limit_price is None:
        return ShadowPipelineDecision(
            PipelineStatus.REJECTED,
            tuple(dict.fromkeys(reasons)),
            regime.regime.value,
            signal.direction.value,
            limit_price,
            None,
        )

    intent = OrderIntent(
        underlying=option.underlying,
        option_type=option.option_type,
        action=OrderAction.BUY,
        position_effect=PositionEffect.OPEN,
        order_type=OrderType.LIMIT,
        quantity=1,
        limit_price=limit_price,
        expiration=option.expiration,
        bid=option.bid,
        ask=option.ask,
        quote_updated_at=option.quote_updated_at,
        volume=option.volume,
        open_interest=option.open_interest,
    )

    shadow_config = copy.deepcopy(safety_config)
    shadow_config["system_mode"] = "SHADOW"
    shadow_config["live_trading_enabled"] = False
    shadow_config["order_tools_enabled"] = False
    # Shadow creates no broker order. The live execution boundary must supply
    # the independently read local switch status again immediately before any
    # broker mutation. Here the value means "simulation execution disabled".
    risk = evaluate_opening_order(
        intent=intent,
        account=account,
        config=shadow_config,
        now=now,
        completed_live_trades=completed_live_trades,
        market_open=market_open,
        within_entry_window=within_entry_window,
        near_forced_exit=near_forced_exit,
        max_quote_age_seconds=maximum_quote_age,
        max_relative_spread=Decimal(str(eligibility["maximum_relative_spread"])),
        minimum_option_volume=int(eligibility["minimum_option_volume"]),
        minimum_open_interest=int(eligibility["minimum_open_interest"]),
        kill_switch_engaged=False,
        kill_switch_reason="SHADOW_SIMULATION_ONLY",
    )
    if not risk.approved:
        return ShadowPipelineDecision(
            PipelineStatus.REJECTED,
            risk.violations,
            regime.regime.value,
            signal.direction.value,
            limit_price,
            risk.estimated_premium_usd,
            True,
        )

    if not pilot_mode and not (
        strategy_policy.get("status") == "SHADOW" or shadow_authorized
    ):
        return ShadowPipelineDecision(
            PipelineStatus.REJECTED,
            ("STRATEGY_NOT_ACTIVATED_FOR_SHADOW",),
            regime.regime.value,
            signal.direction.value,
            limit_price,
            risk.estimated_premium_usd,
        )

    return ShadowPipelineDecision(
        PipelineStatus.PILOT_SIMULATED_ENTRY,
        ("PILOT_NOT_STRATEGY_EVIDENCE",) if pilot_mode else (),
        regime.regime.value,
        signal.direction.value,
        limit_price,
        risk.estimated_premium_usd,
    )
