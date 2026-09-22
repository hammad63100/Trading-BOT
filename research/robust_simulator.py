"""Conservative research simulator in USD and STANDARD-lot equivalents.

M15 OHLC execution is an approximation, not tick-accurate live-bot parity.
"""
import math
import numpy as np
from core.risk_manager import floor_volume


def simulate(df, signals, start, end, equity=50., minimum=.0001, step=.0001, cost_scale=1.):
    if len(df) != len(signals) or not 1 <= start < end <= len(df):
        raise ValueError('Invalid simulation range')
    initial = peak = equity
    times = df.time.tolist()
    op, hi, lo, cl, atr = [df[c].to_numpy(float) for c in ('open','high','low','close','atr14')]
    spread = np.maximum(df.spread.to_numpy(float) * .001 * cost_scale, .001)
    slip, commission = .10 * cost_scale, 7.0 * cost_scale
    position = None
    trades, curve = [], [equity]
    current_day, day_start, count, streak, pause_until = None, equity, 0, 0, 0
    halted = False
    skipped = eligible = 0
    for i in range(start, end):
        ts = times[i].timestamp()
        day = times[i].date()
        if day != current_day:
            current_day, day_start, count, streak = day, equity, 0, 0
        if position is None and not halted and equity > 0:
            d = int(signals[i-1])
            a = atr[i-1]
            if d and math.isfinite(a) and a > 0 and count < 5 and streak < 5 and equity > day_start * .98 and ts >= pause_until:
                # Do not use a signal carried across a session/weekend gap.
                if (times[i]-times[i-1]).total_seconds() > 15*60:
                    continue
                if spread[i] > .15 * a:
                    continue
                eligible += 1
                distance = 1.5 * a
                multiplier = 1 if streak == 0 else (.5 if streak == 1 else .25)
                budget = min(equity * .003 * multiplier, max(0, equity - day_start * .98))
                vol = floor_volume(budget / ((distance + slip)*100 + commission), minimum, 200, step)
                if vol == 0:
                    skipped += 1
                elif op[i] * 100 * vol / 500 > equity * .5:
                    skipped += 1
                else:
                    entry = op[i] + spread[i] + slip if d == 1 else op[i] - slip
                    position = dict(direction=d, entry=entry, stop=entry-d*distance,
                        target=entry+d*3*a, risk=distance, volume=vol, entered=i, time=times[i].isoformat())
                    count += 1
        if position is not None:
            p = position
            d, vol = p['direction'], p['volume']
            add = spread[i] if d == -1 else 0
            qopen,qhigh,qlow,qclose = op[i]+add,hi[i]+add,lo[i]+add,cl[i]+add
            stop, target = p['stop'], p['target']
            gap_stop = qopen <= stop if d == 1 else qopen >= stop
            gap_target = qopen >= target if d == 1 else qopen <= target
            stop_hit = qlow <= stop if d == 1 else qhigh >= stop
            target_hit = qhigh >= target if d == 1 else qlow <= target
            reason = None
            if halted:
                price, reason = qopen - d*slip, 'drawdown_halt'
            elif gap_stop or (stop_hit and not gap_target):
                price = min(stop,qopen)-slip if d == 1 else max(stop,qopen)+slip
                reason = 'stop'
            elif target_hit:
                price,reason = target,'target'
            elif (ts-times[p['entered']].timestamp() >= 4*3600 and d*(qclose-p['entry']) < .3*p['risk']) or i == end-1:
                price,reason = qclose-d*slip,'time_exit'
            if reason:
                pnl = d*(price-p['entry'])*100*vol-commission*vol
                equity += pnl
                trades.append(dict(entry_time=p['time'],exit_time=times[i].isoformat(), direction=d,
                    volume=vol,pnl=pnl,reason=reason))
                streak = streak+1 if pnl < 0 else 0
                if pnl < 0:
                    pause_until = ts + (30 if streak >= 3 else 15)*60
                position = None
            else:
                # Closed-bar decisions become active for the NEXT bar only.
                r = d*(qclose-p['entry'])/p['risk']
                newstop = stop
                if r >= .3:
                    newstop = max(newstop,p['entry']) if d == 1 else min(newstop,p['entry'])
                if r >= 1:
                    lock = p['entry']+d*.5*p['risk']
                    newstop = max(newstop,lock) if d == 1 else min(newstop,lock)
                if r >= 1.5:
                    trail = qclose-d*.75*atr[i]
                    newstop = max(newstop,trail) if d == 1 else min(newstop,trail)
                p['stop'] = newstop
        marked = equity
        if position is not None:
            p = position
            quote = cl[i] + (spread[i] if p['direction'] == -1 else 0)
            marked += p['direction']*(quote-p['entry'])*100*p['volume']-commission*p['volume']
        curve.append(marked)
        peak = max(peak,marked)
        if marked < peak * .9:
            halted = True
    pnls = np.asarray([t['pnl'] for t in trades])
    profits = float(pnls[pnls>0].sum())
    losses = float(-pnls[pnls<0].sum())
    peaks = np.maximum.accumulate(curve)
    return dict(initial=initial,final=equity,net=equity-initial,trades=len(trades),
        win_rate=(float((pnls>0).mean()) if len(pnls) else None),
        profit_factor=(profits/losses if losses else None),
        drawdown_pct=float(np.max((peaks-np.asarray(curve))/peaks)*100),
        eligible=eligible,skipped=skipped,halted=halted, trade_log=trades)
