#!/usr/bin/env python3
"""Self-arming launchd entry point for the read-only shadow collector.

The dominant cause of the 2026-07-17→07-24 data gap was date-pinned launchd:
every plist entry carried a specific ``Month``/``Day``, so the schedule had to be
regenerated and re-loaded by hand every single morning. When nobody re-armed it,
the scheduler simply had no trigger and the day produced zero data — silently.

This worker removes the daily human step. It is driven by a *recurring*
``StartCalendarInterval`` plist (one entry per slot HH:MM, no ``Day`` pin — see
``generate_self_arming_plist.py``) so launchd fires it at each slot every day,
forever. On each fire this script:

  1. anchors "now" in the session timezone and checks the corrected market
     calendar — on weekends, holidays, and after an early close it no-ops
     cleanly (exit 0), so the recurring schedule never acts on a closed market;
  2. auto-registers the *whole* current market day's expectations (idempotent),
     so the watchdog can flag any slot that fails to run — no human
     ``scheduler-expect-day`` step, and no silent miss;
  3. delegates the actual read-only collection to the real, safety-gating worker
     (``launchd_shadow_worker.py``), passing ``ROBINHOOD_SLOT_HHMM`` so it
     collects the exact slot this fire belongs to.

What deliberately stays iron: the real worker still enforces every order-safety
invariant and the 180s freshness guard, so a fire replayed long after the Mac
wakes is REFUSED rather than backfilled. This wrapper only decides *whether* to
delegate; it never relaxes a safety check and never backfills a missed sample.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.daily_schedule import DAILY_SLOTS, SESSION_TIMEZONE, expected_runs_for_date
from monitoring.market_calendar import (
    EXCHANGE_TIMEZONE,
    is_early_close,
    is_market_open,
)
from monitoring.scheduler_watchdog import register_expected_run

# A fire must land within this many seconds of a registered slot to count as a
# slot fire. It is wider than the real worker's 180s freshness guard on purpose:
# this wrapper only decides whether to delegate; the real worker still applies
# the strict 180s guard, so nothing stale is ever collected.
SLOT_MATCH_WINDOW_SECONDS = 300

# NYSE early-close sessions end at 1pm Eastern.
EARLY_CLOSE_EXCHANGE_TIME = time(13, 0)


@dataclass(frozen=True)
class FireDecision:
    """Pure decision for one fire; ``run`` is False when we should no-op."""

    run: bool
    reason: str
    slot_hhmm: str | None = None
    kind: str | None = None
    symbol: str | None = None


def _early_close_local_cutoff() -> time:
    """The session-timezone wall-clock time of a 1pm-ET early close (10:00 PT)."""
    # Both America/New_York and America/Los_Angeles observe DST together, so this
    # is stable, but we compute it rather than hardcode to stay correct.
    reference = datetime.now(EXCHANGE_TIMEZONE).replace(
        hour=EARLY_CLOSE_EXCHANGE_TIME.hour,
        minute=EARLY_CLOSE_EXCHANGE_TIME.minute,
        second=0,
        microsecond=0,
    )
    return reference.astimezone(SESSION_TIMEZONE).time()


def plan_fire(now: datetime) -> FireDecision:
    """Decide, purely, what this fire should do. ``now`` must be tz-aware."""
    # Anchor everything to the session (PT) date the slots are defined in and the
    # worker/observer register under, so the market gate, the early-close cutoff,
    # and expectation registration can never disagree about which day it is.
    local = now.astimezone(SESSION_TIMEZONE)

    if not is_market_open(local.date()):
        return FireDecision(run=False, reason="NON_MARKET_DAY")

    # On an early-close day, do not run (or register) slots at/after the close.
    if is_early_close(local.date()) and local.time() >= _early_close_local_cutoff():
        return FireDecision(run=False, reason="AFTER_EARLY_CLOSE")

    # Find the nearest registered slot; a recurring StartCalendarInterval fires
    # exactly at a slot, so on a healthy fire the distance is ~0.
    nearest: tuple[float, int, int, str, str] | None = None
    for (hour, minute), (kind, symbol) in DAILY_SLOTS.items():
        slot_time = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        distance = abs((local - slot_time).total_seconds())
        if nearest is None or distance < nearest[0]:
            nearest = (distance, hour, minute, kind, symbol)

    if nearest is None or nearest[0] > SLOT_MATCH_WINDOW_SECONDS:
        return FireDecision(run=False, reason="NO_SLOT_WINDOW")

    _distance, hour, minute, kind, symbol = nearest
    return FireDecision(
        run=True,
        reason="SLOT_MATCH",
        slot_hhmm=f"{hour:02d}{minute:02d}",
        kind=kind,
        symbol=symbol,
    )


def register_todays_expectations(now: datetime, *, directory: str | Path = ROOT / "logs/scheduler/expected") -> int:
    """Idempotently register every expectation for the current market day.

    Registering the whole day on any fire means a later slot that never fires
    still has an expectation on record, so the watchdog flags it as a miss
    instead of it vanishing silently. Registration is idempotent (same file per
    run_id), so repeated fires are harmless.
    """
    local = now.astimezone(SESSION_TIMEZONE)
    count = 0
    for run_id, scheduled in expected_runs_for_date(local.date()):
        register_expected_run(run_id=run_id, scheduled_for=scheduled, directory=directory)
        count += 1
    return count


FIRE_LOG = ROOT / "logs/scheduler/self_arming_fires.jsonl"


def _nearest_slot_distance(now: datetime) -> float | None:
    """Seconds from ``now`` to the closest registered slot (for forensics)."""
    local = now.astimezone(SESSION_TIMEZONE)
    distances = [
        abs((local - local.replace(hour=hour, minute=minute, second=0, microsecond=0)).total_seconds())
        for (hour, minute) in DAILY_SLOTS
    ]
    return min(distances) if distances else None


def record_fire(now: datetime, decision: FireDecision) -> None:
    """Append one line per launchd fire, whatever the outcome.

    A refused fire used to leave NO trace at all: when launchd defers a fire
    past the 300s match window the wrapper exited 0 silently, so a lost slot
    could not be attributed afterwards (observed 2026-07-28 11:03 and 11:23).
    Every fire is now recorded; refusals on a market day are the interesting
    ones. Best-effort — logging must never prevent a slot from running.
    """
    local = now.astimezone(SESSION_TIMEZONE)
    try:
        FIRE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FIRE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "at": local.isoformat(),
                "run": decision.run,
                "reason": decision.reason,
                "slot_hhmm": decision.slot_hhmm,
                "nearest_slot_distance_seconds": _nearest_slot_distance(now),
                "market_day": is_market_open(local.date()),
            }, sort_keys=True) + "\n")
    except OSError:
        pass


def main() -> int:
    now = datetime.now(SESSION_TIMEZONE)
    decision = plan_fire(now)
    record_fire(now, decision)
    if not decision.run:
        # A closed-day fire is expected and harmless. A refusal on a MARKET day
        # means launchd fired outside the match window (a deferred/coalesced
        # fire) and a slot was just lost — print it so the launchd stdout log
        # carries the evidence too. The refusal itself stays correct: a late
        # fire must never backfill.
        if is_market_open(now.astimezone(SESSION_TIMEZONE).date()):
            print(json.dumps({
                "status": "FIRE_REFUSED_ON_MARKET_DAY",
                "reason": decision.reason,
                "at": now.astimezone(SESSION_TIMEZONE).isoformat(),
                "nearest_slot_distance_seconds": _nearest_slot_distance(now),
            }, sort_keys=True))
        return 0

    # Make the day's misses visible before delegating.
    register_todays_expectations(now)

    environment = dict(os.environ)
    environment["ROBINHOOD_SLOT_HHMM"] = decision.slot_hhmm or ""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/launchd_shadow_worker.py")],
        cwd=ROOT,
        env=environment,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
