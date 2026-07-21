"""
Emergency Loss Closer — Force Close Losing Positions
======================================================
The MOST CRITICAL safety module added after loss analysis on 2026-07-20.

Runs every cycle and force-closes positions that exceed loss thresholds
WITHOUT waiting for the stop loss to be hit.

Emergency Triggers:
  1. Per-Position Loss   — Single position losing > MAX_LOSS_PER_POSITION ($50 default)
  2. Portfolio Drawdown  — All open positions combined losing > MAX_PORTFOLIO_DRAWDOWN (3% equity)
  3. Consecutive Losses  — 3 losses in a row → 30 min cooldown
  4. Losing Streak Halt  — 5 losses in a row → stop trading for the day
  5. Floating Loss Gate  — Don't allow new orders if current floating loss > 1% equity

Golden Rule: Protect capital FIRST, profits come SECOND.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.logger import log_trade_event
from utils.trade_journal import record_trade_close

log = logging.getLogger("gold_bot.emergency")


def force_close_position(position) -> bool:
    """
    Force closes a single position at market price.

    Args:
        position: MT5 position object.

    Returns:
        True if successfully closed, False otherwise.
    """
    import MetaTrader5 as mt5

    is_buy = position.type == mt5.ORDER_TYPE_BUY
    # To close a BUY, we SELL; to close a SELL, we BUY
    close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY

    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        log.error("Cannot get tick for %s — force close failed", position.symbol)
        return False

    # Close at bid for buys, ask for sells
    close_price = tick.bid if is_buy else tick.ask

    # Detect filling mode
    symbol_info = mt5.symbol_info(position.symbol)
    filling_mode = mt5.ORDER_FILLING_FOK
    if symbol_info and hasattr(symbol_info, "filling_mode"):
        fm = symbol_info.filling_mode
        if fm & 1:
            filling_mode = mt5.ORDER_FILLING_FOK
        elif fm & 2:
            filling_mode = mt5.ORDER_FILLING_IOC
        elif fm & 4:
            filling_mode = mt5.ORDER_FILLING_RETURN

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       position.symbol,
        "volume":       float(position.volume),
        "type":         close_type,
        "position":     position.ticket,
        "price":        float(close_price),
        "deviation":    30,  # Higher deviation for emergency close
        "magic":        int(position.magic),
        "comment":      "gb-EMERGENCY-CLOSE"[:31],
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    result = mt5.order_send(request)

    direction = "BUY" if is_buy else "SELL"

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.warning(
            "🚨 EMERGENCY CLOSE | Ticket: %d | %s | Profit: %.2f | "
            "Entry: %.2f | Close: %.2f",
            position.ticket, direction, position.profit,
            position.price_open, close_price
        )
        log_trade_event(
            "emergency_close",
            ticket=position.ticket,
            direction=direction,
            profit=position.profit,
            entry_price=position.price_open,
            close_price=close_price,
        )
        # Record in trade journal
        record_trade_close(
            ticket=position.ticket,
            close_price=close_price,
            profit=position.profit,
            commission=getattr(position, 'commission', 0.0),
            swap=getattr(position, 'swap', 0.0),
        )
        return True
    else:
        retcode = result.retcode if result else "None"
        comment = result.comment if result else str(mt5.last_error())
        log.error(
            "EMERGENCY CLOSE FAILED | Ticket: %d | Retcode: %s | %s",
            position.ticket, retcode, comment
        )
        return False


def check_per_position_loss(positions: list, equity: float) -> list:
    """
    Check each position's loss against MAX_LOSS_PER_POSITION.
    Returns list of positions that need emergency closing.
    """
    from config.settings import MAX_LOSS_PER_POSITION

    to_close = []
    for pos in positions:
        # pos.profit includes unrealized PnL
        if pos.profit < -abs(MAX_LOSS_PER_POSITION):
            log.warning(
                "🔴 Position %d exceeds max loss: $%.2f (limit: -$%.2f)",
                pos.ticket, pos.profit, MAX_LOSS_PER_POSITION
            )
            to_close.append(pos)

    return to_close


def check_portfolio_drawdown(positions: list, equity: float) -> bool:
    """
    Check if combined unrealized loss of all open positions exceeds
    MAX_PORTFOLIO_DRAWDOWN percentage of equity.

    Returns True if all positions should be emergency closed.
    """
    from config.settings import MAX_PORTFOLIO_DRAWDOWN

    if not positions:
        return False

    total_floating_pnl = sum(pos.profit for pos in positions)
    drawdown_threshold = -(MAX_PORTFOLIO_DRAWDOWN * equity)

    if total_floating_pnl < drawdown_threshold:
        log.critical(
            "🚨🚨 PORTFOLIO DRAWDOWN EXCEEDED | Floating PnL: $%.2f | "
            "Threshold: $%.2f (%.1f%% of equity $%.2f)",
            total_floating_pnl, drawdown_threshold,
            MAX_PORTFOLIO_DRAWDOWN * 100, equity
        )
        return True

    log.debug(
        "Portfolio drawdown OK | Floating PnL: $%.2f | Threshold: $%.2f",
        total_floating_pnl, drawdown_threshold
    )
    return False


def check_consecutive_losses() -> tuple[bool, int]:
    """
    Check how many consecutive losing trades have occurred recently.

    Returns:
        (should_halt, consecutive_loss_count)
    """
    from config.settings import CONSECUTIVE_LOSS_HALT, LOSING_STREAK_COOLDOWN_MIN
    from utils.trade_journal import get_recent_trade_results

    results = get_recent_trade_results(count=10)
    if not results:
        return False, 0

    consecutive_losses = 0
    for profit in results:
        if profit < 0:
            consecutive_losses += 1
        else:
            break  # streak broken

    if consecutive_losses >= CONSECUTIVE_LOSS_HALT:
        log.critical(
            "🚨 LOSING STREAK HALT | %d consecutive losses — STOPPING TRADING FOR THE DAY",
            consecutive_losses
        )
        return True, consecutive_losses

    if consecutive_losses >= 3:
        log.warning(
            "⚠️ LOSING STREAK WARNING | %d consecutive losses — %d min cooldown active",
            consecutive_losses, LOSING_STREAK_COOLDOWN_MIN
        )
        # The cooldown is enforced via the overtrading_guard in risk_manager

    return False, consecutive_losses


def get_floating_pnl() -> float:
    """Returns the total floating (unrealized) PnL of all bot positions."""
    import MetaTrader5 as mt5
    from config.settings import MAGIC_NUMBER, SYMBOL

    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return 0.0

    bot_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
    return sum(p.profit for p in bot_positions)


def floating_loss_gate(equity: float) -> tuple[bool, str]:
    """
    Block new orders if current floating loss exceeds 1% of equity.
    Prevents adding to losing positions.

    Returns (blocked, reason)
    """
    floating = get_floating_pnl()
    threshold = -(0.01 * equity)

    if floating < threshold:
        reason = f"Floating loss gate: ${floating:.2f} exceeds 1% of equity (${threshold:.2f})"
        log.warning("🛑 %s", reason)
        return True, reason

    return False, ""


def run_emergency_checks() -> dict:
    """
    Master emergency check function — runs ALL emergency checks every cycle.

    Returns a dict with:
        - positions_closed: number of positions force-closed
        - portfolio_cleared: whether all positions were cleared
        - streak_halt: whether trading is halted due to losing streak
        - floating_pnl: current floating PnL
    """
    import MetaTrader5 as mt5
    from config.settings import MAGIC_NUMBER, SYMBOL

    result = {
        "positions_closed": 0,
        "portfolio_cleared": False,
        "streak_halt": False,
        "floating_pnl": 0.0,
    }

    try:
        # Get account equity
        account = mt5.account_info()
        if account is None:
            log.error("Cannot get account info for emergency checks")
            return result

        equity = account.equity

        # Get all bot positions
        positions = mt5.positions_get(symbol=SYMBOL)
        bot_positions = []
        if positions:
            bot_positions = [p for p in positions if p.magic == MAGIC_NUMBER]

        if not bot_positions:
            # No positions — just check losing streak
            halt, streak_count = check_consecutive_losses()
            result["streak_halt"] = halt
            return result

        # Calculate floating PnL
        total_floating = sum(p.profit for p in bot_positions)
        result["floating_pnl"] = total_floating

        log.info(
            "📊 Open positions: %d | Floating PnL: $%.2f | Equity: $%.2f",
            len(bot_positions), total_floating, equity
        )

        # --- Check 1: Portfolio drawdown (close ALL if triggered) ---
        if check_portfolio_drawdown(bot_positions, equity):
            log.critical("🚨 CLOSING ALL POSITIONS — Portfolio drawdown exceeded")
            closed = 0
            for pos in bot_positions:
                if force_close_position(pos):
                    closed += 1
                    time.sleep(0.5)  # Small delay between closes
            result["positions_closed"] = closed
            result["portfolio_cleared"] = True
            from utils.alerting import send_alert
            send_alert(
                f"🚨 EMERGENCY: Closed {closed} positions!\n"
                f"Portfolio drawdown exceeded {total_floating:.2f}",
                severity="CRITICAL"
            )
            return result

        # --- Check 2: Per-position loss (close individual losers) ---
        to_close = check_per_position_loss(bot_positions, equity)
        for pos in to_close:
            if force_close_position(pos):
                result["positions_closed"] += 1
                time.sleep(0.3)

        # --- Check 3: Consecutive loss check ---
        halt, streak_count = check_consecutive_losses()
        result["streak_halt"] = halt

    except Exception as e:
        log.error("Emergency check error: %s", e, exc_info=True)

    return result
