"""
Unit Tests for Signal Generation
==================================
Tests signal generation with various market conditions.
"""

import os
import pytest
import pandas as pd
import numpy as np

# Set test config before importing
os.environ.setdefault("BROKER_LOGIN", "99999")
os.environ.setdefault("BROKER_PASSWORD", "test")
os.environ.setdefault("BROKER_SERVER", "TestServer")
os.environ.setdefault("MIN_SIGNAL_CONFIDENCE", "0.6")

from core.signals import generate_signal, Signal


def _make_bullish_df():
    """Creates a DataFrame with strong bullish signals."""
    n = 5
    df = pd.DataFrame({
        "close":       [2650, 2652, 2654, 2656, 2658],
        "ema200":      [2640, 2640, 2640, 2640, 2640],  # Price above EMA200
        "ema50":       [2648, 2649, 2650, 2651, 2652],
        "rsi14":       [28, 30, 32, 33, 34],             # Turning up from oversold
        "macd_hist":   [-0.5, -0.3, -0.1, 0.0, 0.2],   # Bullish cross
        "macd":        [0.1, 0.15, 0.2, 0.25, 0.3],
        "macd_signal": [0.2, 0.2, 0.2, 0.2, 0.1],
        "atr14":       [5.0, 5.0, 5.0, 5.0, 5.0],
        "bb_upper":    [2665, 2665, 2665, 2665, 2665],
        "bb_lower":    [2635, 2635, 2635, 2635, 2635],
        "bb_mid":      [2650, 2650, 2650, 2650, 2650],
    })
    return df


def _make_bearish_df():
    """Creates a DataFrame with strong bearish signals."""
    df = pd.DataFrame({
        "close":       [2660, 2658, 2656, 2654, 2652],
        "ema200":      [2670, 2670, 2670, 2670, 2670],  # Price below EMA200
        "ema50":       [2662, 2661, 2660, 2659, 2658],
        "rsi14":       [72, 70, 68, 67, 66],             # Turning down from OB
        "macd_hist":   [0.5, 0.3, 0.1, 0.0, -0.2],     # Bearish cross
        "macd":        [0.3, 0.25, 0.2, 0.15, 0.1],
        "macd_signal": [0.1, 0.1, 0.1, 0.1, 0.3],
        "atr14":       [5.0, 5.0, 5.0, 5.0, 5.0],
        "bb_upper":    [2675, 2675, 2675, 2675, 2675],
        "bb_lower":    [2645, 2645, 2645, 2645, 2645],
        "bb_mid":      [2660, 2660, 2660, 2660, 2660],
    })
    return df


def _make_flat_df():
    """Creates a DataFrame with no clear signal."""
    df = pd.DataFrame({
        "close":       [2650, 2651, 2650, 2651, 2650],
        "ema200":      [2650, 2650, 2650, 2650, 2650],
        "ema50":       [2650, 2650, 2650, 2650, 2650],
        "rsi14":       [50, 50, 50, 50, 50],             # Neutral
        "macd_hist":   [0.0, 0.0, 0.0, 0.0, 0.01],     # No cross
        "macd":        [0.0, 0.0, 0.0, 0.0, 0.0],
        "macd_signal": [0.0, 0.0, 0.0, 0.0, 0.0],
        "atr14":       [5.0, 5.0, 5.0, 5.0, 5.0],
        "bb_upper":    [2660, 2660, 2660, 2660, 2660],
        "bb_lower":    [2640, 2640, 2640, 2640, 2640],
        "bb_mid":      [2650, 2650, 2650, 2650, 2650],
    })
    return df


class TestSignalGeneration:

    def test_long_signal(self):
        df = _make_bullish_df()
        signal = generate_signal(df)
        assert signal.direction == "LONG"
        assert signal.confidence >= 0.6
        assert "bullish" in signal.reason.lower()

    def test_short_signal(self):
        df = _make_bearish_df()
        signal = generate_signal(df)
        assert signal.direction == "SHORT"
        assert signal.confidence >= 0.6
        assert "bearish" in signal.reason.lower()

    def test_flat_signal(self):
        df = _make_flat_df()
        signal = generate_signal(df)
        assert signal.direction == "FLAT"

    def test_insufficient_data(self):
        df = pd.DataFrame({"close": [2650]})
        signal = generate_signal(df)
        assert signal.direction == "FLAT"
        assert "insufficient" in signal.reason.lower()

    def test_signal_has_timestamp(self):
        df = _make_bullish_df()
        signal = generate_signal(df)
        assert signal.timestamp is not None
        assert len(signal.timestamp) > 0

    def test_confidence_range(self):
        for df_func in [_make_bullish_df, _make_bearish_df, _make_flat_df]:
            df = df_func()
            signal = generate_signal(df)
            assert 0.0 <= signal.confidence <= 1.0

    def test_signal_dataclass_fields(self):
        signal = Signal("LONG", 0.8, "test reason")
        assert signal.direction == "LONG"
        assert signal.confidence == 0.8
        assert signal.reason == "test reason"
