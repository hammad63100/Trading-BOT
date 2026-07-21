#!/usr/bin/env python3
"""
XAU/USD Algorithmic Trading Bot — Entry Point (v2.0 Advanced)
==============================================================
Main event loop with CLI interface for live, demo, and backtest modes.

v2.0 Upgrades:
- Integrated Emergency Loss Closer check every cycle
- Multi-Timeframe (MTF) status monitoring
- Floating PnL reporting per cycle
- Losing streak automatic halt protection

Usage:
    python run.py                  # Run in mode specified by .env (default: demo)
    python run.py --mode demo      # Paper trading on demo account
    python run.py --mode live      # Live trading (requires confirmation)
    python run.py --mode backtest  # Run backtest on historical data

Ref: Blueprint Section 9 (v2.0)
"""

import sys
import os
import time
import signal
import argparse
import logging
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger import setup_logging, log_trade_event
from utils.alerting import send_startup_alert, send_shutdown_alert, send_error_alert
from utils.trade_journal import initialize_journal, record_bot_event

log = logging.getLogger("gold_bot")

# Graceful shutdown flag
_shutdown_requested = False


def signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global _shutdown_requested
    log.info("Shutdown signal received (signal %d) — finishing current cycle...", signum)
    _shutdown_requested = True


def run_trading_loop():
    """
    Main trading loop (v2.0):
    1. Run emergency checks (force close bad losers / portfolio drawdown)
    2. Fetch OHLCV & compute indicators (ADX, VWAP, Ichimoku, Divergence)
    3. Generate multi-strategy signal (5 strategies + MTF filter)
    4. Place order (anti-martingale sizing + risk gates)
    5. Manage open positions (faster breakeven + partial close + 0.75 ATR trail)
    6. Sleep → Repeat
    """
    from config.settings import (
        connect_broker, get_account_equity,
        SYMBOL, TIMEFRAME, POLL_INTERVAL_SECONDS, TRADING_MODE
    )
    from core.data_feed import fetch_ohlcv
    from core.indicators import compute_indicators
    from core.signals import generate_signal
    from core.execution import place_order
    from core.position_manager import manage_open_positions
    from core.emergency_closer import run_emergency_checks
    from core.mtf_filter import get_mtf_summary
    from ml.model import load_model_if_available

    # Connect to broker
    log.info("Connecting to broker...")
    connect_broker()
    log.info("Broker connected successfully")

    # Load ML model if available
    ml_model = load_model_if_available()
    if ml_model:
        log.info("ML model loaded — using blended confidence scoring")
    else:
        log.info("No ML model — using rule-only signals")

    # Send startup notification
    send_startup_alert()
    record_bot_event("startup", f"Mode: {TRADING_MODE}, Symbol: {SYMBOL}, TF: {TIMEFRAME}")

    cycle_count = 0
    error_count = 0
    MAX_CONSECUTIVE_ERRORS = 10

    log.info("═" * 60)
    log.info("  Gold Trading Bot v2.0 — RUNNING")
    log.info("  Mode: %s | Symbol: %s | Timeframe: %s", TRADING_MODE.upper(), SYMBOL, TIMEFRAME)
    log.info("  Poll interval: %ds", POLL_INTERVAL_SECONDS)
    log.info("═" * 60)

    while not _shutdown_requested:
        cycle_count += 1
        try:
            # --- STEP 1: Emergency Loss Checks (CRITICAL FIRST STEP) ---
            emergency_result = run_emergency_checks()
            if emergency_result.get("streak_halt"):
                log.critical("🛑 Trading halted due to consecutive loss streak — loop pausing")
                time.sleep(300)  # Pause 5 minutes before checking again
                continue

            # --- STEP 2: Fetch & Compute Indicators ---
            df = compute_indicators(fetch_ohlcv(SYMBOL, TIMEFRAME))

            # --- STEP 3: Generate Signal ---
            signal_result = generate_signal(df, ml_model=ml_model)

            # --- STEP 4: Cycle Status Log ---
            last = df.iloc[-1]
            import pandas as pd
            rsi_val = f"{last['rsi14']:.1f}" if pd.notna(last.get('rsi14')) else "N/A"
            macd_val = f"{last['macd_hist']:.4f}" if pd.notna(last.get('macd_hist')) else "N/A"
            atr_val = f"{last['atr14']:.2f}" if pd.notna(last.get('atr14')) else "N/A"
            adx_val = f"{last['adx']:.1f}" if pd.notna(last.get('adx')) else "N/A"

            mtf_str = get_mtf_summary(SYMBOL)
            floating = emergency_result.get("floating_pnl", 0.0)

            log.info(
                "Cycle %d | Price: %.2f | RSI: %s | MACD-H: %s | ATR: %s | ADX: %s | "
                "FloatPnL: $%.2f | Signal: %s (%.0f%%) [%s]",
                cycle_count, last["close"], rsi_val, macd_val, atr_val, adx_val,
                floating, signal_result.direction, signal_result.confidence * 100,
                getattr(signal_result, "strategy", "none")
            )

            # --- STEP 5: Order Execution ---
            if signal_result.direction != "FLAT":
                equity = get_account_equity()
                log.info(
                    "🚨 Signal: %s | Confidence: %.2f | Reason: %s | Equity: $%.2f | %s",
                    signal_result.direction, signal_result.confidence,
                    signal_result.reason, equity, mtf_str
                )

                if TRADING_MODE == "live":
                    place_order(signal_result, df, account_equity=equity)
                else:
                    log.info(
                        "[DEMO MODE] Would place %s order — not executing",
                        signal_result.direction
                    )
                    log_trade_event(
                        "demo_signal",
                        direction=signal_result.direction,
                        confidence=signal_result.confidence,
                        reason=signal_result.reason
                    )

            # --- STEP 6: Manage Open Positions (Trail SL, Breakeven, Stale close) ---
            if TRADING_MODE == "live":
                manage_open_positions()

            # Reset error counter on successful cycle
            error_count = 0

        except KeyboardInterrupt:
            break
        except Exception as e:
            error_count += 1
            log.exception("Loop error (cycle %d, error %d/%d) — continuing after backoff",
                         cycle_count, error_count, MAX_CONSECUTIVE_ERRORS)
            send_error_alert(str(e), context=f"Main loop cycle {cycle_count}")

            if error_count >= MAX_CONSECUTIVE_ERRORS:
                log.critical(
                    "Too many consecutive errors (%d) — shutting down for safety",
                    error_count
                )
                send_error_alert(
                    f"Bot shutting down after {error_count} consecutive errors",
                    context="Emergency shutdown"
                )
                break

            backoff = min(POLL_INTERVAL_SECONDS * (2 ** error_count), 300)
            time.sleep(backoff)
            continue

        time.sleep(POLL_INTERVAL_SECONDS)

    # --- Shutdown ---
    log.info("Shutting down...")
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass

    send_shutdown_alert("Graceful shutdown")
    record_bot_event("shutdown", f"After {cycle_count} cycles")
    log.info("Bot stopped after %d cycles", cycle_count)


def run_backtest_mode():
    """Runs the backtesting module on historical data."""
    from config.settings import connect_broker, SYMBOL, TIMEFRAME
    from core.data_feed import fetch_ohlcv
    from backtest.backtester import run_backtest, print_backtest_report

    log.info("Starting backtest mode...")

    connect_broker()

    log.info("Fetching historical data for %s/%s...", SYMBOL, TIMEFRAME)
    df = fetch_ohlcv(SYMBOL, TIMEFRAME, n_bars=5000)
    log.info("Fetched %d bars | From: %s | To: %s",
             len(df), df["time"].iloc[0], df["time"].iloc[-1])

    result = run_backtest(df)
    report = print_backtest_report(result)

    report_path = os.path.join("data", "backtest_report.txt")
    os.makedirs("data", exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)
    log.info("Backtest report saved to %s", report_path)

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="XAU/USD Algorithmic Trading Bot v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                  # Run in default mode (from .env)
  python run.py --mode demo      # Paper trading mode
  python run.py --mode live      # Live trading (requires confirmation)
  python run.py --mode backtest  # Historical backtesting
        """
    )
    parser.add_argument(
        "--mode",
        choices=["demo", "live", "backtest"],
        default=None,
        help="Trading mode (overrides TRADING_MODE in .env)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)"
    )

    args = parser.parse_args()

    setup_logging(log_level=args.log_level)

    if args.mode:
        os.environ["TRADING_MODE"] = args.mode
        import importlib
        import config.settings
        importlib.reload(config.settings)

    from config.settings import validate_config, TRADING_MODE

    try:
        validate_config()
    except ValueError as e:
        log.critical("Configuration error: %s", e)
        sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    initialize_journal()

    if TRADING_MODE == "backtest" or args.mode == "backtest":
        run_backtest_mode()
        return

    if TRADING_MODE == "live" or args.mode == "live":
        log.warning("═" * 60)
        log.warning("  ⚠️  LIVE TRADING MODE")
        log.warning("  Real money will be at risk!")
        log.warning("═" * 60)
        confirm = input("Type 'CONFIRM' to proceed with live trading: ")
        if confirm != "CONFIRM":
            log.info("Live trading cancelled by user")
            sys.exit(0)

    run_trading_loop()


if __name__ == "__main__":
    main()
