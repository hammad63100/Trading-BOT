"""
Real-Time Trading Command Center — High-Skill Animated Dashboard (v2.0)
========================================================================
Institutional-grade, dark-themed, glassmorphic algorithmic trading dashboard.
Features real-time neon indicators, Chart.js glowing equity curves,
animated risk dials, live position monitors, and smooth micro-interactions.

Runs on http://0.0.0.0:5555 alongside the trading bot using background threading.
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
app.config["SECRET_KEY"] = "trading-dashboard-secret-v2"

# Global state updated by the bot (set externally or via helpers)
_bot_status: Dict[str, Any] = {
    "uptime_start": datetime.now(timezone.utc).isoformat(),
    "last_cycle": None,
    "mode": "DEMO",
    "symbol": "XAUUSDm",
    "running": False,
    "mtf_status": "H1: NEUTRAL | H4: NEUTRAL",
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
    mtf_status: Optional[str] = None,
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
        if mtf_status is not None:
            _bot_status["mtf_status"] = mtf_status


def get_bot_status() -> Dict[str, Any]:
    """Return a snapshot of bot status."""
    with _bot_status_lock:
        status = dict(_bot_status)
    try:
        start = datetime.fromisoformat(status["uptime_start"])
        delta = datetime.now(timezone.utc) - start
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        status["uptime"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception:
        status["uptime"] = "00:00:00"
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
            open_time = datetime.fromtimestamp(pos.time, tz=timezone.utc)
            duration_s = (datetime.now(timezone.utc) - open_time).total_seconds()
            hours, rem = divmod(int(duration_s), 3600)
            mins, secs = divmod(rem, 60)
            duration_str = f"{hours:02d}:{mins:02d}:{secs:02d}"

            sl = pos.sl
            risk = abs(entry - sl) if sl and sl != 0.0 else None
            r_multiple = round(pnl / (risk * pos.volume * 100), 2) if risk and risk > 0 else None

            result.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "direction": direction,
                "volume": pos.volume,
                "entry_price": round(entry, 3),
                "current_price": round(current, 3),
                "sl": round(sl, 3) if sl else None,
                "tp": round(pos.tp, 3) if pos.tp else None,
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
        rows = _safe_query("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY open_time DESC")
        normalized: List[Dict[str, Any]] = []
        for p in rows:
            normalized.append({
                "ticket": p.get("ticket", p.get("id", "?")),
                "symbol": p.get("symbol", "XAUUSDm"),
                "direction": p.get("direction", "LONG"),
                "volume": p.get("volume", 0.01),
                "entry_price": p.get("open_price", 0.0),
                "current_price": p.get("open_price", 0.0),
                "sl": p.get("sl", None),
                "tp": p.get("tp", None),
                "pnl": p.get("profit", 0.0),
                "r_multiple": None,
                "duration": "00:00:00",
                "open_time": p.get("open_time", ""),
            })
        positions = normalized
    return jsonify(positions)


@app.route("/api/trades")
def api_trades() -> Response:
    """Closed trades history with full details."""
    trades = _safe_query(
        "SELECT * FROM trades WHERE status != 'OPEN' ORDER BY close_time DESC LIMIT 100"
    )
    return jsonify(trades)


@app.route("/api/equity_curve")
def api_equity_curve() -> Response:
    """Cumulative equity curve from closed trades."""
    trades = _safe_query(
        "SELECT close_time, profit, commission, swap FROM trades WHERE status != 'OPEN' ORDER BY close_time ASC"
    )
    cumulative = 0.0
    curve: List[Dict[str, Any]] = []
    for t in trades:
        pnl_val = (t.get("profit") or 0.0) + (t.get("commission") or 0.0) + (t.get("swap") or 0.0)
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
    """Risk metrics, drawdown, losing streaks, and daily limits."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_trades = _safe_query(
        "SELECT profit, commission, swap FROM trades WHERE close_time LIKE ? AND status != 'OPEN'",
        (f"{today}%",),
    )
    daily_pnl = 0.0
    daily_loss = 0.0
    losing_streak = 0
    current_streak = 0

    # Also compute recent consecutive losses from all closed trades
    recent_closed = _safe_query(
        "SELECT profit, commission, swap FROM trades WHERE status != 'OPEN' ORDER BY close_time DESC LIMIT 15"
    )
    recent_losses_streak = 0
    for r in recent_closed:
        net = (r.get("profit") or 0.0) + (r.get("commission") or 0.0) + (r.get("swap") or 0.0)
        if net < 0:
            recent_losses_streak += 1
        else:
            break

    for t in today_trades:
        net = (t.get("profit") or 0.0) + (t.get("commission") or 0.0) + (t.get("swap") or 0.0)
        daily_pnl += net
        if net < 0:
            daily_loss += net
            current_streak += 1
            losing_streak = max(losing_streak, current_streak)
        else:
            current_streak = 0

    positions = _get_mt5_positions()
    open_count = len(positions)
    floating_pnl = sum(p.get("pnl", 0) for p in positions)

    return jsonify({
        "daily_pnl": round(daily_pnl, 2),
        "daily_loss": round(abs(daily_loss), 2),
        "max_daily_loss": 50.0,
        "positions_open": open_count,
        "max_positions": 2,
        "losing_streak": recent_losses_streak,
        "max_streak_limit": 5,
        "floating_pnl": round(floating_pnl, 2),
        "trades_today": len(today_trades),
        "anti_martingale_multiplier": 0.5 if recent_losses_streak == 1 else (0.25 if recent_losses_streak >= 2 else 1.0),
    })


@app.route("/api/performance")
def api_performance() -> Response:
    """Overall quantitative performance metrics."""
    trades = _safe_query(
        "SELECT profit, commission, swap, signal_reason FROM trades WHERE status != 'OPEN' ORDER BY close_time ASC"
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
        net = (t.get("profit") or 0.0) + (t.get("commission") or 0.0) + (t.get("swap") or 0.0)
        pnls.append(round(float(net), 2))

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_pnl = sum(pnls)
    win_rate = (len(wins) / len(pnls) * 100) if pnls else 0.0

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # Max Drawdown
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
        "profit_factor": round(profit_factor, 2) if profit_factor < 999 else "999+",
        "max_drawdown": round(max_dd, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
    })


# ---------------------------------------------------------------------------
# High-Skill Animated HTML & CSS UI
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Command Center — Autonomous Gold Bot v2.0</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  /* ─────────────────────────────────────────────────────────────
     CYBER QUANT OBSIDIAN THEME & GLASSMORPHISM
     ───────────────────────────────────────────────────────────── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg-space: #060913;
    --bg-card: rgba(14, 21, 38, 0.72);
    --bg-card-hover: rgba(20, 30, 52, 0.85);
    --bg-inner: rgba(9, 14, 28, 0.65);
    --border-subtle: rgba(255, 255, 255, 0.07);
    --border-glow: rgba(0, 245, 196, 0.35);
    --border-active: rgba(59, 130, 246, 0.5);

    --neon-cyan: #00f5c4;
    --neon-emerald: #10b981;
    --neon-rose: #ff4365;
    --neon-gold: #f59e0b;
    --neon-blue: #3b82f6;
    --neon-purple: #a855f7;

    --text-pure: #ffffff;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;

    --font-heading: 'Plus Jakarta Sans', sans-serif;
    --font-sans: 'Inter', -apple-system, sans-serif;
    --font-mono: 'JetBrains Mono', 'SF Mono', monospace;
  }

  body {
    background-color: var(--bg-space);
    background-image: 
      radial-gradient(circle at 15% 10%, rgba(0, 245, 196, 0.08) 0%, transparent 40%),
      radial-gradient(circle at 85% 15%, rgba(59, 130, 246, 0.09) 0%, transparent 45%),
      radial-gradient(circle at 50% 90%, rgba(168, 85, 247, 0.07) 0%, transparent 50%);
    background-attachment: fixed;
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: 13px;
    min-height: 100vh;
    overflow-x: hidden;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }

  /* ─────────────────────────────────────────────────────────────
     HEADER & TICKER TAPE
     ───────────────────────────────────────────────────────────── */
  .top-strip {
    background: rgba(8, 12, 24, 0.85);
    backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border-subtle);
    padding: 6px 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-muted);
    letter-spacing: 0.5px;
  }
  .ticker-wrap { display: flex; align-items: center; gap: 20px; }
  .ticker-item { display: inline-flex; align-items: center; gap: 6px; }
  .ticker-val { color: var(--text-primary); font-weight: 600; }
  .session-badge {
    background: rgba(0, 245, 196, 0.12);
    color: var(--neon-cyan);
    padding: 1px 7px;
    border-radius: 12px;
    font-size: 10px;
    font-weight: 600;
  }

  .main-header {
    background: rgba(11, 17, 33, 0.75);
    backdrop-filter: blur(24px);
    border-bottom: 1px solid var(--border-subtle);
    padding: 14px 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 100;
  }

  .brand-group { display: flex; align-items: center; gap: 14px; }
  .brand-icon {
    width: 40px; height: 40px;
    background: linear-gradient(135deg, rgba(0,245,196,0.2), rgba(59,130,246,0.3));
    border: 1px solid rgba(0, 245, 196, 0.4);
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 0 20px rgba(0, 245, 196, 0.2);
    font-size: 18px;
    animation: floating-glow 4s ease-in-out infinite alternate;
  }

  @keyframes floating-glow {
    0% { box-shadow: 0 0 15px rgba(0, 245, 196, 0.15); transform: translateY(0); }
    100% { box-shadow: 0 0 25px rgba(0, 245, 196, 0.35); transform: translateY(-2px); }
  }

  .brand-title {
    font-family: var(--font-heading);
    font-size: 17px;
    font-weight: 800;
    letter-spacing: -0.3px;
    background: linear-gradient(90deg, #ffffff 30%, #a5f3fc 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .brand-sub {
    font-size: 11px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .header-actions { display: flex; align-items: center; gap: 16px; }

  /* ─────────────────────────────────────────────────────────────
     RADAR PULSE STATUS BEACON
     ───────────────────────────────────────────────────────────── */
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 9px;
    padding: 6px 16px;
    border-radius: 24px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    transition: all 0.3s ease;
  }
  .status-live {
    background: rgba(0, 245, 196, 0.1);
    color: var(--neon-cyan);
    border: 1px solid rgba(0, 245, 196, 0.35);
    box-shadow: 0 0 15px rgba(0, 245, 196, 0.15);
  }
  .status-stopped {
    background: rgba(255, 67, 101, 0.1);
    color: var(--neon-rose);
    border: 1px solid rgba(255, 67, 101, 0.35);
  }

  .radar-point {
    position: relative;
    width: 8px; height: 8px;
    border-radius: 50%;
  }
  .radar-point.live { background: var(--neon-cyan); }
  .radar-point.stopped { background: var(--neon-rose); }
  .radar-point.live::before, .radar-point.live::after {
    content: '';
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    border-radius: 50%;
    border: 1.5px solid var(--neon-cyan);
    animation: radar-wave 2.2s cubic-bezier(0.2, 0.6, 0.3, 1) infinite;
  }
  .radar-point.live::after { animation-delay: 1.1s; }

  @keyframes radar-wave {
    0% { width: 6px; height: 6px; opacity: 1; }
    100% { width: 28px; height: 28px; opacity: 0; }
  }

  .btn-action {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--border-subtle);
    color: var(--text-secondary);
    padding: 7px 14px;
    border-radius: 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.2s ease;
  }
  .btn-action:hover {
    background: rgba(255, 255, 255, 0.1);
    color: #fff;
    border-color: rgba(255, 255, 255, 0.2);
    transform: translateY(-1px);
  }
  .btn-action.spinning svg { animation: spin 0.8s linear infinite; }
  @keyframes spin { 100% { transform: rotate(360deg); } }

  /* ─────────────────────────────────────────────────────────────
     DASHBOARD GRID CONTAINER
     ───────────────────────────────────────────────────────────── */
  .container {
    max-width: 1720px;
    margin: 0 auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  /* ─────────────────────────────────────────────────────────────
     TOP KPI CARDS (GLOWING METRICS)
     ───────────────────────────────────────────────────────────── */
  .kpi-grid {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 16px;
  }

  .kpi-card {
    background: var(--bg-card);
    backdrop-filter: blur(16px);
    border: 1px solid var(--border-subtle);
    border-radius: 14px;
    padding: 18px 20px;
    position: relative;
    overflow: hidden;
    transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    box-shadow: 0 8px 24px -6px rgba(0, 0, 0, 0.4);
  }
  .kpi-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent);
    opacity: 0;
    transition: opacity 0.3s ease;
  }
  .kpi-card:hover {
    transform: translateY(-3px);
    border-color: rgba(255, 255, 255, 0.16);
    box-shadow: 0 16px 32px -8px rgba(0, 0, 0, 0.6);
  }
  .kpi-card:hover::before { opacity: 1; }

  .kpi-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }
  .kpi-title {
    font-family: var(--font-heading);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-muted);
  }
  .kpi-icon-wrap {
    width: 26px; height: 26px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(255, 255, 255, 0.04);
    font-size: 12px;
  }

  .kpi-value {
    font-family: var(--font-mono);
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.5px;
    color: var(--text-pure);
    margin-bottom: 4px;
    font-variant-numeric: tabular-nums;
  }
  .kpi-footer {
    font-size: 11px;
    color: var(--text-muted);
    display: flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-mono);
  }

  .val-positive { color: var(--neon-cyan) !important; text-shadow: 0 0 18px rgba(0, 245, 196, 0.3); }
  .val-negative { color: var(--neon-rose) !important; text-shadow: 0 0 18px rgba(255, 67, 101, 0.3); }
  .val-neutral { color: var(--neon-blue) !important; }
  .val-gold { color: var(--neon-gold) !important; }

  /* ─────────────────────────────────────────────────────────────
     MAIN ANALYTICS SECTION (CHART + RISK CONTROL)
     ───────────────────────────────────────────────────────────── */
  .analytics-row {
    display: grid;
    grid-template-columns: 2.4fr 1fr;
    gap: 20px;
  }

  .panel-card {
    background: var(--bg-card);
    backdrop-filter: blur(16px);
    border: 1px solid var(--border-subtle);
    border-radius: 16px;
    padding: 22px;
    position: relative;
    box-shadow: 0 10px 30px -5px rgba(0, 0, 0, 0.5);
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 18px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border-subtle);
  }
  .panel-title {
    font-family: var(--font-heading);
    font-size: 14px;
    font-weight: 700;
    color: #fff;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .chart-pills { display: flex; gap: 6px; }
  .pill-btn {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--border-subtle);
    color: var(--text-muted);
    padding: 4px 10px;
    border-radius: 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  .pill-btn.active, .pill-btn:hover {
    background: rgba(0, 245, 196, 0.15);
    color: var(--neon-cyan);
    border-color: rgba(0, 245, 196, 0.4);
  }

  .chart-wrapper {
    position: relative;
    height: 310px;
    width: 100%;
  }

  /* ─────────────────────────────────────────────────────────────
     RISK & CIRCUIT BREAKER MONITOR
     ───────────────────────────────────────────────────────────── */
  .risk-grid {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .risk-block {
    background: var(--bg-inner);
    border: 1px solid rgba(255, 255, 255, 0.04);
    border-radius: 10px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .risk-header-row {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    font-family: var(--font-heading);
    font-weight: 600;
  }
  .risk-name { color: var(--text-secondary); }
  .risk-val { font-family: var(--font-mono); color: #fff; }

  .meter-track {
    width: 100%;
    height: 6px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 4px;
    overflow: hidden;
    position: relative;
  }
  .meter-bar {
    height: 100%;
    border-radius: 4px;
    transition: width 0.6s cubic-bezier(0.16, 1, 0.3, 1), background-color 0.4s ease;
  }

  .badge-cluster {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-top: 6px;
  }
  .sub-chip {
    background: var(--bg-inner);
    border: 1px solid rgba(255, 255, 255, 0.04);
    padding: 10px;
    border-radius: 8px;
    text-align: center;
  }
  .sub-chip-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px; }
  .sub-chip-value { font-family: var(--font-mono); font-size: 13px; font-weight: 700; }

  /* ─────────────────────────────────────────────────────────────
     TABLES (POSITIONS & TRADES)
     ───────────────────────────────────────────────────────────── */
  .table-section {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .tab-nav {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .tab-buttons { display: flex; gap: 8px; }
  .tab-btn {
    background: transparent;
    border: 1px solid var(--border-subtle);
    color: var(--text-muted);
    padding: 8px 16px;
    border-radius: 8px;
    font-family: var(--font-heading);
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  .tab-btn.active {
    background: rgba(59, 130, 246, 0.15);
    color: #fff;
    border-color: rgba(59, 130, 246, 0.5);
  }

  .search-input {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 6px 14px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: #fff;
    outline: none;
    transition: border-color 0.2s ease;
    width: 220px;
  }
  .search-input:focus { border-color: var(--neon-cyan); }

  .table-box {
    overflow-x: auto;
    border-radius: 12px;
    border: 1px solid var(--border-subtle);
    background: var(--bg-card);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  thead th {
    background: rgba(14, 21, 38, 0.95);
    text-align: left;
    padding: 12px 16px;
    font-family: var(--font-heading);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border-subtle);
    white-space: nowrap;
  }
  tbody td {
    padding: 12px 16px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text-secondary);
    white-space: nowrap;
    transition: background 0.2s ease;
  }
  tbody tr:hover td {
    background: rgba(255, 255, 255, 0.03);
    color: #fff;
  }

  .dir-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }
  .dir-buy {
    background: rgba(0, 245, 196, 0.12);
    color: var(--neon-cyan);
    border: 1px solid rgba(0, 245, 196, 0.3);
  }
  .dir-sell {
    background: rgba(255, 67, 101, 0.12);
    color: var(--neon-rose);
    border: 1px solid rgba(255, 67, 101, 0.3);
  }

  .empty-banner {
    text-align: center;
    padding: 40px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 12px;
  }

  /* ─────────────────────────────────────────────────────────────
     FOOTER
     ───────────────────────────────────────────────────────────── */
  .footer {
    margin-top: 10px;
    padding: 16px;
    text-align: center;
    font-size: 11px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    border-top: 1px solid var(--border-subtle);
  }

  /* Responsive */
  @media (max-width: 1400px) {
    .kpi-grid { grid-template-columns: repeat(3, 1fr); }
    .analytics-row { grid-template-columns: 1fr; }
  }
  @media (max-width: 768px) {
    .kpi-grid { grid-template-columns: 1fr 1fr; }
    .main-header { flex-direction: column; gap: 12px; align-items: flex-start; }
    .top-strip { display: none; }
  }
</style>
</head>
<body>

<!-- TOP STRIP -->
<div class="top-strip">
  <div class="ticker-wrap">
    <div class="ticker-item"><span>ASSET:</span> <span class="ticker-val" id="top-symbol">XAU/USD (Gold Spot)</span></div>
    <div class="ticker-item"><span>MARKET:</span> <span class="ticker-val">METATRADER 5</span></div>
    <div class="ticker-item"><span>SESSION:</span> <span class="session-badge" id="session-tag">LONDON / NY</span></div>
    <div class="ticker-item"><span>SPREAD:</span> <span class="ticker-val" id="top-spread">~25 pts (0.25)</span></div>
  </div>
  <div class="ticker-wrap">
    <div class="ticker-item"><span>AUTO-SYNC:</span> <span class="ticker-val" id="countdown-strip">10s</span></div>
    <div class="ticker-item"><span>UTC:</span> <span class="ticker-val" id="clock-utc">00:00:00</span></div>
  </div>
</div>

<!-- MAIN HEADER -->
<header class="main-header">
  <div class="brand-group">
    <div class="brand-icon">⚡</div>
    <div>
      <div class="brand-title">Trading Command Center</div>
      <div class="brand-sub">
        <span>XAU/USD Algo Engine v2.0</span>
        <span>•</span>
        <span id="header-mode-tag">MODE: DEMO</span>
      </div>
    </div>
  </div>

  <div class="header-actions">
    <div id="bot-badge" class="status-pill status-stopped">
      <div class="radar-point stopped" id="radar-dot"></div>
      <span id="badge-text">DISCONNECTED</span>
    </div>

    <button class="btn-action" id="btn-refresh" onclick="triggerManualRefresh()">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
      <span>REFRESH</span>
    </button>
  </div>
</header>

<div class="container">

  <!-- ROW 1: 6 HIGH-IMPACT KPI METRIC CARDS -->
  <section class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Net Realized P&L</span>
        <div class="kpi-icon-wrap">💰</div>
      </div>
      <div class="kpi-value" id="kpi-net-pnl">$0.00</div>
      <div class="kpi-footer" id="kpi-net-pnl-sub">All-time realized</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Win Rate</span>
        <div class="kpi-icon-wrap">🎯</div>
      </div>
      <div class="kpi-value val-positive" id="kpi-win-rate">0.0%</div>
      <div class="kpi-footer" id="kpi-win-rate-sub">0 Wins / 0 Losses</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Profit Factor</span>
        <div class="kpi-icon-wrap">⚖️</div>
      </div>
      <div class="kpi-value val-gold" id="kpi-pf">0.00</div>
      <div class="kpi-footer">Gross Gain / Loss</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Max Drawdown</span>
        <div class="kpi-icon-wrap">🛡️</div>
      </div>
      <div class="kpi-value val-negative" id="kpi-dd">$0.00</div>
      <div class="kpi-footer">Peak-to-Trough</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Today's Performance</span>
        <div class="kpi-icon-wrap">📅</div>
      </div>
      <div class="kpi-value val-neutral" id="kpi-today-pnl">$0.00</div>
      <div class="kpi-footer" id="kpi-today-count">0 executions today</div>
    </div>

    <div class="kpi-card">
      <div class="kpi-header">
        <span class="kpi-title">Expected Payoff</span>
        <div class="kpi-icon-wrap">📈</div>
      </div>
      <div class="kpi-value" id="kpi-expectancy">$0.00</div>
      <div class="kpi-footer">Expected / Trade</div>
    </div>
  </section>

  <!-- ROW 2: EQUITY CURVE + RISK GAUGES -->
  <section class="analytics-row">
    <!-- Chart Card -->
    <div class="panel-card">
      <div class="panel-header">
        <div class="panel-title">
          <span>📈 Equity Curve & Portfolio Trajectory</span>
        </div>
        <div class="chart-pills">
          <button class="pill-btn active" onclick="setChartFilter('all', this)">ALL TIME</button>
          <button class="pill-btn" onclick="setChartFilter('30', this)">LAST 30</button>
          <button class="pill-btn" onclick="setChartFilter('10', this)">LAST 10</button>
        </div>
      </div>
      <div class="chart-wrapper">
        <canvas id="equityChartCanvas"></canvas>
      </div>
    </div>

    <!-- Risk & Guardrails Card -->
    <div class="panel-card">
      <div class="panel-header">
        <div class="panel-title">
          <span>🛡️ Risk & Safety Monitor</span>
        </div>
        <span class="session-badge" id="risk-status-pill">ACTIVE</span>
      </div>

      <div class="risk-grid">
        <!-- Daily Loss Meter -->
        <div class="risk-block">
          <div class="risk-header-row">
            <span class="risk-name">Daily Loss Used</span>
            <span class="risk-val" id="risk-daily-text">$0.00 / $50.00</span>
          </div>
          <div class="meter-track">
            <div class="meter-bar" id="risk-daily-bar" style="width: 0%; background: var(--neon-cyan);"></div>
          </div>
        </div>

        <!-- Losing Streak Breaker -->
        <div class="risk-block">
          <div class="risk-header-row">
            <span class="risk-name">Consecutive Loss Circuit</span>
            <span class="risk-val" id="risk-streak-text">0 / 5 Max</span>
          </div>
          <div class="meter-track">
            <div class="meter-bar" id="risk-streak-bar" style="width: 0%; background: var(--neon-cyan);"></div>
          </div>
        </div>

        <!-- Metric Cluster -->
        <div class="badge-cluster">
          <div class="sub-chip">
            <div class="sub-chip-label">Open Positions</div>
            <div class="sub-chip-value val-neutral" id="risk-open-count">0 / 2</div>
          </div>
          <div class="sub-chip">
            <div class="sub-chip-label">Floating P&L</div>
            <div class="sub-chip-value" id="risk-floating-val">$0.00</div>
          </div>
          <div class="sub-chip">
            <div class="sub-chip-label">Anti-Martingale</div>
            <div class="sub-chip-value val-positive" id="anti-martingale-val">100% SIZE</div>
          </div>
          <div class="sub-chip">
            <div class="sub-chip-label">Bot Uptime</div>
            <div class="sub-chip-value" id="bot-uptime-val">00:00:00</div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- ROW 3: TABBED POSITIONS & TRADES TABLE -->
  <section class="table-section">
    <div class="tab-nav">
      <div class="tab-buttons">
        <button class="tab-btn active" id="tab-btn-positions" onclick="switchTableTab('positions')">Open Positions (<span id="open-tab-count">0</span>)</button>
        <button class="tab-btn" id="tab-btn-trades" onclick="switchTableTab('trades')">Trade Execution Log</button>
      </div>
      <input type="text" id="table-search" class="search-input" placeholder="Search ticket, direction..." onkeyup="filterTable()">
    </div>

    <!-- Active Positions Table -->
    <div class="table-box" id="positions-table-box">
      <table>
        <thead>
          <tr>
            <th>Ticket</th>
            <th>Symbol</th>
            <th>Direction</th>
            <th>Volume</th>
            <th>Entry Price</th>
            <th>Current Price</th>
            <th>Stop Loss</th>
            <th>Take Profit</th>
            <th>Floating P&L</th>
            <th>R-Multiple</th>
            <th>Duration</th>
          </tr>
        </thead>
        <tbody id="positions-tbody">
          <tr><td colspan="11" class="empty-banner">No open positions currently active</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Closed Trades History Table -->
    <div class="table-box" id="trades-table-box" style="display: none;">
      <table>
        <thead>
          <tr>
            <th>Closed At</th>
            <th>Ticket</th>
            <th>Symbol</th>
            <th>Direction</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>Volume</th>
            <th>Net P&L</th>
            <th>Strategy Reason</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="trades-tbody">
          <tr><td colspan="10" class="empty-banner">No closed trade records found</td></tr>
        </tbody>
      </table>
    </div>
  </section>

</div>

<footer class="footer">
  TRADING COMMAND CENTER v2.0 • MetaTrader 5 Bridge • Connected Database: <code>data/trade_journal.db</code>
</footer>

<script>
  let rawEquityData = [];
  let equityChart = null;
  let chartFilterMode = 'all';
  let activeTab = 'positions';
  let countdown = 10;

  // ── UTC Clock ──
  function tickClock() {
    const d = new Date();
    document.getElementById('clock-utc').textContent = d.toUTCString().slice(17, 25);
  }
  setInterval(tickClock, 1000);
  tickClock();

  // ── Countdown Loop ──
  setInterval(() => {
    countdown--;
    if (countdown <= 0) {
      countdown = 10;
      refreshAll();
    }
    document.getElementById('countdown-strip').textContent = countdown + 's';
  }, 1000);

  // ── Helpers ──
  function fmt$(val) {
    const n = parseFloat(val) || 0;
    const sign = n >= 0 ? '+' : '';
    return sign + '$' + n.toFixed(2);
  }

  function getPnlClass(val) {
    const n = parseFloat(val) || 0;
    return n > 0 ? 'val-positive' : (n < 0 ? 'val-negative' : '');
  }

  // ── Tab Switcher ──
  function switchTableTab(tab) {
    activeTab = tab;
    document.getElementById('tab-btn-positions').classList.toggle('active', tab === 'positions');
    document.getElementById('tab-btn-trades').classList.toggle('active', tab === 'trades');
    document.getElementById('positions-table-box').style.display = tab === 'positions' ? 'block' : 'none';
    document.getElementById('trades-table-box').style.display = tab === 'trades' ? 'block' : 'none';
  }

  // ── Table Search Filter ──
  function filterTable() {
    const q = document.getElementById('table-search').value.toLowerCase();
    const activeTbodyId = activeTab === 'positions' ? 'positions-tbody' : 'trades-tbody';
    const rows = document.getElementById(activeTbodyId).getElementsByTagName('tr');
    for (let r of rows) {
      const text = r.textContent.toLowerCase();
      r.style.display = text.includes(q) ? '' : 'none';
    }
  }

  // ── Fetch JSON Helper ──
  async function fetchJSON(url) {
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      return null;
    }
  }

  // ── Update Status & Top Badges ──
  async function updateStatus() {
    const st = await fetchJSON('/api/status');
    if (!st) return;

    const isRunning = st.running === true;
    const badge = document.getElementById('bot-badge');
    const radar = document.getElementById('radar-dot');
    const badgeText = document.getElementById('badge-text');

    if (isRunning) {
      badge.className = 'status-pill status-live';
      radar.className = 'radar-point live';
      badgeText.textContent = 'ALGO ENGINE LIVE';
    } else {
      badge.className = 'status-pill status-stopped';
      radar.className = 'radar-point stopped';
      badgeText.textContent = 'STANDBY / IDLE';
    }

    document.getElementById('header-mode-tag').textContent = 'MODE: ' + (st.mode || 'DEMO').toUpperCase();
    document.getElementById('top-symbol').textContent = st.symbol ? st.symbol + ' (Gold Spot)' : 'XAUUSDm (Gold Spot)';
    document.getElementById('bot-uptime-val').textContent = st.uptime || '00:00:00';
  }

  // ── Update Performance & KPIs ──
  async function updatePerformance() {
    const p = await fetchJSON('/api/performance');
    if (!p) return;

    const pnlEl = document.getElementById('kpi-net-pnl');
    pnlEl.textContent = fmt$(p.net_pnl);
    pnlEl.className = 'kpi-value ' + getPnlClass(p.net_pnl);
    document.getElementById('kpi-net-pnl-sub').textContent = p.total_trades + ' Total Trades';

    document.getElementById('kpi-win-rate').textContent = p.win_rate.toFixed(1) + '%';
    document.getElementById('kpi-win-rate-sub').textContent = `${p.wins} Wins / ${p.losses} Losses`;

    document.getElementById('kpi-pf').textContent = p.profit_factor;
    document.getElementById('kpi-dd').textContent = '$' + p.max_drawdown.toFixed(2);

    const expEl = document.getElementById('kpi-expectancy');
    expEl.textContent = fmt$(p.expectancy);
    expEl.className = 'kpi-value ' + getPnlClass(p.expectancy);
  }

  // ── Update Risk Monitor ──
  async function updateRisk() {
    const r = await fetchJSON('/api/risk_status');
    if (!r) return;

    document.getElementById('kpi-today-pnl').textContent = fmt$(r.daily_pnl);
    document.getElementById('kpi-today-pnl').className = 'kpi-value ' + getPnlClass(r.daily_pnl);
    document.getElementById('kpi-today-count').textContent = r.trades_today + ' closed today';

    document.getElementById('risk-daily-text').textContent = `$${r.daily_loss.toFixed(2)} / $${r.max_daily_loss.toFixed(2)}`;
    const lossPct = Math.min(100, (r.daily_loss / r.max_daily_loss) * 100);
    const dailyBar = document.getElementById('risk-daily-bar');
    dailyBar.style.width = lossPct + '%';
    dailyBar.style.background = lossPct > 70 ? 'var(--neon-rose)' : (lossPct > 40 ? 'var(--neon-gold)' : 'var(--neon-cyan)');

    document.getElementById('risk-streak-text').textContent = `${r.losing_streak} / ${r.max_streak_limit} Streak`;
    const streakPct = Math.min(100, (r.losing_streak / r.max_streak_limit) * 100);
    const streakBar = document.getElementById('risk-streak-bar');
    streakBar.style.width = streakPct + '%';
    streakBar.style.background = streakPct >= 60 ? 'var(--neon-rose)' : 'var(--neon-cyan)';

    document.getElementById('risk-open-count').textContent = `${r.positions_open} / ${r.max_positions}`;
    document.getElementById('open-tab-count').textContent = r.positions_open;

    const floatEl = document.getElementById('risk-floating-val');
    floatEl.textContent = fmt$(r.floating_pnl);
    floatEl.className = 'sub-chip-value ' + getPnlClass(r.floating_pnl);

    const multi = r.anti_martingale_multiplier || 1.0;
    const multiEl = document.getElementById('anti-martingale-val');
    multiEl.textContent = `${Math.round(multi * 100)}% SIZE`;
    multiEl.className = 'sub-chip-value ' + (multi < 1.0 ? 'val-gold' : 'val-positive');
  }

  // ── Update Active Positions ──
  async function updatePositions() {
    const posList = await fetchJSON('/api/positions');
    const tbody = document.getElementById('positions-tbody');
    if (!posList || posList.length === 0) {
      tbody.innerHTML = '<tr><td colspan="11" class="empty-banner">No open positions currently active</td></tr>';
      return;
    }

    tbody.innerHTML = posList.map(p => {
      const isBuy = (p.direction || '').toUpperCase().includes('BUY') || (p.direction || '').toUpperCase() === 'LONG';
      const badgeCls = isBuy ? 'dir-buy' : 'dir-sell';
      const badgeLabel = isBuy ? '▲ BUY' : '▼ SELL';
      const pnlClass = getPnlClass(p.pnl);

      return `<tr>
        <td><strong>#${p.ticket}</strong></td>
        <td>${p.symbol}</td>
        <td><span class="dir-badge ${badgeCls}">${badgeLabel}</span></td>
        <td>${p.volume}</td>
        <td>${p.entry_price}</td>
        <td>${p.current_price}</td>
        <td>${p.sl != null ? p.sl : '—'}</td>
        <td>${p.tp != null ? p.tp : '—'}</td>
        <td class="${pnlClass}">${fmt$(p.pnl)}</td>
        <td class="${pnlClass}">${p.r_multiple != null ? p.r_multiple.toFixed(2) + 'R' : '—'}</td>
        <td>${p.duration || '—'}</td>
      </tr>`;
    }).join('');
  }

  // ── Update Closed Trades History ──
  async function updateTrades() {
    const trades = await fetchJSON('/api/trades');
    const tbody = document.getElementById('trades-tbody');
    if (!trades || trades.length === 0) {
      tbody.innerHTML = '<tr><td colspan="10" class="empty-banner">No closed trade records found</td></tr>';
      return;
    }

    tbody.innerHTML = trades.slice(0, 50).map(t => {
      const isBuy = (t.direction || '').toUpperCase().includes('BUY') || (t.direction || '').toUpperCase() === 'LONG';
      const badgeCls = isBuy ? 'dir-buy' : 'dir-sell';
      const badgeLabel = isBuy ? '▲ BUY' : '▼ SELL';
      const netPnl = (t.profit || 0) + (t.commission || 0) + (t.swap || 0);
      const pnlClass = getPnlClass(netPnl);
      const closeTime = t.close_time ? t.close_time.replace('T', ' ').slice(0, 19) : '—';
      const reason = t.signal_reason || 'Confluence';

      return `<tr>
        <td>${closeTime}</td>
        <td>#${t.ticket || t.id}</td>
        <td>${t.symbol}</td>
        <td><span class="dir-badge ${badgeCls}">${badgeLabel}</span></td>
        <td>${t.open_price}</td>
        <td>${t.close_price != null ? t.close_price : '—'}</td>
        <td>${t.volume}</td>
        <td class="${pnlClass}"><strong>${fmt$(netPnl)}</strong></td>
        <td><span style="font-size:11px;color:var(--text-muted);">${reason.slice(0, 32)}</span></td>
        <td><span style="color:var(--neon-cyan);font-weight:600;">${t.status || 'CLOSED'}</span></td>
      </tr>`;
    }).join('');
  }

  // ── Filter Equity Chart Time Range ──
  function setChartFilter(mode, btn) {
    chartFilterMode = mode;
    document.querySelectorAll('.chart-pills .pill-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    renderEquityChart();
  }

  // ── Render Chart.js Glowing Curve ──
  function renderEquityChart() {
    if (!rawEquityData || rawEquityData.length === 0) return;

    let dataset = [...rawEquityData];
    if (chartFilterMode === '10') dataset = dataset.slice(-10);
    else if (chartFilterMode === '30') dataset = dataset.slice(-30);

    const labels = dataset.map((d, idx) => {
      if (!d.time) return `#${idx + 1}`;
      const t = d.time.replace('T', ' ');
      return t.length > 16 ? t.slice(5, 16) : t;
    });
    const values = dataset.map(d => d.equity);
    const lastVal = values[values.length - 1] || 0;
    const isPositive = lastVal >= 0;

    const ctx = document.getElementById('equityChartCanvas').getContext('2d');

    // Create subtle animated gradient fill
    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    if (isPositive) {
      gradient.addColorStop(0, 'rgba(0, 245, 196, 0.28)');
      gradient.addColorStop(0.7, 'rgba(0, 245, 196, 0.05)');
      gradient.addColorStop(1, 'rgba(0, 245, 196, 0.0)');
    } else {
      gradient.addColorStop(0, 'rgba(255, 67, 101, 0.28)');
      gradient.addColorStop(0.7, 'rgba(255, 67, 101, 0.05)');
      gradient.addColorStop(1, 'rgba(255, 67, 101, 0.0)');
    }

    const lineColor = isPositive ? '#00f5c4' : '#ff4365';

    if (equityChart) {
      equityChart.data.labels = labels;
      equityChart.data.datasets[0].data = values;
      equityChart.data.datasets[0].borderColor = lineColor;
      equityChart.data.datasets[0].backgroundColor = gradient;
      equityChart.update('none');
      return;
    }

    equityChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Cumulative Equity',
          data: values,
          borderColor: lineColor,
          backgroundColor: gradient,
          borderWidth: 2.5,
          fill: true,
          tension: 0.35,
          pointRadius: values.length > 40 ? 0 : 3,
          pointHoverRadius: 6,
          pointBackgroundColor: lineColor,
          pointBorderColor: '#060913',
          pointBorderWidth: 2,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(14, 21, 38, 0.95)',
            titleColor: '#f1f5f9',
            bodyColor: '#a5f3fc',
            borderColor: 'rgba(255, 255, 255, 0.1)',
            borderWidth: 1,
            padding: 12,
            boxPadding: 4,
            cornerRadius: 8,
            titleFont: { family: "'Plus Jakarta Sans', sans-serif", size: 12, weight: 'bold' },
            bodyFont: { family: "'JetBrains Mono', monospace", size: 12 },
            callbacks: {
              label: (ctx) => ' Equity: ' + (ctx.parsed.y >= 0 ? '+' : '') + '$' + ctx.parsed.y.toFixed(2)
            }
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.03)', drawBorder: false },
            ticks: { color: '#64748b', font: { family: "'JetBrains Mono', monospace", size: 10 }, maxTicksLimit: 8 }
          },
          y: {
            grid: { color: 'rgba(255, 255, 255, 0.04)', drawBorder: false },
            ticks: {
              color: '#64748b',
              font: { family: "'JetBrains Mono', monospace", size: 10 },
              callback: (v) => (v >= 0 ? '+' : '') + '$' + v.toFixed(0)
            }
          }
        }
      }
    });
  }

  async function updateEquity() {
    const data = await fetchJSON('/api/equity_curve');
    if (data) {
      rawEquityData = data;
      renderEquityChart();
    }
  }

  // ── Manual Refresh ──
  async function triggerManualRefresh() {
    const btn = document.getElementById('btn-refresh');
    btn.classList.add('spinning');
    await refreshAll();
    setTimeout(() => btn.classList.remove('spinning'), 500);
  }

  // ── Master Refresh ──
  async function refreshAll() {
    countdown = 10;
    await Promise.all([
      updateStatus(),
      updatePerformance(),
      updateRisk(),
      updatePositions(),
      updateTrades(),
      updateEquity()
    ]);
  }

  // Initial Boot
  refreshAll();
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
    """Start the dashboard in a background daemon thread."""
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
    print("=" * 55)
    print("  TRADING COMMAND CENTER — HIGH-SKILL DASHBOARD")
    print(f"  Live UI: http://localhost:5555")
    print("=" * 55)
    update_bot_status(running=True, mode="STANDALONE", symbol="XAUUSDm")
    app.run(host="0.0.0.0", port=5555, debug=True)
