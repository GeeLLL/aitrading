from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import build_status
from monitoring.shadow_readiness import build_shadow_readiness


AUTOMATION_ROOT = Path.home() / ".codex/automations"
EXPECTED_ROOT = ROOT / "logs/scheduler/expected"
OUTPUT = ROOT / "logs/qualification/latest.preopen.json"


def _automation_text(automation_id: str) -> str | None:
    path = AUTOMATION_ROOT / automation_id / "automation.toml"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _check_automation(automation_id: str) -> tuple[bool, list[str]]:
    text = _automation_text(automation_id)
    if text is None:
        return False, ["AUTOMATION_MANIFEST_MISSING"]
    reasons: list[str] = []
    if 'status = "ACTIVE"' not in text:
        reasons.append("AUTOMATION_NOT_ACTIVE")
    if "scheduler-ack" not in text:
        reasons.append("ATOMIC_START_ACK_NOT_REQUIRED")
    if "COUNT=1" in text:
        reasons.append("DEFECTIVE_COUNT_ONE_RECURRENCE")
    if "READ_ONLY" not in text:
        reasons.append("READ_ONLY_REQUIREMENT_MISSING")
    return not reasons, reasons


def _check_legacy_automation_paused(automation_id: str) -> tuple[bool, list[str]]:
    text = _automation_text(automation_id)
    if text is None:
        return False, ["AUTOMATION_MANIFEST_MISSING"]
    reasons = [] if 'status = "PAUSED"' in text else ["DUPLICATE_SCHEDULER_NOT_PAUSED"]
    return not reasons, reasons


def _launchd_service_loaded(label: str, required_fragment: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{subprocess.check_output(['/usr/bin/id', '-u'], text=True).strip()}/{label}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and required_fragment in result.stdout, result.stdout[-2000:]


def build_report() -> dict[str, object]:
    status = build_status()
    readiness = build_shadow_readiness().to_dict()
    automation_results = {
        automation_id: dict(zip(("passed", "reasons"), _check_legacy_automation_paused(automation_id)))
        for automation_id in ("robinhood-canary", "robinhood", "robinhood-pilot", "robinhood-pilot-2")
    }
    expected = []
    for path in sorted(EXPECTED_ROOT.glob("*.expected.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") == "EXPECTED":
            expected.append(path.name)
    required_expectations = {
        "preopen_canary": "launchd-canary-20260721-0610.expected.json",
        "market_gate": "market-gate-20260721-0635.expected.json",
        "first_sample": "pilot-20260721-0703.expected.json",
        "close_summary": "pilot-close-canary-20260721-1305.expected.json",
    }
    expectation_checks = {
        name: filename in expected for name, filename in required_expectations.items()
    }
    watchdog_ok, watchdog_detail = _launchd_service_loaded(
        "com.robinhood-ai-trader.watchdog", "run interval = 60 seconds"
    )
    shadow_worker_ok, shadow_worker_detail = _launchd_service_loaded(
        "com.robinhood-ai-trader.shadow-worker-v2", "calendarinterval"
    )
    safety_ok = (
        status["system_mode"] == "READ_ONLY"
        and status["live_trading_enabled"] is False
        and status["order_tools_enabled"] is False
        and status["kill_switch_engaged"] is True
    )
    passed = (
        safety_ok
        and readiness["offline_ready"] is True
        and all(item["passed"] for item in automation_results.values())
        and all(expectation_checks.values())
        and watchdog_ok
        and shadow_worker_ok
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PREOPEN_READY" if passed else "NO_GO",
        "safety_ok": safety_ok,
        "safety": status,
        "offline_ready": readiness["offline_ready"],
        "formal_shadow_authorized": readiness["formal_shadow_authorized"],
        "market_checks_pending": readiness["pending_market_checks"],
        "automations": automation_results,
        "expected_run_count": len(expected),
        "required_expectations": expectation_checks,
        "watchdog_loaded": watchdog_ok,
        "watchdog_detail_tail": watchdog_detail,
        "shadow_worker_loaded": shadow_worker_ok,
        "shadow_worker_detail_tail": shadow_worker_detail,
        "note": "launchd is the sole primary scheduler; legacy app automations are paused to prevent duplicate runs. PREOPEN_READY authorizes read-only Pilot preparation only; market gates and formal Shadow authorization remain separate.",
    }


def main() -> int:
    report = build_report()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PREOPEN_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
