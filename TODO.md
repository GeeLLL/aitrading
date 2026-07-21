# Project Roadmap and Blocking TODOs

## Project mandate

> **Risk control is the foundation. Profitable, risk-adjusted operation is the
> objective. The system must use AI where it can create measurable options-
> trading advantage, while deterministic code retains authority over safety,
> accounting, order validity, and execution state.**

This mandate is a design constraint, not a slogan:

- safety controls must prevent ruin, unauthorized exposure, and operational
  errors, but safety alone does not make a successful trading system;
- profitability must be evaluated after spread, slippage, fees, failed fills,
  settlement constraints, and model/operational errors;
- AI must demonstrate incremental value against a non-AI baseline rather than
  merely replacing deterministic calculations with model output;
- no return target may weaken a hard-risk rule or force the system to trade;
- `NO_TRADE`, abstention, and fail-closed behavior remain first-class outcomes.

## Current status

- System mode: `READ_ONLY`
- Live trading: prohibited
- Formal multi-day Shadow evidence collection: not started
- Current strategy parameters: research hypotheses, not validated optima
- Architecture review: completed; local foundations below are partly complete

## P0 — blocks formal Shadow evidence collection

### P0.0 Make scheduling observable and fail closed

- [x] Require an atomic start ACK for every scheduled Shadow/Pilot job.
- [x] Detect missing, invalid, mismatched, and late ACKs deterministically.
- [x] Implement an independent macOS LaunchAgent watchdog that is not dependent
  on the Codex automation scheduler. Installation/bootstrapping remains an
  explicit host configuration step.
- [x] Alert the owner once and create a durable fail-closed incident record
  within two minutes of a missed start.
- [x] Add fail-closed restart/catch-up policy and end-to-end expectation ->
  ACK -> watchdog canary tests. Missed market samples are never backfilled;
  delayed close summaries may use local logs only and remain incomplete.

Acceptance: `ACTIVE` is never treated as execution proof; a missing ACK blocks
the session and raises a visible incident.

### P0.1 Wire the kill switch into every order decision

- [x] Make the independent kill-switch state a mandatory validator input.
- [x] Reject when the switch is engaged, missing, unreadable, or inconsistent.
- [ ] Recheck immediately before every future submit, replace, or new-entry
  action; exit-only emergency handling must have a separately defined path.
- [x] Add an independent final execution-boundary guard and bypass tests.

Acceptance: engaging or corrupting the switch deterministically blocks every
new-entry path, including after restart.

### P0.2 Fail closed on unknown option identity or direction

- [x] Remove any fallback that converts an unknown option type into Call/Put.
- [x] Require verified instrument ID, underlying, type, strike, expiration,
  quantity, and opening/closing effect.
- [x] Add malformed and contradictory normalized-response tests.

Acceptance: missing, unknown, or conflicting contract identity produces an
explicit rejection and can never become a trade candidate.

### P0.3 Separate the trusted data plane from AI interpretation

- [x] Add immutable, hashed storage for official raw MCP responses.
- [x] Record source timestamp, authoritative local receipt timestamp, request
  parameters, schema version, and content hash.
- [x] Move strategy-critical indicator, breakout, quote-age, and contract-filter
  calculations to deterministic local code. Official raw-payload integration
  remains a market-session verification item.
- [x] Preserve raw snapshot hash and schema lineage for replay and audit.

Acceptance: identical raw snapshots produce identical features without an LLM.

### P0.4 Establish trustworthy time and completed-bar semantics

- [x] Add bar start/end timestamps, interval, completion status, source time,
  and local receipt time.
- [x] Reject incomplete, future, stale, duplicated, or misaligned bars.
- [x] Calculate quote age from a trusted local receipt/decision clock.
- [x] Add explicit anti-look-ahead tests.

Acceptance: no decision can use information unavailable at decision time.

### P0.5 Build durable state and restart reconciliation

- [x] Define a persistent order/position finite-state machine.
- [x] Persist intent, validation, submission, acknowledgement, partial fill,
  fill, cancellation, exit, and terminal states.
- [x] Add idempotency keys and duplicate-order prevention.
- [x] Implement one combined startup/reconnect gate that reconciles local order,
  broker order/position, cash, and safety state before permitting a new entry.
  Official broker payload wiring remains a market-session integration item.
- [x] Halt on unknown, contradictory, or unrecognized broker state.

Acceptance: simulated crashes at every state recover without duplicate orders
or lost positions.

### P0.6 Add cash settlement accounting

- [x] Track settled cash, unsettled proceeds, buying power, withdrawable cash,
  expected settlement dates, and reserved order funds separately.
- [x] Encode cash-account reuse restrictions and reject unavailable funds.
- [x] Implement deterministic reconciliation against official settled,
  unsettled, reserved, and buying-power fields. Official field availability
  remains a market-session verification item.

Acceptance: buying power alone can never authorize an otherwise unsettled or
reserved purchase.

### P0.7 Repair experiment integrity and reporting

- [x] Classify every deterministic risk rejection as a hard-rule result without
  relying on a reason-code naming prefix.
- [x] Order PnL and drawdown by actual event timestamps, not filenames.
- [x] Preserve `NO_TRADE`, rejected, pilot, drill, and formal Shadow records as
  distinct populations.
- [x] Add a controlled, auditable `DESIGN -> SHADOW` authorization record while keeping
  live trading disabled.

Acceptance: reports reproduce the event log chronologically and cannot count
pilots/drills as strategy performance.

### P0.8 Use authoritative instrument-specific market sessions

- [x] Add an instrument-specific session model for regular and special closes;
  official session ingestion remains an integration task.
- [x] Define deterministic new-entry, monitoring, and forced-exit cutoffs.
- [x] Reject when official market status or applicable session is unknown.

Acceptance: time checks remain correct on normal, early-close, and special-
session days.

## P1 — blocks any Live Mode

### P1.1 Event-driven execution and friction simulator

- [x] Model displayed bid/ask size, latency ticks, partial fills, rejects,
  tick size, forced-exit spread cost, and cancel/fill races locally. Queue
  position calibration and quote-stream replay still require observed data.
- [x] Produce optimistic, base, and stress quote-based fill scenarios.
- [x] Add explicit per-contract and regulatory-fee inputs to net execution
  estimates. Exact Robinhood/venue fee calibration remains required.

### P1.2 Options-specific expression model

- [x] Separate underlying-direction alpha from option-contract expression.
- [x] Implement deterministic Delta/Gamma/Theta/Vega expression plus IV level,
  skew, term slope, IV/realized-volatility ratio, spread, and exit-liquidity
  assessment. Historical calibration and event-risk feed remain required.
- [x] Treat fixed stop/profit percentages as hypotheses requiring evidence.

### P1.3 Research methodology and statistical evidence

- [x] Implement chronological training, validation, and out-of-sample splits.
- [x] Implement chronological expanding walk-forward and regime-level summary
  primitives. Evidence cannot be claimed until sufficient market samples exist.
- [x] Implement reproducible bootstrap expectancy intervals, worst/tail loss,
  drawdown, and regime summaries. Larger samples and sensitivity runs remain.
- [ ] Guard against multiple testing, selection bias, and survivorship bias.

### P1.4 Define the economic objective correctly

- [ ] Treat monthly return as a reporting metric, never a forced trading quota.
- [ ] Optimize net risk-adjusted growth subject to capital, ruin-probability,
  drawdown, operational-error, turnover, and liquidity constraints.
- [ ] Separate the real `$300` reliability account from research shadow-notional
  budget bands used to measure opportunity-set bias.

### P1.5 Full operational qualification

- [x] Define and locally simulate OAuth expiry, MCP disconnect, stale/missing data, unknown order state,
  partial fill, duplicate intent, process crash, restart, early close, forced
  exit, clock skew, cash/position mismatch, and cancel/fill races. True end-to-
  end qualification against official market/account responses remains open.
- [ ] Rehearse the complete manual emergency-stop procedure with the owner.
- [ ] Require explicit owner approval for the exact strategy version.

## P2 — demonstrate and maximize AI advantage

### P2.0 Accelerated profitability and AI-edge protocol

- [x] Define the three-lane historical, prospective quote-trajectory, and
  paired AI-baseline experiment in
  `research/ACCELERATED_PROFITABILITY_AND_AI_EDGE_V1_CN.md`.
- [ ] Freeze `BASE_25`, `BASE_30`, and `AI_RANK_V1` before the next market
  sample; changes require a new version and cannot rewrite prior outcomes.
- [ ] Capture repeated official option bid/ask trajectories for selected,
  rejected, and near-miss candidates, not only the daily policy choice.
- [ ] Add a multiple-testing ledger and date-clustered sequential evidence
  report with explicit success and futility boundaries.
- [ ] Label every reconstructed historical option result
  `SYNTHETIC_OPTION_STRESS`; never mix it with observed quote PnL.

### P2.1 Establish non-AI baselines

- [x] Implement deterministic and random-control comparison primitives.
  Underlying-only and realized market evidence remain to be collected.
- [ ] Measure whether AI improves net expectancy, drawdown, opportunity ranking,
  or bad-trade avoidance after all costs.

### P2.2 Build a bounded AI alpha layer

- [ ] Use AI for market-regime/context classification, cross-sectional ranking,
  event interpretation from authorized sources, anomaly diagnosis, hypothesis
  generation, and post-trade attribution.
- [ ] Keep data parsing, timestamps, feature calculations, accounting, risk,
  order construction, and execution state deterministic.
- [x] Require a strict versioned AI-output schema, model/prompt versions, bounded confidence,
  explicit abstention, caching, and replayability.

### P2.3 Monitor AI quality and drift

- [x] Implement feature-mean drift, schema-failure, NO_TRADE-rate, rejection,
  and incremental-lift monitoring primitives. Calibration thresholds remain
  hypotheses until formal Shadow supplies enough observations.
- [x] Reject unavailable, invalid, contradictory, or out-of-schema AI output;
  confidence/OOD policy still requires evidence.

Acceptance: AI remains in the system only where controlled experiments show a
repeatable net benefit; narrative plausibility is not evidence.

## Mandatory human-parameter review before Live Mode

Status: **OPEN — blocks Live Mode**

Review and experimentally validate every human-selected parameter, including:

- entry window and end-of-day cutoffs;
- 7–21 DTE and absolute Delta ranges;
- minimum volume/open interest, maximum spread, and quote age;
- universe membership, breakout lookback, EMA/VWAP/volume conditions;
- `$75` Stage 1 cap, later-stage caps, and the `$120` absolute ceiling;
- option stop, profit target, maximum holding time, and earnings blackout;
- fills, latency, slippage, fees, sampling, and monitoring cadence;
- drawdown, consecutive-loss pause, account circuit breaker, and scaling gates.

Required evidence:

- preserve the original baseline and version every alternative;
- preserve `NO_TRADE` and rejection decisions, not only entries;
- test normal, volatile, event-driven, and abnormal-data sessions;
- evaluate net expectancy, payoff ratio, profit factor, tail loss, drawdown,
  fill rate, opportunity coverage, friction sensitivity, rule compliance, and
  operational reliability;
- document results and require explicit owner approval for any new version.

## Immutable release gates

- Formal Shadow cannot begin until all P0 acceptance criteria pass.
- Live Mode cannot begin until P0 and P1 pass, the mandatory parameter review
  is complete, Shadow evidence is accepted, emergency procedures are rehearsed,
  and the owner explicitly approves the exact release.
- P2 may progress in research alongside Shadow, but AI can affect a live trade
  only after demonstrating incremental value and passing the same controls.
- The LLM may propose changes but may never modify or relax hard-risk rules.
