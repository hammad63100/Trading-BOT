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
    initial_equity: float = 10000.0,
    risk_per_trade: float = 0.005,
    atr_sl_mult: float = 1.5,
    atr_tp_mult: float = 3.0,
    spread_points: float = 3.0,      # Realistic gold spread in points
    commission_per_lot: float = 7.0, # Round-trip commission per lot
    slippage_points: float = 1.0,    # Average slippage per entry
    min_confidence: float = 0.6,
) -> BacktestResult:
    """
    Runs a vectorized backtest on historical OHLCV data.

    Args:
        df: Raw OHLCV DataFrame (will compute indicators internally).
        initial_equity: Starting equity for the simulation.
        risk_per_trade: Fraction of equity risked per trade.
        atr_sl_mult: ATR multiplier for stop loss.
        atr_tp_mult: ATR multiplier for take profit.
        spread_points: Simulated spread in price points.
        commission_per_lot: Round-trip commission per standard lot.
        slippage_points: Average slippage per trade entry.
        min_confidence: Minimum signal confidence to take a trade.

    Returns:
        BacktestResult with all performance metrics and trade list.
    """
    log.info("Starting backtest | Bars: %d | Equity: %.2f | Risk: %.1f%%",
             len(df), initial_equity, risk_per_trade * 100)

    # Compute indicators
    df = compute_indicators(df.copy())

    equity = initial_equity
    peak_equity = initial_equity
    max_drawdown = 0.0
    trades = []
    equity_curve = [initial_equity]
    current_position: Optional[BacktestTrade] = None
    consecutive_losses = 0
    max_consecutive_losses = 0

    # Iterate bar by bar (start after warm-up period)
    start_idx = max(200, df["ema200"].first_valid_index() or 200)
    if isinstance(start_idx, int):
        pass
    else:
        start_idx = 200

    for i in range(start_idx + 1, len(df)):
        bar = df.iloc[i]
        prev_bar = df.iloc[i - 1]

        # --- Check if current position hit SL or TP ---
        if current_position is not None:
            hit_sl, hit_tp = _check_exit(
                current_position, bar
            )

            if hit_sl or hit_tp:
                if hit_sl:
                    exit_price = current_position.sl
                    exit_reason = "SL"
                else:
                    exit_price = current_position.tp
                    exit_reason = "TP"

                # Calculate PnL
                if current_position.direction == "LONG":
                    raw_pnl = (exit_price - current_position.entry_price) * current_position.lots * 100
                else:
                    raw_pnl = (current_position.entry_price - exit_price) * current_position.lots * 100

                commission = commission_per_lot * current_position.lots
                net_trade_pnl = raw_pnl - commission

                current_position.exit_time = bar["time"]
                current_position.exit_price = exit_price
                current_position.pnl = net_trade_pnl
                current_position.commission = commission
                current_position.exit_reason = exit_reason

                trades.append(current_position)
                equity += net_trade_pnl

                if net_trade_pnl < 0:
                    consecutive_losses += 1
                    max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                else:
                    consecutive_losses = 0

                current_position = None

        # --- Generate signal if no position ---
        if current_position is None:
            # Use a slice for signal generation
            signal_df = df.iloc[max(0, i - 250):i + 1].copy()
            if len(signal_df) >= 2:
                signal = generate_signal(signal_df)

                if signal.direction != "FLAT" and signal.confidence >= min_confidence:
                    atr = bar["atr14"]
                    if pd.notna(atr) and atr > 0:
                        # Entry with slippage
                        entry_slip = slippage_points
                        if signal.direction == "LONG":
                            entry_price = bar["close"] + spread_points / 2 + entry_slip
                            sl = entry_price - atr * atr_sl_mult
                            tp = entry_price + atr * atr_tp_mult
                        else:
                            entry_price = bar["close"] - spread_points / 2 - entry_slip
                            sl = entry_price + atr * atr_sl_mult
                            tp = entry_price - atr * atr_tp_mult

                        # Position sizing
                        sl_distance = atr * atr_sl_mult
                        risk_amount = equity * risk_per_trade
                        lots = round(risk_amount / (sl_distance * 10), 2)
                        lots = max(lots, 0.01)

                        current_position = BacktestTrade(
                            entry_time=bar["time"],
                            direction=signal.direction,
                            entry_price=entry_price,
                            sl=sl,
                            tp=tp,
                            lots=lots,
                            slippage=entry_slip,
                            confidence=signal.confidence,
                        )

        # Track equity curve and drawdown
        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        drawdown = peak_equity - equity
        max_drawdown = max(max_drawdown, drawdown)

    # --- Close any remaining position at last bar ---
    if current_position is not None:
        last_bar = df.iloc[-1]
        exit_price = last_bar["close"]
        if current_position.direction == "LONG":
            raw_pnl = (exit_price - current_position.entry_price) * current_position.lots * 100
        else:
            raw_pnl = (current_position.entry_price - exit_price) * current_position.lots * 100

        commission = commission_per_lot * current_position.lots
        current_position.exit_time = last_bar["time"]
        current_position.exit_price = exit_price
        current_position.pnl = raw_pnl - commission
        current_position.commission = commission
        current_position.exit_reason = "end_of_data"
        trades.append(current_position)
        equity += current_position.pnl

    # --- Calculate metrics ---
    result = _calculate_metrics(trades, initial_equity, equity, max_drawdown, equity_curve, max_consecutive_losses)
    
    log.info("Backtest complete | Trades: %d | Net PnL: %.2f | Win rate: %.1f%% | "
             "Sharpe: %.2f | Max DD: %.2f (%.1f%%)",
             result.total_trades, result.net_pnl, result.win_rate * 100,
             result.sharpe_ratio, result.max_drawdown, result.max_drawdown_pct * 100)

    return result


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
    result.max_drawdown_pct = max_drawdown / initial_equity if initial_equity > 0 else 0
    result.avg_win = np.mean(wins) if wins else 0
    result.avg_loss = np.mean(losses) if losses else 0
    result.avg_rr = abs(result.avg_win / result.avg_loss) if result.avg_loss != 0 else 0
    result.total_commission = sum(t.commission for t in trades)

    # Sharpe ratio (annualized, assuming 15-min bars)
    if len(pnls) > 1:
        pnl_array = np.array(pnls)
        mean_return = np.mean(pnl_array)
        std_return = np.std(pnl_array)
        if std_return > 0:
            # Annualize: ~35,000 15-min bars per year
            trades_per_year = min(len(trades), 252 * 26)  # trading days * bars per day
            result.sharpe_ratio = (mean_return / std_return) * np.sqrt(trades_per_year)
        else:
            result.sharpe_ratio = 0.0

    return result


def print_backtest_report(result: BacktestResult) -> str:
    """Formats a human-readable backtest report."""
    report = f"""
╔══════════════════════════════════════════════════════════════╗
║                    BACKTEST REPORT                          ║
╠══════════════════════════════════════════════════════════════╣
║  Total Trades:          {result.total_trades:<10}                       ║
║  Winning Trades:        {result.winning_trades:<10}                       ║
║  Losing Trades:         {result.losing_trades:<10}                       ║
║  Win Rate:              {result.win_rate*100:>6.1f}%                          ║
╠══════════════════════════════════════════════════════════════╣
║  Gross Profit:          ${result.gross_profit:>10.2f}                    ║
║  Gross Loss:            ${result.gross_loss:>10.2f}                    ║
║  Net PnL:               ${result.net_pnl:>10.2f}                    ║
║  Total Commission:      ${result.total_commission:>10.2f}                    ║
╠══════════════════════════════════════════════════════════════╣
║  Profit Factor:         {result.profit_factor:>10.2f}                    ║
║  Sharpe Ratio:          {result.sharpe_ratio:>10.2f}                    ║
║  Avg Win:               ${result.avg_win:>10.2f}                    ║
║  Avg Loss:              ${result.avg_loss:>10.2f}                    ║
║  Avg Risk:Reward:       {result.avg_rr:>10.2f}                    ║
╠══════════════════════════════════════════════════════════════╣
║  Max Drawdown:          ${result.max_drawdown:>10.2f}                    ║
║  Max Drawdown %:        {result.max_drawdown_pct*100:>6.1f}%                          ║
║  Max Consec. Losses:    {result.max_consecutive_losses:<10}                       ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(report)
    return report
