from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class InvalidStrategyPolicyError(ValueError):
    """Raised when versioned strategy policy is missing or loosened."""


def load_strategy_policy(
    path: str | Path = "strategy/strategy_v1.0.toml",
) -> dict[str, Any]:
    with Path(path).open("rb") as handle:
        return tomllib.load(handle)


def validate_strategy_policy(policy: dict[str, Any]) -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise InvalidStrategyPolicyError(message)

    require(policy.get("strategy_version") == "strategy_v1.0", "Unexpected strategy version.")
    require(policy.get("status") == "DESIGN", "Strategy must remain in DESIGN.")

    eligibility = policy.get("contract_eligibility")
    require(isinstance(eligibility, dict), "Contract eligibility policy is required.")

    require(
        eligibility.get("minimum_absolute_delta") == 0.30,
        "Minimum absolute delta must remain 0.30.",
    )
    require(
        eligibility.get("maximum_absolute_delta") == 0.65,
        "Maximum absolute delta must remain 0.65.",
    )
    require(
        eligibility.get("maximum_quote_age_seconds") == 10,
        "Maximum quote age must remain 10 seconds.",
    )
    require(
        eligibility.get("maximum_relative_spread") == 0.05,
        "Maximum relative spread must remain 5 percent.",
    )
    require(
        eligibility.get("minimum_option_volume") == 500,
        "Minimum option volume must remain 500.",
    )
    require(
        eligibility.get("minimum_open_interest") == 500,
        "Minimum open interest must remain 500.",
    )
    require(
        eligibility.get("earnings_blackout_calendar_days") == 3,
        "Earnings blackout must remain three calendar days.",
    )
    require(
        eligibility.get("unknown_required_field_rejects") is True,
        "Unknown required fields must reject the candidate.",
    )
    require(
        eligibility.get("maximum_contracts") == 1,
        "Contract quantity must remain one.",
    )
    require(
        eligibility.get("stage_1_maximum_premium_usd") == 75,
        "Stage 1 premium ceiling must remain 75 dollars.",
    )

    integrity = policy.get("data_integrity")
    require(isinstance(integrity, dict), "Data integrity policy is required.")
    require(integrity.get("require_trusted_bar_metadata") is True, "Trusted bar metadata is required.")
    require(integrity.get("maximum_bar_receipt_delay_seconds") == 10, "Bar receipt delay must remain 10 seconds.")
    require(
        integrity.get("bar_provenance_mode")
        == "INTERVAL_BOUNDARY_PLUS_IMMUTABLE_BATCH_RECEIPT",
        "Historical bar provenance must use interval boundaries and immutable batch receipt.",
    )
    require(
        integrity.get("require_per_bar_source_updated_at") is False,
        "Unavailable per-bar source update timestamps must not be fabricated or required.",
    )
    require(
        integrity.get("maximum_latest_completed_bar_lag_seconds") == 420,
        "Latest completed bar lag must remain capped at 420 seconds.",
    )
    require(integrity.get("reject_incomplete_bars") is True, "Incomplete bars must reject.")
    require(integrity.get("reject_future_bars") is True, "Future bars must reject.")
    require(integrity.get("reject_duplicate_or_out_of_order_bars") is True, "Duplicate bars must reject.")

    regime = policy.get("market_regime")
    require(isinstance(regime, dict), "Market regime policy is required.")
    require(
        regime.get("reference_symbols") == ["SPY", "QQQ"],
        "Market regime must use SPY and QQQ.",
    )
    require(regime.get("bar_interval_minutes") == 5, "Market bars must remain five minutes.")
    require(
        regime.get("confirmation_completed_bars") == 2,
        "Market direction must use two completed confirmation bars.",
    )
    require(regime.get("fast_ema_period") == 9, "Fast EMA must remain EMA 9.")
    require(regime.get("slow_ema_period") == 20, "Slow EMA must remain EMA 20.")
    require(
        regime.get("require_price_vs_vwap_alignment") is True,
        "Price must align with VWAP.",
    )
    require(regime.get("require_ema_alignment") is True, "EMA alignment is required.")
    require(
        regime.get("mixed_or_unknown_means_no_trade") is True,
        "Mixed or unknown market state must mean no trade.",
    )

    signal = policy.get("underlying_signal")
    require(isinstance(signal, dict), "Underlying signal policy is required.")
    require(
        signal.get("setup") == "FIVE_MINUTE_VOLUME_BREAKOUT",
        "Version 1.0 must use the five-minute volume breakout setup.",
    )
    require(signal.get("bar_interval_minutes") == 5, "Signal bars must remain five minutes.")
    require(
        signal.get("breakout_lookback_completed_bars") == 6,
        "Breakout lookback must remain six completed bars.",
    )
    require(
        signal.get("volume_average_lookback_bars") == 20,
        "Volume average must use 20 bars.",
    )
    require(
        signal.get("minimum_volume_ratio") == 1.50,
        "Breakout volume must be at least 1.50 times average.",
    )
    require(
        signal.get("require_price_vs_vwap_alignment") is True,
        "Underlying price must align with VWAP.",
    )
    require(signal.get("require_ema_alignment") is True, "Underlying EMA alignment is required.")
    require(
        signal.get("require_market_regime_alignment") is True,
        "Underlying direction must align with market regime.",
    )
    require(
        signal.get("unknown_required_field_means_no_trade") is True,
        "Unknown signal data must mean no trade.",
    )

    entry = policy.get("entry_execution")
    require(isinstance(entry, dict), "Entry execution policy is required.")
    require(entry.get("order_type") == "LIMIT", "Entry orders must remain limit orders.")
    require(
        entry.get("limit_formula") == "MID_PLUS_25_PERCENT_OF_SPREAD",
        "Entry limit formula must remain fixed.",
    )
    require(entry.get("maximum_fill_wait_seconds") == 60, "Entry fill wait must remain 60 seconds.")
    require(entry.get("maximum_reprices") == 0, "Version 1.0 must not chase unfilled entries.")
    require(
        entry.get("shadow_fill_requires_observed_ask_at_or_below_limit") is True,
        "Shadow fills require an observed executable ask.",
    )
    require(
        entry.get("unfilled_order_action") == "CANCEL_AND_NO_TRADE",
        "Unfilled entries must be abandoned.",
    )

    exit_policy = policy.get("exit_management")
    require(isinstance(exit_policy, dict), "Exit management policy is required.")
    require(exit_policy.get("stop_loss_option_pct") == 20.0, "Stop loss must remain 20 percent.")
    require(exit_policy.get("profit_target_option_pct") == 30.0, "Profit target must remain 30 percent.")
    require(exit_policy.get("maximum_holding_minutes") == 60, "Maximum holding time must remain 60 minutes.")
    require(
        exit_policy.get("exit_on_underlying_vwap_invalidation") is True,
        "VWAP invalidation exit is required.",
    )
    require(exit_policy.get("shadow_exit_price_source") == "BID", "Shadow exits must use bid.")
    require(exit_policy.get("force_exit_before_close_minutes") == 30, "Force exit must begin 30 minutes before close.")
    require(exit_policy.get("emergency_exit_before_close_minutes") == 15, "Emergency exit must begin 15 minutes before close.")
    require(
        exit_policy.get("unknown_position_or_quote_requires_manual_intervention") is True,
        "Unknown live position state must require manual intervention.",
    )

    governance = policy.get("governance")
    require(isinstance(governance, dict), "Strategy governance is required.")
    require(
        governance.get("llm_may_modify_automatically") is False,
        "The LLM must not modify strategy policy automatically.",
    )
    require(
        governance.get("changes_require_new_version") is True,
        "Strategy changes must require a new version.",
    )


if __name__ == "__main__":
    validate_strategy_policy(load_strategy_policy())
    print("STRATEGY POLICY VALID")
