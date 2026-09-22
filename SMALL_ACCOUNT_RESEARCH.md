# Small-account strategy research

No candidate is claimed to outperform an experienced trader. No live orders or account switches were made.

Four fixed rule sets tested on chronological periods. The entire dataset has been seen in earlier work, so later periods are retrospective validation, not a pristine holdout.

| Strategy | Period | Exposure | End USD | Trades | Win rate | Max DD |
|---|---|---|---:|---:|---:|---:|
| current_rules | development | current_contract | 50.00 | 0 | N/A | 0.00% |
| current_rules | development | hypothetical_100x_smaller | 51.94 | 287 | 32.4% | 3.07% |
| current_rules | validation | current_contract | 50.00 | 0 | N/A | 0.00% |
| current_rules | validation | hypothetical_100x_smaller | 50.13 | 61 | 31.1% | 2.01% |
| current_rules | later_test | current_contract | 50.00 | 0 | N/A | 0.00% |
| current_rules | later_test | hypothetical_100x_smaller | 50.21 | 55 | 29.1% | 1.92% |
| current_rules | later_test_double_cost | hypothetical_100x_smaller | 50.46 | 56 | 33.9% | 1.99% |
| trend_pullback | development | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_pullback | development | hypothetical_100x_smaller | 50.85 | 41 | 34.1% | 0.94% |
| trend_pullback | validation | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_pullback | validation | hypothetical_100x_smaller | 50.08 | 7 | 28.6% | 0.46% |
| trend_pullback | later_test | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_pullback | later_test | hypothetical_100x_smaller | 49.64 | 9 | 11.1% | 1.24% |
| trend_pullback | later_test_double_cost | hypothetical_100x_smaller | 49.63 | 9 | 11.1% | 1.26% |
| trend_breakout | development | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_breakout | development | hypothetical_100x_smaller | 49.33 | 35 | 25.7% | 1.70% |
| trend_breakout | validation | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_breakout | validation | hypothetical_100x_smaller | 49.92 | 3 | 33.3% | 0.44% |
| trend_breakout | later_test | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_breakout | later_test | hypothetical_100x_smaller | 49.75 | 5 | 20.0% | 0.98% |
| trend_breakout | later_test_double_cost | hypothetical_100x_smaller | 49.74 | 5 | 20.0% | 0.98% |
| trend_reclaim | development | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_reclaim | development | hypothetical_100x_smaller | 50.68 | 32 | 40.6% | 0.64% |
| trend_reclaim | validation | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_reclaim | validation | hypothetical_100x_smaller | 50.28 | 3 | 33.3% | 0.19% |
| trend_reclaim | later_test | current_contract | 50.00 | 0 | N/A | 0.00% |
| trend_reclaim | later_test | hypothetical_100x_smaller | 49.92 | 7 | 14.3% | 0.70% |
| trend_reclaim | later_test_double_cost | hypothetical_100x_smaller | 49.91 | 7 | 14.3% | 0.71% |

## Decision

Candidates passing all research gates: NONE.
Gate: at least 30 trades, net positive, profit factor above 1.1, drawdown below 10%, no drawdown halt in EVERY development/validation/later-test/double-cost period. No candidate was activated automatically.

## Assumptions

Starting balance $50; risk 0.3%; maximum five entries/day; 2% realized daily loss entry halt; losing-streak sizing reductions/cooldown; one position; original-risk breakeven/profit-lock/trailing; 4-hour stale close; 10% equity drawdown entry halt; margin estimated at 1:500 with half equity reserved. Chronological H1/H4 indicators are built only from complete M15 groups and published when their candles close.
Measured bar spreads, assumed $7 round-trip commission per standard-lot equivalent and $0.10 slippage per entry and market/stop exit; double-cost stress doubles these. Swap omitted.
M15 bar-level stop/trailing/fill/margin models approximate live behavior. Missing tick paths and quote-level broker restrictions mean this is still not exact live parity. Bid OHLC is used; ambiguous stop/target bars prefer stops.
Current contract uses actual .01 standard-lot minimum. Hypothetical exposure uses .0001 standard-lot minimum/step, 100 times smaller. This is NOT a verified XAUUSDc backtest, and does not assert your account can trade it. All balances remain USD equivalents, not cent-account display balances.
Do not interpret cent display balances as dollar growth. Broker actual symbol, volume, currency conversion, fees and availability must be verified before switching. No deposits or account creation are required for this research.

Source for possible smaller account type: [Exness Standard Cent documentation](https://get.exness.help/hc/en-us/articles/17537782786588-Standard-Cent-account). Availability is subject to the user account and region.

Reproduce: `python research/evaluate_candidates.py`. Detailed outputs: `data/strategy_research/evaluation.json`.

## Current-contract affordability

Historical 1.5 ATR stops at 0.01 standard lot, with the stated cost assumptions: minimum estimated stop cost $2.91; median $11.66. The $50 account budget at 0.3% is $0.15. These estimates are scenario-dependent, not maximum possible losses. No risk setting was increased.
