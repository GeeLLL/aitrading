# 2026-07-21 Pre-open and Pilot Execution Plan

Status: `PREOPEN_READY` for read-only Pilot preparation only.

This plan does not authorize Formal Shadow or live trading. The system remains
`READ_ONLY`, `live=false`, `order_tools=false`, with the kill switch engaged.

## Primary scheduler

macOS `launchd` service: `com.robinhood-ai-trader.shadow-worker-v2`

- 06:10 PDT: unattended official-MCP canary. It verifies Codex auth, Robinhood
  OAuth, network access, immutable raw snapshot storage, and SHA-256 replay.
- 06:35 PDT: market qualification gate. Missing or uncertain evidence remains
  `UNKNOWN` and cannot authorize Formal Shadow.
- 07:03–11:23 PDT: fourteen controlled Pilot samples, every twenty minutes.
- 13:05 PDT: local-log-only close summary.

The app-level Codex automations are paused. This prevents duplicate jobs and
prevents a late app automation from overwriting a timely launchd ACK.

## Independent watchdog

`com.robinhood-ai-trader.watchdog` checks pre-registered expectations every 60
seconds. A missing or late ACK creates a durable critical incident and does not
backfill the missed market sample.

## Verified canary evidence

The 2026-07-20 16:43 PDT end-to-end canary completed:

- scheduler ACK written;
- deterministic safety gate passed;
- only official read-only Robinhood MCP tools exposed;
- immutable raw snapshot stored;
- SHA-256 independently verified;
- no order tool exposed or invoked.

Evidence:

- `logs/launchd_worker/2026-07-21/launchd-canary-20260720-1643.json`
- `logs/raw/2026-07-20/2bb9f664-34f6-48b7-a7bd-b9e12ca9b7a7.json`
- SHA-256: `fccb120e9d19b57793358645135ce41f9dd7e254a00d423cf516ba747362c205`

## Failure policy

- No ACK: incident, no backfill.
- OAuth/network/MCP failure: fail closed and preserve the reason.
- Unknown account, market, order, position, quote, or instrument state: no
  qualification and no virtual policy trade.
- Overlap: later run is skipped and logged.
- Formal performance remains unavailable until the six official market checks
  pass and Formal Shadow is separately authorized.

