"""
Unit Tests for Indicator Engine
================================
Verifies indicator calculations against known values.
"""

import pytest
import pandas as pd
import numpy as np
from core.indicators import compute_indicators


def _make_sample_df(n_bars=300):
    """Creates a synthetic OHLCV DataFrame for testing."""
    np.random.seed(42)
    base_price = 2650.0
    close = base_price + np.cumsum(np.random.randn(n_bars) * 2)
    high = close + np.abs(np.random.randn(n_bars) * 1.5)
    low = close - np.abs(np.random.randn(n_bars) * 1.5)
    open_ = close + np.random.randn(n_bars) * 0.5

    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n_bars, freq="15min"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": np.random.randint(100, 5000, n_bars),
        "spread": np.random.randint(1, 5, n_bars),
        "real_volume": np.zeros(n_bars),
    })
    return df


class TestComputeIndicators:
    """Tests for the compute_indicators function."""

    def test_all_columns_added(self):
        df = _make_sample_df()
        result = compute_indicators(df)
        expected_cols = ["ema50", "ema200", "rsi14", "macd", "macd_signal",
                         "macd_hist", "atr14", "bb_upper", "bb_lower", "bb_mid"]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_rsi_range(self):
        df = _make_sample_df()
        result = compute_indicators(df)
        valid_rsi = result["rsi14"].dropna()
        assert (valid_rsi >= 0).all(), "RSI should be >= 0"
        assert (valid_rsi <= 100).all(), "RSI should be <= 100"

    def test_atr_positive(self):
        df = _make_sample_df()
        result = compute_indicators(df)
        valid_atr = result["atr14"].dropna()
        assert (valid_atr > 0).all(), "ATR should be positive"

    def test_bollinger_band_ordering(self):
        df = _make_sample_df()
        result = compute_indicators(df)
        valid_mask = result["bb_upper"].notna() & result["bb_lower"].notna()
        valid = result[valid_mask]
        assert (valid["bb_upper"] >= valid["bb_lower"]).all(), \
            "Upper BB should always be >= Lower BB"

    def test_ema_convergence(self):
        df = _make_sample_df()
        result = compute_indicators(df)
        # After warm-up, EMAs should be close to price range
        last_ema50 = result["ema50"].iloc[-1]
        last_close = result["close"].iloc[-1]
        assert abs(last_ema50 - last_close) < 50, \
            "EMA50 should be within reasonable range of close"

    def test_insufficient_data_warning(self):
        df = _make_sample_df(n_bars=50)
        # Should not raise, but rolling-based indicators will have NaN warm-up rows
        result = compute_indicators(df)
        # RSI uses rolling(14) which produces NaN for first 14 rows
        assert result["rsi14"].isna().sum() > 0, \
            "RSI should have NaN values during warm-up period"
        # ATR uses rolling(14) which produces NaN for first ~14 rows
        assert result["atr14"].isna().sum() > 0, \
            "ATR should have NaN values during warm-up period"

    def test_macd_histogram(self):
        df = _make_sample_df()
        result = compute_indicators(df)
        valid_mask = result["macd"].notna() & result["macd_signal"].notna()
        valid = result[valid_mask]
        # Histogram should equal MACD - Signal
        expected_hist = valid["macd"] - valid["macd_signal"]
        np.testing.assert_array_almost_equal(
            valid["macd_hist"].values,
            expected_hist.values,
            decimal=6,
            err_msg="MACD histogram should equal MACD line - Signal line"
        )
