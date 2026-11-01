#!/usr/bin/env python3
"""
Windows Service Runner — Auto-Restart Daemon
==============================================
Runs the trading bot as a resilient background process with:
- Automatic restart on crash (up to MAX_RESTARTS within RESTART_WINDOW)
- Lock file to prevent duplicate instances
- Health monitoring and self-healing
- Clean shutdown via Ctrl+C or SIGTERM
- Startup delay for system boot scenarios

Usage:
    python scripts/service_runner.py              # Run with auto-restart
    python scripts/service_runner.py --once       # Single run (no restart)
    python scripts/service_runner.py --delay 30   # Wait 30s before starting
"""

import sys
import os
import time
import signal
import argparse
import logging
import subprocess
import atexit
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_RESTARTS = 5                # Max restarts within the window
RESTART_WINDOW_SECONDS = 3600   # 1 hour window for restart counting
RESTART_DELAY_SECONDS = 10      # Delay between restarts (increases with failures)
LOCK_FILE = PROJECT_ROOT / "data" / "bot.lock"
LOG_FILE = PROJECT_ROOT / "logs" / "service_runner.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)
log = logging.getLogger("service_runner")

_shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global _shutdown_requested
    log.info("Shutdown signal received (%d)", signum)
    _shutdown_requested = True


def acquire_lock() -> bool:
    """Acquire a lock file to prevent duplicate bot instances."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            # Check if process is still running (Windows-compatible)
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                log.error(
                    "Another bot instance is already running (PID: %d). "
                    "Remove %s if this is stale.", pid, LOCK_FILE
                )
                return False
            else:
                log.warning("Stale lock file found (PID %d not running) — removing", pid)
                LOCK_FILE.unlink()
        except (ValueError, OSError):
            log.warning("Invalid lock file — removing")
            LOCK_FILE.unlink()

    LOCK_FILE.write_text(str(os.getpid()))
    log.info("Lock acquired (PID: %d)", os.getpid())
    return True


def release_lock():
    """Release the lock file."""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
            log.info("Lock released")
    except OSError:
        pass


def run_bot_process() -> int:
    """Run the trading bot as a subprocess. Returns exit code."""
    bot_script = PROJECT_ROOT / "run.py"
    cmd = [sys.executable, str(bot_script)]

    log.info("Starting bot process: %s", " ".join(cmd))
    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Stream output in real-time
    try:
        while process.poll() is None:
            if _shutdown_requested:
                log.info("Sending terminate signal to bot process...")
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    log.warning("Bot didn't stop gracefully — killing")
                    process.kill()
                break

            line = process.stdout.readline()
            if line:
                print(line.rstrip())

        # Drain remaining output
        remaining = process.stdout.read()
        if remaining:
            print(remaining.rstrip())

    except KeyboardInterrupt:
        process.terminate()

    return process.returncode or 0


def main():
    parser = argparse.ArgumentParser(description="Trading Bot Service Runner")
    parser.add_argument("--once", action="store_true", help="Run once without auto-restart")
    parser.add_argument("--delay", type=int, default=0, help="Startup delay in seconds")
    parser.add_argument("--mode", choices=["demo", "live"], default=None, help="Override trading mode")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Startup delay
    if args.delay > 0:
        log.info("Waiting %d seconds before starting...", args.delay)
        time.sleep(args.delay)

    # Acquire lock
    if not acquire_lock():
        sys.exit(1)
    atexit.register(release_lock)

    if args.mode:
        os.environ["TRADING_MODE"] = args.mode

    # Track restarts
    restart_times: list[float] = []
    restart_count = 0

    log.info("═" * 60)
    log.info("  Trading Bot Service Runner")
    log.info("  Mode: %s", "single-run" if args.once else "auto-restart")
    log.info("  Max restarts: %d per %d seconds", MAX_RESTARTS, RESTART_WINDOW_SECONDS)
    log.info("═" * 60)

    while not _shutdown_requested:
        start_time = time.time()
        exit_code = run_bot_process()
        elapsed = time.time() - start_time

        if _shutdown_requested:
            log.info("Clean shutdown completed")
            break

        log.warning(
            "Bot process exited with code %d after %.0f seconds",
            exit_code, elapsed
        )

        if args.once:
            log.info("Single-run mode — not restarting")
            break

        # Track restart timing
        now = time.time()
        restart_times = [t for t in restart_times if now - t < RESTART_WINDOW_SECONDS]
        restart_times.append(now)
        restart_count = len(restart_times)

        if restart_count >= MAX_RESTARTS:
            log.critical(
                "Too many restarts (%d in %d seconds) — stopping service. "
                "Check logs and fix the underlying issue.",
                restart_count, RESTART_WINDOW_SECONDS
            )
            try:
                from utils.alerting import send_alert
                send_alert(
                    f"🚨 Bot service stopped after {restart_count} restarts!\n"
                    f"Last exit code: {exit_code}\n"
                    "Manual intervention required.",
                    severity="CRITICAL"
                )
            except Exception:
                pass
            break

        # Exponential backoff on restarts
        delay = min(RESTART_DELAY_SECONDS * (2 ** (restart_count - 1)), 300)
        log.info(
            "Restarting in %d seconds (restart %d/%d)...",
            delay, restart_count, MAX_RESTARTS
        )
        time.sleep(delay)

    release_lock()
    log.info("Service runner stopped")


if __name__ == "__main__":
    main()

