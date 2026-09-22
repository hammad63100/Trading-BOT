"""Fixed-candidate chronological evaluation. Never changes live strategy settings."""
import json
import logging
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.indicators import compute_indicators
from core.signals import generate_signal
from research.candidates import candidate_signals, higher_timeframe_trend
from research.robust_simulator import simulate

logging.disable(logging.CRITICAL)
root=Path(__file__).resolve().parents[1]
out=root/'data'/'strategy_research'
out.mkdir(parents=True,exist_ok=True)
df=pd.read_csv(root/'data'/'historical_test'/'gold_m15.csv')
df.time=pd.to_datetime(df.time,utc=True)
segments=[g.reset_index(drop=True) for _,g in df.groupby((df.time.diff()>pd.Timedelta(days=5)).cumsum())]
df=max(segments,key=len)
df=compute_indicators(df)
print('Features prepared',len(df),flush=True)
candidates=candidate_signals(df)
legacy=np.zeros(len(df),dtype=int)
for i in range(201,len(df)):
    signal=generate_signal(df.iloc[max(0,i-250):i+1])
    legacy[i]={'LONG':1,'SHORT':-1,'FLAT':0}[signal.direction]
h1=higher_timeframe_trend(df,1);h4=higher_timeframe_trend(df,4)
legacy[(legacy==1)&((h1<0)|(h4<0))]=0
legacy[(legacy==-1)&((h1>0)|(h4>0))]=0
candidates={'current_rules':legacy,**candidates}
print('Signals prepared',flush=True)
periods={'development':(1200,int(df.time.searchsorted(pd.Timestamp('2026-01-01',tz='UTC')))),
         'validation':(int(df.time.searchsorted(pd.Timestamp('2026-01-01',tz='UTC'))),int(df.time.searchsorted(pd.Timestamp('2026-05-01',tz='UTC')))),
         'later_test':(int(df.time.searchsorted(pd.Timestamp('2026-05-01',tz='UTC'))),len(df))}
rows=[]
for name,signals in candidates.items():
    for period,(start,end) in periods.items():
        for exposure,minimum in [('current_contract',.01),('hypothetical_100x_smaller',.0001)]:
            r=simulate(df,signals,start,end,minimum=minimum,step=minimum)
            trades=r.pop('trade_log')
            pd.DataFrame(trades).to_csv(out/f'{name}_{period}_{exposure}.csv',index=False)
            row=dict(strategy=name,period=period,exposure=exposure,start=str(df.time.iloc[start]),end=str(df.time.iloc[end-1]),**r)
            rows.append(row)
            print(json.dumps(row),flush=True)
    start,end=periods['later_test']
    r=simulate(df,signals,start,end,minimum=.0001,step=.0001,cost_scale=2)
    r.pop('trade_log')
    rows.append(dict(strategy=name,period='later_test_double_cost',exposure='hypothetical_100x_smaller',**r))
# Acceptance gate declared here: independent period profitability, sample size, costs, DD.
accepted=[]
for name in candidates:
    if name=='current_rules': continue
    check=[r for r in rows if r['strategy']==name and r['exposure']=='hypothetical_100x_smaller']
    if all(r['trades']>=30 and r['net']>0 and (r['profit_factor'] or 0)>1.1 and r['drawdown_pct']<10 and not r['halted'] for r in check):
        accepted.append(name)
report=dict(start=str(df.time.iloc[1200]),end=str(df.time.iloc[-1]),starting_usd=50,
    risk_per_trade=.003,accepted_candidates=accepted,rows=rows,
    hypothetical_contract_verified=False,live_configuration_changed=False)
(out/'evaluation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
lines=['# Small-account strategy research','',
    'No candidate is claimed to outperform an experienced trader. No live orders or account switches were made.',
    '', 'Four fixed rule sets tested on chronological periods. The entire dataset has been seen in earlier work, so later periods are retrospective validation, not a pristine holdout.',
    '', '| Strategy | Period | Exposure | End USD | Trades | Win rate | Max DD |',
    '|---|---|---|---:|---:|---:|---:|']
for r in rows:
    wr=f"{r['win_rate']*100:.1f}%" if r['win_rate'] is not None else 'N/A'
    lines.append(f"| {r['strategy']} | {r['period']} | {r['exposure']} | {r['final']:.2f} | {r['trades']} | {wr} | {r['drawdown_pct']:.2f}% |")
lines+=['','## Decision','',f"Candidates passing all research gates: {', '.join(accepted) if accepted else 'NONE'}.",
    'Gate: at least 30 trades, net positive, profit factor above 1.1, drawdown below 10%, no drawdown halt in EVERY development/validation/later-test/double-cost period. No candidate was activated automatically.',
    '', '## Assumptions', '',
    'Starting balance $50; risk 0.3%; maximum five entries/day; 2% realized daily loss entry halt; losing-streak sizing reductions/cooldown; one position; original-risk breakeven/profit-lock/trailing; 4-hour stale close; 10% equity drawdown entry halt; margin estimated at 1:500 with half equity reserved. Chronological H1/H4 indicators are built only from complete M15 groups and published when their candles close.',
    'Measured bar spreads, assumed $7 round-trip commission per standard-lot equivalent and $0.10 slippage per entry and market/stop exit; double-cost stress doubles these. Swap omitted.',
    'M15 bar-level stop/trailing/fill/margin models approximate live behavior. Missing tick paths and quote-level broker restrictions mean this is still not exact live parity. Bid OHLC is used; ambiguous stop/target bars prefer stops.',
    'Current contract uses actual .01 standard-lot minimum. Hypothetical exposure uses .0001 standard-lot minimum/step, 100 times smaller. This is NOT a verified XAUUSDc backtest, and does not assert your account can trade it. All balances remain USD equivalents, not cent-account display balances.',
    'Do not interpret cent display balances as dollar growth. Broker actual symbol, volume, currency conversion, fees and availability must be verified before switching. No deposits or account creation are required for this research.',
    '', 'Source for possible smaller account type: [Exness Standard Cent documentation](https://get.exness.help/hc/en-us/articles/17537782786588-Standard-Cent-account). Availability is subject to the user account and region.',
    '', 'Reproduce: `python research/evaluate_candidates.py`. Detailed outputs: `data/strategy_research/evaluation.json`.']
(root/'SMALL_ACCOUNT_RESEARCH.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('DECISION',accepted,flush=True)
