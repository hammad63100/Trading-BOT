"""
Structured Logging System
==========================
Provides structured JSON logging with rotation, credential scrubbing,
and trade event logging.

Ref: Blueprint Section 10
"""

import logging
import logging.handlers
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Credential Scrubbing Filter
# ---------------------------------------------------------------------------
class CredentialScrubFilter(logging.Filter):
    """
    Scrubs sensitive fields from log records to prevent credential leakage.
    Matches common patterns: passwords, tokens, keys, secrets.
    """
    SENSITIVE_PATTERNS = [
        re.compile(r'(password|passwd|pwd|secret|token|api_key|apikey|auth)\s*[=:]\s*\S+', re.IGNORECASE),
        re.compile(r'(BROKER_PASSWORD|ALERT_WEBHOOK_URL)\s*=\s*\S+', re.IGNORECASE),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pattern in self.SENSITIVE_PATTERNS:
                record.msg = pattern.sub(lambda m: m.group().split('=')[0] + '=***REDACTED***'
                                         if '=' in m.group()
                                         else m.group().split(':')[0] + ':***REDACTED***',
                                         record.msg)
        return True


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------
class JSONFormatter(logging.Formatter):
    """Formats log records as JSON lines for structured log analysis."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Include any extra fields passed via log_trade_event
        if hasattr(record, 'trade_data'):
            log_entry["trade_data"] = record.trade_data

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Logger Setup
# ---------------------------------------------------------------------------
def setup_logging(log_dir: str = "logs", log_level: str = "INFO") -> logging.Logger:
    """
    Configures the root 'gold_bot' logger with:
    - Console handler (human-readable)
    - Rotating file handler (JSON structured, 10MB max, 5 backups)
    - Credential scrubbing filter on all handlers
    """
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    logger = logging.getLogger("gold_bot")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Prevent duplicate handlers on re-initialization
    if logger.handlers:
        return logger

    # --- Console Handler (human-readable) ---
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_fmt)
    console_handler.addFilter(CredentialScrubFilter())
    logger.addHandler(console_handler)

    # --- File Handler (JSON structured, rotating) ---
    file_handler = logging.handlers.RotatingFileHandler(
        log_path / "gold_bot.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JSONFormatter())
    file_handler.addFilter(CredentialScrubFilter())
    logger.addHandler(file_handler)

    # --- Trade Events File Handler (separate file for trade-specific logs) ---
    trade_handler = logging.handlers.RotatingFileHandler(
        log_path / "trades.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8"
    )
    trade_handler.setLevel(logging.INFO)
    trade_handler.setFormatter(JSONFormatter())
    trade_handler.addFilter(CredentialScrubFilter())

    trade_logger = logging.getLogger("gold_bot.trades")
    trade_logger.addHandler(trade_handler)

    logger.info("Logging initialized | Level: %s | Log dir: %s", log_level, log_path.resolve())
    return logger


# ---------------------------------------------------------------------------
# Trade Event Logger
# ---------------------------------------------------------------------------
def log_trade_event(event_type: str, **fields) -> None:
    """
    Structured record for every signal, order, fill, trail, and error.
    Never logs credentials.

    Usage:
        log_trade_event("order_placed", direction="LONG", lots=0.05, price=2650.30)
        log_trade_event("order_blocked", reason="daily_loss_breaker")
        log_trade_event("sl_trailed", ticket=12345, new_sl=2648.50)
    """
    trade_logger = logging.getLogger("gold_bot.trades")
    event_data = {
        "event": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        **fields
    }

    # Create a log record with trade_data attached for the JSON formatter
    record = trade_logger.makeRecord(
        name="gold_bot.trades",
        level=logging.INFO,
        fn="",
        lno=0,
        msg=f"Trade Event: {event_type} | {fields}",
        args=(),
        exc_info=None
    )
    record.trade_data = event_data
    trade_logger.handle(record)

    # Also log to the main logger at INFO level
    main_logger = logging.getLogger("gold_bot")
    main_logger.info("Trade Event: %s | %s", event_type, fields)
