# Historical gold test results

Read-only broker export; no trading orders submitted.

Source: configured MT5 demo broker, XAUUSDm, M15 BID candles; currency USD.
Exported history: 2025-04-23 04:45:00+00:00 to 2026-09-18 20:30:00+00:00 (30,000 closed bars).
Evaluated continuous segment: 2025-04-23 04:45:00+00:00 to 2026-07-30 10:30:00+00:00 (29,917 bars). Excluded 83 bars after a 49-day data gap; recent calendar-month performance cannot be measured.

| Scenario | Start | End | Trades | Win rate | Net P/L | Max drawdown | Skipped sizing |
|---|---:|---:|---:|---:|---:|---:|---:|
| 50_continuous_history | 50.00 | 50.00 | 0 | N/A | 0.00 | 0.00% | 15165 |
| 10000_continuous_history_diagnostic | 10000.00 | 10825.75 | 506 | 35.8% | 825.75 | 4.11% | 10869 |
| 50_last_30_days | 50.00 | 50.00 | 0 | N/A | 0.00 | 0.00% | 1149 |
| 10000_last_30_days_diagnostic | 10000.00 | 9896.52 | 14 | 28.6% | -103.48 | 1.15% | 1050 |
| 10000_last_30_days_wide_spread | 10000.00 | 9896.52 | 14 | 28.6% | -103.48 | 1.15% | 1050 |

## Assumptions and limits

Fixed settings: risk 0.300%, stop 1.5 ATR, target 3.0 ATR, confidence threshold 0.65. No optimization was performed.
Signals use prior completed candles, next-bar execution, broker volume grid, and broker account-currency contract valuation.
Spread is constant per scenario, measured from exported bar spreads (median or 90th percentile). Commission is an assumed 7 account-currency units per round-trip lot; slippage is an assumed 0.10 price units per entry and stop/market exit. Swap is omitted.
This is a signal baseline. Historical MTF alignment, live trailing, margin, daily-loss and emergency gates are not simulated. Consequently these are NOT exact full-bot performance results.
The 10,000 scenarios only diagnose signals at a balance that can afford the minimum lot; they do not project returns for a 50 account.
Zero trades means no measurable win rate or demonstrated profitable edge. Closed-candle equity drawdown can miss intrabar extremes.
The last 30 days are a fixed recent test, not a proven untouched holdout: prior strategy development may have used overlapping dates.
Results are historical and do not establish future returns.

Reproduce: `python research/run_historical_evaluation.py`. Raw CSVs and JSON are in `data/historical_test/`.
