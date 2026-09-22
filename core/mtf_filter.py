"""
Multi-Timeframe (MTF) Trend Filter
====================================
Prevents counter-trend trades by checking H1 and H4 trend direction.

Added after loss analysis on 2026-07-20 where the bot took multiple BUY
orders on M15 while the higher timeframe trend was bearish.

Logic:
  1. Fetch H1 data → compute EMA20 & EMA50
  2. Fetch H4 data → compute EMA20 & EMA50
  3. Determine trend direction for each:
     - EMA20 > EMA50 = BULLISH
     - EMA20 < EMA50 = BEARISH
     - Flat zone (within 0.1%) = NEUTRAL
  4. For a LONG signal to pass:
     - H1 must be BULLISH or NEUTRAL
     - H4 must be BULLISH or NEUTRAL
  5. For a SHORT signal to pass:
     - H1 must be BEARISH or NEUTRAL
     - H4 must be BEARISH or NEUTRAL

A trade only goes through if higher timeframes AGREE with the direction.
"""

import logging
import pandas as pd

log = logging.getLogger("gold_bot.mtf_filter")


def get_htf_trend(symbol: str, timeframe: str) -> dict:
    """
    Fetches higher timeframe data and determines trend direction.

    Args:
        symbol: Trading symbol (e.g., 'XAUUSDm')
        timeframe: Timeframe string (e.g., 'H1', 'H4')

    Returns:
        Dict with keys:
            - direction: 'BULLISH', 'BEARISH', or 'NEUTRAL'
            - ema20: EMA20 value
            - ema50: EMA50 value
            - close: latest close
            - strength: trend strength percentage
    """
    import MetaTrader5 as mt5
    from config.settings import get_mt5_timeframe

    result = {
        "direction": "UNAVAILABLE",
        "ema20": 0.0,
        "ema50": 0.0,
        "close": 0.0,
        "strength": 0.0,
        "timeframe": timeframe,
    }

    try:
        mt5_tf = get_mt5_timeframe(timeframe)
        from core.data_feed import fetch_ohlcv
        rates = fetch_ohlcv(symbol, timeframe, 200)

        if rates is None or len(rates) < 55:
            log.warning("Not enough %s data for MTF filter (got %d bars)",
                       timeframe, len(rates) if rates is not None else 0)
            return result

        df = pd.DataFrame(rates)
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

        # Also compute EMA200 for overall trend context
        if len(df) >= 200:
            df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        last = df.iloc[-1]
        ema20 = last["ema20"]
        ema50 = last["ema50"]
        close = last["close"]

        result["ema20"] = round(ema20, 2)
        result["ema50"] = round(ema50, 2)
        result["close"] = round(close, 2)

        # Calculate trend strength as percentage distance between EMAs
        ema_distance_pct = (ema20 - ema50) / ema50 * 100

        # Define neutral zone — EMAs within 0.1% of each other
        if abs(ema_distance_pct) < 0.1:
            result["direction"] = "NEUTRAL"
        elif ema20 > ema50:
            result["direction"] = "BULLISH"
        else:
            result["direction"] = "BEARISH"

        result["strength"] = round(abs(ema_distance_pct), 4)

        # Extra confirmation: price position relative to EMA50
        price_vs_ema50 = "above" if close > ema50 else "below"

        log.debug(
            "MTF %s | Trend: %s | EMA20: %.2f | EMA50: %.2f | Close: %.2f (%s EMA50) | "
            "Strength: %.4f%%",
            timeframe, result["direction"], ema20, ema50, close,
            price_vs_ema50, result["strength"]
        )

    except Exception as e:
        log.error("MTF filter error for %s: %s", timeframe, e)

    return result


def check_mtf_alignment(symbol: str, signal_direction: str) -> tuple[bool, str]:
    """
    Checks if the signal direction aligns with H1 and H4 trends.

    Args:
        symbol: Trading symbol
        signal_direction: 'LONG' or 'SHORT'

    Returns:
        (is_aligned, reason_string)
        - is_aligned: True if signal aligns with HTF trend
        - reason_string: explanation of the MTF check result
    """
    from config.settings import MTF_ENABLED, MTF_TIMEFRAME_1, MTF_TIMEFRAME_2

    if not MTF_ENABLED:
        return True, "MTF filter disabled"

    h1_trend = get_htf_trend(symbol, MTF_TIMEFRAME_1)
    h4_trend = get_htf_trend(symbol, MTF_TIMEFRAME_2)

    h1_dir = h1_trend["direction"]
    h4_dir = h4_trend["direction"]

    log.info(
        "🔍 MTF Check | Signal: %s | %s: %s (%.4f%%) | %s: %s (%.4f%%)",
        signal_direction,
        MTF_TIMEFRAME_1, h1_dir, h1_trend["strength"],
        MTF_TIMEFRAME_2, h4_dir, h4_trend["strength"],
    )

    if signal_direction == "LONG":
        # For LONG: H1 must not be BEARISH, H4 must not be BEARISH
        h1_ok = h1_dir in ("BULLISH", "NEUTRAL")
        h4_ok = h4_dir in ("BULLISH", "NEUTRAL")

        if not h1_ok and not h4_ok:
            reason = (
                f"MTF BLOCKED: Both {MTF_TIMEFRAME_1}({h1_dir}) and "
                f"{MTF_TIMEFRAME_2}({h4_dir}) bearish — LONG rejected"
            )
            log.warning("🚫 %s", reason)
            return False, reason

        if not h1_ok:
            reason = f"MTF BLOCKED: {MTF_TIMEFRAME_1} is {h1_dir} — LONG rejected"
            log.warning("🚫 %s", reason)
            return False, reason

        if not h4_ok:
            reason = f"MTF BLOCKED: {MTF_TIMEFRAME_2} is {h4_dir} — LONG rejected"
            log.warning("🚫 %s", reason)
            return False, reason

        reason = f"MTF OK: {MTF_TIMEFRAME_1}={h1_dir}, {MTF_TIMEFRAME_2}={h4_dir} — LONG aligned"
        log.info("✅ %s", reason)
        return True, reason

    elif signal_direction == "SHORT":
        # For SHORT: H1 must not be BULLISH, H4 must not be BULLISH
        h1_ok = h1_dir in ("BEARISH", "NEUTRAL")
        h4_ok = h4_dir in ("BEARISH", "NEUTRAL")

        if not h1_ok and not h4_ok:
            reason = (
                f"MTF BLOCKED: Both {MTF_TIMEFRAME_1}({h1_dir}) and "
                f"{MTF_TIMEFRAME_2}({h4_dir}) bullish — SHORT rejected"
            )
            log.warning("🚫 %s", reason)
            return False, reason

        if not h1_ok:
            reason = f"MTF BLOCKED: {MTF_TIMEFRAME_1} is {h1_dir} — SHORT rejected"
            log.warning("🚫 %s", reason)
            return False, reason

        if not h4_ok:
            reason = f"MTF BLOCKED: {MTF_TIMEFRAME_2} is {h4_dir} — SHORT rejected"
            log.warning("🚫 %s", reason)
            return False, reason

        reason = f"MTF OK: {MTF_TIMEFRAME_1}={h1_dir}, {MTF_TIMEFRAME_2}={h4_dir} — SHORT aligned"
        log.info("✅ %s", reason)
        return True, reason

    # FLAT signal — always passes
    return True, "FLAT signal — no MTF check needed"


def get_mtf_summary(symbol: str) -> str:
    """Returns a human-readable summary of the current MTF trend state."""
    from config.settings import MTF_TIMEFRAME_1, MTF_TIMEFRAME_2

    h1 = get_htf_trend(symbol, MTF_TIMEFRAME_1)
    h4 = get_htf_trend(symbol, MTF_TIMEFRAME_2)

    return (
        f"MTF Status: {MTF_TIMEFRAME_1}={h1['direction']}({h1['strength']:.3f}%) | "
        f"{MTF_TIMEFRAME_2}={h4['direction']}({h4['strength']:.3f}%)"
    )
