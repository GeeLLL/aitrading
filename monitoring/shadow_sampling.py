from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class SampleStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class SampleDecision:
    status: SampleStatus
    reason: str
    age_seconds: Decimal | None


@dataclass
class ReadOnlySampleGate:
    """Fail-closed validation for snapshots supplied by a read-only collector."""

    maximum_age_seconds: Decimal = Decimal("10")
    maximum_future_skew_seconds: Decimal = Decimal("2")
    last_source_updated_at: datetime | None = None
    _seen_ids: set[str] = field(default_factory=set)

    def evaluate(
        self,
        *,
        sample_id: str,
        source_updated_at: datetime | None,
        received_at: datetime,
    ) -> SampleDecision:
        if not sample_id.strip():
            return SampleDecision(SampleStatus.REJECTED, "SAMPLE_ID_MISSING", None)
        if sample_id in self._seen_ids:
            return SampleDecision(SampleStatus.REJECTED, "DUPLICATE_SAMPLE", None)
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            return SampleDecision(SampleStatus.REJECTED, "RECEIVED_TIME_NOT_TIMEZONE_AWARE", None)
        if (
            source_updated_at is None
            or source_updated_at.tzinfo is None
            or source_updated_at.utcoffset() is None
        ):
            return SampleDecision(SampleStatus.REJECTED, "SOURCE_TIME_UNKNOWN", None)

        age_seconds = Decimal(str((received_at - source_updated_at).total_seconds()))
        if age_seconds < -self.maximum_future_skew_seconds:
            return SampleDecision(SampleStatus.REJECTED, "SOURCE_TIME_TOO_FAR_IN_FUTURE", age_seconds)
        if age_seconds > self.maximum_age_seconds:
            return SampleDecision(SampleStatus.REJECTED, "STALE_SAMPLE", age_seconds)
        if (
            self.last_source_updated_at is not None
            and source_updated_at <= self.last_source_updated_at
        ):
            return SampleDecision(SampleStatus.REJECTED, "OUT_OF_ORDER_SAMPLE", age_seconds)

        self._seen_ids.add(sample_id)
        self.last_source_updated_at = source_updated_at
        return SampleDecision(SampleStatus.ACCEPTED, "SAMPLE_ACCEPTED", age_seconds)

