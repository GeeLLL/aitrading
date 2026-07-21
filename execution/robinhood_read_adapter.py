from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from risk.models import AccountSnapshot, OptionType
from strategy.market_regime import CompletedMarketBar
from strategy.shadow_pipeline import OptionCandidate
from strategy.underlying_signal import UnderlyingSignalSnapshot


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    # Robinhood may return nanoseconds; Python datetime stores microseconds.
    normalized = re.sub(r"(\.\d{6})\d+(?=Z|[+-]\d\d:\d\d$)", r"\1", value)
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def option_candidate_from_mcp(
    *,
    instrument: Mapping[str, Any],
    quote: Mapping[str, Any],
    earnings_date: str | None,
    received_at: datetime | None = None,
) -> OptionCandidate:
    """Convert unwrapped official MCP option instrument/quote fields."""

    option_type_raw = str(_first(instrument, "type", "option_type") or "").upper()
    if option_type_raw not in {"CALL", "PUT"}:
        raise ValueError("Official option instrument type must be CALL or PUT.")
    option_type = OptionType(option_type_raw)

    return OptionCandidate(
        underlying=str(_first(instrument, "chain_symbol", "underlying_symbol", "symbol") or ""),
        option_type=option_type,
        strike=_decimal(_first(instrument, "strike_price", "strike")) or Decimal("-1"),
        expiration=_date(_first(instrument, "expiration_date", "expiration")) or date.min,
        bid=_decimal(_first(quote, "bid_price", "bid")),
        ask=_decimal(_first(quote, "ask_price", "ask")),
        delta=_decimal(_first(quote, "delta")),
        quote_updated_at=_datetime(_first(quote, "updated_at", "quote_updated_at")),
        quote_received_at=received_at,
        volume=_integer(_first(quote, "volume")),
        open_interest=_integer(_first(quote, "open_interest")),
        earnings_date=_date(earnings_date),
    )


def account_snapshot_from_mcp(
    *,
    account_type: Any,
    equity: Any,
    buying_power: Any,
    option_position_count: Any,
    equity_position_count: Any,
    open_option_order_count: Any,
    open_equity_order_count: Any,
    consecutive_losses: Any,
    entries_today: Any,
    settled_cash: Any = None,
    unsettled_cash: Any = None,
    reserved_cash: Any = None,
) -> AccountSnapshot:
    """Build only required risk fields; account identifiers are not accepted."""

    return AccountSnapshot(
        account_type=str(account_type) if account_type is not None else None,
        equity=_decimal(equity),
        buying_power=_decimal(buying_power),
        option_position_count=_integer(option_position_count),
        equity_position_count=_integer(equity_position_count),
        open_option_order_count=_integer(open_option_order_count),
        open_equity_order_count=_integer(open_equity_order_count),
        consecutive_losses=_integer(consecutive_losses),
        entries_today=_integer(entries_today),
        settled_cash=_decimal(settled_cash),
        unsettled_cash=_decimal(unsettled_cash),
        reserved_cash=_decimal(reserved_cash),
    )


def completed_market_bar_from_mcp(
    *,
    symbol: str,
    historical_bar: Mapping[str, Any],
    indicators: Mapping[str, Any],
    interval_minutes: int | None = None,
    received_at: datetime | None = None,
) -> CompletedMarketBar:
    # Robinhood's ``begins_at`` is the opening boundary of the candle.  Do not
    # relabel it as the end time, and never synthesize a broker update time.
    explicit_end = _datetime(_first(historical_bar, "ended_at"))
    begins_at = _datetime(_first(historical_bar, "begins_at", "timestamp"))
    if explicit_end is not None:
        ended_at = explicit_end
        started_at = (
            ended_at - timedelta(minutes=interval_minutes)
            if interval_minutes is not None else begins_at
        )
    else:
        started_at = begins_at
        ended_at = (
            started_at + timedelta(minutes=interval_minutes)
            if started_at is not None and interval_minutes is not None else None
        )
    source_updated_at = _datetime(_first(historical_bar, "updated_at", "source_updated_at"))
    completed_raw = _first(historical_bar, "completed", "is_complete")
    completed = completed_raw is True
    if completed_raw is None and ended_at is not None and received_at is not None:
        completed = ended_at <= received_at
    return CompletedMarketBar(
        symbol=symbol,
        close=_decimal(_first(historical_bar, "close", "close_price")),
        vwap=_decimal(_first(indicators, "vwap")),
        ema_fast=_decimal(_first(indicators, "ema_9", "ema9", "fast_ema")),
        ema_slow=_decimal(_first(indicators, "ema_20", "ema20", "slow_ema")),
        interval_minutes=interval_minutes,
        started_at=started_at,
        ended_at=ended_at,
        source_updated_at=source_updated_at,
        received_at=received_at,
        completed=completed,
    )


def underlying_signal_snapshot_from_mcp(
    *,
    symbol: str,
    completed_bars: Sequence[Mapping[str, Any]],
    indicators: Mapping[str, Any],
    breakout_lookback: int = 6,
    volume_lookback: int = 20,
) -> UnderlyingSignalSnapshot:
    """Compute breakout and volume references using only prior completed bars."""

    if not completed_bars:
        return UnderlyingSignalSnapshot(symbol, None, None, None, None, None, None, None, None)

    current = completed_bars[-1]
    prior = list(completed_bars[:-1])
    breakout_bars = prior[-breakout_lookback:]
    volume_bars = prior[-volume_lookback:]

    highs = [_decimal(_first(bar, "high", "high_price")) for bar in breakout_bars]
    lows = [_decimal(_first(bar, "low", "low_price")) for bar in breakout_bars]
    volumes = [_integer(_first(bar, "volume")) for bar in volume_bars]

    breakout_high = max(highs) if len(highs) == breakout_lookback and None not in highs else None
    breakdown_low = min(lows) if len(lows) == breakout_lookback and None not in lows else None
    average_volume = (
        Decimal(sum(volumes)) / Decimal(volume_lookback)
        if len(volumes) == volume_lookback and None not in volumes
        else None
    )

    return UnderlyingSignalSnapshot(
        symbol=symbol,
        close=_decimal(_first(current, "close", "close_price")),
        vwap=_decimal(_first(indicators, "vwap")),
        ema_fast=_decimal(_first(indicators, "ema_9", "ema9", "fast_ema")),
        ema_slow=_decimal(_first(indicators, "ema_20", "ema20", "slow_ema")),
        breakout_high=breakout_high,
        breakdown_low=breakdown_low,
        current_volume=_integer(_first(current, "volume")),
        average_volume=average_volume,
    )
