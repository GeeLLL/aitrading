from __future__ import annotations

from dataclasses import dataclass
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
