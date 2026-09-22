"""
Configuration & Secure Credential Handling
===========================================
Loads all configuration from environment variables. Credentials are never hardcoded.
For local development, use a .env file (excluded from version control).
For production, use a secrets manager or container orchestrator's secret store.

Ref: Blueprint Section 2

v2.0 — Major upgrade: added emergency closer, MTF filter, anti-martingale,
       spread filter, and tighter risk defaults after loss analysis.
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Load .env file if present (local development only)
load_dotenv()

log = logging.getLogger("gold_bot.config")

# ---------------------------------------------------------------------------
# BROKER CREDENTIALS — loaded from environment, never from source
# ---------------------------------------------------------------------------
BROKER_LOGIN: int = int(os.getenv("BROKER_LOGIN", "0"))
BROKER_PASSWORD: str = os.getenv("BROKER_PASSWORD", "")  # never logged, never printed
BROKER_SERVER: str = os.getenv("BROKER_SERVER", "")

# ---------------------------------------------------------------------------
# ALERT CONFIGURATION
# ---------------------------------------------------------------------------
ALERT_WEBHOOK_URL: str = os.getenv("ALERT_WEBHOOK_URL", "")
ALERT_CHAT_ID: str = os.getenv("ALERT_CHAT_ID", "")

# ---------------------------------------------------------------------------
# TRADING PARAMETERS
# ---------------------------------------------------------------------------
SYMBOL: str = os.getenv("SYMBOL", "XAUUSD")
TIMEFRAME: str = os.getenv("TIMEFRAME", "M15")
POLL_INTERVAL_SECONDS: int = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
TRADING_MODE: str = os.getenv("TRADING_MODE", "demo")  # 'demo' or 'live'
BYPASS_STALENESS_CHECK: bool = os.getenv("BYPASS_STALENESS_CHECK", "False").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# RISK PARAMETERS — tightened after loss analysis
# ---------------------------------------------------------------------------
MAX_RISK_PER_TRADE: float = float(os.getenv("MAX_RISK_PER_TRADE", "0.003"))        # 0.3% of equity (was 0.5%)
MAX_DAILY_LOSS_PCT: float = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))         # 2% daily loss halt
MAX_OPEN_POSITIONS: int = int(os.getenv("MAX_OPEN_POSITIONS", "1"))                 # reduced from 5 to 3
MAX_TRADES_PER_DAY: int = int(os.getenv("MAX_TRADES_PER_DAY", "5"))                # reduced from 30 to 15
COOLDOWN_AFTER_LOSS_MIN: int = int(os.getenv("COOLDOWN_AFTER_LOSS_MIN", "15"))       # 15 min pause after loss (was 5)
ATR_SL_MULTIPLIER: float = float(os.getenv("ATR_SL_MULTIPLIER", "1.5"))              # wider SL for gold (was 1.0)
ATR_TP_MULTIPLIER: float = float(os.getenv("ATR_TP_MULTIPLIER", "3.0"))              # better R:R (was 2.0)

# Signal confidence gate — raised to 0.65 for higher quality trades
MIN_SIGNAL_CONFIDENCE: float = float(os.getenv("MIN_SIGNAL_CONFIDENCE", "0.65"))

# RSI thresholds for mean-reversion strategy
RSI_OVERSOLD:    float = float(os.getenv("RSI_OVERSOLD",  "35.0"))      # tightened from 40
RSI_OVERBOUGHT:  float = float(os.getenv("RSI_OVERBOUGHT", "65.0"))     # tightened from 60

# ---------------------------------------------------------------------------
# EMERGENCY CLOSER — force close losing positions before SL hit
# ---------------------------------------------------------------------------
MAX_LOSS_PER_POSITION: float = float(os.getenv("MAX_LOSS_PER_POSITION", "50.0"))        # $50 max loss per position
MAX_PORTFOLIO_DRAWDOWN: float = float(os.getenv("MAX_PORTFOLIO_DRAWDOWN", "0.03"))       # 3% equity max combined loss
CONSECUTIVE_LOSS_HALT: int = int(os.getenv("CONSECUTIVE_LOSS_HALT", "5"))                # stop after 5 straight losses
LOSING_STREAK_COOLDOWN_MIN: int = int(os.getenv("LOSING_STREAK_COOLDOWN_MIN", "30"))     # 30 min after 3 consec losses

# ---------------------------------------------------------------------------
# ANTI-STACKING — prevent order pileups like yesterday
# ---------------------------------------------------------------------------
MAX_SAME_DIRECTION_POS: int = int(os.getenv("MAX_SAME_DIRECTION_POS", "1"))             # max 2 same direction (was 5)
MIN_ENTRY_SPACING_SEC: int = int(os.getenv("MIN_ENTRY_SPACING_SEC", "300"))              # 5 min between entries (was 90s)
MIN_ENTRY_SPACING_ATR: float = float(os.getenv("MIN_ENTRY_SPACING_ATR", "1.0"))          # 1 ATR distance (was 0.3)

# ---------------------------------------------------------------------------
# MARKET QUALITY FILTERS
# ---------------------------------------------------------------------------
MAX_SPREAD_MULTIPLIER: float = float(os.getenv("MAX_SPREAD_MULTIPLIER", "3.0"))          # block if spread > 3x normal
ADX_MIN_TREND_STRENGTH: float = float(os.getenv("ADX_MIN_TREND_STRENGTH", "20.0"))       # only trade trending markets
MIN_VOLUME_RATIO: float = float(os.getenv("MIN_VOLUME_RATIO", "0.8"))                    # min volume vs 20-bar avg
MAX_POSITION_AGE_HOURS: float = float(os.getenv("MAX_POSITION_AGE_HOURS", "4.0"))        # close stale positions

# ---------------------------------------------------------------------------
# ANTI-MARTINGALE — reduce size after losses
# ---------------------------------------------------------------------------
ANTI_MARTINGALE_ENABLED: bool = os.getenv("ANTI_MARTINGALE_ENABLED", "True").lower() in ("true", "1", "yes")
SIZE_AFTER_1_LOSS: float = float(os.getenv("SIZE_AFTER_1_LOSS", "0.5"))                  # 50% size after 1 loss
SIZE_AFTER_2_LOSS: float = float(os.getenv("SIZE_AFTER_2_LOSS", "0.25"))                 # 25% size after 2+ losses

# ---------------------------------------------------------------------------
# MULTI-TIMEFRAME FILTER
# ---------------------------------------------------------------------------
MTF_ENABLED: bool = os.getenv("MTF_ENABLED", "True").lower() in ("true", "1", "yes")
MTF_TIMEFRAME_1: str = os.getenv("MTF_TIMEFRAME_1", "H1")       # first higher timeframe
MTF_TIMEFRAME_2: str = os.getenv("MTF_TIMEFRAME_2", "H4")       # second higher timeframe

# ---------------------------------------------------------------------------
# BOT IDENTIFICATION
# ---------------------------------------------------------------------------
MAGIC_NUMBER: int = int(os.getenv("MAGIC_NUMBER", "20260719"))  # unique ID for bot's orders

# ---------------------------------------------------------------------------
# TIMEFRAME MAPPING
# ---------------------------------------------------------------------------
TIMEFRAME_MAP: dict = {}  # Populated at runtime after MT5 init


def _build_timeframe_map() -> dict:
    """Build timeframe string -> MT5 constant mapping. Must be called after MT5 import."""
    try:
        import MetaTrader5 as mt5
        return {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
            "W1": mt5.TIMEFRAME_W1,
            "MN1": mt5.TIMEFRAME_MN1,
        }
    except ImportError:
        log.warning("MetaTrader5 not installed — timeframe map unavailable")
        return {}


def get_mt5_timeframe(timeframe_str: str):
    """Convert timeframe string (e.g., 'M15') to MT5 constant."""
    global TIMEFRAME_MAP
    if not TIMEFRAME_MAP:
        TIMEFRAME_MAP = _build_timeframe_map()
    if timeframe_str not in TIMEFRAME_MAP:
        raise ValueError(f"Unknown timeframe: {timeframe_str}. Valid: {list(TIMEFRAME_MAP.keys())}")
    return TIMEFRAME_MAP[timeframe_str]


def validate_config() -> bool:
    """
    Validates that all required configuration is present.
    Returns True if valid, raises ValueError if critical config is missing.
    """
    errors = []

    if BROKER_LOGIN == 0:
        errors.append("BROKER_LOGIN is not set or is 0")
    if not BROKER_PASSWORD:
        errors.append("BROKER_PASSWORD is not set")
    if not BROKER_SERVER:
        errors.append("BROKER_SERVER is not set")

    if TRADING_MODE not in ("demo", "live", "backtest"):
        errors.append(f"TRADING_MODE must be 'demo' or 'live', got '{TRADING_MODE}'")

    if MAX_RISK_PER_TRADE <= 0 or MAX_RISK_PER_TRADE > 0.05:
        errors.append(f"MAX_RISK_PER_TRADE={MAX_RISK_PER_TRADE} is outside safe range (0, 0.05]")

    if MAX_DAILY_LOSS_PCT <= 0 or MAX_DAILY_LOSS_PCT > 0.10:
        errors.append(f"MAX_DAILY_LOSS_PCT={MAX_DAILY_LOSS_PCT} is outside safe range (0, 0.10]")

    if ATR_SL_MULTIPLIER <= 0:
        errors.append("ATR_SL_MULTIPLIER must be positive")
    if ATR_TP_MULTIPLIER <= 0:
        errors.append("ATR_TP_MULTIPLIER must be positive")
    if ATR_TP_MULTIPLIER <= ATR_SL_MULTIPLIER:
        log.warning("ATR_TP_MULTIPLIER <= ATR_SL_MULTIPLIER — risk:reward ratio is below 1:1")

    if errors:
        for e in errors:
            log.error(f"Config validation error: {e}")
        raise ValueError(f"Configuration validation failed: {'; '.join(errors)}")

    log.info("Configuration validated successfully")
    log.info(f"  Symbol: {SYMBOL}, Timeframe: {TIMEFRAME}, Mode: {TRADING_MODE}")
    log.info(f"  Risk/trade: {MAX_RISK_PER_TRADE*100:.1f}%, Daily loss cap: {MAX_DAILY_LOSS_PCT*100:.1f}%")
    log.info(f"  SL: {ATR_SL_MULTIPLIER}x ATR, TP: {ATR_TP_MULTIPLIER}x ATR")
    log.info(f"  Max positions: {MAX_OPEN_POSITIONS}, Max same-dir: {MAX_SAME_DIRECTION_POS}")
    log.info(f"  Min signal confidence: {MIN_SIGNAL_CONFIDENCE:.0%} | RSI OS/OB: {RSI_OVERSOLD}/{RSI_OVERBOUGHT}")
    log.info(f"  Emergency closer: ${MAX_LOSS_PER_POSITION}/pos, {MAX_PORTFOLIO_DRAWDOWN*100:.0f}% portfolio")
    log.info(f"  MTF filter: {'ON' if MTF_ENABLED else 'OFF'} ({MTF_TIMEFRAME_1}/{MTF_TIMEFRAME_2})")
    log.info(f"  Anti-martingale: {'ON' if ANTI_MARTINGALE_ENABLED else 'OFF'}")
    log.info(f"  Entry spacing: {MIN_ENTRY_SPACING_SEC}s + {MIN_ENTRY_SPACING_ATR}x ATR")
    return True


def connect_broker() -> bool:
    """
    Initializes a session with the broker's trade server using credentials
    pulled from environment variables.

    Raises ConnectionError on failure, returns True on success.
    """
    import MetaTrader5 as mt5

    if not mt5.initialize(
        login=BROKER_LOGIN,
        password=BROKER_PASSWORD,
        server=BROKER_SERVER
    ):
        error = mt5.last_error()
        raise ConnectionError(f"Broker initialization failed: {error}")

    # Ensure the trading symbol is visible in Market Watch
    if not mt5.symbol_select(SYMBOL, True):
        raise ConnectionError(f"Failed to select symbol {SYMBOL} in Market Watch")

    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        raise ConnectionError("Account information unavailable")
    log.info(f"Connected to {BROKER_SERVER} | Account: {account.login} | "
             f"Balance: {account.balance:.2f} | Equity: {account.equity:.2f}")

    if TRADING_MODE == "demo" and account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        mt5.shutdown()
        raise ConnectionError("Demo mode requires a demo account")

    return True


def get_account_equity() -> float:
    """Returns the current account equity from the broker."""
    import MetaTrader5 as mt5
    info = mt5.account_info()
    if info is None:
        raise RuntimeError("Failed to get account info — broker may be disconnected")
    return info.equity


def get_account_info():
    """Returns the full account info object from the broker."""
    import MetaTrader5 as mt5
    info = mt5.account_info()
    if info is None:
        raise RuntimeError("Failed to get account info — broker may be disconnected")
    return info
