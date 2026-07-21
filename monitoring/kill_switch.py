from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class KillSwitchStatus:
    engaged: bool
    reason: str


class KillSwitch:
    """Fail-closed local kill switch.

    New positions are considered disabled unless an explicit runtime marker
    exists. No arming method is provided during READ_ONLY development.
    """

    def __init__(self, marker_path: str | Path = "state/trading_armed") -> None:
        self.marker_path = Path(marker_path)

    def status(self) -> KillSwitchStatus:
        if not self.marker_path.is_file():
            return KillSwitchStatus(
                engaged=True,
                reason="TRADING_ARM_MARKER_ABSENT",
            )
        return KillSwitchStatus(engaged=False, reason="TRADING_ARM_MARKER_PRESENT")

    def engage(self) -> KillSwitchStatus:
        """Remove the arming marker and verify the stopped state."""

        self.marker_path.unlink(missing_ok=True)
        return self.status()


class AutomationHalt:
    """Owner emergency stop for the read-only automation loop.

    The kill switch blocks entries but is also the normal READ_ONLY state, so
    it cannot signal "stop the scheduled worker". This marker can. There is
    intentionally no clear() method: resuming requires the owner to remove the
    marker file manually after review.
    """

    def __init__(self, marker_path: str | Path = "state/automation_halt.json") -> None:
        self.marker_path = Path(marker_path)

    def active(self) -> bool:
        return self.marker_path.exists()

    def engage(self, reason: str = "OWNER_EMERGENCY_STOP") -> Path:
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.marker_path.with_suffix(".tmp")
        payload = {
            "halted_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.marker_path)
        return self.marker_path
