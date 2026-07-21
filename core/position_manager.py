"""
Smart Position Management — Trailing SL, Breakeven & Stale Position Closer (v2.0)
===================================================================================
Continuously monitors open positions and applies a 4-step SL & position management strategy:

  Step 1 — Faster Breakeven Lock (+0.3R)
    Once price moves 0.3x the original risk in our favor, move SL to
    entry price (breakeven). Eliminates downside risk rapidly on gold.

  Step 2 — Lock Partial Profit (+1.0R)
    Once price moves 1.0x the original risk in our favor, trail SL to
    entry + 0.5R. Guarantees profit even on total reversal.

  Step 3 — Continuous Tighter ATR Trail (+1.5R onwards)
    Once price moves 1.5x original risk in our favor, trail SL
    continuously at (current_price - 0.75 ATR) for buys or
    (current_price + 0.75 ATR) for sells. Lets winners run while ratcheting floor higher.

  Step 4 — Time-Based Stale Position Close
    If a position has been open for > MAX_POSITION_AGE_HOURS (4 hours default)
    and has failed to move beyond +0.3R, close it at market price to free margin.

Golden Rule: NEVER widen a stop. New SL must always be more protective.

Ref: Blueprint Section 8 (v2.0 enhanced)
"""

import time
import logging
from datetime import datetime, timezone
import pandas as pd

from utils.logger import log_trade_event

log = logging.getLogger("gold_bot.position_mgr")


def manage_open_positions() -> None:
    """
    Iterates through all open positions managed by this bot and
    applies smart trailing SL logic and stale position checks on every cycle.
    """
    import MetaTrader5 as mt5
    from config.settings import SYMBOL, TIMEFRAME, MAGIC_NUMBER
    from core.data_feed import fetch_ohlcv
    from core.indicators import compute_indicators

    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None or len(positions) == 0:
        log.debug("No open positions to manage")
        return

    bot_positions = [p for p in positions if p.magic == MAGIC_NUMBER]
    if not bot_positions:
        log.debug("No bot-managed positions found (magic: %d)", MAGIC_NUMBER)
        return

    try:
        df = compute_indicators(fetch_ohlcv(SYMBOL, TIMEFRAME))
        atr = df["atr14"].iloc[-1]
        if pd.isna(atr) or atr <= 0:
            log.warning("ATR is invalid (%.4f) — skipping position management", atr)
            return
    except Exception as e:
        log.error("Failed to fetch data for position management: %s", e)
        return

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        log.error("Failed to get tick data — skipping position management")
        return

    log.debug(
        "Managing %d open positions | ATR: %.2f | Bid: %.2f | Ask: %.2f",
        len(bot_positions), atr, tick.bid, tick.ask
    )

    for pos in bot_positions:
        try:
            # 1. Check for stale position timeout
            if _check_stale_position(pos, tick):
                continue  # position closed, move to next
            # 2. Smart trail
            _smart_trail(pos, atr, tick)
        except Exception as e:
            log.error("Error managing position %d: %s", pos.ticket, e)


def _check_stale_position(pos, tick) -> bool:
    """
    Closes positions that have been open for too long without reaching target profit.
    Returns True if position was closed.
    """
    from config.settings import MAX_POSITION_AGE_HOURS
    from core.emergency_closer import force_close_position

    now_ts = time.time()
    age_seconds = now_ts - pos.time
    max_age_seconds = MAX_POSITION_AGE_HOURS * 3600

    if age_seconds > max_age_seconds:
        risk_unit = abs(pos.price_open - pos.sl) if pos.sl > 0 else 1.0
        is_buy = pos.type == 0  # ORDER_TYPE_BUY
        current_price = tick.bid if is_buy else tick.ask
        fav_move = (current_price - pos.price_open) if is_buy else (pos.price_open - current_price)
        r_mult = fav_move / risk_unit if risk_unit > 0 else 0

        # If trade is stagnant (under +0.3R profit after max age), close it
        if r_mult < 0.3:
            log.warning(
                "⌛ STALE POSITION TIMEOUT | Ticket: %d | Age: %.1f hrs > max %.1f hrs | "
                "PnL: $%.2f (%.2fR) — Force closing to free capital",
                pos.ticket, age_seconds / 3600, MAX_POSITION_AGE_HOURS,
                pos.profit, r_mult
            )
            return force_close_position(pos)

    return False


def _smart_trail(pos, atr: float, tick) -> None:
    """
    Applies the 4-step smart trailing SL to a single position.

    Args:
        pos: MT5 position object.
        atr: Current ATR value.
        tick: Current tick data with bid/ask.
    """
    import MetaTrader5 as mt5

    is_buy = pos.type == mt5.ORDER_TYPE_BUY
    current_price = tick.bid if is_buy else tick.ask

    risk_unit = abs(pos.price_open - pos.sl)
    if risk_unit <= 0:
        log.debug("Position %d has no SL set — skipping", pos.ticket)
        return

    if is_buy:
        favorable_move = current_price - pos.price_open
    else:
        favorable_move = pos.price_open - current_price

    r_multiple = favorable_move / risk_unit if risk_unit > 0 else 0

    new_sl = pos.sl

    # ---------------------------------------------------------------
    # Step 1: Faster Breakeven Lock (+0.3R)
    # ---------------------------------------------------------------
    if r_multiple >= 0.3:
        breakeven_sl = pos.price_open
        if is_buy:
            new_sl = max(new_sl, breakeven_sl)
        else:
            new_sl = min(new_sl, breakeven_sl)

    # ---------------------------------------------------------------
    # Step 2: Lock Partial Profit (+1.0R) → Move SL to entry + 0.5R
    # ---------------------------------------------------------------
    if r_multiple >= 1.0:
        if is_buy:
            lock_sl = pos.price_open + 0.5 * risk_unit
            new_sl = max(new_sl, lock_sl)
        else:
            lock_sl = pos.price_open - 0.5 * risk_unit
            new_sl = min(new_sl, lock_sl)

    # ---------------------------------------------------------------
    # Step 3: Tighter ATR Trail (+1.5R onwards) → 0.75 × ATR
    # ---------------------------------------------------------------
    if r_multiple >= 1.5:
        trail_dist = 0.75 * atr
        if is_buy:
            trail_sl = current_price - trail_dist
            new_sl = max(new_sl, trail_sl)
        else:
            trail_sl = current_price + trail_dist
            new_sl = min(new_sl, trail_sl)

    # ---------------------------------------------------------------
    # Golden Rule: NEVER widen a stop
    # ---------------------------------------------------------------
    if is_buy and new_sl < pos.sl:
        return
    if not is_buy and new_sl > pos.sl:
        return

    # Only modify if SL actually changed by at least $0.01
    if abs(new_sl - pos.sl) < 0.01:
        return

    new_sl = float(round(new_sl, 2))

    # Send SL modification to broker
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": pos.ticket,
        "symbol":   pos.symbol,
        "sl":       new_sl,
        "tp":       float(pos.tp),
    }

    result = mt5.order_send(request)
    direction = "BUY" if is_buy else "SELL"

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        if r_multiple >= 1.5:
            step = "ATR_TRAIL"
        elif r_multiple >= 1.0:
            step = "LOCK_PROFIT"
        else:
            step = "BREAKEVEN"

        log.info(
            "🔄 SL moved [%s] | Ticket: %d | %s | SL: %.2f → %.2f | "
            "R-multiple: %.2f | Fav move: $%.2f | ATR: %.2f",
            step, pos.ticket, direction, pos.sl, new_sl,
            r_multiple, favorable_move, atr
        )
        log_trade_event(
            "sl_trailed",
            ticket=pos.ticket,
            direction=direction,
            step=step,
            old_sl=pos.sl,
            new_sl=new_sl,
            r_multiple=round(r_multiple, 2),
            favorable_move=round(favorable_move, 2),
            atr=round(atr, 2),
        )
    elif result:
        log.error(
            "SL trail failed for ticket %d | Retcode: %d | Comment: %s",
            pos.ticket, result.retcode, result.comment
        )
    else:
        err = mt5.last_error()
        log.error("SL trail returned None for ticket %d | MT5 error: %s", pos.ticket, err)
