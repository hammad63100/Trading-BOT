"""
Backtesting Framework
======================
Historical strategy validation with realistic cost modeling.

Key principles (from Blueprint Section 11):
- Model realistic spread, commission, and slippage
- Walk-forward (rolling) windows, never random split
- Meaningful sample size across multiple market regimes
- Compare against naive baseline
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from core.indicators import compute_indicators
from core.signals import generate_signal, Signal

log = logging.getLogger("gold_bot.backtest")


@dataclass
class BacktestTrade:
    """Record of a single simulated trade."""
    entry_time: datetime
    exit_time: Optional[datetime] = None
    direction: str = "LONG"
    entry_price: float = 0.0
    exit_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    lots: float = 0.01
    pnl: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    exit_reason: str = ""   # 'SL', 'TP', 'signal_reversal', 'end_of_data'
    confidence: float = 0.0


@dataclass
class BacktestResult:
    """Aggregated backtest performance metrics."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_rr: float = 0.0       # Average risk-reward realized
    max_consecutive_losses: int = 0
    total_commission: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


def run_backtest(
    df: pd.DataFrame,
    initial_equity: float = 50.0,
    risk_per_trade: float = 0.003,
    atr_sl_mult: float = 1.5,
    atr_tp_mult: float = 3.0,
    spread_points: float = 0.30,
    commission_per_lot: float = 7.0,
    slippage_points: float = 0.10,
    min_confidence: float = 0.65,
    contract_size: float = 100.0,
    volume_min: float = 0.01,
    volume_step: float = 0.01,
    volume_max: float = 100.0,
) -> BacktestResult:
    """Research simulation on BID OHLC bars, with next-bar entries.

    Costs named points are PRICE units. contract_size is account-currency value
    per price unit per lot; supply currency conversion for non-USD accounts.
    This is a signal baseline, not a replica of live MTF/trailing/margin gates.
    """
    import math
    from core.risk_manager import floor_volume
    positive = (initial_equity, risk_per_trade, atr_sl_mult, atr_tp_mult,
                contract_size, volume_min, volume_step, volume_max)
    if not all(math.isfinite(x) and x > 0 for x in positive):
        raise ValueError("Backtest inputs must be finite and positive")
    if risk_per_trade > 0.05 or volume_max < volume_min:
        raise ValueError("Invalid risk or volume limits")
    if not all(math.isfinite(x) and x >= 0 for x in (spread_points, commission_per_lot, slippage_points)):
        raise ValueError("Costs must be finite and nonnegative")
    if df.empty or not {"time", "open", "high", "low", "close"}.issubset(df.columns):
        raise ValueError("OHLC history is required")
    df = df.sort_values("time").reset_index(drop=True)
    if df["time"].duplicated().any() or not np.isfinite(df[["open", "high", "low", "close"]].to_numpy()).all():
        raise ValueError("Duplicate timestamps or invalid prices")
    df = compute_indicators(df.copy())
    equity = peak_equity = initial_equity
    max_drawdown = 0.0
    trades, equity_curve = [], [initial_equity]
    current_position = None
    consecutive_losses = max_consecutive_losses = 0

    for i in range(201, len(df)):
        bar = df.iloc[i]
        # Only completed bars preceding the execution bar can inform the entry.
        if current_position is None and equity > 0:
            signal = generate_signal(df.iloc[max(0, i - 250):i])
            atr = df.iloc[i - 1]["atr14"]
            if signal.direction in ("LONG", "SHORT") and signal.confidence >= min_confidence and math.isfinite(atr) and atr > 0:
                distance = atr * atr_sl_mult
                multiplier = 1.0 if consecutive_losses == 0 else (0.5 if consecutive_losses == 1 else 0.25)
                loss_per_lot = (distance + slippage_points) * contract_size + commission_per_lot
                lots = floor_volume(equity * risk_per_trade * multiplier / loss_per_lot,
                                    volume_min, volume_max, volume_step)
                if lots > 0:
                    buy = signal.direction == "LONG"
                    entry = bar["open"] + spread_points + slippage_points if buy else bar["open"] - slippage_points
                    current_position = BacktestTrade(
                        entry_time=bar["time"], direction=signal.direction, entry_price=entry,
                        sl=entry - distance if buy else entry + distance,
                        tp=entry + atr * atr_tp_mult if buy else entry - atr * atr_tp_mult,
                        lots=lots, slippage=slippage_points, confidence=signal.confidence)
        if current_position is not None:
            p = current_position
            buy = p.direction == "LONG"
            quote_bar = bar.copy()
            if not buy:
                for col in ("open", "high", "low", "close"):
                    quote_bar[col] += spread_points
            hit_sl, hit_tp = _check_exit(p, quote_bar)
            # Opening gaps occur before intrabar extremes; otherwise SL wins ambiguity.
            gap_sl = quote_bar["open"] <= p.sl if buy else quote_bar["open"] >= p.sl
            gap_tp = quote_bar["open"] >= p.tp if buy else quote_bar["open"] <= p.tp
            if gap_sl or (hit_sl and not gap_tp):
                exit_price = min(p.sl, quote_bar["open"]) - slippage_points if buy else max(p.sl, quote_bar["open"]) + slippage_points
                reason = "SL"
            elif hit_tp:
                exit_price, reason = p.tp, "TP"
            elif i == len(df) - 1:
                exit_price = quote_bar["close"] - slippage_points if buy else quote_bar["close"] + slippage_points
                reason = "end_of_data"
            else:
                reason = None
            if reason:
                p.exit_time, p.exit_price, p.exit_reason = bar["time"], exit_price, reason
                p.commission = commission_per_lot * p.lots
                p.pnl = (exit_price - p.entry_price) * (1 if buy else -1) * p.lots * contract_size - p.commission
                equity += p.pnl
                trades.append(p)
                consecutive_losses = consecutive_losses + 1 if p.pnl < 0 else 0
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                current_position = None
        marked_equity = equity
        if current_position is not None:
            p = current_position
            mark = bar["close"] if p.direction == "LONG" else bar["close"] + spread_points
            marked_equity += (mark - p.entry_price) * (1 if p.direction == "LONG" else -1) * p.lots * contract_size - commission_per_lot * p.lots
        equity_curve.append(marked_equity)
        peak_equity = max(peak_equity, marked_equity)
        max_drawdown = max(max_drawdown, peak_equity - marked_equity)
    return _calculate_metrics(trades, initial_equity, equity, max_drawdown, equity_curve, max_consecutive_losses)


def _check_exit(position: BacktestTrade, bar) -> tuple[bool, bool]:
    """Check if a bar's high/low hit the SL or TP."""
    if position.direction == "LONG":
        hit_sl = bar["low"] <= position.sl
        hit_tp = bar["high"] >= position.tp
    else:
        hit_sl = bar["high"] >= position.sl
        hit_tp = bar["low"] <= position.tp
    return hit_sl, hit_tp


def _calculate_metrics(
    trades: list[BacktestTrade],
    initial_equity: float,
    final_equity: float,
    max_drawdown: float,
    equity_curve: list[float],
    max_consecutive_losses: int,
) -> BacktestResult:
    """Calculates aggregated performance metrics from trade list."""
    result = BacktestResult()
    result.trades = trades
    result.equity_curve = equity_curve
    result.max_consecutive_losses = max_consecutive_losses

    if not trades:
        return result

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    result.total_trades = len(trades)
    result.winning_trades = len(wins)
    result.losing_trades = len(losses)
    result.win_rate = len(wins) / len(trades) if trades else 0
    result.gross_profit = sum(wins)
    result.gross_loss = abs(sum(losses))
    result.net_pnl = final_equity - initial_equity
    result.profit_factor = result.gross_profit / result.gross_loss if result.gross_loss > 0 else float('inf')
    result.max_drawdown = max_drawdown
    peaks = np.maximum.accumulate(equity_curve)
    result.max_drawdown_pct = float(np.max((peaks - equity_curve) / peaks)) if len(peaks) else 0
    result.avg_win = np.mean(wins) if wins else 0
    result.avg_loss = np.mean(losses) if losses else 0
    result.avg_rr = abs(result.avg_win / result.avg_loss) if result.avg_loss != 0 else 0
    result.total_commission = sum(t.commission for t in trades)

    # No unsupported annualization: bar frequency and trading calendar vary.
    curve = np.asarray(equity_curve, dtype=float)
    returns = np.diff(curve) / np.maximum(curve[:-1], 1e-12)
    if len(returns) > 1 and np.std(returns, ddof=1) > 0:
        result.sharpe_ratio = float(np.mean(returns) / np.std(returns, ddof=1))

    return result


def print_backtest_report(result: BacktestResult) -> str:
    """Formats a human-readable backtest report."""
    report = f"""
+--------------------------------------------------------------+
|                    BACKTEST REPORT                           |
+--------------------------------------------------------------+
|  Total Trades:          {result.total_trades:<10}                   |
|  Winning Trades:        {result.winning_trades:<10}                   |
|  Losing Trades:         {result.losing_trades:<10}                   |
|  Win Rate:              {result.win_rate*100:>6.1f}%                     |
+--------------------------------------------------------------+
|  Gross Profit:          ${result.gross_profit:>10.2f}                |
|  Gross Loss:            ${result.gross_loss:>10.2f}                |
|  Net PnL:               ${result.net_pnl:>10.2f}                |
|  Total Commission:      ${result.total_commission:>10.2f}                |
+--------------------------------------------------------------+
|  Profit Factor:         {result.profit_factor:>10.2f}                |
|  Bar Sharpe (raw):          {result.sharpe_ratio:>10.2f}                |
|  Avg Win:               ${result.avg_win:>10.2f}                |
|  Avg Loss:              ${result.avg_loss:>10.2f}                |
|  Avg Risk:Reward:       {result.avg_rr:>10.2f}                |
+--------------------------------------------------------------+
|  Max Drawdown:          ${result.max_drawdown:>10.2f}                |
|  Max Drawdown %:        {result.max_drawdown_pct*100:>6.1f}%                     |
|  Max Consec. Losses:    {result.max_consecutive_losses:<10}                   |
+--------------------------------------------------------------+
"""
    report += "Signal baseline only; no live MTF, trailing, margin or daily-loss gates.\n"
    print(report)
    return report
