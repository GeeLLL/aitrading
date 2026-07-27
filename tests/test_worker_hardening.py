from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class PromptTemplateTests(unittest.TestCase):
    """The worker passes {kind} explicitly; the template must accept every
    placeholder the worker supplies and must not leave unfilled braces."""

    def test_template_formats_with_worker_placeholders(self):
        template = (ROOT / "prompts/launchd_pilot_worker.md").read_text(encoding="utf-8")
        rendered = template.format(
            run_id="pilot-20260727-0703",
            kind="PILOT_SAMPLE",
            scheduled_for="2026-07-27T07:03:00-07:00",
            symbol="SPY",
            log_root="/tmp/log_root",
            trajectory_root="/tmp/trajectories",
        )
        self.assertIn("Run kind: PILOT_SAMPLE", rendered)
        self.assertIn("/tmp/log_root/pilot-20260727-0703.summary.json", rendered)
        self.assertNotIn("{kind}", rendered)

    def test_template_tells_agent_not_to_infer_kind_from_run_id(self):
        template = (ROOT / "prompts/launchd_pilot_worker.md").read_text(encoding="utf-8")
        self.assertIn("Do not\ninfer the kind from the Run ID string", template)

    def test_universe_comes_from_config_not_a_hardcoded_count(self):
        # Route B (commit 9cf9f10) added SOFI/RIVN/BAC to config/universe.toml,
        # but a stale "ten-symbol" phrase in this prompt made agents evaluate
        # only 10 names for 5 straight days. The config must be the single
        # source of truth and the prompt must never pin a symbol count.
        template = (ROOT / "prompts/launchd_pilot_worker.md").read_text(encoding="utf-8")
        self.assertIn("config/universe.toml", template)
        self.assertNotIn("ten-symbol research universe", template)

        from strategy.universe import load_universe_policy
        policy = load_universe_policy(ROOT / "config/universe.toml")
        self.assertIn("SOFI", policy["symbols"])
        self.assertIn("RIVN", policy["symbols"])
        self.assertIn("BAC", policy["symbols"])


class CanaryRetryTests(unittest.TestCase):
    def test_transient_failure_then_success_completes_with_two_attempts(self):
        from scripts import launchd_shadow_worker as worker
        from execution.official_mcp_collector import OfficialCollectorError

        receipt = mock.Mock(path="/tmp/x.json", content_sha256="abc")
        verified = mock.Mock(path=Path("/tmp/x.json"), content_sha256="abc")
        with mock.patch.object(
            worker, "collect_official_raw_snapshot",
            side_effect=[OfficialCollectorError("transient stream drift"), receipt],
        ), mock.patch.object(worker.RawDataVault, "verify", return_value=verified), \
             mock.patch.object(worker, "_rebuild_dashboard"), \
             mock.patch.object(worker, "_atomic_json") as write:
            code = worker._run_canary("run-1", "SPY", Path("/tmp/ack"), Path("/tmp/logroot"))

        self.assertEqual(code, 0)
        summary = write.call_args[0][1]
        self.assertEqual(summary["status"], "COMPLETED")
        self.assertEqual(summary["attempts"], 2)
        self.assertIsNone(summary["failure_reason"])

    def test_two_failures_fail_closed_with_both_reasons(self):
        from scripts import launchd_shadow_worker as worker
        from execution.official_mcp_collector import OfficialCollectorError

        with mock.patch.object(
            worker, "collect_official_raw_snapshot",
            side_effect=OfficialCollectorError("boom"),
        ), mock.patch.object(worker, "_rebuild_dashboard"), \
             mock.patch.object(worker, "_atomic_json") as write:
            code = worker._run_canary("run-1", "SPY", Path("/tmp/ack"), Path("/tmp/logroot"))

        self.assertEqual(code, 2)
        summary = write.call_args[0][1]
        self.assertEqual(summary["status"], "FAILED_CLOSED")
        self.assertEqual(summary["attempts"], 2)
        self.assertIn("attempt 1", summary["failure_reason"])
        self.assertIn("attempt 2", summary["failure_reason"])

    def test_retry_budget_constants_stay_inside_slot_spacing(self):
        from scripts import launchd_shadow_worker as worker
        # Pilot worst case: full first attempt + fast-failure retry must land
        # inside the 1200s slot spacing with margin for ack/dashboard overhead.
        worst_pilot = worker.PILOT_TIMEOUT_SECONDS  # slow attempt, no retry
        worst_retry = worker.PILOT_FAST_FAILURE_SECONDS + worker.PILOT_RETRY_TIMEOUT_SECONDS
        self.assertLessEqual(worst_pilot, 1200 - 120)
        self.assertLessEqual(worst_retry, 1200 - 120)
        self.assertLessEqual(worker.CANARY_RETRY_ELAPSED_CAP_SECONDS + 300, 1200 - 120)


class AgentProcessGroupTests(unittest.TestCase):
    def test_child_pid_is_recorded_for_the_reaper(self):
        import json
        import tempfile
        from scripts.launchd_shadow_worker import _run_agent_once

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pid_path = base / "run.pid"
            pid_path.write_text(json.dumps({"schema_version": 1, "pid": 1}), encoding="utf-8")
            code, timed_out = _run_agent_once(
                ["/bin/cat"], "hello", base / "out.log", base / "err.log",
                timeout_seconds=10, attempt=1, pid_path=pid_path,
            )
            self.assertEqual(code, 0)
            self.assertFalse(timed_out)
            record = json.loads(pid_path.read_text(encoding="utf-8"))
            self.assertIsInstance(record.get("child_pid"), int)

    def test_timeout_kills_the_whole_child_group(self):
        import tempfile
        from scripts.launchd_shadow_worker import _run_agent_once

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            code, timed_out = _run_agent_once(
                ["/bin/sleep", "30"], "", base / "out.log", base / "err.log",
                timeout_seconds=1, attempt=1,
            )
            self.assertEqual(code, 2)
            self.assertTrue(timed_out)
            # the sleep must not linger past the call (group was killed)
            import subprocess
            leftovers = subprocess.run(
                ["pgrep", "-f", "^/bin/sleep 30$"], capture_output=True, text=True,
            )
            self.assertNotEqual(leftovers.returncode, 0, "orphaned sleep survived the group kill")


class SafetyInvariantTests(unittest.TestCase):
    def test_no_environment_bypass_in_worker_source(self):
        source = (ROOT / "scripts/launchd_shadow_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("SHADOW_TRADING_TEST_MODE", source)
        self.assertIn("SLOT_FIRED_OUTSIDE_180_SECONDS", source)
        self.assertIn("NO_REGISTERED_SLOT_WITHIN_180_SECONDS", source)

    def test_overlap_skip_now_exits_nonzero(self):
        source = (ROOT / "scripts/launchd_shadow_worker.py").read_text(encoding="utf-8")
        overlap_block = source.split('"status": "OVERLAP_SKIPPED"')[1][:220]
        self.assertIn("return 2", overlap_block)


if __name__ == "__main__":
    unittest.main()
