"""
Indicator Engine (Advanced v2.0)
==================================
Computes a comprehensive set of technical indicators from raw OHLCV data.

Indicators computed:
- RSI(14): Momentum, overbought/oversold
- MACD(12, 26, 9): Trend-momentum crossover
- ATR(14): Volatility measure, drives SL/TP distance
- EMA(20, 50, 200): Trend filters (short, medium, long)
- Bollinger Bands(20, 2): Volatility envelope
- Stochastic %K/%D(14, 3): Secondary momentum oscillator
- Rolling High/Low(20): Breakout reference levels
- MACD histogram slope: Rate of change of momentum
- Session flag: London / New York active session marker
- Body ratio: Candle quality filter

NEW in v2.0:
- ADX(14): Trend strength — only trade when ADX > threshold
- VWAP: Volume-Weighted Average Price
- RSI Divergence: Bullish/Bearish divergence detection
- Market Structure: Higher Highs, Higher Lows tracking
- Volume Profile: Relative volume vs moving average
- Ichimoku Cloud: Trend confirmation (Tenkan/Kijun/Senkou)

Ref: Blueprint Section 4 (extended v2)
"""

import logging
import numpy as np
import pandas as pd

log = logging.getLogger("gold_bot.indicators")


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds technical indicators to the OHLCV DataFrame.

    Uses pure pandas calculations with proper NaN handling for warm-up periods.

    Args:
        df: DataFrame with columns: open, high, low, close, tick_volume, time

    Returns:
        DataFrame with additional indicator columns (all original columns preserved).
    """
    if len(df) < 200:
        log.warning(
            "DataFrame has only %d rows — EMA200 needs 200+ bars for full warm-up. "
            "Results may be less reliable.", len(df)
        )

    # -----------------------------------------------------------------------
    # EMA (Exponential Moving Averages)
    # -----------------------------------------------------------------------
    df["ema20"]  = df["close"].ewm(span=20,  adjust=False).mean()
    df["ema50"]  = df["close"].ewm(span=50,  adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    # -----------------------------------------------------------------------
    # RSI (Relative Strength Index, period=14) — standard Wilder smoothing
    # -----------------------------------------------------------------------
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    df["rsi14"] = 100 - (100 / (1 + rs))

    # -----------------------------------------------------------------------
    # MACD (12, 26, 9)
    # -----------------------------------------------------------------------
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"]        = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # MACD histogram slope (positive = accelerating momentum)
    df["macd_hist_slope"] = df["macd_hist"].diff()

    # -----------------------------------------------------------------------
    # ATR (Average True Range, period=14)
    # -----------------------------------------------------------------------
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr14"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

    # -----------------------------------------------------------------------
    # Bollinger Bands (20, 2)
    # -----------------------------------------------------------------------
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    df["bb_mid"]   = sma20
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20
    # BB width as % of mid — measures volatility expansion
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    # -----------------------------------------------------------------------
    # Stochastic Oscillator %K and %D (14, 3)
    # -----------------------------------------------------------------------
    low14  = df["low"].rolling(14).min()
    high14 = df["high"].rolling(14).max()
    df["stoch_k"] = 100 * (df["close"] - low14) / (high14 - low14).replace(0, float("nan"))
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    # -----------------------------------------------------------------------
    # Rolling 20-bar High/Low (breakout levels)
    # -----------------------------------------------------------------------
    # Exclude current bar to avoid look-ahead — use shift(1)
    df["roll_high20"] = df["high"].shift(1).rolling(20).max()
    df["roll_low20"]  = df["low"].shift(1).rolling(20).min()

    # -----------------------------------------------------------------------
    # Candle Body Ratio (body / total range) — quality filter
    # Higher = strong directional candle, lower = doji/indecision
    # -----------------------------------------------------------------------
    candle_range = (df["high"] - df["low"]).replace(0, float("nan"))
    df["body_ratio"] = (df["close"] - df["open"]).abs() / candle_range

    # -----------------------------------------------------------------------
    # ADX (Average Directional Index, period=14)
    # Measures trend STRENGTH regardless of direction.
    # ADX > 25 = trending, ADX < 20 = choppy/ranging
    # -----------------------------------------------------------------------
    df = _compute_adx(df, period=14)

    # -----------------------------------------------------------------------
    # VWAP (Volume Weighted Average Price) — intraday benchmark
    # Institutional traders use VWAP as a reference level.
    # -----------------------------------------------------------------------
    df = _compute_vwap(df)

    # -----------------------------------------------------------------------
    # Volume Profile — relative volume vs 20-bar average
    # -----------------------------------------------------------------------
    if "tick_volume" in df.columns:
        vol_ma = df["tick_volume"].rolling(20).mean()
        df["volume_ratio"] = df["tick_volume"] / vol_ma.replace(0, float("nan"))
    else:
        df["volume_ratio"] = 1.0

    # -----------------------------------------------------------------------
    # RSI Divergence Detection
    # -----------------------------------------------------------------------
    df = _detect_rsi_divergence(df, lookback=14)

    # -----------------------------------------------------------------------
    # Market Structure (Higher Highs / Lower Lows)
    # -----------------------------------------------------------------------
    df = _detect_market_structure(df, lookback=20)

    # -----------------------------------------------------------------------
    # Ichimoku Cloud (simplified — Tenkan, Kijun, Senkou A/B)
    # -----------------------------------------------------------------------
    df = _compute_ichimoku(df)

    # -----------------------------------------------------------------------
    # Session Flag — London: 07:00-12:00 UTC, New York: 12:00-17:00 UTC
    # Gold is most liquid and trends best during these sessions.
    # -----------------------------------------------------------------------
    if "time" in df.columns:
        try:
            hours = pd.to_datetime(df["time"]).dt.hour
            df["in_session"] = (
                ((hours >= 7) & (hours < 12)) |   # London morning
                ((hours >= 12) & (hours < 17))     # New York afternoon
            ).astype(int)
        except Exception:
            df["in_session"] = 1  # Default: always in session if time unavailable
    else:
        df["in_session"] = 1

    # -----------------------------------------------------------------------
    # Warm-up status log
    # -----------------------------------------------------------------------
    warm_up_complete = (
        df["ema200"].notna() & df["rsi14"].notna() &
        df["atr14"].notna()  & df["macd_hist"].notna()
    )
    n_warmup = (~warm_up_complete).sum()
    if n_warmup > 0:
        log.debug("Warm-up rows with NaN indicators: %d", n_warmup)

    log.debug(
        "Indicators computed | Rows: %d | RSI: %.1f | ATR: %.2f | MACD-H: %.4f "
        "| EMA50: %.2f | EMA200: %.2f | Stoch-K: %.1f | ADX: %.1f | Vol-Ratio: %.2f",
        len(df),
        df["rsi14"].iloc[-1]       if pd.notna(df["rsi14"].iloc[-1])       else 0,
        df["atr14"].iloc[-1]       if pd.notna(df["atr14"].iloc[-1])       else 0,
        df["macd_hist"].iloc[-1]   if pd.notna(df["macd_hist"].iloc[-1])   else 0,
        df["ema50"].iloc[-1]       if pd.notna(df["ema50"].iloc[-1])       else 0,
        df["ema200"].iloc[-1]      if pd.notna(df["ema200"].iloc[-1])      else 0,
        df["stoch_k"].iloc[-1]     if pd.notna(df["stoch_k"].iloc[-1])     else 0,
        df["adx"].iloc[-1]         if pd.notna(df.get("adx", pd.Series([0])).iloc[-1]) else 0,
        df["volume_ratio"].iloc[-1] if pd.notna(df["volume_ratio"].iloc[-1]) else 0,
    )
    return df


def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Computes Average Directional Index (ADX) to measure trend strength.
    ADX > 25 = trending market (good for trading)
    ADX < 20 = ranging/choppy market (avoid trading)
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # +DM and -DM
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    # True Range
    tr_hl = high - low
    tr_hc = (high - close.shift()).abs()
    tr_lc = (low - close.shift()).abs()
    tr = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)

    # Smoothed with Wilder's smoothing (alpha = 1/period)
    atr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() /
                     atr_smooth.replace(0, float("nan")))
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() /
                      atr_smooth.replace(0, float("nan")))

    # DX = |+DI - -DI| / |+DI + -DI|
    di_sum = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / di_sum

    # ADX = smoothed DX
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    df["adx"] = dx.ewm(alpha=1/period, adjust=False).mean()

    return df


def _compute_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes Volume Weighted Average Price (VWAP).
    Uses cumulative calculation reset daily if time info is available.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3

    if "tick_volume" in df.columns:
        volume = df["tick_volume"].replace(0, 1)  # avoid division by zero
    else:
        volume = pd.Series(1, index=df.index)

    # Simple rolling VWAP (last 20 bars) — works across sessions
    cum_tp_vol = (typical_price * volume).rolling(20).sum()
    cum_vol = volume.rolling(20).sum().replace(0, float("nan"))
    df["vwap"] = cum_tp_vol / cum_vol

    # Distance from VWAP in ATR units
    if "atr14" in df.columns:
        atr = df["atr14"].replace(0, float("nan"))
        df["vwap_distance"] = (df["close"] - df["vwap"]) / atr
    else:
        df["vwap_distance"] = 0.0

    return df


def _detect_rsi_divergence(df: pd.DataFrame, lookback: int = 14) -> pd.DataFrame:
    """
    Detects bullish and bearish RSI divergence.

    Bullish divergence: Price makes lower low but RSI makes higher low
    Bearish divergence: Price makes higher high but RSI makes lower high

    Adds columns:
        rsi_bull_div: 1 if bullish divergence detected, else 0
        rsi_bear_div: 1 if bearish divergence detected, else 0
    """
    df["rsi_bull_div"] = 0
    df["rsi_bear_div"] = 0

    if "rsi14" not in df.columns or len(df) < lookback + 5:
        return df

    close = df["close"]
    rsi = df["rsi14"]

    # Compare current lows/highs with lookback period
    for i in range(lookback + 2, len(df)):
        # Bullish divergence: price lower low, RSI higher low
        price_low_now = close.iloc[i]
        price_low_prev = close.iloc[i - lookback:i].min()
        rsi_low_now = rsi.iloc[i]
        rsi_low_prev = rsi.iloc[i - lookback:i].min()

        if (price_low_now < price_low_prev and
            pd.notna(rsi_low_now) and pd.notna(rsi_low_prev) and
            rsi_low_now > rsi_low_prev and rsi_low_now < 45):
            df.iloc[i, df.columns.get_loc("rsi_bull_div")] = 1

        # Bearish divergence: price higher high, RSI lower high
        price_high_now = close.iloc[i]
        price_high_prev = close.iloc[i - lookback:i].max()
        rsi_high_now = rsi.iloc[i]
        rsi_high_prev = rsi.iloc[i - lookback:i].max()

        if (price_high_now > price_high_prev and
            pd.notna(rsi_high_now) and pd.notna(rsi_high_prev) and
            rsi_high_now < rsi_high_prev and rsi_high_now > 55):
            df.iloc[i, df.columns.get_loc("rsi_bear_div")] = 1

    return df


def _detect_market_structure(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """
    Detects market structure patterns:
    - Higher highs + higher lows = uptrend structure
    - Lower highs + lower lows = downtrend structure
    - Mixed = ranging

    Adds column:
        market_structure: 1 (uptrend), -1 (downtrend), 0 (ranging)
    """
    df["market_structure"] = 0

    if len(df) < lookback * 2:
        return df

    # Use rolling windows to find swing highs and lows
    roll_high = df["high"].rolling(lookback).max()
    roll_low = df["low"].rolling(lookback).min()

    # Compare current rolling extremes with previous ones
    prev_high = roll_high.shift(lookback)
    prev_low = roll_low.shift(lookback)

    # Higher high + higher low = uptrend
    hh = roll_high > prev_high
    hl = roll_low > prev_low

    # Lower high + lower low = downtrend
    lh = roll_high < prev_high
    ll = roll_low < prev_low

    df.loc[hh & hl, "market_structure"] = 1    # Uptrend
    df.loc[lh & ll, "market_structure"] = -1   # Downtrend

    return df


def _compute_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes simplified Ichimoku Cloud components.

    - Tenkan-sen (Conversion Line): (9-period high + 9-period low) / 2
    - Kijun-sen (Base Line): (26-period high + 26-period low) / 2
    - Senkou Span A (Leading Span A): (Tenkan + Kijun) / 2, plotted 26 periods ahead
    - Senkou Span B (Leading Span B): (52-period high + 52-period low) / 2, plotted 26 periods ahead

    Cloud interpretation:
    - Price above cloud = bullish
    - Price below cloud = bearish
    - Price inside cloud = neutral/transitioning
    """
    # Tenkan-sen (9 periods)
    high9 = df["high"].rolling(9).max()
    low9 = df["low"].rolling(9).min()
    df["ichi_tenkan"] = (high9 + low9) / 2

    # Kijun-sen (26 periods)
    high26 = df["high"].rolling(26).max()
    low26 = df["low"].rolling(26).min()
    df["ichi_kijun"] = (high26 + low26) / 2

    # Senkou Span A (shifted forward 26 periods — we use current for signal)
    df["ichi_senkou_a"] = (df["ichi_tenkan"] + df["ichi_kijun"]) / 2

    # Senkou Span B (52 periods)
    high52 = df["high"].rolling(52).max()
    low52 = df["low"].rolling(52).min()
    df["ichi_senkou_b"] = (high52 + low52) / 2

    # Cloud top and bottom
    df["ichi_cloud_top"] = df[["ichi_senkou_a", "ichi_senkou_b"]].max(axis=1)
    df["ichi_cloud_bottom"] = df[["ichi_senkou_a", "ichi_senkou_b"]].min(axis=1)

    # Price vs cloud: 1 = above, -1 = below, 0 = inside
    df["ichi_signal"] = 0
    df.loc[df["close"] > df["ichi_cloud_top"], "ichi_signal"] = 1
    df.loc[df["close"] < df["ichi_cloud_bottom"], "ichi_signal"] = -1

    return df
