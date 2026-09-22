# Gold bot safety review — 19 September 2026

This revision fixes execution and evaluation defects. It does not establish a profitable strategy or promise $50 to $2,000 in a month. Signal confidence is a rule score, not a calibrated win probability.

## Findings and changes

- Position sizing forced at least 0.01 lot even when that exceeded the risk budget. It now floors volume to the broker step and skips unaffordable trades. Invalid numbers and invalid stops block entries.
- Execution estimates stop loss in account currency through MT5 order_calc_profit, validates normalized stop prices, checks fresh quotes, free margin and broker order_check before submission. The live risk estimate covers entry-to-stop price loss; actual commissions, swap, gaps and fills can exceed it.
- The local journal contained 30 OPEN rows and no CLOSED rows. SL/TP exits were not reconciled. Broker deal history now supplies realized results, including commissions, fees and swap. Incomplete reconciliation blocks entries and preserves existing records. Journal files should be kept separate per broker account; legacy rows from another account require investigation, not deletion to bypass the gate.
- Signals use closed candles. MTF fetches also use closed candles, and missing MTF data blocks entries. Feed age now accounts for timeframe duration.
- Historical signals no longer call live broker data. ML probability is aligned to LONG or SHORT direction. Trend-momentum entries now require the appropriate EMA trend, preventing a flat trend from passing four other votes.
- Trailing uses the original journal stop distance, so moving the stop to breakeven no longer stops subsequent trailing. Positions without a known original risk are not trailed automatically.
- Emergency per-position loss is capped by the smaller of the fixed money limit and configured equity risk. Losing-streak cooldown uses elapsed time; a daily streak halt expires on the next UTC day. Monitoring continues during entry halts.
- One entry attempt per closed candle per running process. Restart persistence and multi-process locking are not implemented; run one instance only.
- Demo mode now actually executes on a verified demo account, and refuses real accounts. The old demo path only logged signals even though emergency management could submit closes.
- The backtester previously sized using 10 and calculated PnL using 100. It now uses one configurable contract value, rounds volume down, includes costs, enters after the signal candle, handles stop gaps conservatively, and marks floating equity through final liquidation. Sharpe is a non-annualized bar-return statistic.

## Current local testing configuration

The existing credentials are preserved. Local .env is now demo, with 0.3% per-trade risk, one open position, one same-direction position, five entries per day, and feed-staleness checks enabled. On $50, 0.3% is $0.15; many gold contracts at 0.01 lot will therefore be rejected. Leverage does not reduce loss for a given lot size and adverse price move.

No trading loop was started and no order was submitted during this revision.

## Validation and remaining evidence

Run `python -m pytest -q`. Unit tests use temporary journals and mocked broker APIs. They cover underfunded accounts, invalid sizing, broker rejection, demo-account enforcement, SL/TP reconciliation, breakeven trailing, closed candles and historical sizing/entry timing. They are not evidence of trading returns.

Historical broker data was subsequently exported and evaluated: see HISTORICAL_TEST_RESULTS.md and SMALL_ACCOUNT_RESEARCH.md. No forward demo or pristine out-of-sample performance is claimed.

Offline research command (no broker credentials required):

```powershell
python run.py --mode backtest --csv data/gold_m15.csv --initial-equity 50
```

CSV columns: time (UTC), open, high, low, close, tick_volume; prices are BID OHLC. Python run_backtest accepts contract_size, volume_min, volume_step, volume_max, spread_points, commission_per_lot and slippage_points. Its default contract value is 100 account-currency units per 1.0 price move per lot; default costs are illustrative and must be replaced with broker/account measurements. Cost parameters named points are price units.

The simulator is a signal baseline, not full live parity: it omits higher-timeframe alignment, trailing, margin, daily-loss and some emergency rules. Bar-close drawdown can miss intrabar excursions. It must not be used as proof that the live bot is profitable. Validate unchanged parameters on a separate chronological sample and then on the verified demo account before considering live use.

MT5 reference: https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py
