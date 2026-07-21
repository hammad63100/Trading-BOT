"""
Trade Journal — SQLite Database
================================
Persistent record of every trade executed by the bot.
Used for performance analysis, auditing, and the daily PnL calculation
required by the risk manager.

Ref: Blueprint Section 13 (suggested tech stack)
"""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("gold_bot.journal")

# Database path
DB_PATH = Path("data/trade_journal.db")


def _get_connection() -> sqlite3.Connection:
    """Returns a connection to the trade journal database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent read performance
    return conn


def initialize_journal() -> None:
    """Creates the trade journal tables if they don't exist."""
    conn = _get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER UNIQUE,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,         -- 'LONG' or 'SHORT'
                open_time TEXT NOT NULL,
                close_time TEXT,
                open_price REAL NOT NULL,
                close_price REAL,
                volume REAL NOT NULL,
                sl REAL,
                tp REAL,
                profit REAL DEFAULT 0.0,
                commission REAL DEFAULT 0.0,
                swap REAL DEFAULT 0.0,
                signal_confidence REAL,
                signal_reason TEXT,
                magic_number INTEGER,
                status TEXT DEFAULT 'OPEN',      -- 'OPEN', 'CLOSED', 'CANCELLED'
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                gross_profit REAL DEFAULT 0.0,
                gross_loss REAL DEFAULT 0.0,
                net_pnl REAL DEFAULT 0.0,
                max_drawdown REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS bot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                details TEXT,
                timestamp TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_open_time ON trades(open_time);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
            CREATE INDEX IF NOT EXISTS idx_trades_ticket ON trades(ticket);
        """)
        conn.commit()
        log.info("Trade journal initialized at %s", DB_PATH.resolve())
    finally:
        conn.close()


def record_trade_open(
    ticket: int,
    symbol: str,
    direction: str,
    open_price: float,
    volume: float,
    sl: float,
    tp: float,
    signal_confidence: float = 0.0,
    signal_reason: str = "",
    magic_number: int = 0
) -> None:
    """Records a newly opened trade."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO trades
            (ticket, symbol, direction, open_time, open_price, volume, sl, tp,
             signal_confidence, signal_reason, magic_number, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            """,
            (
                ticket, symbol, direction,
                datetime.now(timezone.utc).isoformat(),
                open_price, volume, sl, tp,
                signal_confidence, signal_reason, magic_number
            )
        )
        conn.commit()
        log.info("Journal: Recorded trade open — Ticket: %d, %s %s @ %.2f",
                 ticket, direction, symbol, open_price)
    finally:
        conn.close()


def record_trade_close(
    ticket: int,
    close_price: float,
    profit: float,
    commission: float = 0.0,
    swap: float = 0.0
) -> None:
    """Updates a trade record when it is closed."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            UPDATE trades
            SET close_time = ?, close_price = ?, profit = ?,
                commission = ?, swap = ?, status = 'CLOSED'
            WHERE ticket = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                close_price, profit, commission, swap, ticket
            )
        )
        conn.commit()
        log.info("Journal: Recorded trade close — Ticket: %d, Profit: %.2f", ticket, profit)
    finally:
        conn.close()


def get_trades_since(since: datetime) -> list[dict]:
    """Returns all trades opened since the given timestamp."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM trades WHERE open_time >= ? ORDER BY open_time",
            (since.isoformat(),)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_realized_pnl_since(since: datetime) -> float:
    """Returns the total realized PnL (profit + commission + swap) since the given time."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT COALESCE(SUM(profit + commission + swap), 0.0) as total_pnl
            FROM trades
            WHERE status = 'CLOSED' AND close_time >= ?
            """,
            (since.isoformat(),)
        )
        row = cursor.fetchone()
        return row["total_pnl"] if row else 0.0
    finally:
        conn.close()


def get_trades_today_count() -> int:
    """Returns the number of trades opened today."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE open_time >= ?",
            (today_start.isoformat(),)
        )
        row = cursor.fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def get_last_loss_time() -> Optional[datetime]:
    """Returns the timestamp of the most recent losing trade, or None."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT close_time FROM trades
            WHERE status = 'CLOSED' AND profit < 0
            ORDER BY close_time DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row and row["close_time"]:
            return datetime.fromisoformat(row["close_time"])
        return None
    finally:
        conn.close()


def get_recent_trade_results(count: int = 10) -> list[float]:
    """Returns profits of the last `count` closed trades, ordered most recent first."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT profit + commission + swap as net_profit FROM trades
            WHERE status = 'CLOSED'
            ORDER BY close_time DESC LIMIT ?
            """,
            (count,)
        )
        return [row["net_profit"] for row in cursor.fetchall()]
    finally:
        conn.close()



def get_daily_stats(date_str: Optional[str] = None) -> dict:
    """Returns aggregated stats for a given date (default: today)."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    day_start = f"{date_str}T00:00:00"
    day_end = f"{date_str}T23:59:59"

    conn = _get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as losing_trades,
                COALESCE(SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END), 0) as gross_profit,
                COALESCE(SUM(CASE WHEN profit < 0 THEN profit ELSE 0 END), 0) as gross_loss,
                COALESCE(SUM(profit + commission + swap), 0) as net_pnl
            FROM trades
            WHERE status = 'CLOSED'
            AND close_time BETWEEN ? AND ?
            """,
            (day_start, day_end)
        )
        row = cursor.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def record_bot_event(event_type: str, details: str = "") -> None:
    """Records a bot lifecycle event (startup, shutdown, error, etc.)."""
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO bot_events (event_type, details) VALUES (?, ?)",
            (event_type, details)
        )
        conn.commit()
    finally:
        conn.close()
