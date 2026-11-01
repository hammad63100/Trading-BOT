#!/usr/bin/env python3
"""
Machine Learning Model Training Pipeline
=========================================
Trains the XGBoost Signal Confidence Model using Walk-Forward Validation.

Data Sources:
- Live MT5 broker history (if MT5 available)
- Offline CSV export (data/historical_test/gold_m15.csv)

Labels:
- Forward simulation: Did price reach +1.5x ATR before -1.0x ATR within 20 bars?
  1 = Positive / Profitable Opportunity
  0 = Stop-out or Adverse Movement

Ref: Blueprint Section 5 & 11
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Tuple, List, Optional

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.indicators import compute_indicators
from ml.features import extract_feature_vector
from ml.model import save_model, MODEL_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("ml_trainer")


def load_dataset(csv_path: Optional[str] = None, bars: int = 10000) -> pd.DataFrame:
    required_cols = {"open", "high", "low", "close"}

    if csv_path and os.path.exists(csv_path):
        log.info("Loading historical data from CSV: %s", csv_path)
        df = pd.read_csv(csv_path)
        if required_cols.issubset(df.columns):
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], utc=True)
            return df
        else:
            log.warning("Specified CSV does not contain required OHLC columns")

    # Check known local gold dataset first
    known_csv = PROJECT_ROOT / "data" / "historical_test" / "gold_m15.csv"
    if known_csv.exists():
        log.info("Found local historical dataset: %s", known_csv)
        df = pd.read_csv(known_csv)
        if required_cols.issubset(df.columns):
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"], utc=True)
            log.info("Loaded %d bars from local dataset", len(df))
            return df

    # Attempt to fetch from MT5 with staleness bypass
    log.info("Attempting to fetch %d bars from MT5...", bars)
    os.environ["BYPASS_STALENESS_CHECK"] = "True"
    try:
        from config.settings import connect_broker, SYMBOL, TIMEFRAME
        from core.data_feed import fetch_ohlcv
        connect_broker()
        df = fetch_ohlcv(SYMBOL, TIMEFRAME, n_bars=bars)
        log.info("Successfully fetched %d bars from MT5 for %s/%s", len(df), SYMBOL, TIMEFRAME)
        return df
    except Exception as e:
        log.warning("MT5 data fetch failed: %s", e)

    # Search any CSV in data/
    for p in (PROJECT_ROOT / "data").glob("**/*.csv"):
        try:
            sample = pd.read_csv(p, nrows=2)
            if required_cols.issubset(sample.columns):
                log.info("Falling back to valid OHLC CSV: %s", p)
                df = pd.read_csv(p)
                if "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"], utc=True)
                return df
        except Exception:
            continue

    raise RuntimeError("No historical OHLCV data available from MT5 or CSV.")


def generate_features_and_labels(
    df: pd.DataFrame,
    horizon_bars: int = 20,
    target_atr_mult: float = 1.5,
    stop_atr_mult: float = 1.0,
    max_samples: int = 8000
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    log.info("Computing indicators for feature extraction on %d bars...", len(df))
    df = compute_indicators(df.copy())

    feature_names = [
        "rsi_norm",
        "macd_hist_atr",
        "ema200_dist_atr",
        "ema50_dist_atr",
        "bb_pos",
        "atr_roc",
        "vol_ratio",
        "momentum_5bar",
    ]

    X_list = []
    y_list = []
    n = len(df)

    start_idx = max(200, n - max_samples - horizon_bars)
    log.info("Extracting features from bar %d to %d (horizon=%d)...", start_idx, n - horizon_bars, horizon_bars)

    for i in range(start_idx, n - horizon_bars):
        window = df.iloc[max(0, i - 250) : i + 1]
        try:
            fv = extract_feature_vector(window)
        except Exception:
            continue

        entry_price = df["close"].iloc[i]
        atr = df["atr14"].iloc[i]
        if pd.isna(atr) or atr <= 0:
            continue

        target_price = entry_price + (atr * target_atr_mult)
        stop_price = entry_price - (atr * stop_atr_mult)

        future_highs = df["high"].iloc[i + 1 : i + 1 + horizon_bars].values
        future_lows = df["low"].iloc[i + 1 : i + 1 + horizon_bars].values

        label = 0
        for high, low in zip(future_highs, future_lows):
            if low <= stop_price:
                label = 0
                break
            if high >= target_price:
                label = 1
                break

        X_list.append(fv)
        y_list.append(label)

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int32)
    log.info("Dataset prepared: %d samples. Positive label rate: %.2f%%", len(y), (np.mean(y) * 100))
    return X, y, feature_names


def train_walk_forward(X: np.ndarray, y: np.ndarray, feature_names: List[str], n_splits: int = 5):
    try:
        from xgboost import XGBClassifier
        model_cls = XGBClassifier
    except ImportError:
        log.warning("xgboost not installed, using RandomForestClassifier as fallback")
        from sklearn.ensemble import RandomForestClassifier
        model_cls = RandomForestClassifier

    from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score, f1_score

    n_samples = len(X)
    fold_size = n_samples // (n_splits + 1)

    scores = []
    log.info("Starting %d-fold Walk-Forward Cross-Validation...", n_splits)

    for fold in range(1, n_splits + 1):
        train_end = fold * fold_size
        test_end = min((fold + 1) * fold_size, n_samples)

        X_train, y_train = X[:train_end], y[:train_end]
        X_test, y_test = X[train_end:test_end], y[train_end:test_end]

        if model_cls.__name__ == "XGBClassifier":
            clf = model_cls(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                eval_metric="logloss"
            )
        else:
            clf = model_cls(n_estimators=100, max_depth=6, random_state=42)

        clf.fit(X_train, y_train)
        preds = clf.predict(X_test)
        probs = clf.predict_proba(X_test)[:, 1] if hasattr(clf, "predict_proba") else preds

        acc = accuracy_score(y_test, preds)
        prec = precision_score(y_test, preds, zero_division=0)
        rec = recall_score(y_test, preds, zero_division=0)
        f1 = f1_score(y_test, preds, zero_division=0)
        auc = roc_auc_score(y_test, probs) if len(np.unique(y_test)) > 1 else 0.5

        scores.append((acc, prec, rec, f1, auc))
        log.info(
            "Fold %d/%d | Train: %d, Test: %d | Acc: %.3f | Prec: %.3f | Rec: %.3f | AUC: %.3f",
            fold, n_splits, len(X_train), len(X_test), acc, prec, rec, auc
        )

    avg_acc = np.mean([s[0] for s in scores])
    avg_prec = np.mean([s[1] for s in scores])
    avg_rec = np.mean([s[2] for s in scores])
    avg_auc = np.mean([s[4] for s in scores])

    log.info("=" * 60)
    log.info("WALK-FORWARD VALIDATION SUMMARY:")
    log.info("  Mean Accuracy:  %.2f%%", avg_acc * 100)
    log.info("  Mean Precision: %.2f%%", avg_prec * 100)
    log.info("  Mean Recall:    %.2f%%", avg_rec * 100)
    log.info("  Mean ROC-AUC:   %.3f", avg_auc)
    log.info("=" * 60)

    # Train final model on full dataset
    log.info("Training final production model on %d samples...", len(X))
    if model_cls.__name__ == "XGBClassifier":
        final_model = model_cls(
            n_estimators=150,
            max_depth=4,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="logloss"
        )
    else:
        final_model = model_cls(n_estimators=150, max_depth=6, random_state=42)

    final_model.fit(X, y)

    # Feature Importance
    if hasattr(final_model, "feature_importances_"):
        importances = final_model.feature_importances_
        log.info("FEATURE IMPORTANCE RANKING:")
        for name, imp in sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True):
            log.info("  %-20s: %.4f", name, imp)

    save_model(final_model)
    log.info("[OK] Model saved successfully to: %s", MODEL_PATH)
    return final_model


def main():
    parser = argparse.ArgumentParser(description="Train ML Signal Confidence Model")
    parser.add_argument("--csv", type=str, default=None, help="Path to offline historical OHLCV CSV")
    parser.add_argument("--bars", type=int, default=8000, help="Number of bars to fetch from MT5")
    args = parser.parse_args()

    df = load_dataset(csv_path=args.csv, bars=args.bars)
    X, y, feature_names = generate_features_and_labels(df, max_samples=args.bars)
    train_walk_forward(X, y, feature_names, n_splits=5)


if __name__ == "__main__":
    main()
