"""
ML Model Management
====================
Handles loading, saving, and using the optional ML confidence scoring model.
Uses XGBoost gradient-boosted trees as the default algorithm.

Training should use walk-forward (rolling-origin) validation,
not random k-fold splits.

Ref: Blueprint Section 5 (ML layer notes)
"""

import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("gold_bot.ml.model")

# Default model save path
MODEL_DIR = Path("models")
MODEL_PATH = MODEL_DIR / "signal_confidence_model.pkl"


def load_model_if_available() -> Optional[object]:
    """
    Loads a saved ML model if one exists on disk.
    Returns None if no model is found (the bot will use rule-only signals).

    Returns:
        Trained model with predict_proba method, or None.
    """
    if not MODEL_PATH.exists():
        log.debug("No ML model found at %s — using rule-only signals", MODEL_PATH)
        return None

    try:
        import joblib
        model = joblib.load(MODEL_PATH)
        log.info("ML model loaded from %s", MODEL_PATH)
        return model
    except Exception as e:
        log.warning("Failed to load ML model: %s — falling back to rule-only", e)
        return None


def save_model(model) -> None:
    """Saves a trained model to disk."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import joblib
        joblib.dump(model, MODEL_PATH)
        log.info("ML model saved to %s", MODEL_PATH)
    except Exception as e:
        log.error("Failed to save ML model: %s", e)


def train_model(features_list: list, labels: list, test_size: float = 0.2) -> Optional[object]:
    """
    Trains an XGBoost classifier using walk-forward validation.

    This is a basic implementation — for production use, implement
    proper walk-forward (rolling-origin) cross-validation with
    multiple train/validate/test windows.

    Args:
        features_list: List of feature vectors (from extract_feature_vector).
        labels: List of labels (1 for profitable trade, 0 for loss).
        test_size: Fraction of data for final test set (time-ordered, not random).

    Returns:
        Trained model, or None on failure.
    """
    try:
        import numpy as np
        from xgboost import XGBClassifier
        from sklearn.metrics import accuracy_score, classification_report

        X = np.array(features_list)
        y = np.array(labels)

        if len(X) < 100:
            log.warning("Only %d samples — need at least 100 for meaningful training", len(X))
            return None

        # Time-ordered split (NOT random — critical for time series)
        split_idx = int(len(X) * (1 - test_size))
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        log.info("Training ML model | Train: %d samples | Test: %d samples", len(X_train), len(X_test))

        model = XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
        )

        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False,
        )

        # Evaluate on test set
        y_pred = model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)
        log.info("ML model accuracy on test set: %.2f%%", accuracy * 100)
        log.info("\n%s", classification_report(y_test, y_pred, zero_division=0))

        if accuracy < 0.52:
            log.warning(
                "Model accuracy (%.2f%%) is barely above random — "
                "consider not using the ML layer until more data is available",
                accuracy * 100
            )

        save_model(model)
        return model

    except ImportError as e:
        log.error("Required ML libraries not installed: %s", e)
        return None
    except Exception as e:
        log.error("Model training failed: %s", e)
        return None
