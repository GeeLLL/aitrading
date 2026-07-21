# Scheduler Incident — 2026-07-20

## Summary

The 06:15 PDT read-only Shadow automation was configured as `ACTIVE` but did
not create a task, thread, start acknowledgement, or experiment log.

No Robinhood write tool was called and no trade or account mutation occurred.

## Root cause

The automation used a one-occurrence recurrence (`COUNT=1`) without an explicit
start timestamp. In this Codex scheduler path, the implicit initial occurrence
consumed the count and left no future runnable occurrence at 06:15.

## Controlled reproduction

- A local-only 10:13 probe with `COUNT=1` did not start.
- The same probe without `COUNT` started and wrote
  `logs/scheduler_probe_no_count_20260720.txt`.
- The Mac had active display/lid-open sleep-prevention assertions around 06:15.
- No scheduler task/thread existed for the failed run.

## Corrective actions completed

- Paused the defective original automation.
- Removed `COUNT` from active recovery schedules and bounded them with an end
  time instead.
- Added atomic scheduled-start acknowledgements.
- Added deterministic missing, invalid, mismatched, and late ACK detection.
- Added five tests and reran the full suite: 178/178 passing.
- Added pre-close canary runs so the final summary is not the first test of the
  schedule.

## Remaining qualification work

- Prove at least one complete next-session canary -> sample -> close-summary
  sequence using the installed independent LaunchAgent watchdog.
- Never treat automation status `ACTIVE` as evidence that a run occurred.

## Additional corrective actions completed after close

- Installed a user-level macOS LaunchAgent that checks pre-registered expected
  runs every 60 seconds independently of Codex automation execution.
- Added durable critical incident records and one-time macOS notifications.
- Added strict ACK envelope, run ID, schedule, and lateness validation.
- Added a fail-closed recovery policy: missed market samples are never
  backfilled; a delayed close summary may use existing local logs only and must
  remain marked incomplete.
- Pre-registered 14 read-only Pilot samples plus the 13:05 close summary for
  2026-07-21.

This incident blocks formal Shadow qualification until the remaining
operational checks pass.
