"""
Real-Time Trading Dashboard
============================
Professional web dashboard for monitoring the trading bot.
Dark-themed Bloomberg-style UI with real-time updates.

Runs on http://0.0.0.0:5555 alongside the trading bot using threading.
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, render_template_string

# ---------------------------------------------------------------------------
# Optional MT5 import
# ---------------------------------------------------------------------------
try:
    import MetaTrader5 as mt5  # type: ignore

    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "trade_journal.db")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = "trading-dashboard-secret"

# Global state updated by the bot (set externally or via helpers)
_bot_status: Dict[str, Any] = {
    "uptime_start": datetime.now(timezone.utc).isoformat(),
    "last_cycle": None,
    "mode": "UNKNOWN",
    "symbol": "UNKNOWN",
    "running": False,
}

_bot_status_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public helpers – called by the bot to push status
# ---------------------------------------------------------------------------

def update_bot_status(
    *,
    mode: Optional[str] = None,
    symbol: Optional[str] = None,
    last_cycle: Optional[str] = None,
    running: Optional[bool] = None,
) -> None:
    """Thread-safe update of bot status fields."""
    with _bot_status_lock:
        if mode is not None:
            _bot_status["mode"] = mode
        if symbol is not None:
            _bot_status["symbol"] = symbol
        if last_cycle is not None:
            _bot_status["last_cycle"] = last_cycle
        if running is not None:
            _bot_status["running"] = running


def get_bot_status() -> Dict[str, Any]:
    """Return a snapshot of bot status."""
    with _bot_status_lock:
        status = dict(_bot_status)
    # Compute uptime
    try:
        start = datetime.fromisoformat(status["uptime_start"])
        delta = datetime.now(timezone.utc) - start
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        status["uptime"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception:
        status["uptime"] = "N/A"
    return status


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Open a read-only connection to the trade journal database."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_query(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Execute a read query and return list of dicts. Returns [] on error."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = _get_db()
        cursor = conn.execute(query, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _safe_query_one(query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    """Execute a read query and return one dict or None."""
    rows = _safe_query(query, params)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# MT5 live positions
# ---------------------------------------------------------------------------

def _get_mt5_positions() -> List[Dict[str, Any]]:
    """Fetch live positions from MetaTrader 5 if available."""
    if not MT5_AVAILABLE:
        return []
    try:
        if not mt5.terminal_info():
            return []
        positions = mt5.positions_get()
        if positions is None:
            return []
        result = []
        for pos in positions:
            direction = "BUY" if pos.type == 0 else "SELL"
            pnl = pos.profit
            entry = pos.price_open
            current = pos.price_current
            # Duration
            open_time = datetime.fromtimestamp(pos.time, tz=timezone.utc)
            duration_s = (datetime.now(timezone.utc) - open_time).total_seconds()
            hours, rem = divmod(int(duration_s), 3600)
            mins, secs = divmod(rem, 60)
            duration_str = f"{hours:02d}:{mins:02d}:{secs:02d}"
            # R-multiple (approximate – uses SL if available)
            sl = pos.sl
            risk = abs(entry - sl) if sl and sl != 0.0 else None
            r_multiple = round(pnl / (risk * pos.volume * 100000), 2) if risk and risk > 0 else None

            result.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "direction": direction,
                "volume": pos.volume,
                "entry_price": round(entry, 5),
                "current_price": round(current, 5),
                "sl": round(sl, 5) if sl else None,
                "tp": round(pos.tp, 5) if pos.tp else None,
                "pnl": round(pnl, 2),
                "r_multiple": r_multiple,
                "duration": duration_str,
                "open_time": open_time.isoformat(),
            })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status() -> Response:
    """Bot status endpoint."""
    return jsonify(get_bot_status())


@app.route("/api/positions")
def api_positions() -> Response:
    """Open positions – live from MT5 + fallback from DB."""
    positions = _get_mt5_positions()
    if not positions:
        # Fallback: check DB for open trades
        positions = _safe_query(
            "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY open_time DESC"
        )
        # Normalize field names for the dashboard
        normalized: List[Dict[str, Any]] = []
        for p in positions:
            normalized.append({
                "ticket": p.get("ticket", p.get("id", "?")),
                "symbol": p.get("symbol", "?"),
                "direction": p.get("direction", p.get("type", "?")),
                "volume": p.get("volume", p.get("lot_size", 0)),
                "entry_price": p.get("entry_price", p.get("open_price", 0)),
                "current_price": p.get("current_price", p.get("entry_price", 0)),
                "sl": p.get("sl", p.get("stop_loss", None)),
                "tp": p.get("tp", p.get("take_profit", None)),
                "pnl": p.get("pnl", p.get("profit", 0)),
                "r_multiple": p.get("r_multiple", None),
                "duration": p.get("duration", "—"),
                "open_time": p.get("open_time", ""),
            })
        positions = normalized
    return jsonify(positions)


@app.route("/api/trades")
def api_trades() -> Response:
    """Today's closed trades."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trades = _safe_query(
        "SELECT * FROM trades WHERE close_time LIKE ? ORDER BY close_time DESC",
        (f"{today}%",),
    )
    if not trades:
        # Try broader: last 50 trades
        trades = _safe_query(
            "SELECT * FROM trades WHERE status != 'OPEN' ORDER BY close_time DESC LIMIT 50"
        )
    return jsonify(trades)


@app.route("/api/equity_curve")
def api_equity_curve() -> Response:
    """Cumulative equity curve from closed trades."""
    trades = _safe_query(
        "SELECT close_time, pnl, profit FROM trades WHERE status != 'OPEN' ORDER BY close_time ASC"
    )
    cumulative = 0.0
    curve: List[Dict[str, Any]] = []
    for t in trades:
        pnl_val = t.get("pnl") or t.get("profit") or 0
        try:
            pnl_val = float(pnl_val)
        except (ValueError, TypeError):
            pnl_val = 0.0
        cumulative += pnl_val
        curve.append({
            "time": t.get("close_time", ""),
            "equity": round(cumulative, 2),
            "pnl": round(pnl_val, 2),
        })
    return jsonify(curve)


@app.route("/api/risk_status")
def api_risk_status() -> Response:
    """Risk metrics for today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Today's trades
    today_trades = _safe_query(
        "SELECT pnl, profit FROM trades WHERE close_time LIKE ? AND status != 'OPEN'",
        (f"{today}%",),
    )
    daily_pnl = 0.0
    daily_loss = 0.0
    losing_streak = 0
    current_streak = 0
    for t in today_trades:
        pnl_val = t.get("pnl") or t.get("profit") or 0
        try:
            pnl_val = float(pnl_val)
        except (ValueError, TypeError):
            pnl_val = 0.0
        daily_pnl += pnl_val
        if pnl_val < 0:
            daily_loss += pnl_val
            current_streak += 1
            losing_streak = max(losing_streak, current_streak)
        else:
            current_streak = 0

    # Open positions
    positions = _get_mt5_positions()
    open_count = len(positions)
    floating_pnl = sum(p.get("pnl", 0) for p in positions)

    return jsonify({
        "daily_pnl": round(daily_pnl, 2),
        "daily_loss": round(daily_loss, 2),
        "positions_open": open_count,
        "losing_streak": losing_streak,
        "floating_pnl": round(floating_pnl, 2),
        "trades_today": len(today_trades),
    })


@app.route("/api/performance")
def api_performance() -> Response:
    """Overall performance summary."""
    trades = _safe_query(
        "SELECT pnl, profit FROM trades WHERE status != 'OPEN'"
    )
    if not trades:
        return jsonify({
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
        })

    pnls: List[float] = []
    for t in trades:
        pnl_val = t.get("pnl") or t.get("profit") or 0
        try:
            pnls.append(float(pnl_val))
        except (ValueError, TypeError):
            pnls.append(0.0)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_pnl = sum(pnls)
    win_rate = (len(wins) / len(pnls) * 100) if pnls else 0

    # Profit factor
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    # Max drawdown
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    return jsonify({
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "net_pnl": round(net_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "∞",
        "max_drawdown": round(max_dd, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
    })


# ---------------------------------------------------------------------------
# HTML Template — Bloomberg-style dark dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Command Center</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  /* ───────────── Reset & Base ───────────── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg-primary:   #0a0e17;
    --bg-secondary: #111827;
    --bg-card:      #151c2c;
    --bg-card-hover:#1a2236;
    --border:       #1e293b;
    --border-light: #2d3a4f;
    --text-primary: #e2e8f0;
    --text-secondary:#94a3b8;
    --text-muted:   #64748b;
    --accent-green: #00d4aa;
    --accent-red:   #ff4757;
    --accent-blue:  #3b82f6;
    --accent-amber: #f59e0b;
    --accent-purple:#8b5cf6;
    --font-mono:    'JetBrains Mono', 'Fira Code', 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
    --font-sans:    'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }
  body {
    background: var(--bg-primary);
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: 13px;
    line-height: 1.5;
    min-height: 100vh;
    overflow-x: hidden;
  }
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

  /* ───────────── Header ───────────── */
  .header {
    background: linear-gradient(180deg, #111827 0%, #0f1520 100%);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(12px);
  }
  .header-left { display: flex; align-items: center; gap: 16px; }
  .logo {
    font-family: var(--font-mono);
    font-size: 18px;
    font-weight: 700;
    color: var(--accent-green);
    letter-spacing: -0.5px;
  }
  .logo-sub {
    font-size: 10px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 2px;
    font-weight: 500;
  }
  .header-right { display: flex; align-items: center; gap: 20px; }
  .status-badge {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .status-live {
    background: rgba(0,212,170,0.12);
    color: var(--accent-green);
    border: 1px solid rgba(0,212,170,0.25);
  }
  .status-offline {
    background: rgba(255,71,87,0.12);
    color: var(--accent-red);
    border: 1px solid rgba(255,71,87,0.25);
  }
  .pulse {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent-green);
    animation: pulse-anim 2s ease-in-out infinite;
  }
  @keyframes pulse-anim {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(0,212,170,0.4); }
    50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(0,212,170,0); }
  }
  .clock {
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--text-secondary);
    font-weight: 500;
  }

  /* ───────────── Grid Layout ───────────── */
  .dashboard {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    grid-template-rows: auto auto 1fr 1fr;
    gap: 12px;
    padding: 16px 24px;
    max-width: 1920px;
    margin: 0 auto;
  }
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    transition: border-color 0.2s;
  }
  .card:hover { border-color: var(--border-light); }
  .card-title {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--text-muted);
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .card-title .icon { font-size: 13px; }

  /* ───────────── KPI Cards (top row) ───────────── */
  .kpi-row { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
  .kpi {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    text-align: center;
  }
  .kpi-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: var(--text-muted);
    margin-bottom: 6px;
  }
  .kpi-value {
    font-family: var(--font-mono);
    font-size: 22px;
    font-weight: 700;
    color: var(--text-primary);
  }
  .kpi-sub {
    font-size: 11px;
    color: var(--text-secondary);
    margin-top: 2px;
    font-family: var(--font-mono);
  }
  .positive { color: var(--accent-green) !important; }
  .negative { color: var(--accent-red) !important; }
  .neutral  { color: var(--accent-blue) !important; }
  .warning  { color: var(--accent-amber) !important; }

  /* ───────────── Chart ───────────── */
  .chart-card {
    grid-column: 1 / 4;
    grid-row: span 1;
    min-height: 300px;
  }
  .chart-container {
    position: relative;
    width: 100%;
    height: 260px;
  }

  /* ───────────── Risk Panel ───────────── */
  .risk-card { grid-column: 4 / 5; }
  .risk-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .risk-item:last-child { border-bottom: none; }
  .risk-label { color: var(--text-secondary); font-size: 12px; }
  .risk-value {
    font-family: var(--font-mono);
    font-weight: 600;
    font-size: 14px;
  }
  .risk-bar {
    width: 100%;
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    margin-top: 4px;
    overflow: hidden;
  }
  .risk-bar-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.5s ease;
  }

  /* ───────────── Bot Info ───────────── */
  .bot-info-card { grid-column: 1 / 3; }
  .bot-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
  }
  .bot-item { text-align: center; }
  .bot-item-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-muted);
    margin-bottom: 4px;
  }
  .bot-item-value {
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
  }

  /* ───────────── Performance ───────────── */
  .perf-card { grid-column: 3 / 5; }
  .perf-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
  }

  /* ───────────── Tables ───────────── */
  .table-card { grid-column: 1 / -1; max-height: 350px; overflow-y: auto; }
  .table-card::-webkit-scrollbar { width: 6px; }
  .table-card::-webkit-scrollbar-track { background: var(--bg-card); }
  .table-card::-webkit-scrollbar-thumb { background: var(--border-light); border-radius: 3px; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th {
    text-align: left;
    padding: 8px 12px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    background: var(--bg-card);
    z-index: 1;
  }
  td {
    padding: 8px 12px;
    border-bottom: 1px solid rgba(30,41,59,0.5);
    font-family: var(--font-mono);
    font-size: 12px;
    white-space: nowrap;
  }
  tr:hover td { background: rgba(59,130,246,0.04); }
  .dir-buy {
    background: rgba(0,212,170,0.1);
    color: var(--accent-green);
    padding: 2px 8px;
    border-radius: 3px;
    font-weight: 600;
    font-size: 10px;
  }
  .dir-sell {
    background: rgba(255,71,87,0.1);
    color: var(--accent-red);
    padding: 2px 8px;
    border-radius: 3px;
    font-weight: 600;
    font-size: 10px;
  }

  /* ───────────── Footer ───────────── */
  .footer {
    text-align: center;
    padding: 12px;
    color: var(--text-muted);
    font-size: 11px;
    border-top: 1px solid var(--border);
    margin-top: 8px;
  }
  .footer span { font-family: var(--font-mono); }

  /* ───────────── Responsive ───────────── */
  @media (max-width: 1200px) {
    .dashboard { grid-template-columns: repeat(2, 1fr); }
    .kpi-row { grid-template-columns: repeat(3, 1fr); }
    .chart-card { grid-column: 1 / -1; }
    .risk-card { grid-column: 1 / -1; }
    .bot-info-card { grid-column: 1 / -1; }
    .perf-card { grid-column: 1 / -1; }
  }
  @media (max-width: 768px) {
    .kpi-row { grid-template-columns: repeat(2, 1fr); }
    .dashboard { grid-template-columns: 1fr; padding: 8px; }
  }

  /* ───────────── Animations ───────────── */
  .fade-in { animation: fadeIn 0.3s ease-in; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  .flash { animation: flash 0.4s ease; }
  @keyframes flash { 0%,100% { background: transparent; } 50% { background: rgba(59,130,246,0.08); } }

  /* ───────────── Empty state ───────────── */
  .empty-state {
    text-align: center;
    padding: 40px 20px;
    color: var(--text-muted);
    font-size: 13px;
  }
  .empty-state .icon { font-size: 28px; margin-bottom: 8px; }
</style>
</head>
<body>

<!-- ════════════ HEADER ════════════ -->
<header class="header">
  <div class="header-left">
    <div>
      <div class="logo">◆ TRADING COMMAND CENTER</div>
      <div class="logo-sub">Autonomous Trading System v2.0</div>
    </div>
  </div>
  <div class="header-right">
    <div id="bot-status-badge" class="status-badge status-offline">
      <div class="pulse" style="background:var(--accent-red)"></div>
      <span>OFFLINE</span>
    </div>
    <div class="clock" id="clock">--:--:--</div>
  </div>
</header>

<!-- ════════════ DASHBOARD GRID ════════════ -->
<main class="dashboard">

  <!-- KPI Row -->
  <div class="kpi-row">
    <div class="kpi">
      <div class="kpi-label">Net P&L</div>
      <div class="kpi-value" id="kpi-net-pnl">$0.00</div>
      <div class="kpi-sub" id="kpi-net-pnl-sub">—</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Win Rate</div>
      <div class="kpi-value" id="kpi-win-rate">0%</div>
      <div class="kpi-sub" id="kpi-win-rate-sub">0W / 0L</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Profit Factor</div>
      <div class="kpi-value neutral" id="kpi-pf">0.00</div>
      <div class="kpi-sub">gross win / gross loss</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Max Drawdown</div>
      <div class="kpi-value negative" id="kpi-dd">$0.00</div>
      <div class="kpi-sub">peak to trough</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Trades Today</div>
      <div class="kpi-value neutral" id="kpi-trades-today">0</div>
      <div class="kpi-sub" id="kpi-daily-pnl">$0.00</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Expectancy</div>
      <div class="kpi-value" id="kpi-expectancy">$0.00</div>
      <div class="kpi-sub">per trade</div>
    </div>
  </div>

  <!-- Equity Curve Chart -->
  <div class="card chart-card">
    <div class="card-title"><span class="icon">📈</span> Equity Curve</div>
    <div class="chart-container">
      <canvas id="equityChart"></canvas>
    </div>
  </div>

  <!-- Risk Status -->
  <div class="card risk-card">
    <div class="card-title"><span class="icon">🛡️</span> Risk Monitor</div>
    <div class="risk-item">
      <span class="risk-label">Daily Loss Used</span>
      <span class="risk-value" id="risk-daily-loss">$0.00</span>
    </div>
    <div class="risk-bar"><div class="risk-bar-fill" id="risk-bar-daily" style="width:0%;background:var(--accent-green)"></div></div>
    <div class="risk-item">
      <span class="risk-label">Open Positions</span>
      <span class="risk-value neutral" id="risk-open-pos">0</span>
    </div>
    <div class="risk-item">
      <span class="risk-label">Losing Streak</span>
      <span class="risk-value" id="risk-streak">0</span>
    </div>
    <div class="risk-item">
      <span class="risk-label">Floating P&L</span>
      <span class="risk-value" id="risk-floating">$0.00</span>
    </div>
    <div class="risk-item">
      <span class="risk-label">Trades Today</span>
      <span class="risk-value neutral" id="risk-trades-today">0</span>
    </div>
  </div>

  <!-- Bot Info -->
  <div class="card bot-info-card">
    <div class="card-title"><span class="icon">🤖</span> Bot Status</div>
    <div class="bot-grid">
      <div class="bot-item">
        <div class="bot-item-label">Uptime</div>
        <div class="bot-item-value" id="bot-uptime">00:00:00</div>
      </div>
      <div class="bot-item">
        <div class="bot-item-label">Last Cycle</div>
        <div class="bot-item-value" id="bot-last-cycle">—</div>
      </div>
      <div class="bot-item">
        <div class="bot-item-label">Mode</div>
        <div class="bot-item-value" id="bot-mode">—</div>
      </div>
      <div class="bot-item">
        <div class="bot-item-label">Symbol</div>
        <div class="bot-item-value" id="bot-symbol">—</div>
      </div>
      <div class="bot-item">
        <div class="bot-item-label">Status</div>
        <div class="bot-item-value" id="bot-running">—</div>
      </div>
      <div class="bot-item">
        <div class="bot-item-label">Refresh</div>
        <div class="bot-item-value" id="bot-refresh">10s</div>
      </div>
    </div>
  </div>

  <!-- Performance Summary -->
  <div class="card perf-card">
    <div class="card-title"><span class="icon">📊</span> Performance Summary</div>
    <div class="perf-grid">
      <div class="bot-item">
        <div class="bot-item-label">Total Trades</div>
        <div class="bot-item-value" id="perf-total">0</div>
      </div>
      <div class="bot-item">
        <div class="bot-item-label">Avg Win</div>
        <div class="bot-item-value positive" id="perf-avg-win">$0.00</div>
      </div>
      <div class="bot-item">
        <div class="bot-item-label">Avg Loss</div>
        <div class="bot-item-value negative" id="perf-avg-loss">$0.00</div>
      </div>
    </div>
  </div>

  <!-- Open Positions Table -->
  <div class="card table-card">
    <div class="card-title"><span class="icon">📋</span> Open Positions</div>
    <table>
      <thead>
        <tr>
          <th>Ticket</th>
          <th>Symbol</th>
          <th>Direction</th>
          <th>Volume</th>
          <th>Entry Price</th>
          <th>Current Price</th>
          <th>SL</th>
          <th>TP</th>
          <th>P&L</th>
          <th>R-Multiple</th>
          <th>Duration</th>
        </tr>
      </thead>
      <tbody id="positions-body">
        <tr><td colspan="11" class="empty-state"><div class="icon">📭</div>No open positions</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Trade History Table -->
  <div class="card table-card">
    <div class="card-title"><span class="icon">📜</span> Trade History</div>
    <table>
      <thead>
        <tr>
          <th>Close Time</th>
          <th>Symbol</th>
          <th>Direction</th>
          <th>Entry</th>
          <th>Exit</th>
          <th>Volume</th>
          <th>P&L</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="trades-body">
        <tr><td colspan="8" class="empty-state"><div class="icon">📭</div>No trades recorded</td></tr>
      </tbody>
    </table>
  </div>

</main>

<footer class="footer">
  <span>TRADING COMMAND CENTER</span> · Auto-refresh <span id="countdown">10</span>s · Data from <span>trade_journal.db</span>
</footer>

<!-- ════════════ JAVASCRIPT ════════════ -->
<script>
  // ── Globals ──
  let equityChart = null;
  let refreshInterval = 10;
  let countdown = refreshInterval;

  // ── Clock ──
  function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent =
      now.toUTCString().slice(-12, -4) + ' UTC';
  }
  setInterval(updateClock, 1000);
  updateClock();

  // ── Countdown ──
  setInterval(() => {
    countdown--;
    if (countdown < 0) countdown = refreshInterval;
    document.getElementById('countdown').textContent = countdown;
  }, 1000);

  // ── Helpers ──
  function pnlClass(v) { return v > 0 ? 'positive' : v < 0 ? 'negative' : ''; }
  function fmt$(v) {
    const n = parseFloat(v) || 0;
    return (n >= 0 ? '+' : '') + '$' + n.toFixed(2);
  }
  function dirBadge(d) {
    const cls = (d||'').toUpperCase().includes('BUY') ? 'dir-buy' : 'dir-sell';
    return `<span class="${cls}">${(d||'?').toUpperCase()}</span>`;
  }

  // ── Fetch helpers ──
  async function fetchJSON(url) {
    try {
      const r = await fetch(url);
      return await r.json();
    } catch { return null; }
  }

  // ── Update KPIs & Performance ──
  async function updatePerformance() {
    const d = await fetchJSON('/api/performance');
    if (!d) return;
    const netEl = document.getElementById('kpi-net-pnl');
    netEl.textContent = fmt$(d.net_pnl);
    netEl.className = 'kpi-value ' + pnlClass(d.net_pnl);
    document.getElementById('kpi-net-pnl-sub').textContent = d.total_trades + ' trades';
    document.getElementById('kpi-win-rate').textContent = d.win_rate + '%';
    document.getElementById('kpi-win-rate-sub').textContent = d.wins + 'W / ' + d.losses + 'L';
    document.getElementById('kpi-pf').textContent = d.profit_factor;
    document.getElementById('kpi-dd').textContent = '$' + d.max_drawdown;
    const expEl = document.getElementById('kpi-expectancy');
    expEl.textContent = fmt$(d.expectancy);
    expEl.className = 'kpi-value ' + pnlClass(d.expectancy);
    document.getElementById('perf-total').textContent = d.total_trades;
    document.getElementById('perf-avg-win').textContent = fmt$(d.avg_win);
    document.getElementById('perf-avg-loss').textContent = fmt$(d.avg_loss);
  }

  // ── Update Bot Status ──
  async function updateBotStatus() {
    const d = await fetchJSON('/api/status');
    if (!d) return;
    document.getElementById('bot-uptime').textContent = d.uptime || '—';
    document.getElementById('bot-last-cycle').textContent = d.last_cycle ? d.last_cycle.slice(11,19) : '—';
    document.getElementById('bot-mode').textContent = d.mode || '—';
    document.getElementById('bot-symbol').textContent = d.symbol || '—';
    const running = d.running;
    const runEl = document.getElementById('bot-running');
    runEl.textContent = running ? 'ACTIVE' : 'STOPPED';
    runEl.className = 'bot-item-value ' + (running ? 'positive' : 'negative');
    // Header badge
    const badge = document.getElementById('bot-status-badge');
    if (running) {
      badge.className = 'status-badge status-live';
      badge.innerHTML = '<div class="pulse"></div><span>LIVE</span>';
    } else {
      badge.className = 'status-badge status-offline';
      badge.innerHTML = '<div class="pulse" style="background:var(--accent-red)"></div><span>OFFLINE</span>';
    }
  }

  // ── Update Risk ──
  async function updateRisk() {
    const d = await fetchJSON('/api/risk_status');
    if (!d) return;
    const lossEl = document.getElementById('risk-daily-loss');
    lossEl.textContent = fmt$(d.daily_loss);
    lossEl.className = 'risk-value ' + pnlClass(d.daily_loss);
    document.getElementById('risk-open-pos').textContent = d.positions_open;
    const streakEl = document.getElementById('risk-streak');
    streakEl.textContent = d.losing_streak;
    streakEl.className = 'risk-value ' + (d.losing_streak >= 3 ? 'negative' : d.losing_streak >= 2 ? 'warning' : '');
    const floatEl = document.getElementById('risk-floating');
    floatEl.textContent = fmt$(d.floating_pnl);
    floatEl.className = 'risk-value ' + pnlClass(d.floating_pnl);
    document.getElementById('risk-trades-today').textContent = d.trades_today;
    document.getElementById('kpi-trades-today').textContent = d.trades_today;
    const dpnlEl = document.getElementById('kpi-daily-pnl');
    dpnlEl.textContent = fmt$(d.daily_pnl);
    dpnlEl.className = 'kpi-sub ' + pnlClass(d.daily_pnl);
    // Risk bar
    const pct = Math.min(Math.abs(d.daily_loss) / 500 * 100, 100); // Assuming $500 daily limit
    const bar = document.getElementById('risk-bar-daily');
    bar.style.width = pct + '%';
    bar.style.background = pct > 75 ? 'var(--accent-red)' : pct > 50 ? 'var(--accent-amber)' : 'var(--accent-green)';
  }

  // ── Update Positions ──
  async function updatePositions() {
    const data = await fetchJSON('/api/positions');
    const body = document.getElementById('positions-body');
    if (!data || data.length === 0) {
      body.innerHTML = '<tr><td colspan="11" class="empty-state"><div class="icon">📭</div>No open positions</td></tr>';
      return;
    }
    body.innerHTML = data.map(p => `<tr class="fade-in">
      <td>${p.ticket || '—'}</td>
      <td>${p.symbol || '—'}</td>
      <td>${dirBadge(p.direction)}</td>
      <td>${p.volume || '—'}</td>
      <td>${p.entry_price || '—'}</td>
      <td>${p.current_price || '—'}</td>
      <td>${p.sl || '—'}</td>
      <td>${p.tp || '—'}</td>
      <td class="${pnlClass(p.pnl)}">${fmt$(p.pnl)}</td>
      <td class="${pnlClass(p.r_multiple)}">${p.r_multiple != null ? p.r_multiple.toFixed(2) + 'R' : '—'}</td>
      <td>${p.duration || '—'}</td>
    </tr>`).join('');
  }

  // ── Update Trades ──
  async function updateTrades() {
    const data = await fetchJSON('/api/trades');
    const body = document.getElementById('trades-body');
    if (!data || data.length === 0) {
      body.innerHTML = '<tr><td colspan="8" class="empty-state"><div class="icon">📭</div>No trades recorded</td></tr>';
      return;
    }
    body.innerHTML = data.slice(0, 50).map(t => {
      const pnl = t.pnl || t.profit || 0;
      return `<tr class="fade-in">
        <td>${(t.close_time || '—').slice(0,19)}</td>
        <td>${t.symbol || '—'}</td>
        <td>${dirBadge(t.direction || t.type)}</td>
        <td>${t.entry_price || t.open_price || '—'}</td>
        <td>${t.exit_price || t.close_price || '—'}</td>
        <td>${t.volume || t.lot_size || '—'}</td>
        <td class="${pnlClass(pnl)}">${fmt$(pnl)}</td>
        <td>${t.status || 'CLOSED'}</td>
      </tr>`;
    }).join('');
  }

  // ── Equity Chart ──
  async function updateEquityChart() {
    const data = await fetchJSON('/api/equity_curve');
    if (!data || data.length === 0) return;
    const labels = data.map(d => {
      const t = d.time || '';
      return t.length > 10 ? t.slice(5, 16) : t;
    });
    const values = data.map(d => d.equity);
    if (equityChart) {
      equityChart.data.labels = labels;
      equityChart.data.datasets[0].data = values;
      equityChart.update('none');
      return;
    }
    const ctx = document.getElementById('equityChart').getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 260);
    const lastVal = values[values.length - 1] || 0;
    if (lastVal >= 0) {
      gradient.addColorStop(0, 'rgba(0,212,170,0.25)');
      gradient.addColorStop(1, 'rgba(0,212,170,0.0)');
    } else {
      gradient.addColorStop(0, 'rgba(255,71,87,0.25)');
      gradient.addColorStop(1, 'rgba(255,71,87,0.0)');
    }
    equityChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Equity',
          data: values,
          borderColor: lastVal >= 0 ? '#00d4aa' : '#ff4757',
          backgroundColor: gradient,
          borderWidth: 2,
          fill: true,
          tension: 0.3,
          pointRadius: values.length > 50 ? 0 : 2,
          pointHoverRadius: 4,
          pointBackgroundColor: lastVal >= 0 ? '#00d4aa' : '#ff4757',
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1e293b',
            titleColor: '#e2e8f0',
            bodyColor: '#94a3b8',
            borderColor: '#334155',
            borderWidth: 1,
            cornerRadius: 6,
            padding: 10,
            bodyFont: { family: "'JetBrains Mono', monospace", size: 12 },
            callbacks: {
              label: (ctx) => 'Equity: $' + ctx.parsed.y.toFixed(2)
            }
          }
        },
        scales: {
          x: {
            display: true,
            grid: { color: 'rgba(30,41,59,0.5)', drawBorder: false },
            ticks: { color: '#64748b', font: { size: 10, family: "'JetBrains Mono', monospace" }, maxTicksLimit: 10 }
          },
          y: {
            display: true,
            grid: { color: 'rgba(30,41,59,0.5)', drawBorder: false },
            ticks: {
              color: '#64748b',
              font: { size: 10, family: "'JetBrains Mono', monospace" },
              callback: (v) => '$' + v.toFixed(0)
            }
          }
        }
      }
    });
  }

  // ── Master refresh ──
  async function refreshAll() {
    countdown = refreshInterval;
    await Promise.all([
      updatePerformance(),
      updateBotStatus(),
      updateRisk(),
      updatePositions(),
      updateTrades(),
      updateEquityChart(),
    ]);
  }

  // Initial load + auto-refresh
  refreshAll();
  setInterval(refreshAll, refreshInterval * 1000);
</script>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@app.route("/")
def index() -> str:
    """Serve the dashboard."""
    return render_template_string(DASHBOARD_HTML)


# ---------------------------------------------------------------------------
# Dashboard launcher (threaded)
# ---------------------------------------------------------------------------

_dashboard_thread: Optional[threading.Thread] = None


def start_dashboard(
    host: str = "0.0.0.0",
    port: int = 5555,
    debug: bool = False,
) -> threading.Thread:
    """Start the dashboard in a background daemon thread.

    Args:
        host: Bind address.
        port: Bind port.
        debug: Flask debug mode (disable in production).

    Returns:
        The background Thread running the server.
    """
    global _dashboard_thread

    def _run() -> None:
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.WARNING)
        app.run(host=host, port=port, debug=debug, use_reloader=False)

    _dashboard_thread = threading.Thread(target=_run, name="DashboardServer", daemon=True)
    _dashboard_thread.start()
    print(f"[Dashboard] ✓ Running at http://{host}:{port}")
    return _dashboard_thread


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 50)
    print("  Trading Command Center Dashboard")
    print(f"  http://localhost:5555")
    print("=" * 50)
    update_bot_status(running=True, mode="STANDALONE", symbol="—")
    app.run(host="0.0.0.0", port=5555, debug=True)
