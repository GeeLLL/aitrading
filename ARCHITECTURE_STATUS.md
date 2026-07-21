# Architecture Status

Updated: 2026-07-20 (post-close)

## Safety state

- System mode is `READ_ONLY`.
- Live trading and Robinhood order tools remain disabled.
- The local kill switch fails closed and is engaged by default.
- No formal Shadow activation record has been issued.
- All strategy thresholds are hypotheses awaiting controlled validation.

## Locally implemented foundations

- deterministic opening-order validation, including capital, settled cash,
  reservation, position/order, liquidity, quote-age, session, loss-pause, and
  kill-switch checks;
- strict Long Call/Long Put contract identity parsing with no type fallback;
- completed-bar metadata and anti-look-ahead validation;
- durable order-state machine, idempotency keys, restart-safe persistence, and
  fail-closed reconciliation primitives;
- cash settlement and order-fund reservation ledger;
- immutable hashed raw-data vault and a raw-only official MCP collection
  contract;
- chronological Shadow reporting with explicit pilot/drill/rejection
  separation and controlled Shadow authorization records;
- instrument-specific session-window logic;
- optimistic/base/stress quote-friction scenarios;
- option-risk expression features separated from underlying direction;
- strict bounded AI-output contract with explicit abstention;
- chronological research splits, drawdown/performance summaries, and
  incremental AI-lift comparison;
- deterministic RSI, ATR, momentum, ROC, OBV, realized-volatility, and volume
  features calculated from completed local OHLCV bars;
- one combined startup/reconnect reconciliation gate for broker orders,
  positions, cash, and safety state;
- displayed-size, tick, latency, partial-fill, fee, and cancel/fill-race
  execution simulation;
- option IV-level, skew, term-slope, IV/realized-volatility, spread, and exit-
  liquidity assessment;
- expanding walk-forward folds, reproducible bootstrap confidence intervals,
  regime summaries, tail loss, and deterministic/random baseline comparison;
- a versioned inventory of every mandatory human-selected parameter, all
  explicitly marked unvalidated;
- deterministic evidence eligibility that quarantines Pilot, Drill,
  unauthorized, stale, missing, anomalous, and rule-violating runs;
- a complete virtual-position lifecycle using observed ask for entry and bid
  for exit, with gross/friction/net P&L kept separately;
- batch replay summaries, immutable research parameter grids, drift alerts,
  fourteen local fault scenarios, and an owner emergency-stop runbook;
- automated tests for the above controls.

## Not yet integrated or validated

The following cannot be represented as complete merely because supporting
classes exist:

1. Official raw MCP payloads have not been exercised end to end through the new
   raw-data path during market hours.
2. Strategy-critical calculations now have deterministic implementations, but
   official raw-payload-to-feature replay must still be verified end to end.
3. Startup/reconnect reconciliation is implemented but not connected to official Robinhood
   orders and positions because no executable broker adapter exists.
4. Cash reconciliation is implemented, but Robinhood field availability and
   semantics must be verified with an official read-only payload.
5. Product-specific official session data is not yet ingested automatically.
6. The friction simulator models conservative local scenarios but still lacks
   empirical queue-position calibration and full quote-stream replay.
7. IV/skew/term/realized-volatility calculations exist; historical surfaces
   and a trusted event-risk feed have not yet been collected.
8. Walk-forward, bootstrap, and baseline tooling exists, but sufficient formal
   out-of-sample evidence and calibrated AI lift have not yet been collected.
9. Formal Shadow Mode remains blocked until every P0 acceptance criterion is
   verified and the owner explicitly authorizes the exact strategy version.
10. Live Mode remains out of scope until P0/P1, emergency drills, multi-day
    Shadow evidence, parameter review, and explicit owner approval all pass.

## Meaning of the current build

This is a safety- and research-oriented local foundation. It is not a trading
system release, profitability claim, or authorization to trade. The next valid
milestone is trusted raw-data integration followed by formal Shadow evidence;
it is not enabling order tools.
