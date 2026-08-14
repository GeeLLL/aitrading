# Is our signal family one that works? — literature review, 2026-08-14

Written overnight at the owner's request ("研究什么样的策略是最好的"). Nothing in
here was deployed. The frozen strategy is unchanged; this is input for a decision
the owner makes awake.

## What we measured ourselves first

From vaulted bars, replaying the full frozen signal (regime + VWAP + EMA
alignment + six-bar breakout) and scoring the **signal's own direction** over the
40-minute horizon:

| volume ratio | n | mean directional return | win rate |
|---|---|---|---|
| < 1.5 | 564 | −0.052% | 44.0% |
| 1.5–1.8 | 73 | −0.096% | 43.8% |
| 1.8–2.5 | 62 | −0.043% | 46.8% |
| ≥ 2.5 | 26 | −0.281% | 23.1% |

Every bucket negative, every win rate below 50%. Separately, the *magnitude* of
the subsequent move in the 1.5–1.8 bucket (median 0.169%) is indistinguishable
from a randomly chosen bar (0.170%) — the volume threshold is not selecting
anything. Only above 2.5 does magnitude separate (0.272%).

Caveats stated plainly: ~3 weeks, one regime, and n of 26–73 in the upper
buckets is not enough to trust a point estimate. The n=564 bucket and the
consistent sign across all four are the parts worth weighting.

## What the literature says about this exact family

**Our signal is an OHLCV-derived intraday signal** — it consumes nothing but
open/high/low/close/volume bars. That family has been tested systematically and
published as a negative result.

A falsification study across **fourteen** OHLCV intraday signal families in MNQ
futures found gross returns of roughly **0.07 to 1.50 points per trade against an
assumed 2-point round-trip friction** — i.e. the gross edge, where it existed at
all, was smaller than the cost of trading it. The one family with strong
statistics (t = 3.23) produced **22 trades in three years**, below any deployment
threshold. The author's framing is that consistent evaluation standards revealed
where intraday OHLCV approaches *systematically* fail under realistic execution.

An older NYSE intraday study reached the same shape of conclusion from the other
direction: reversal effects existed at mid-quote, but **"when replacing mid-quote
pricing by the best bid-ask pricing assumptions, that are more realistic, these
effects are too small to generate profits."**

This is not a fringe finding, and it matches our own numbers. We are not
observing bad luck in a small sample of a good strategy; we are reproducing a
documented result about a signal family.

## The cost problem is worse for us than for the studies

Those studies trade the underlying. We buy options, which adds two costs the
futures studies do not carry:

- **Spread as a fraction of premium.** Measured live: the ex-ante hurdle on the
  2026-08-03 BAC calibration trade was **4.01% of premium** (spread 3.00 + fees
  0.40 + decay 0.17). Our frozen friction constant understated real cost by
  **2.55–4.29×**.
- **Theta.** Strictly negative, paid every minute held, and not offset by
  anything unless the move arrives.

Leverage cuts both ways: a 0.17% typical underlying move times an ATM elasticity
of ~15 is ~2.5% on the option — still short of a ~4% hurdle, and that is before
the sign of our measured edge is negative.

Worth noting against the "just use 0DTE, the spreads are tight" intuition: JP
Morgan researchers found **E-mini futures have lower effective transaction costs
on a delta-adjusted basis** than 0DTE options, despite the options' tight nominal
spreads.

## What does have documented predictive power

The literature does not say "nothing works intraday". It says the working signals
come from **options-market microstructure**, used to predict the *underlying* —
the opposite direction of information flow from what we are doing:

- **Signed order flow** predicts contemporaneous and one-month-ahead returns.
- **Hedging-demand spikes** produce 1–5 day drifts followed by reversal.
- **Open interest, moneyness concentration, and trader-segmented sentiment**
  improve short-horizon prediction.
- **Implied-volatility skew** predicts returns negatively — but with a large
  asterisk: that predictability **drops by at least two-thirds once high-fee
  stocks are excluded**, so much of it appears to be stock-borrow cost rather
  than an anomaly. This is a good example of a published edge that mostly
  evaporates on inspection, and a reason to be slow rather than fast here.

Note what all of these require: options chain data over time — open interest,
volume by strike, IV surface — none of which we currently persist beyond the one
contract we quote. Moving in this direction is a **data-collection change first**,
not a strategy parameter change.

## Recommendation

1. **Do not tune thresholds on this data.** The measurement says the current
   thresholds select nothing; lowering them adds trades with no edge and real
   cost. Raising them to 2.5+ selects larger moves but the directional return
   there was the worst of all four buckets on n=26.
2. **Do not flip the sign either**, tempting as the consistent negative is. A
   3-week single-regime sample is exactly the kind of evidence that produces a
   confident, wrong inversion.
3. **Keep collecting under the now-working pipeline.** As of 2026-08-14 a
   simulated fill is possible at all for the first time, positions take the side
   their signal picked, and every qualifying symbol is recorded. The next weeks
   of data will be the first that actually measure the strategy rather than
   measuring our bugs.
4. **Start persisting options-chain state** (open interest and volume by strike,
   IV by strike) on the symbols we already touch. It costs one extra vaulted call
   per slot and is the prerequisite for testing any of the signal families above.
   Without it, we cannot even ask the question in three months' time.

## Sources

- [Structural Limits of OHLCV-Based Intraday Signals in MNQ Futures: A Systematic Falsification Study](https://arxiv.org/abs/2605.04004)
- [Intraday Price Reversals and Momentum: Evidence from the NYSE](http://arno.uvt.nl/show.cgi?fid=144554)
- [Why does options market information predict stock returns? (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S0304405X25001618)
- [Option-Implied Volatility Measures and Stock Return Predictability](https://eprints.lancs.ac.uk/id/eprint/80351/2/JoD_1_.pdf)
- [Zero-day options: unique market dynamics and risk considerations (Risk.net)](https://www.risk.net/insight/markets/7959202/zero-day-options-unique-market-dynamics-and-risk-considerations)
- [0DTE trading strategies: practical approaches to more efficient backtesting (Numerix)](https://www.numerix.com/resources/white-paper/0dte-trading-strategies-practical-approaches-more-efficient-backtesting)
- [Improving 0-DTE trading returns by avoiding expensive exits (Volos)](https://www.volossoftware.com/insights/improving-0dte-trading-returns)
