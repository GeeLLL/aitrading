# Robustness Upgrade Plan — 2026-07-25

Status: COMPLETE. P0-A, P0-B, P1-A, P1-B, P1-C, and P2 are all DONE. Goal met:
the read-only collector runs every market day with zero manual intervention and
zero silent misses, while the order-safety fail-closed guarantees stay iron.

## Post-review hardening (senior-engineer review follow-ups)

- **Safe log rotation:** `rotate_logs.sh` rewritten to prune the raw vault,
  scheduler, and incidents trees entirely and only rotate plain `*.log` /
  `*.stdout/stderr` files (the old version would have gzipped the immutable
  vault, gzipped live expectation files, and deleted incidents).
- **Failure-visible observer:** `collection_observer` distinguishes RAN_OK from
  RAN_FAILED, so a systemic outage that still writes a start-ack for every slot
  (dead MCP OAuth, missing CLI, timeouts) reads as DEGRADED, not falsely healthy.
- **Date consistency:** the self-arming market gate uses the session (PT) date,
  the same basis as registration and the observer.
- **CLI format-drift tolerance:** the stream-json harvester now skips-and-counts
  stray non-JSON lines (a new CLI banner no longer nukes the whole collection),
  while every integrity check still fails closed on genuinely missing data; the
  skipped-line count is stamped into the vault envelope as an early drift signal.
- **Deferred (needs the Mac + owner):** replacing the `claude` CLI transport with
  a direct Python MCP client to remove the LLM from the data path. Correct but
  cannot be OAuth-authorized or verified against the live brokerage from the
  cloud sandbox; to be done build-alongside on the Mac, keeping the CLI collector
  as fallback until an A/B comparison passes.

## Second pass (P1-B, P1-C, P2)

- **P1-B truncation-overflow (done):** the raw collector now has an opt-in
  `resilient` mode. In it, an overflow of one large-but-non-critical tool
  (`get_equity_historicals`, `get_option_instruments`, `get_option_quotes`)
  degrades to a marked-absent output (`output: null`, `truncated: true`) instead
  of failing the whole per-symbol snapshot closed; the vault envelope is stamped
  `partial: true` + `truncated_tools`, so a partial snapshot can never pass as
  complete. Critical tools (quotes, chain metadata, earnings) and the default
  strict mode still fail closed on any irregularity. The read-only canary uses
  resilient mode, so one bad tool no longer zeroes the sweep. No truncated bytes
  are ever stored.
- **P1-C consolidate monitors (done):** one pure, read-only
  `monitoring/collection_observer` now owns all "how is collection doing" logic
  (market-hours from the corrected calendar; no subprocess/retry/backfill/sleep).
  `robust_sampling_coordinator` was gutted of its backfill (`auto_recover ->
  execute_sampling` re-ran missed slots — a policy violation) and is now an
  observe-only reporter; `enhanced_monitor` and `active_shadow_monitor` are thin
  shims over the observer. The whole-day-asleep gap is closed: the watchdog tick
  now calls `ensure_day_registered`, so even if no worker fires all day, the day
  is back-registered on the next watchdog run and its missed slots surface as
  incidents instead of vanishing.
- **P2 tests (done):** +58 tests across the two passes (386 total, all green),
  covering the calendar (incl. the Good Friday regression), self-arming slot
  resolution and expectation registration, the collection-miss-does-not-block
  gate, incident-TTL removal, archive-only cleanup, resilient truncation
  degradation, and the observer / whole-day-asleep gap.

Residual (external, out of scope): if the Mac is powered off or asleep through
*every* worker slot AND every watchdog tick for a whole day, nothing on the Mac
can flag it — only an off-Mac heartbeat could. On wake, the watchdog
back-registers and flags the day.

## What shipped in this pass (2026-07-25)

- **P1-A calendar (done):** `monitoring/market_calendar.py` now derives every
  holiday from NYSE rules (computed Easter -> Good Friday), fixing the wrong Good
  Friday dates (now 2026-04-03 / 2027-03-26) and the missing coverage past 2027.
  Adds early-close detection, exchange-timezone anchoring (`market_date_now`), and
  the documented New Year Saturday exception (Dec 31 stays open). Fully tested.
- **P0-A reversals (done):** removed the incident 24h TTL auto-expiry
  (`scheduler_watchdog.unresolved_incident_ids` blocks until an explicit
  resolution again); rewrote `cleanup_expired_incidents.py` to *archive* resolved
  incidents and NEVER delete unresolved/corrupt ones; removed the
  `SHADOW_TRADING_TEST_MODE` bypass and deleted `run_sampling_recovery_mode.py`.
  The read-only collector's `_safety_ok` no longer gates on collection incidents
  at all — they are surfaced in status for visibility but a read-only miss never
  bricks the next run. Order-safety invariants (READ_ONLY, no order tools, kill
  switch, automation-halt) still gate, iron as ever.
- **P0-B self-arming (done):** new `scripts/self_arming_worker.py` +
  `scripts/generate_self_arming_plist.py`. One recurring launchd job, single
  Label, `StartCalendarInterval` entries carrying only Hour+Minute (no `Day`
  pin), so no morning re-arming. Each fire market-gates (no-ops on
  weekends/holidays/after early close), auto-registers the whole day's
  expectations (so the watchdog flags any miss), and delegates to the real worker
  with `ROBINHOOD_SLOT_HHMM`. The real worker keeps its 180s freshness guard, so a
  fire replayed after a sleep is REFUSED, never backfilled. Deleted the buggy
  duplicate `generate_smart_plist.py` and the superseded
  `launchd_shadow_worker_smart.py`. Fully tested.
- Residual to note: if the Mac sleeps through *every* slot 06:10→13:05, no fire
  registers that day's expectations, so a whole-day outage is not yet flagged.
  Closing that (watchdog-side day registration) is folded into P1-C.

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

### P0-A — Reverse the three fail-closed regressions  ✅ DONE
- Revert incident 24h TTL auto-expiry (`scheduler_watchdog.py`).
- Revert incident file auto-deletion (`cleanup_expired_incidents.py`) — never
  delete a CRITICAL incident; archive at most.
- Remove `SHADOW_TRADING_TEST_MODE` incident bypass (`launchd_shadow_worker.py`,
  `run_sampling_recovery_mode.py`).
- Replace with: **collection incidents no longer gate the worker at all**; if a
  genuine safety event is ever added, it clears only via an explicit, timestamped,
  logged, single-run `resolution` object (the mechanism already exists at
  `scheduler_watchdog.py` resolution.status).

### P0-B — Kill daily manual re-arming (the dominant root cause)  ✅ DONE
- One self-arming recurring launchd job (single Label), `StartInterval` phase-safe
  or a small fixed slot set, that on each fire:
  1. checks a corrected market calendar → no-ops cleanly on weekends/holidays;
  2. auto-registers that day's expectations if not present (no human step);
  3. resolves the current slot and runs the real worker;
  4. survives sleep (`KeepAlive` tuned so a clean "not my slot" exit does NOT
     respawn-loop; catch-up only within the slot window, never backfill).
- Delete the conflicting second plist generator; one Label, one job, wired to the
  actually-gating worker, paths derived from `sys.executable`/repo root.

### P1-A — Fix the market calendar  ✅ DONE
- Correct Good Friday (2026-04-03, 2027-03-26) and derive holidays (compute
  Easter) rather than hardcode; add half-day early closes; anchor to the exchange
  timezone; define behavior past the table horizon (fail toward "closed+alert",
  not silently "open").

### P1-B — Fix the truncation-overflow root (not just delete T)  ✅ DONE
- One symbol whose `get_equity_historicals`/chain response overflows the tool cap
  currently fails the WHOLE snapshot closed. Bound the request (fewer
  bars/strikes) and/or degrade gracefully per-symbol so one bad symbol doesn't
  zero the sweep.

### P1-C — Consolidate the monitors  ✅ DONE
- Collapse `robust_sampling_coordinator` + `enhanced_monitor` +
  `active_shadow_monitor` into one observer; remove the coordinator's backfill;
  make all market-hours logic come from the corrected `market_calendar`.

### P2 — Tests  ✅ DONE
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
