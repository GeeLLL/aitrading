# Emergency Stop Runbook

This procedure is deliberately manual and must be rehearsed before Live Mode.
It is not needed to operate the current READ_ONLY build.

## Trigger conditions

- unknown or contradictory order/position state;
- duplicate-order risk or MCP/OAuth failure during an order lifecycle;
- stale/missing market data or market-session uncertainty;
- cash/account mismatch, equity circuit breaker, or three consecutive losses;
- any rule bypass, unexpected process behavior, or owner instruction.

## Ordered response

1. Engage the local kill switch (`python3 main.py kill`). This also writes
   `state/automation_halt.json`, which makes every subsequent scheduled
   Shadow/Pilot worker run refuse to start. Resuming automation requires the
   owner to remove that marker file manually after review; there is no command
   to clear it.
2. Confirm the configuration disables new entries.
3. In the official Robinhood interface, inspect all open option orders.
4. Cancel open orders manually or confirm the count is zero.
5. Inspect all option positions and verify contract identity and quantity.
6. Close a known position manually if safe; otherwise escalate to Robinhood.
7. Disconnect Robinhood agent authorization when system integrity is uncertain.
8. Preserve logs and record the incident without credentials/account numbers.
9. Do not recover or re-arm without root-cause analysis and owner approval.

## Pass criteria

Every step in `monitoring.emergency_runbook.REQUIRED_STEPS` must have explicit,
timestamped evidence. A partial rehearsal does not qualify Live Mode.
