from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from journal.shadow_recorder import (
    ShadowEventType,
    ShadowSessionState,
)
from monitoring.shadow_sampling import ReadOnlySampleGate, SampleDecision, SampleStatus
from strategy.shadow_pipeline import ShadowPipelineDecision
from strategy.shadow_runner import PositionObservation, ShadowRunner, ShadowSnapshot
from strategy.trade_management import ExitDecision


@dataclass
class ShadowSessionController:
    """Coordinate one trading day without exposing any broker write operation."""

    runner: ShadowRunner
    maximum_sample_age_seconds: Decimal = Decimal("10")
    _gates: dict[str, ReadOnlySampleGate] = field(default_factory=dict)

    def start(self) -> None:
        self.runner.start()

    def _gate(
        self,
        *,
        channel: str,
        sample_id: str,
        source_updated_at: datetime | None,
        received_at: datetime,
    ) -> SampleDecision:
        gate = self._gates.setdefault(
            channel,
            ReadOnlySampleGate(maximum_age_seconds=self.maximum_sample_age_seconds),
        )
        decision = gate.evaluate(
            sample_id=sample_id,
            source_updated_at=source_updated_at,
            received_at=received_at,
        )
        if decision.status is SampleStatus.REJECTED:
            self.runner.recorder.append(
                ShadowEventType.SESSION_ERROR,
                {
                    "reason": decision.reason,
                    "channel": channel,
                    "sample_id": sample_id,
                    "sample_age_seconds": decision.age_seconds,
                },
            )
        return decision

    def process_decision_snapshot(
        self,
        *,
        sample_id: str,
        source_updated_at: datetime | None,
        received_at: datetime,
        snapshot: ShadowSnapshot,
    ) -> ShadowPipelineDecision | None:
        sample = self._gate(
            channel="DECISION",
            sample_id=sample_id,
            source_updated_at=source_updated_at,
            received_at=received_at,
        )
        if sample.status is SampleStatus.REJECTED:
            return None
        return self.runner.evaluate(snapshot)

    def process_entry_quote(
        self,
        *,
        sample_id: str,
        quote_updated_at: datetime | None,
        received_at: datetime,
        observed_ask: Decimal | None,
        timed_out: bool,
    ) -> bool | None:
        sample = self._gate(
            channel="ENTRY_QUOTE",
            sample_id=sample_id,
            source_updated_at=quote_updated_at,
            received_at=received_at,
        )
        if sample.status is SampleStatus.REJECTED:
            return None
        return self.runner.observe_entry(
            observed_at=received_at,
            observed_ask=observed_ask,
            timed_out=timed_out,
        )

    def process_position_observation(
        self,
        *,
        sample_id: str,
        observation: PositionObservation,
    ) -> ExitDecision | None:
        sample = self._gate(
            channel="POSITION_QUOTE",
            sample_id=sample_id,
            source_updated_at=observation.quote_updated_at,
            received_at=observation.observed_at,
        )
        if sample.status is SampleStatus.REJECTED:
            return None
        return self.runner.observe_position(observation)

    def finish_day(self, *, reason: str = "MARKET_SESSION_ENDED") -> None:
        state = self.runner.recorder.state
        if state is ShadowSessionState.OBSERVING:
            self.runner.recorder.append(
                ShadowEventType.SESSION_COMPLETED,
                {"reason": reason, "trade_count": 0},
            )
            return
        if state is ShadowSessionState.ENTRY_PENDING:
            self.runner.recorder.append(
                ShadowEventType.ENTRY_UNFILLED,
                {"reason": "SESSION_ENDED_WITH_UNFILLED_SIMULATED_ENTRY"},
            )
            self.runner.pending_limit_price = None
            return
        if state is ShadowSessionState.POSITION_OPEN:
            raise RuntimeError("Cannot finish a shadow day with a simulated position still open.")
        if state not in {ShadowSessionState.COMPLETE, ShadowSessionState.ERROR}:
            raise RuntimeError(f"Cannot finish a shadow day while session is {state.value}.")

