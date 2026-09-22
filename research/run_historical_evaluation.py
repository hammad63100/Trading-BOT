"""Reproducible offline, fixed-parameter signal-baseline evaluation."""
import json
import logging
import math
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backtest.backtester import run_backtest
from core.risk_manager import floor_volume
from config import settings as cfg

logging.disable(logging.CRITICAL)
root = Path(__file__).resolve().parents[1]
folder = root / 'data' / 'historical_test'
meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
df = pd.read_csv(folder / 'gold_m15.csv')
df['time'] = pd.to_datetime(df.time, utc=True)
value = meta['value_per_price_per_lot']
if not value or not math.isfinite(value) or value <= 0:
    raise RuntimeError('Broker contract valuation is missing')
# Never carry indicators or positions across a multi-week missing-data interval.
gaps = df.time.diff() > pd.Timedelta(days=5)
segments = [g.reset_index(drop=True) for _, g in df.groupby(gaps.cumsum())]
usable = [g for g in segments if len(g) >= 1000 and g.time.iloc[-1] - g.time.iloc[0] >= pd.Timedelta(days=30)]
if not usable:
    raise RuntimeError('No continuous segment long enough for evaluation')
raw_bars = len(df)
df = usable[-1]
quality = dict(raw_bars=raw_bars, selected_bars=len(df), excluded_bars=raw_bars-len(df),
    selected_start=str(df.time.iloc[0]), selected_end=str(df.time.iloc[-1]),
    multiweek_gap_detected=bool(gaps.any()))
(folder / 'data_quality.json').write_text(json.dumps(quality, indent=2),encoding='utf-8')
print(json.dumps({'data_quality':quality}),flush=True)
results = []
last = df.time.iloc[-1]
cutoff = last - pd.Timedelta(days=30)
start = int(df.time.searchsorted(cutoff))
# 201 warm-up bars precede the reported evaluation period.
month = df.iloc[max(0, start - 201):].reset_index(drop=True)
scenarios = [
    ('50_continuous_history', df, 50., meta['spread_price_median']),
    ('10000_continuous_history_diagnostic', df, 10000., meta['spread_price_median']),
    ('50_last_30_days', month, 50., meta['spread_price_median']),
    ('10000_last_30_days_diagnostic', month, 10000., meta['spread_price_median']),
    ('10000_last_30_days_wide_spread', month, 10000., meta['spread_price_p90']),
]
for name, history, capital, spread in scenarios:
    counts = {'eligible_signals': 0, 'skipped_minimum_volume': 0}
    def measured_volume(*args):
        size = floor_volume(*args)
        counts['eligible_signals'] += 1
        counts['skipped_minimum_volume'] += int(size == 0)
        return size
    with patch('core.risk_manager.floor_volume', measured_volume):
        r = run_backtest(history, initial_equity=capital, risk_per_trade=cfg.MAX_RISK_PER_TRADE,
            atr_sl_mult=cfg.ATR_SL_MULTIPLIER, atr_tp_mult=cfg.ATR_TP_MULTIPLIER,
            min_confidence=cfg.MIN_SIGNAL_CONFIDENCE, spread_points=spread,
            commission_per_lot=7., slippage_points=.10, contract_size=value,
            volume_min=meta['volume_min'], volume_step=meta['volume_step'], volume_max=meta['volume_max'])
    row = dict(scenario=name, initial_equity=capital, final_equity=capital+r.net_pnl,
        net_pnl=r.net_pnl, return_pct=100*r.net_pnl/capital, trades=r.total_trades,
        wins=r.winning_trades, losses=r.losing_trades, win_rate_pct=r.win_rate*100 if r.total_trades else None,
        profit_factor=r.profit_factor if r.total_trades and math.isfinite(r.profit_factor) else None,
        max_drawdown=r.max_drawdown, max_drawdown_pct=100*r.max_drawdown_pct,
        max_consecutive_losses=r.max_consecutive_losses, commission=r.total_commission,
        spread_price=spread, evaluation_start=str(history.time.iloc[201]), evaluation_end=str(history.time.iloc[-1]), **counts)
    results.append(row)
    pd.DataFrame([asdict(t) for t in r.trades]).to_csv(folder / f'{name}_trades.csv', index=False)
    pd.DataFrame({'equity':r.equity_curve}).to_csv(folder / f'{name}_equity.csv', index=False)
    print(json.dumps(row), flush=True)
(folder / 'results.json').write_text(json.dumps({'metadata':meta, 'results':results}, indent=2),encoding='utf-8')
lines = ['# Historical gold test results', '', 'Read-only broker export; no trading orders submitted.', '',
    f"Source: configured MT5 demo broker, {meta['symbol']}, M15 BID candles; currency {meta['account_currency']}.",
    f"Exported history: {meta['first']} to {meta['last']} ({meta['bars']:,} closed bars).",
    f"Evaluated continuous segment: {quality['selected_start']} to {quality['selected_end']} ({quality['selected_bars']:,} bars). Excluded {quality['excluded_bars']} bars after a 49-day data gap; recent calendar-month performance cannot be measured.", '',
    '| Scenario | Start | End | Trades | Win rate | Net P/L | Max drawdown | Skipped sizing |',
    '|---|---:|---:|---:|---:|---:|---:|---:|']
for r in results:
    win = f"{r['win_rate_pct']:.1f}%" if r['win_rate_pct'] is not None else 'N/A'
    lines.append(f"| {r['scenario']} | {r['initial_equity']:.2f} | {r['final_equity']:.2f} | {r['trades']} | {win} | {r['net_pnl']:.2f} | {r['max_drawdown_pct']:.2f}% | {r['skipped_minimum_volume']} |")
lines += ['', '## Assumptions and limits', '',
    f"Fixed settings: risk {cfg.MAX_RISK_PER_TRADE:.3%}, stop {cfg.ATR_SL_MULTIPLIER} ATR, target {cfg.ATR_TP_MULTIPLIER} ATR, confidence threshold {cfg.MIN_SIGNAL_CONFIDENCE}. No optimization was performed.",
    'Signals use prior completed candles, next-bar execution, broker volume grid, and broker account-currency contract valuation.',
    'Spread is constant per scenario, measured from exported bar spreads (median or 90th percentile). Commission is an assumed 7 account-currency units per round-trip lot; slippage is an assumed 0.10 price units per entry and stop/market exit. Swap is omitted.',
    'This is a signal baseline. Historical MTF alignment, live trailing, margin, daily-loss and emergency gates are not simulated. Consequently these are NOT exact full-bot performance results.',
    'The 10,000 scenarios only diagnose signals at a balance that can afford the minimum lot; they do not project returns for a 50 account.',
    'Zero trades means no measurable win rate or demonstrated profitable edge. Closed-candle equity drawdown can miss intrabar extremes.',
    'The last 30 days are a fixed recent test, not a proven untouched holdout: prior strategy development may have used overlapping dates.',
    'Results are historical and do not establish future returns.', '',
    'Reproduce: `python research/run_historical_evaluation.py`. Raw CSVs and JSON are in `data/historical_test/`.', '']
(root/'HISTORICAL_TEST_RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
