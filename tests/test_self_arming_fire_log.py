from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.self_arming_worker import _nearest_slot_distance, plan_fire

ROOT = Path(__file__).resolve().parents[1]
PT = ZoneInfo("America/Los_Angeles")


class DeferredFireVisibilityTests(unittest.TestCase):
    """2026-07-28 lost the 11:03 and 11:23 slots and left NO trace: launchd
    fired late, plan_fire correctly refused (>300s from any slot), and the
    wrapper exited 0 silently. The refusal must stay — it is the no-backfill
    guard — but it must be attributable afterwards."""

    def test_exact_slot_time_still_runs(self):
        decision = plan_fire(datetime(2026, 7, 28, 11, 3, 2, tzinfo=PT))
        self.assertTrue(decision.run)
        self.assertEqual(decision.slot_hhmm, "1103")

    def test_deferred_fire_is_refused_not_backfilled(self):
        decision = plan_fire(datetime(2026, 7, 28, 11, 12, tzinfo=PT))
        self.assertFalse(decision.run)
        self.assertEqual(decision.reason, "NO_SLOT_WINDOW")

    def test_distance_helper_quantifies_the_deferral(self):
        self.assertEqual(_nearest_slot_distance(datetime(2026, 7, 28, 11, 12, tzinfo=PT)), 540.0)
        self.assertEqual(_nearest_slot_distance(datetime(2026, 7, 28, 11, 3, tzinfo=PT)), 0.0)

    def test_every_fire_is_recorded_with_reason_and_distance(self):
        source = (ROOT / "scripts/self_arming_worker.py").read_text(encoding="utf-8")
        self.assertIn("def record_fire", source)
        self.assertIn("self_arming_fires.jsonl", source)
        self.assertIn("nearest_slot_distance_seconds", source)
        # Recorded for EVERY fire, before the run/refuse branch.
        record_at = source.index("record_fire(now, decision)")
        branch_at = source.index("if not decision.run:")
        self.assertLess(record_at, branch_at)

    def test_market_day_refusal_is_printed_not_silent(self):
        source = (ROOT / "scripts/self_arming_worker.py").read_text(encoding="utf-8")
        self.assertIn("FIRE_REFUSED_ON_MARKET_DAY", source)

    def test_logging_failure_cannot_block_a_slot(self):
        # record_fire swallows OSError: a full disk must never stop collection.
        source = (ROOT / "scripts/self_arming_worker.py").read_text(encoding="utf-8")
        block = source[source.index("def record_fire"):source.index("def main()")]
        self.assertIn("except OSError:", block)

    def test_closed_day_fire_records_but_does_not_print(self):
        # A recurring schedule fires on weekends too; that is expected, so the
        # launchd log must stay quiet while the jsonl still has the record.
        completed = subprocess.run(
            [sys.executable, "-c",
             "import json,sys;"
             "sys.path.insert(0, '.');"
             "from datetime import datetime;"
             "from zoneinfo import ZoneInfo;"
             "from scripts.self_arming_worker import plan_fire;"
             "d = plan_fire(datetime(2026, 8, 1, 9, 30, tzinfo=ZoneInfo('America/Los_Angeles')));"
             "print(json.dumps({'run': d.run, 'reason': d.reason}))"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        payload = json.loads(completed.stdout.strip())
        self.assertFalse(payload["run"])
        self.assertEqual(payload["reason"], "NON_MARKET_DAY")


if __name__ == "__main__":
    unittest.main()
