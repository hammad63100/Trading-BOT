"""
Feature Engineering for ML Model
=================================
Extracts normalized feature vectors from indicator data for the
optional ML confidence scoring layer.

Ref: Blueprint Section 5 (ML layer)
"""

import logging
import numpy as np
import pandas as pd

log = logging.getLogger("gold_bot.ml.features")


def extract_feature_vector(df: pd.DataFrame) -> list[float]:
    """
    Extracts a normalized feature vector from the latest indicator values.

    Features:
    1. RSI(14) normalized to [0, 1]
    2. MACD histogram (normalized by ATR)
    3. Distance from EMA200 in ATR units
    4. Distance from EMA50 in ATR units
    5. Bollinger Band position (0=lower, 0.5=mid, 1=upper)
    6. ATR(14) rate of change
    7. Volume relative to moving average
    8. Price momentum (close change over 5 bars, normalized by ATR)

    Args:
        df: DataFrame with computed indicators.

    Returns:
        List of normalized feature values.
    """
    last = df.iloc[-1]
    atr = last["atr14"] if pd.notna(last["atr14"]) and last["atr14"] > 0 else 1.0

    features = []

    # 1. RSI normalized [0, 1]
    rsi = last["rsi14"] / 100.0 if pd.notna(last["rsi14"]) else 0.5
    features.append(rsi)

    # 2. MACD histogram normalized by ATR
    macd_h = last["macd_hist"] / atr if pd.notna(last["macd_hist"]) else 0.0
    features.append(np.clip(macd_h, -3, 3) / 3.0)  # Clip to [-1, 1]

    # 3. Distance from EMA200 in ATR units
    if pd.notna(last["ema200"]):
        ema200_dist = (last["close"] - last["ema200"]) / atr
    else:
        ema200_dist = 0.0
    features.append(np.clip(ema200_dist, -5, 5) / 5.0)

    # 4. Distance from EMA50 in ATR units
    if pd.notna(last["ema50"]):
        ema50_dist = (last["close"] - last["ema50"]) / atr
    else:
        ema50_dist = 0.0
    features.append(np.clip(ema50_dist, -5, 5) / 5.0)

    # 5. Bollinger Band position [0, 1]
    if pd.notna(last.get("bb_upper")) and pd.notna(last.get("bb_lower")):
        bb_range = last["bb_upper"] - last["bb_lower"]
        if bb_range > 0:
            bb_pos = (last["close"] - last["bb_lower"]) / bb_range
        else:
            bb_pos = 0.5
    else:
        bb_pos = 0.5
    features.append(np.clip(bb_pos, 0, 1))

    # 6. ATR rate of change (current vs 5 bars ago)
    if len(df) >= 6 and pd.notna(df["atr14"].iloc[-6]):
        atr_prev = df["atr14"].iloc[-6]
        if atr_prev > 0:
            atr_roc = (atr - atr_prev) / atr_prev
        else:
            atr_roc = 0.0
    else:
        atr_roc = 0.0
    features.append(np.clip(atr_roc, -1, 1))

    # 7. Volume relative to 20-bar MA
    if "tick_volume" in df.columns:
        vol_ma = df["tick_volume"].rolling(20).mean().iloc[-1]
        if pd.notna(vol_ma) and vol_ma > 0:
            vol_ratio = last["tick_volume"] / vol_ma
        else:
            vol_ratio = 1.0
    else:
        vol_ratio = 1.0
    features.append(np.clip(vol_ratio / 3.0, 0, 1))  # Normalize, cap at 3x average

    # 8. Price momentum (5-bar close change normalized by ATR)
    if len(df) >= 6:
        momentum = (last["close"] - df["close"].iloc[-6]) / atr
    else:
        momentum = 0.0
    features.append(np.clip(momentum, -3, 3) / 3.0)

    return features
