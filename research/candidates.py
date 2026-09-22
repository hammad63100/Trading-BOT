"""Research-only candidates. No broker access or live activation."""
import numpy as np
import pandas as pd


def higher_timeframe_trend(df, hours):
    """Return trend available by each M15 candle CLOSE, excluding partial HTF bars."""
    x = df.set_index('time')
    bars = x.resample(f'{hours}h', closed='left', label='right').agg(close=('close','last'), count=('close','count'))
    bars = bars[bars['count'] == hours * 4]
    fast = bars.close.ewm(span=20, adjust=False, min_periods=20).mean()
    slow = bars.close.ewm(span=50, adjust=False, min_periods=50).mean()
    trend = pd.Series(np.where(fast > slow, 1, np.where(fast < slow, -1, 0)), index=bars.index)
    return trend.reindex(pd.DatetimeIndex(df.time + pd.Timedelta(minutes=15)), method='ffill').fillna(0).to_numpy()


def candidate_signals(df):
    """Three fixed hypotheses; outputs are directions, not win probabilities."""
    close, op = df.close, df.open
    aligned = higher_timeframe_trend(df, 1) + higher_timeframe_trend(df, 4)
    trend = np.where(aligned == 2, 1, np.where(aligned == -2, -1, 0))
    quality = (df.adx >= 25) & (df.adx <= 50) & (df.time.dt.hour >= 8) & (df.time.dt.hour < 17)
    quality &= (df.atr14 / df.close).between(.0004, .008)
    rising = (trend == 1) & (df.ema20 > df.ema50) & (df.ema50 > df.ema200)
    falling = (trend == -1) & (df.ema20 < df.ema50) & (df.ema50 < df.ema200)
    pull_buy = rising & (df.low <= df.ema20) & (close > df.ema20) & (close > op) & df.rsi14.between(45,65)
    pull_sell = falling & (df.high >= df.ema20) & (close < df.ema20) & (close < op) & df.rsi14.between(35,55)
    break_buy = rising & (close > df.roll_high20) & (df.body_ratio > .6) & (df.volume_ratio > 1.2)
    break_sell = falling & (close < df.roll_low20) & (df.body_ratio > .6) & (df.volume_ratio > 1.2)
    reclaim_buy = rising & (df.low < df.low.shift(1).rolling(5).min()) & (close > op) & (close > df.close.shift(1))
    reclaim_sell = falling & (df.high > df.high.shift(1).rolling(5).max()) & (close < op) & (close < df.close.shift(1))
    return {name: np.where(quality & buy, 1, np.where(quality & sell, -1, 0))
        for name,buy,sell in [('trend_pullback',pull_buy,pull_sell),('trend_breakout',break_buy,break_sell),('trend_reclaim',reclaim_buy,reclaim_sell)]}
