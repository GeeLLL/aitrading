from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from journal.shadow_recorder import ShadowEventType, ShadowSessionRecorder
from risk.models import AccountSnapshot
from strategy.market_regime import CompletedMarketBar
from strategy.shadow_pipeline import (
    OptionCandidate,
    PipelineStatus,
    ShadowPipelineDecision,
    evaluate_shadow_candidate,
)
from strategy.trade_management import ExitAction, ExitDecision, evaluate_exit, shadow_entry_filled
from strategy.underlying_signal import UnderlyingSignalSnapshot


@dataclass(frozen=True)
class ShadowSnapshot:
    """A complete, normalized, read-only input for one decision cycle."""

    observed_at: datetime
    market_bars: tuple[CompletedMarketBar, ...]
    underlying: UnderlyingSignalSnapshot
    option: OptionCandidate
    account: AccountSnapshot
    completed_live_trades: int | None
    market_open: bool | None
    within_entry_window: bool | None
    near_forced_exit: bool | None


@dataclass(frozen=True)
class PositionObservation:
    """Fresh read-only data used to manage an existing simulated position."""

    observed_at: datetime
    quote_updated_at: datetime | None
    current_bid: Decimal | None
    current_mark: Decimal | None
    underlying_vwap_still_valid: bool | None
    force_exit_due: bool
    emergency_exit_due: bool


class ShadowRunner:
    """Orchestrate one shadow session without owning any broker/MCP client."""

    def __init__(
        self,
        *,
        recorder: ShadowSessionRecorder,
        safety_config: dict[str, Any],
        strategy_policy: dict[str, Any],
        pilot_mode: bool = False,
        shadow_authorized: bool = False,
    ) -> None:
        self.recorder = recorder
        self.safety_config = safety_config
        self.strategy_policy = strategy_policy
        self.pilot_mode = pilot_mode
        self.shadow_authorized = shadow_authorized
        self.pending_limit_price: Decimal | None = None
        self.entry_fill_price: Decimal | None = None
        self.entry_filled_at: datetime | None = None

    def start(self) -> None:
        self.recorder.append(
            ShadowEventType.SESSION_STARTED,
            {
                "mode": "PILOT" if self.pilot_mode else "SHADOW",
                "read_only": True,
                "order_tools_present": False,
            },
        )

    def evaluate(self, snapshot: ShadowSnapshot) -> ShadowPipelineDecision:
        decision = evaluate_shadow_candidate(
            market_bars=snapshot.market_bars,
            underlying_snapshot=snapshot.underlying,
            option=snapshot.option,
            account=snapshot.account,
            safety_config=self.safety_config,
            strategy_policy=self.strategy_policy,
            now=snapshot.observed_at,
            completed_live_trades=snapshot.completed_live_trades,
            market_open=snapshot.market_open,
            within_entry_window=snapshot.within_entry_window,
            near_forced_exit=snapshot.near_forced_exit,
            pilot_mode=self.pilot_mode,
            shadow_authorized=self.shadow_authorized,
        )
        self.recorder.append(
            ShadowEventType.DECISION_RECORDED,
            {
                "observed_at": snapshot.observed_at,
                "symbol": snapshot.underlying.symbol,
                "option_type": snapshot.option.option_type,
                "strike": snapshot.option.strike,
                "expiration": snapshot.option.expiration,
                "status": decision.status,
                "reasons": decision.reasons,
                "market_regime": decision.market_regime,
                "signal_direction": decision.signal_direction,
                "proposed_limit_price": decision.proposed_limit_price,
                "estimated_premium_usd": decision.estimated_premium_usd,
                "hard_rule_rejection": decision.hard_rule_rejection,
            },
        )
        if decision.status is PipelineStatus.PILOT_SIMULATED_ENTRY:
            if decision.proposed_limit_price is None:
                raise RuntimeError("Approved shadow decision has no limit price.")
            self.pending_limit_price = decision.proposed_limit_price
            self.recorder.append(
                ShadowEventType.ENTRY_WORKING,
                {
                    "symbol": snapshot.underlying.symbol,
                    "limit_price": self.pending_limit_price,
                    "quantity": 1,
                    "simulation_only": True,
                },
            )
        return decision

    def observe_entry(
        self,
        *,
        observed_at: datetime,
        observed_ask: Decimal | None,
        timed_out: bool,
    ) -> bool:
        """Resolve a simulated limit order using an observed executable ask."""

        if self.pending_limit_price is None:
            raise RuntimeError("No simulated entry is pending.")
        if shadow_entry_filled(observed_ask=observed_ask, limit_price=self.pending_limit_price):
            self.entry_fill_price = self.pending_limit_price
            self.entry_filled_at = observed_at
            self.recorder.append(
                ShadowEventType.ENTRY_FILLED,
                {
                    "fill_price": self.pending_limit_price,
                    "observed_ask": observed_ask,
                    "simulation_only": True,
                },
            )
            self.pending_limit_price = None
            return True
        if timed_out:
            self.recorder.append(
                ShadowEventType.ENTRY_UNFILLED,
                {
                    "reason": "SIMULATED_LIMIT_TIMEOUT",
                    "last_observed_ask": observed_ask,
                    "simulation_only": True,
                },
            )
            self.pending_limit_price = None
        return False

    def _exit_friction_usd(self) -> Decimal:
        """Deterministic single-contract round-trip friction from safety config.

        The friction_model section is mandatory (validate_safety_config), so a
        missing section is a hard error rather than silently fee-free P&L.
        """

        model = self.safety_config["friction_model"]
        per_contract = Decimal(str(model["per_contract_fee_usd"]))
        regulatory_exit = Decimal(str(model["regulatory_exit_fee_usd"]))
        slippage_ticks = Decimal(str(model["exit_latency_slippage_ticks"]))
        tick_size = Decimal(str(model["option_tick_size_usd"]))
        fees = per_contract * Decimal("2") + regulatory_exit
        exit_slippage = slippage_ticks * tick_size * Decimal("100")
        return fees + exit_slippage

    def observe_position(self, observation: PositionObservation) -> ExitDecision:
        """Evaluate and record one monitoring cycle for a simulated open position."""

        if self.entry_fill_price is None or self.entry_filled_at is None:
            raise RuntimeError("No simulated position is open.")

        times_known = (
            observation.observed_at.tzinfo is not None
            and observation.observed_at.utcoffset() is not None
            and self.entry_filled_at.tzinfo is not None
            and self.entry_filled_at.utcoffset() is not None
        )
        holding_minutes: int | None = None
        if times_known:
            elapsed = observation.observed_at - self.entry_filled_at
            if elapsed.total_seconds() >= 0:
                holding_minutes = int(elapsed.total_seconds() // 60)

        quote_age_seconds: Decimal | None = None
        if (
            observation.quote_updated_at is not None
            and observation.quote_updated_at.tzinfo is not None
            and observation.quote_updated_at.utcoffset() is not None
            and observation.observed_at.tzinfo is not None
            and observation.observed_at.utcoffset() is not None
        ):
            age = observation.observed_at - observation.quote_updated_at
            quote_age_seconds = Decimal(str(age.total_seconds()))

        maximum_age = Decimal(
            str(self.strategy_policy["contract_eligibility"]["maximum_quote_age_seconds"])
        )
        state_known = (
            holding_minutes is not None
            and quote_age_seconds is not None
            and Decimal("0") <= quote_age_seconds <= maximum_age
        )
        decision = evaluate_exit(
            entry_fill_price=self.entry_fill_price,
            current_bid=observation.current_bid,
            current_mark=observation.current_mark,
            holding_minutes=holding_minutes,
            underlying_vwap_still_valid=observation.underlying_vwap_still_valid,
            force_exit_due=observation.force_exit_due,
            emergency_exit_due=observation.emergency_exit_due,
            position_and_quote_state_known=state_known,
        )
        common = {
            "observed_at": observation.observed_at,
            "quote_updated_at": observation.quote_updated_at,
            "quote_age_seconds": quote_age_seconds,
            "bid": observation.current_bid,
            "mark": observation.current_mark,
            "holding_minutes": holding_minutes,
            "action": decision.action,
            "reason": decision.reason,
            "simulation_only": True,
        }
        if decision.action is ExitAction.HOLD:
            self.recorder.append(ShadowEventType.POSITION_SNAPSHOT, common)
        elif decision.action is ExitAction.EXIT:
            assert decision.simulated_exit_price is not None
            gross = (decision.simulated_exit_price - self.entry_fill_price) * Decimal("100")
            friction = self._exit_friction_usd()
            net = gross - friction
            return_pct = (decision.simulated_exit_price / self.entry_fill_price - Decimal("1")) * Decimal("100")
            self.recorder.append(
                ShadowEventType.EXITED,
                {
                    **common,
                    "exit_price": decision.simulated_exit_price,
                    "simulated_gross_pnl_usd": gross,
                    "friction_usd": friction,
                    "simulated_pnl_usd": net,
                    "simulated_return_pct": return_pct,
                },
            )
            self.entry_fill_price = None
            self.entry_filled_at = None
        else:
            self.recorder.append(ShadowEventType.SESSION_ERROR, common)
        return decision
