from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


REQUIRED_STEPS = (
    "LOCAL_KILL_SWITCH_ENGAGED", "NEW_ENTRIES_DISABLED", "OPEN_ORDERS_CHECKED",
    "OPEN_ORDERS_CANCELLED_OR_ZERO", "POSITIONS_CHECKED",
    "POSITIONS_CLOSED_OR_ESCALATED", "ROBINHOOD_AUTH_REVOKED_IF_NEEDED",
    "INCIDENT_RECORDED", "RECOVERY_REQUIRES_OWNER_APPROVAL",
)


@dataclass(frozen=True)
class EmergencyQualification:
    complete: bool
    missing: tuple[str, ...]


def qualify_emergency_drill(steps: Mapping[str, bool]) -> EmergencyQualification:
    missing = tuple(step for step in REQUIRED_STEPS if steps.get(step) is not True)
    return EmergencyQualification(not missing, missing)
