from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from execution.execution_guard import evaluate_execution_boundary
from monitoring.kill_switch import KillSwitch


class ExecutionGuardTests(unittest.TestCase):
    def test_missing_arm_marker_blocks_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            decision = evaluate_execution_boundary(
                local_orders=(), broker_open_idempotency_keys=set(), broker_position_count=0,
                kill_switch=KillSwitch(Path(directory) / "absent"),
            )
            self.assertFalse(decision.allowed)
            self.assertTrue(any(reason.startswith("KILL_SWITCH_ENGAGED") for reason in decision.reasons))
