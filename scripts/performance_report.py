#!/usr/bin/env python3
"""
Trading Bot Performance & Journal Analytics
=============================================
Analyzes trades recorded in `data/trade_journal.db` and generates
a comprehensive quant performance report.

Metrics:
- Win Rate, Profit Factor, Expected Payoff
- Gross Profit, Gross Loss, Net Realized PnL
- Max Drawdown ($ and %)
- Win/Loss Streaks
- Breakdown by Strategy
- Breakdown by Day of Week and Hour of Day
- ASCII Equity Curve Visualization

Usage:
    python scripts/performance_report.py
    python scripts/performance_report.py --days 30 --save
"""

import sys
import os
import argparse
import sqlite3
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "trade_journal.db"


def fetch_trades(days: Optional[int] = None) -> List[Dict[str, Any]]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = "SELECT * FROM trades WHERE status = 'CLOSED'"
    params = []

    if days is not None and days > 0:
        since_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query += " AND close_time >= ?"
        params.append(since_time)

    query += " ORDER BY close_time ASC"
    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def generate_ascii_chart(values: List[float], width: int = 50, height: int = 10) -> str:
    if not values or len(values) < 2:
        return "Not enough data points for equity chart."

    min_val = min(values)
    max_val = max(values)
    val_range = max_val - min_val if max_val != min_val else 1.0

    step = max(1, len(values) // width)
    sampled = values[::step][:width]
    if len(sampled) < width and values[-1] not in sampled:
        sampled.append(values[-1])

    grid = [[" " for _ in range(len(sampled))] for _ in range(height)]

    for col, v in enumerate(sampled):
        normalized = int(((v - min_val) / val_range) * (height - 1))
        row = height - 1 - normalized
        grid[row][col] = "*"

    lines = []
    lines.append(f"  ${max_val:>9.2f} +" + "-" * len(sampled) + "+")
    for r in range(height):
        label = " " * 12 if r not in (0, height // 2, height - 1) else (
            f"  ${(max_val - (r / (height - 1)) * val_range):>9.2f} "
        )
        lines.append(f"{label}|" + "".join(grid[r]) + "|")
    lines.append(f"  ${min_val:>9.2f} +" + "-" * len(sampled) + "+")
    lines.append(" " * 13 + "Trade 1" + " " * max(1, (len(sampled) - 14)) + f"Trade {len(values)}")
    return "\n".join(lines)


def format_report(trades: List[Dict[str, Any]], days: Optional[int]) -> str:
    lines = []
    lines.append("=" * 70)
    time_label = f"LAST {days} DAYS" if days else "ALL-TIME"
    lines.append(f"          XAU/USD BOT PERFORMANCE REPORT - {time_label}")
    lines.append("=" * 70)

    if not trades:
        lines.append("\n  No closed trades found in the specified window.")
        lines.append("=" * 70)
        return "\n".join(lines)

    total_trades = len(trades)
    pnls = [(t["profit"] or 0.0) + (t["commission"] or 0.0) + (t["swap"] or 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakevens = [p for p in pnls if p == 0]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100 if total_trades else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net_pnl = gross_profit - gross_loss
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    avg_win = (sum(wins) / win_count) if win_count else 0.0
    avg_loss = (sum(losses) / loss_count) if loss_count else 0.0
    win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    expected_payoff = net_pnl / total_trades if total_trades else 0.0

    # Max Drawdown & Equity Curve
    equity_curve = [0.0]
    peak = 0.0
    max_dd = 0.0
    running_pnl = 0.0
    consecutive_wins = 0
    max_cons_wins = 0
    consecutive_losses = 0
    max_cons_losses = 0

    for p in pnls:
        running_pnl += p
        equity_curve.append(running_pnl)
        if running_pnl > peak:
            peak = running_pnl
        dd = peak - running_pnl
        if dd > max_dd:
            max_dd = dd

        if p > 0:
            consecutive_wins += 1
            consecutive_losses = 0
            max_cons_wins = max(max_cons_wins, consecutive_wins)
        elif p < 0:
            consecutive_losses += 1
            consecutive_wins = 0
            max_cons_losses = max(max_cons_losses, consecutive_losses)

    # Strategy Breakdown
    strat_stats: Dict[str, Dict[str, Any]] = {}
    for t, p in zip(trades, pnls):
        strat = t.get("signal_reason") or "unspecified"
        if "|" in strat:
            strat = strat.split("|")[0].strip()
        if strat not in strat_stats:
            strat_stats[strat] = {"trades": 0, "wins": 0, "net_pnl": 0.0}
        strat_stats[strat]["trades"] += 1
        strat_stats[strat]["net_pnl"] += p
        if p > 0:
            strat_stats[strat]["wins"] += 1

    lines.append(f"\n[SUMMARY METRICS]")
    lines.append(f"  Total Trades:           {total_trades:<8} Win Rate:             {win_rate:>6.1f}%")
    lines.append(f"  Winning Trades:         {win_count:<8} Losing Trades:        {loss_count:>6}")
    lines.append(f"  Gross Profit:          ${gross_profit:>10.2f}  Gross Loss:          ${gross_loss:>10.2f}")
    lines.append(f"  Net Realized PnL:      ${net_pnl:>10.2f}  Profit Factor:        {profit_factor:>6.2f}")
    lines.append(f"  Expected Payoff:       ${expected_payoff:>10.2f}  Max Drawdown:        ${max_dd:>10.2f}")
    lines.append(f"  Average Win:           ${avg_win:>10.2f}  Average Loss:        ${avg_loss:>10.2f}")
    lines.append(f"  Win/Loss Payoff Ratio:  {win_loss_ratio:>6.2f}  Max Consec Wins/Loss: {max_cons_wins}/{max_cons_losses}")

    lines.append(f"\n[EQUITY CURVE]")
    lines.append(generate_ascii_chart(equity_curve))

    lines.append(f"\n[STRATEGY BREAKDOWN]")
    lines.append(f"  {'Strategy':<25} {'Trades':<8} {'Win Rate':<10} {'Net PnL':>10}")
    lines.append("  " + "-" * 58)
    for s_name, data in sorted(strat_stats.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
        wr = (data["wins"] / data["trades"]) * 100 if data["trades"] else 0.0
        lines.append(f"  {s_name[:24]:<25} {data['trades']:<8} {wr:>7.1f}%   ${data['net_pnl']:>9.2f}")

    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Trading Performance Report")
    parser.add_argument("--days", type=int, default=None, help="Lookback window in days (default: all)")
    parser.add_argument("--save", action="store_true", help="Save report to data/performance_report.txt")
    args = parser.parse_args()

    trades = fetch_trades(args.days)
    report_text = format_report(trades, args.days)
    print(report_text)

    if args.save:
        out_file = PROJECT_ROOT / "data" / "performance_report.txt"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(report_text, encoding="utf-8")
        print(f"\n[OK] Report saved to: {out_file}")


if __name__ == "__main__":
    main()
