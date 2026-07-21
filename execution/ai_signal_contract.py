from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping


class AiDirection(str, Enum):
    CALL = "CALL"
    PUT = "PUT"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class AiSignal:
    model_version: str
    prompt_version: str
    candidate: str
    direction: AiDirection
    confidence: Decimal
    regime: str
    event_risk: str
    abstain: bool
    reason_codes: tuple[str, ...]


def parse_ai_signal(value: Mapping[str, Any]) -> AiSignal:
    """Strict bounded AI output. Invalid output must become NO_TRADE upstream."""

    required = {
        "model_version", "prompt_version", "candidate", "direction",
        "confidence", "regime", "event_risk", "abstain", "reason_codes",
    }
    if set(value) != required:
        raise ValueError("AI signal fields do not match the versioned schema")
    if not isinstance(value["abstain"], bool) or not isinstance(value["reason_codes"], list):
        raise ValueError("Invalid AI signal types")
    try:
        direction = AiDirection(str(value["direction"]))
    except ValueError as error:
        raise ValueError("Unknown AI direction") from error
    raw_confidence = value["confidence"]
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float, str)):
        raise ValueError("AI confidence must be numeric")
    try:
        confidence = Decimal(str(raw_confidence))
    except InvalidOperation as error:
        raise ValueError("AI confidence must be numeric") from error
    if not confidence.is_finite():
        raise ValueError("AI confidence must be finite")
    if not Decimal("0") <= confidence <= Decimal("1"):
        raise ValueError("AI confidence must be between zero and one")
    candidate = str(value["candidate"]).strip().upper()
    if not candidate or not all(char.isalnum() or char in ".-" for char in candidate):
        raise ValueError("Invalid AI candidate symbol")
    if value["abstain"] is True and direction is not AiDirection.NO_TRADE:
        raise ValueError("Abstention must use NO_TRADE")
    return AiSignal(
        str(value["model_version"]), str(value["prompt_version"]), candidate,
        direction, confidence, str(value["regime"]), str(value["event_risk"]),
        value["abstain"], tuple(str(reason) for reason in value["reason_codes"]),
    )
