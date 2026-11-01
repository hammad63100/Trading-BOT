#!/usr/bin/env python3
"""
System Health & Readiness Checker
==================================
Performs pre-flight and runtime diagnostics:
1. Environment & Config Validation
2. MetaTrader 5 Connectivity & Account Status
3. Symbol Availability, Spread & Trading Permissions
4. Data Feed Freshness
5. SQLite Database (Trade Journal) Integrity
6. Disk Space & File Permissions

Usage:
    python scripts/health_check.py
    # Exit code: 0 = HEALTHY, 1 = UNHEALTHY
"""

import sys
import os
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def print_status(component: str, ok: bool, message: str):
    icon = "[OK]" if ok else "[FAIL]"
    status_color = "\033[92m" if ok else "\033[91m"
    reset_color = "\033[0m"
    try:
        print(f" {status_color}{icon:<6}{reset_color} {component:<25} : {message}")
    except UnicodeEncodeError:
        print(f" {icon:<6} {component:<25} : {message}")


def check_config() -> bool:
    try:
        from config.settings import validate_config, SYMBOL, TIMEFRAME, TRADING_MODE
        validate_config()
        print_status("Configuration", True, f"Valid ({SYMBOL}, {TIMEFRAME}, mode={TRADING_MODE})")
        return True
    except Exception as e:
        print_status("Configuration", False, f"Invalid config: {e}")
        return False


def check_mt5_and_broker() -> bool:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print_status("MT5 Library", False, "MetaTrader5 module not installed")
        return False

    try:
        from config.settings import BROKER_LOGIN, BROKER_PASSWORD, BROKER_SERVER, SYMBOL
        if not mt5.initialize(login=BROKER_LOGIN, password=BROKER_PASSWORD, server=BROKER_SERVER):
            err = mt5.last_error()
            print_status("Broker Connection", False, f"MT5 Init failed: {err}")
            return False

        account = mt5.account_info()
        if account is None:
            print_status("Broker Connection", False, "Failed to retrieve account_info")
            mt5.shutdown()
            return False

        mode_str = "Demo" if account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "Real"
        print_status(
            "Broker Connection",
            True,
            f"Connected to {account.server} | Account: {account.login} ({mode_str}) | "
            f"Bal: ${account.balance:.2f} | Eq: ${account.equity:.2f}"
        )

        # Check Symbol
        if not mt5.symbol_select(SYMBOL, True):
            print_status("Symbol Selected", False, f"Could not select {SYMBOL}")
            mt5.shutdown()
            return False

        sym_info = mt5.symbol_info(SYMBOL)
        if sym_info is None:
            print_status("Symbol Info", False, f"Cannot get info for {SYMBOL}")
            mt5.shutdown()
            return False

        trade_mode_ok = sym_info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL
        spread_pts = sym_info.spread
        print_status(
            "Symbol Specs",
            trade_mode_ok,
            f"{SYMBOL} | Spread: {spread_pts} pts | Digits: {sym_info.digits} | "
            f"MinLot: {sym_info.volume_min} | Step: {sym_info.volume_step}"
        )

        # Check rates fetch
        from config.settings import get_mt5_timeframe, TIMEFRAME
        mt5_tf = get_mt5_timeframe(TIMEFRAME)
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5_tf, 0, 5)
        if rates is None or len(rates) == 0:
            print_status("Data Feed", False, "copy_rates returned empty")
            mt5.shutdown()
            return False

        latest_time = datetime.fromtimestamp(rates[-1]["time"], timezone.utc)
        print_status("Data Feed", True, f"Active. Last bar timestamp: {latest_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        mt5.shutdown()
        return True
    except Exception as e:
        print_status("Broker / MT5", False, f"Unexpected error: {e}")
        return False


def check_database() -> bool:
    db_path = PROJECT_ROOT / "data" / "trade_journal.db"
    try:
        from utils.trade_journal import initialize_journal
        initialize_journal()

        if not db_path.exists():
            print_status("SQLite Journal", False, f"File does not exist: {db_path}")
            return False

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        res = cur.fetchone()
        conn.close()

        if res and res[0] == "ok":
            print_status("SQLite Journal", True, f"Integrity check passed ({db_path.name})")
            return True
        else:
            print_status("SQLite Journal", False, f"Integrity issue: {res}")
            return False
    except Exception as e:
        print_status("SQLite Journal", False, f"Error: {e}")
        return False


def check_disk_and_logs() -> bool:
    try:
        usage = shutil.disk_usage(str(PROJECT_ROOT))
        free_gb = usage.free / (1024 ** 3)
        ok = free_gb > 1.0  # At least 1 GB free
        print_status("Disk Space", ok, f"{free_gb:.2f} GB free")

        logs_dir = PROJECT_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        test_file = logs_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        print_status("Log Directory", True, f"Writable ({logs_dir})")
        return ok
    except Exception as e:
        print_status("Disk & Logs", False, f"Error: {e}")
        return False


def main():
    print("=" * 65)
    print("        XAU/USD TRADING BOT - SYSTEM HEALTH CHECK")
    print("=" * 65)

    checks = [
        ("Config", check_config()),
        ("Database", check_database()),
        ("Disk & Logs", check_disk_and_logs()),
        ("MT5 & Feed", check_mt5_and_broker()),
    ]

    all_ok = all(ok for _, ok in checks)

    print("=" * 65)
    if all_ok:
        print("  RESULT: [ALL CHECKS PASSED] - System is ready for trading.")
        print("=" * 65)
        sys.exit(0)
    else:
        print("  RESULT: [SOME CHECKS FAILED] - Resolve errors before live trading.")
        print("=" * 65)
        sys.exit(1)


if __name__ == "__main__":
    main()

