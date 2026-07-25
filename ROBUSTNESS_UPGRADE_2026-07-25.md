# Robustness Upgrade Plan — 2026-07-25

Status: PROPOSED (awaiting owner go-ahead). Goal: the read-only collector runs
every market day with zero manual intervention and zero silent misses, while the
order-safety fail-closed guarantees stay iron.

## Diagnosis (evidence-based, from two independent audits)

**Why the week 07-17→07-24 produced almost no data:** the launchd worker is
pinned to a single calendar `Day=`, so it must be manually re-armed every
morning (`prepare_observation_day.py` only *prints* the `launchctl` commands).
After 07-21 nobody re-armed it → 07-22/23/24 the scheduler had no trigger at all
→ zero data. Every other failure the week fixed (Codex `COUNT=1`, repo-move path
break, Codex→Claude transcription/earnings-overflow canary failures) had landed
green by mid-07-21, but they are irrelevant on days the scheduler never fires.
Secondary: the host slept ~10:23 on 07-21, truncating even the one prepared day.

**The 07-24 "robustness" commit made safety worse** while trying to escape the
incident lock-in: incident 24h auto-expiry, incident file auto-deletion, and an
env-var test-mode bypass — all of which silently defeat fail-closed. Plus a real
Good Friday calendar bug, duplicate plists under one Label, a coordinator that
backfills (violating policy), and zero test coverage.

## The core architectural fix

Separate two concerns that were conflated:

1. **Collection resilience** — a missed read-only sample is a *reliability*
   event, not a safety event. It must be recorded and alerted, but must NEVER
   block the next run (re-collecting read-only data is harmless). This removes
   the "one incident bricks everything → must bypass" whipsaw at the root.
2. **Order-safety fail-closed** — never act on unknown/contradictory account,
   order, position, quote, or kill-switch state; order tools never enabled. This
   stays iron. It lives in the risk validator / order path, which does not even
   run during read-only collection.

Consequence: we do NOT need incident TTL, file deletion, or a test-mode bypass.
Collection simply stops blocking on collection-misses.

## Workstream (prioritized)

### P0-A — Reverse the three fail-closed regressions
- Revert incident 24h TTL auto-expiry (`scheduler_watchdog.py`).
- Revert incident file auto-deletion (`cleanup_expired_incidents.py`) — never
  delete a CRITICAL incident; archive at most.
- Remove `SHADOW_TRADING_TEST_MODE` incident bypass (`launchd_shadow_worker.py`,
  `run_sampling_recovery_mode.py`).
- Replace with: **collection incidents no longer gate the worker at all**; if a
  genuine safety event is ever added, it clears only via an explicit, timestamped,
  logged, single-run `resolution` object (the mechanism already exists at
  `scheduler_watchdog.py` resolution.status).

### P0-B — Kill daily manual re-arming (the dominant root cause)
- One self-arming recurring launchd job (single Label), `StartInterval` phase-safe
  or a small fixed slot set, that on each fire:
  1. checks a corrected market calendar → no-ops cleanly on weekends/holidays;
  2. auto-registers that day's expectations if not present (no human step);
  3. resolves the current slot and runs the real worker;
  4. survives sleep (`KeepAlive` tuned so a clean "not my slot" exit does NOT
     respawn-loop; catch-up only within the slot window, never backfill).
- Delete the conflicting second plist generator; one Label, one job, wired to the
  actually-gating worker, paths derived from `sys.executable`/repo root.

### P1-A — Fix the market calendar
- Correct Good Friday (2026-04-03, 2027-03-26) and derive holidays (compute
  Easter) rather than hardcode; add half-day early closes; anchor to the exchange
  timezone; define behavior past the table horizon (fail toward "closed+alert",
  not silently "open").

### P1-B — Fix the truncation-overflow root (not just delete T)
- One symbol whose `get_equity_historicals`/chain response overflows the tool cap
  currently fails the WHOLE snapshot closed. Bound the request (fewer
  bars/strikes) and/or degrade gracefully per-symbol so one bad symbol doesn't
  zero the sweep.

### P1-C — Consolidate the monitors
- Collapse `robust_sampling_coordinator` + `enhanced_monitor` +
  `active_shadow_monitor` into one observer; remove the coordinator's backfill;
  make all market-hours logic come from the corrected `market_calendar`.

### P2 — Tests
- All new reliability code has zero coverage. Add tests for: market-calendar
  (incl. the Good Friday regression), self-arming/slot resolution, the "collection
  miss does not block" behavior, and truncation-overflow graceful degradation.

## Acceptance criteria (proposed)
- **5 consecutive market days, zero manual intervention, zero silent miss:** every
  expected slot either produces a snapshot or a visible incident; no day is
  skipped for lack of arming; no run is bypassed invisibly.
- Order-safety invariants unchanged: READ_ONLY, order tools absent, kill switch
  engaged, no backfill of missed market samples.
- Every new/changed reliability component has tests.

## What stays iron vs becomes resilient
- **Iron (unchanged):** no order tools; no trading on unknown state; no backfill
  of missed market samples; immutable hashed raw vault; never treat `ACTIVE` as
  proof of execution.
- **Resilient (new):** collection auto-arms daily, no-ops on non-market days,
  retries within the slot window, and never bricks itself on a read-only miss.
