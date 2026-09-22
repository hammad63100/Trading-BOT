"""
Market Data Ingestion
=====================
Pulls live and historical OHLCV bars for XAU/USD from MetaTrader 5.
Includes staleness checks, automatic reconnect with exponential backoff,
and gap detection.

Ref: Blueprint Section 3
"""

import time
import logging
import pandas as pd

log = logging.getLogger("gold_bot.data_feed")

# Maximum retries for reconnection
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2


def fetch_ohlcv(symbol: str, timeframe: str, n_bars: int = 500) -> pd.DataFrame:
    """
    Pulls the most recent n_bars of OHLCV candles from MetaTrader 5.
    Validates that data actually arrived and isn't stale before handing
    it downstream.

    Args:
        symbol: Trading symbol (e.g., 'XAUUSD').
        timeframe: Timeframe string (e.g., 'M15', 'H1').
        n_bars: Number of recent bars to fetch.

    Returns:
        DataFrame with columns: time, open, high, low, close, tick_volume, spread, real_volume

    Raises:
        RuntimeError: If data is unavailable or stale.
        ConnectionError: If broker connection fails after retries.
    """
    import MetaTrader5 as mt5
    from config.settings import get_mt5_timeframe

    mt5_tf = get_mt5_timeframe(timeframe)

    # Attempt to fetch with automatic reconnect on failure
    rates = None
    for attempt in range(1, MAX_RETRIES + 1):
        rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 1, n_bars)

        if rates is not None and len(rates) > 0:
            break

        # Reconnect attempt
        log.warning(
            "Data fetch attempt %d/%d failed for %s — retrying after backoff",
            attempt, MAX_RETRIES, symbol
        )
        if attempt < MAX_RETRIES:
            backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))  # Exponential backoff
            time.sleep(backoff)
            # Try to re-initialize the connection
            try:
                from config.settings import connect_broker
                connect_broker()
            except Exception as e:
                log.error("Reconnect failed on attempt %d: %s", attempt, e)

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"No data returned for {symbol} after {MAX_RETRIES} attempts. "
            f"Check broker connection and symbol availability."
        )

    # Build DataFrame
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    # --- Staleness check ---
    from config.settings import BYPASS_STALENESS_CHECK
    latest_bar_age = pd.Timestamp.utcnow().tz_localize(None) - df["time"].iloc[-1]
    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60,
               "H4": 240, "D1": 1440, "W1": 10080, "MN1": 44640}[timeframe]
    if latest_bar_age > pd.Timedelta(minutes=2 * minutes + 5) and not BYPASS_STALENESS_CHECK:
        log.warning(
            "Stale feed detected: latest bar for %s is %s old",
            symbol, latest_bar_age
        )
        raise RuntimeError(
            f"Stale feed: latest bar is {latest_bar_age} old. "
            f"Market may be closed or feed is frozen."
        )

    # --- Gap detection ---
    _detect_gaps(df, timeframe)

    log.debug(
        "Fetched %d bars for %s/%s | Latest: %s | Close: %.2f",
        len(df), symbol, timeframe, df["time"].iloc[-1], df["close"].iloc[-1]
    )
    return df


def _detect_gaps(df: pd.DataFrame, timeframe: str) -> None:
    """
    Detects unusual gaps between consecutive bar timestamps.
    Ignores known normal gaps: weekends and daily session breaks.
    Only logs truly unexpected gaps.
    """
    expected_intervals = {
        "M1": pd.Timedelta(minutes=1),
        "M5": pd.Timedelta(minutes=5),
        "M15": pd.Timedelta(minutes=15),
        "M30": pd.Timedelta(minutes=30),
        "H1": pd.Timedelta(hours=1),
        "H4": pd.Timedelta(hours=4),
        "D1": pd.Timedelta(days=1),
    }

    expected = expected_intervals.get(timeframe)
    if expected is None:
        return

    time_diffs = df["time"].diff().dropna()
    # Allow up to 3x expected interval for normal variation
    threshold = expected * 3

    large_gaps = time_diffs[time_diffs > threshold]
    if len(large_gaps) > 0:
        for idx in large_gaps.index:
            gap_size = large_gaps[idx]
            gap_time = df["time"].iloc[idx]
            prev_time = df["time"].iloc[idx - 1] if idx > 0 else None

            # --- Skip known normal gaps ---
            # Weekend gap: Friday evening to Sunday/Monday (typically 2+ days)
            if prev_time is not None and prev_time.weekday() == 4:  # Friday
                continue
            # Daily session break: gaps around 20:00-23:59 (broker daily rollover)
            if prev_time is not None and prev_time.hour >= 20 and gap_size < pd.Timedelta(hours=4):
                continue

            log.warning(
                "Unexpected data gap at %s: %s gap (expected ~%s)",
                gap_time, gap_size, expected
            )


def get_current_tick(symbol: str) -> dict:
    """
    Returns the current bid/ask tick for a symbol.

    Returns:
        Dict with 'bid', 'ask', 'last', 'time' keys.
    """
    import MetaTrader5 as mt5

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"Failed to get tick for {symbol}")

    return {
        "bid": tick.bid,
        "ask": tick.ask,
        "last": tick.last,
        "time": pd.Timestamp(tick.time, unit="s"),
        "volume": tick.volume,
    }
