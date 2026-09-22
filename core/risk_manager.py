"""
Risk Management & Position Sizing (v2.0)
===========================================
Implements fixed-fractional & anti-martingale position sizing and multiple circuit breakers:
- Per-trade risk cap
- Anti-martingale scaling (reduced lot size after consecutive losses)
- Daily loss halt
- Max open positions & max same-direction positions (cap at 2)
- Entry spacing guard (5 minutes & 1.0 ATR distance)
- Spread filter (block during abnormal spread spikes)
- Floating loss gate (block new trades if current open trades losing > 1% equity)
- Overtrading guard & cooldown after loss

Ref: Blueprint Section 6 (v2.0 enhanced)
"""

import time
import logging
import math
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd

log = logging.getLogger("gold_bot.risk")


def floor_volume(lots: float, minimum: float, maximum: float, step: float) -> float:
    """Round down to the broker grid; never force an unaffordable minimum lot."""
    if not all(math.isfinite(v) and v > 0 for v in (lots, minimum, maximum, step)):
        return 0.0
    if maximum < minimum or lots < minimum:
        return 0.0
    units = (Decimal(str(min(lots, maximum))) / Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)
    volume = float(units * Decimal(str(step)))
    return volume if volume >= minimum else 0.0


def position_size(
    account_equity: float,
    risk_pct: float,
    sl_distance_price: float,
    pip_value_per_lot: float = 100.0,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
    volume_step: float = 0.01,
) -> float:
    """
    Fixed-fractional sizing with Anti-Martingale scaling:
    Risks a fixed % of equity per trade, scaled DOWN after consecutive losses
    to preserve capital during drawdowns.

    Args:
        account_equity: Current account equity.
        risk_pct: Fraction of equity to risk (e.g., 0.003 for 0.3%).
        sl_distance_price: Distance from entry to stop loss in price units.
        pip_value_per_lot: Account currency per 1.0 price move per lot.
            Legacy argument name; this is NOT a pip value. Pass broker-derived value.

    Returns:
        Lots rounded DOWN to the broker step, or zero if minimum is unaffordable.
    """
    from config.settings import (
        ANTI_MARTINGALE_ENABLED, SIZE_AFTER_1_LOSS, SIZE_AFTER_2_LOSS
    )
    from utils.trade_journal import get_recent_trade_results

    if not all(math.isfinite(v) and v > 0 for v in
               (account_equity, risk_pct, sl_distance_price, pip_value_per_lot)) or risk_pct > 0.05:
        log.error("Invalid position sizing inputs")
        return 0.0

    # Base lot calculation
    risk_amount = account_equity * risk_pct
    lots = risk_amount / (sl_distance_price * pip_value_per_lot)

    # Anti-Martingale Scaling
    multiplier = 1.0
    if ANTI_MARTINGALE_ENABLED:
        recent = get_recent_trade_results(count=5)
        consecutive_losses = 0
        for profit in recent:
            if profit < 0:
                consecutive_losses += 1
            else:
                break

        if consecutive_losses == 1:
            multiplier = SIZE_AFTER_1_LOSS
            log.info("📉 Anti-Martingale: 1 prior loss → Lot size scaled to %.0f%%", multiplier * 100)
        elif consecutive_losses >= 2:
            multiplier = SIZE_AFTER_2_LOSS
            log.info("📉 Anti-Martingale: %d prior losses → Lot size scaled to %.0f%%",
                     consecutive_losses, multiplier * 100)

    if not math.isfinite(multiplier) or not 0 < multiplier <= 1:
        return 0.0
    lots = floor_volume(lots * multiplier, volume_min, volume_max, volume_step)

    log.debug(
        "Position size: Equity=%.2f, Risk%%=%.3f, Risk$=%.2f, SL dist=%.4f, Multiplier=%.2f, Lots=%.2f",
        account_equity, risk_pct, risk_amount, sl_distance_price, multiplier, lots
    )
    return lots


def daily_loss_breaker_triggered(account_equity: float) -> bool:
    """
    Halts new orders once today's realized loss exceeds the daily loss cap.

    Returns:
        True if trading should be HALTED, False if OK to continue.
    """
    from config.settings import MAX_DAILY_LOSS_PCT
    from utils.trade_journal import get_realized_pnl_since

    if not math.isfinite(account_equity) or account_equity <= 0:
        log.warning("Daily loss check skipped — equity returned %.2f", account_equity)
        return True

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_pnl = get_realized_pnl_since(today_start)
    loss_threshold = -(MAX_DAILY_LOSS_PCT * account_equity)

    if today_pnl <= loss_threshold:
        log.warning(
            "🛑 DAILY LOSS BREAKER TRIGGERED | Today PnL: %.2f | Threshold: %.2f | Equity: %.2f",
            today_pnl, loss_threshold, account_equity
        )
        return True

    return False


def overtrading_guard() -> bool:
    """
    Returns True if a new trade should be BLOCKED.
    Blocks if:
    - Today's trade count >= MAX_TRADES_PER_DAY
    - A losing trade was closed within the cooldown window
    """
    from config.settings import MAX_TRADES_PER_DAY, COOLDOWN_AFTER_LOSS_MIN
    from utils.trade_journal import get_trades_today_count, get_last_loss_time

    trades_today = get_trades_today_count()
    if trades_today >= MAX_TRADES_PER_DAY:
        log.warning(
            "🛑 OVERTRADING GUARD: %d/%d trades today — blocked",
            trades_today, MAX_TRADES_PER_DAY
        )
        return True

    last_loss = get_last_loss_time()
    if last_loss:
        cooldown_end = last_loss + timedelta(minutes=COOLDOWN_AFTER_LOSS_MIN)
        now_utc = datetime.now(timezone.utc)
        if now_utc < cooldown_end:
            remaining = (cooldown_end - now_utc).total_seconds() / 60
            log.warning(
                "🛑 COOLDOWN ACTIVE: Last loss at %s — %.1f min remaining",
                last_loss.isoformat(), remaining
            )
            return True

    return False


def open_position_count(symbol: Optional[str] = None) -> int:
    """Returns total number of open positions managed by this bot."""
    import MetaTrader5 as mt5
    from config.settings import MAGIC_NUMBER

    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        raise RuntimeError("Position query failed; new entries blocked")

    bot_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
    return len(bot_positions)


def same_direction_position_count(symbol: str, direction: str) -> int:
    """Returns number of open positions in the exact same direction."""
    import MetaTrader5 as mt5
    from config.settings import MAGIC_NUMBER

    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        raise RuntimeError("Position query failed; new entries blocked")
    if not positions:
        return 0

    bot_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
    target_type = mt5.ORDER_TYPE_BUY if direction == "LONG" else mt5.ORDER_TYPE_SELL
    same_dir = [p for p in bot_positions if p.type == target_type]
    return len(same_dir)


def recent_entry_spacing_check(signal_direction: str, current_price: float, atr: float) -> tuple[bool, str]:
    """
    Wider entry spacing check:
    Blocks if a position in the same direction was opened less than MIN_ENTRY_SPACING_SEC (300s / 5min) ago
    OR if the current price is within MIN_ENTRY_SPACING_ATR (1.0 ATR) of the last entry.
    """
    import MetaTrader5 as mt5
    from config.settings import (
        MAGIC_NUMBER, SYMBOL, MIN_ENTRY_SPACING_SEC, MIN_ENTRY_SPACING_ATR
    )

    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        raise RuntimeError("Position query failed; new entries blocked")
    if not positions:
        return False, ""

    bot_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
    if not bot_positions:
        return False, ""

    target_type = mt5.ORDER_TYPE_BUY if signal_direction == "LONG" else mt5.ORDER_TYPE_SELL
    same_dir_positions = [p for p in bot_positions if p.type == target_type]

    if not same_dir_positions:
        return False, ""

    latest_pos = max(same_dir_positions, key=lambda p: p.time)
    now_ts = time.time()
    time_diff_seconds = now_ts - latest_pos.time

    price_diff = abs(current_price - latest_pos.price_open)
    min_price_dist = MIN_ENTRY_SPACING_ATR * (atr if atr > 0 else 5.0)

    if time_diff_seconds < MIN_ENTRY_SPACING_SEC:
        return True, f"Entry spacing: trade opened {int(time_diff_seconds)}s ago (min {MIN_ENTRY_SPACING_SEC}s)"

    if price_diff < min_price_dist:
        return True, f"Entry spacing: price diff ${price_diff:.2f} < min ${min_price_dist:.2f} (1 ATR)"

    return False, ""


def check_spread_filter(symbol: str) -> tuple[bool, str]:
    """
    Blocks trading if current spread is abnormally wide (e.g. > 3x normal).
    High spread destroys profitability during news or low liquidity.
    """
    import MetaTrader5 as mt5
    from config.settings import MAX_SPREAD_MULTIPLIER

    info = mt5.symbol_info(symbol)
    if not info:
        return True, "Symbol information unavailable"

    current_spread = info.spread  # in points
    # Typical gold spread is ~15-20 points ($0.15-$0.20)
    normal_spread_points = 25
    max_allowed = normal_spread_points * MAX_SPREAD_MULTIPLIER

    if current_spread > max_allowed:
        reason = f"Spread filter: Current spread {current_spread} pts exceeds max {max_allowed:.0f} pts"
        log.warning("🛑 %s", reason)
        return True, reason

    return False, ""


def check_all_risk_gates(
    account_equity: float,
    signal_direction: str = "FLAT",
    current_price: float = 0.0,
    atr: float = 0.0
) -> tuple[bool, str]:
    """
    Runs all risk checks. Returns (is_blocked, reason).
    """
    from config.settings import (
        MAX_OPEN_POSITIONS, MAX_SAME_DIRECTION_POS, SYMBOL
    )
    from utils.alerting import send_risk_gate_alert
    from core.emergency_closer import floating_loss_gate

    # 1. Daily loss breaker
    if daily_loss_breaker_triggered(account_equity):
        reason = "Daily loss limit exceeded"
        send_risk_gate_alert("daily_loss_breaker", reason)
        return True, reason

    # 2. Max open positions total
    current_positions = open_position_count(SYMBOL)
    if current_positions >= MAX_OPEN_POSITIONS:
        reason = f"Max open positions reached ({current_positions}/{MAX_OPEN_POSITIONS})"
        log.warning("🛑 %s", reason)
        send_risk_gate_alert("max_positions", reason)
        return True, reason

    # 3. Max same-direction positions (prevent stacking!)
    if signal_direction != "FLAT":
        same_dir = same_direction_position_count(SYMBOL, signal_direction)
        if same_dir >= MAX_SAME_DIRECTION_POS:
            reason = f"Max same-direction positions reached for {signal_direction} ({same_dir}/{MAX_SAME_DIRECTION_POS})"
            log.warning("🛑 %s", reason)
            return True, reason

    # 4. Floating loss gate (don't open new trades if floating loss > 1% equity)
    blocked, f_reason = floating_loss_gate(account_equity)
    if blocked:
        return True, f_reason

    # 5. Overtrading guard (trade count + loss cooldown)
    if overtrading_guard():
        reason = "Overtrading guard or loss cooldown active"
        send_risk_gate_alert("overtrading_guard", reason)
        return True, reason

    # 6. Spread filter
    blocked, s_reason = check_spread_filter(SYMBOL)
    if blocked:
        return True, s_reason

    # 7. Entry spacing guard (smart multi-position spacing)
    if signal_direction != "FLAT" and current_price > 0:
        spaced_out, space_reason = recent_entry_spacing_check(signal_direction, current_price, atr)
        if spaced_out:
            log.info("⏳ %s", space_reason)
            return True, space_reason

    return False, ""
