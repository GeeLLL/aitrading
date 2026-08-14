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

    def test_market_gate_section_uses_deterministic_adjudication_chain(self):
        template = (ROOT / "prompts/launchd_pilot_worker.md").read_text(encoding="utf-8")
        self.assertIn("fresh-quote-probe", template)
        self.assertIn("bar-time-verify", template)
        self.assertIn("market-check-verify", template)
        self.assertIn("--fresh-quote-snapshot", template)
        # The session claim is harvested from the snapshot's own equity quote.
        self.assertIn("Session evidence needs NO action from you", template)
        self.assertIn("Your own\n   words are not evidence here", template)
        # get_equity_tradability requires an account number, which must never
        # enter the vault — the agent is told explicitly not to call it.
        self.assertIn("do not call `get_equity_tradability`", template)
        self.assertNotIn("tradability-probe", template)

    def test_pilot_fill_window_is_adjudicated_within_the_run(self):
        template = (ROOT / "prompts/launchd_pilot_worker.md").read_text(encoding="utf-8")
        self.assertIn("adjudicated inside this same run", template)
        self.assertIn("NO_FILL_WINDOW_EXPIRED", template)
        self.assertIn("FILL_WINDOW_NOT_ADJUDICABLE", template)
        # A limit met in a LATER slot must never count as a fill.
        self.assertIn("NOT a fill", template)

    def test_calibration_trade_is_isolated_from_strategy_evidence(self):
        template = (ROOT / "prompts/launchd_pilot_worker.md").read_text(encoding="utf-8")
        self.assertIn("CALIBRATION_EXCLUDED_FROM_PERFORMANCE", template)
        self.assertIn("never consumes the one-per-day policy-trade budget", template)
        self.assertIn("Never compute P&L\n  yourself", template)
        self.assertIn("Never overwrite an existing entry", template)
        # Entry unconditional at ask; exit at bid — machinery, not selectivity.
        self.assertIn("AT THE OBSERVED ASK", template)
        self.assertIn("OBSERVED BID", template)

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
    def test_authorization_deny_rules_are_declared_but_not_sufficient_alone(self):
        # Honest test: these deny rules block the TOOL paths, but the allowlist
        # also grants Bash(python3:*) = arbitrary code execution, which reaches
        # state/ regardless. Assert both the rules AND the compensating
        # detection, so this can never again read as full prevention.
        from scripts.launchd_shadow_worker import (
            PILOT_ALLOWED_TOOLS,
            PILOT_DISALLOWED_TOOLS,
        )
        self.assertIn("shadow-authorize", PILOT_DISALLOWED_TOOLS)
        self.assertIn("Write(state/**)", PILOT_DISALLOWED_TOOLS)
        self.assertIn("Edit(state/**)", PILOT_DISALLOWED_TOOLS)
        source = (ROOT / "scripts/launchd_shadow_worker.py").read_text(encoding="utf-8")
        self.assertIn('"--disallowedTools", PILOT_DISALLOWED_TOOLS', source)
        # The residual hole is real and must stay visible until it is closed.
        self.assertIn("Bash(python3:*)", PILOT_ALLOWED_TOOLS)
        # Compensating control: every governed file is fingerprint-watched.
        from monitoring.authorization_watch import GOVERNED_PATHS
        self.assertIn("state/shadow_authorization.json", GOVERNED_PATHS.values())
        self.assertIn("state/trading_armed", GOVERNED_PATHS.values())

    def test_market_gate_timeout_stays_under_reaper_deadline(self):
        from scripts.launchd_shadow_worker import MARKET_GATE_TIMEOUT_SECONDS
        from monitoring.worker_reaper import DEFAULT_DEADLINE_SECONDS
        # Gate cap + ack/dashboard overhead must finish before the reaper fires.
        self.assertLessEqual(MARKET_GATE_TIMEOUT_SECONDS + 60, DEFAULT_DEADLINE_SECONDS)

    def test_account_fields_are_redacted_from_transcripts(self):
        import tempfile
        from scripts.launchd_shadow_worker import _redact_account_identifiers
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text(
                '{"account_number":"5PY123456","rhs_account_id":"998877","volume":1641,'
                '"AccountNumber":"ABC-1","note":"account of events"}',
                encoding="utf-8",
            )
            _redact_account_identifiers(path)
            text = path.read_text(encoding="utf-8")
        self.assertNotIn("5PY123456", text)
        self.assertNotIn("998877", text)
        self.assertNotIn("ABC-1", text)
        self.assertIn('"volume":1641', text)          # market data untouched
        self.assertIn("account of events", text)      # non-key prose untouched

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


class TimeoutChainInvariantTests(unittest.TestCase):
    """The three caps have to nest, and it is easy to raise one and silently
    break the nesting: a pilot cap above the reaper deadline gets legitimate
    slow runs killed as hung, and a reaper deadline above the slot spacing lets
    a hung worker survive into the next slot and starve it via the flock."""

    def test_pilot_cap_fits_under_the_reaper_deadline(self):
        from monitoring.worker_reaper import DEFAULT_DEADLINE_SECONDS
        from scripts import launchd_shadow_worker as worker

        overhead = 60      # start ack + dashboard rebuild + process teardown
        self.assertLessEqual(
            worker.PILOT_TIMEOUT_SECONDS + overhead, DEFAULT_DEADLINE_SECONDS,
        )

    def test_reaper_deadline_fits_inside_one_slot(self):
        from monitoring.worker_reaper import DEADLINE_SECONDS, DEFAULT_DEADLINE_SECONDS

        for deadline in (*DEADLINE_SECONDS.values(), DEFAULT_DEADLINE_SECONDS):
            self.assertLess(deadline, 1200)

    def test_the_pilot_cap_covers_a_measured_worst_case_slot(self):
        """bars probe ~145s + open candidate ~90s + calibration entry ~300s."""
        from scripts import launchd_shadow_worker as worker

        self.assertGreaterEqual(worker.PILOT_TIMEOUT_SECONDS, 145 + 90 + 300)


class SlotBudgetWithCandidatesTests(unittest.TestCase):
    """Opening candidates is no longer capped at one per day, so the slot's cost
    now scales with how many symbols qualify. The fan-out has to stay inside the
    pilot cap: a slot reaped mid-run loses everything, including trajectories it
    had already written."""

    def test_the_measured_worst_case_slot_still_fits(self):
        from scripts.deterministic_slots import MAX_CANDIDATES_PER_SLOT
        from scripts import launchd_shadow_worker as worker

        bars = 145                       # 13 chunks, one symbol per call
        per_candidate = 86               # snapshot ~40s + window wait ~35s + probe ~11s
        calibration = 300
        worst = bars + MAX_CANDIDATES_PER_SLOT * per_candidate + calibration
        self.assertLessEqual(worst, worker.PILOT_TIMEOUT_SECONDS)

    def test_the_cap_is_reported_not_silent(self):
        from pathlib import Path
        from scripts import deterministic_slots as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertIn("SLOT_CANDIDATE_CAP", source)
        self.assertIn('"deferred"', source)
