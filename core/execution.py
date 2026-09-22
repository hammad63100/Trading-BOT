"""
Order Execution (v2.0)
=======================
Turns an approved signal into a live order with SL/TP attached.
All orders are gated by risk manager and MTF alignment before submission.

Ref: Blueprint Section 7 (v2.0)
"""

import logging
import re
import math
import time
from typing import Optional

import pandas as pd

from core.signals import Signal
from core.risk_manager import position_size, check_all_risk_gates
from core.mtf_filter import check_mtf_alignment
from utils.logger import log_trade_event
from utils.trade_journal import record_trade_open

log = logging.getLogger("gold_bot.execution")


def place_order(
    signal: Signal,
    df: pd.DataFrame,
    account_equity: float
) -> Optional[object]:
    """
    Places a market order with ATR-derived SL/TP, gated by every risk check.

    Args:
        signal: Trading signal with direction and confidence.
        df: DataFrame with computed indicators.
        account_equity: Current account equity.

    Returns:
        MT5 order result on success, None if blocked or failed.
    """
    import MetaTrader5 as mt5
    from config.settings import (
        SYMBOL, ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER,
        MAX_RISK_PER_TRADE, MAGIC_NUMBER, TRADING_MODE, MIN_SIGNAL_CONFIDENCE
    )

    if signal.direction not in ("LONG", "SHORT") or not math.isfinite(signal.confidence) or signal.confidence < MIN_SIGNAL_CONFIDENCE or df.empty:
        return None
    account = mt5.account_info()
    if account is None or TRADING_MODE not in ("demo", "live"):
        return None
    if TRADING_MODE == "demo" and account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        log.error("Demo mode cannot send orders to a real account")
        return None
    account_equity = account.equity
    # --- Price & ATR ---
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None or not all(math.isfinite(v) and v > 0 for v in (tick.bid, tick.ask)) or tick.ask < tick.bid or not -5 <= time.time() - tick.time <= 60:
        log.error("Failed to get tick data for %s", SYMBOL)
        log_trade_event("order_failed", reason="no_tick_data")
        return None

    price = tick.ask if signal.direction == "LONG" else tick.bid
    atr = df["atr14"].iloc[-1] if "atr14" in df.columns else 0.0

    # --- 1. Multi-Timeframe Alignment Verification ---
    mtf_ok, mtf_reason = check_mtf_alignment(SYMBOL, signal.direction)
    if not mtf_ok:
        log_trade_event("order_blocked", reason=mtf_reason, signal=signal.direction)
        log.info("Order blocked by MTF filter: %s", mtf_reason)
        return None

    # --- 2. Comprehensive Risk Gate Check ---
    is_blocked, block_reason = check_all_risk_gates(account_equity, signal.direction, price, atr)
    if is_blocked:
        log_trade_event("order_blocked", reason=block_reason, signal=signal.direction)
        log.info("Order blocked by Risk Manager: %s", block_reason)
        return None

    if not math.isfinite(atr) or atr <= 0:
        log.error("ATR is invalid (%.4f) — cannot calculate SL/TP", atr)
        log_trade_event("order_failed", reason="invalid_atr", atr=atr)
        return None

    # --- SL/TP Calculation (1.5x ATR SL, 3.0x ATR TP = 1:2 R:R ratio) ---
    sl_distance = atr * ATR_SL_MULTIPLIER
    tp_distance = atr * ATR_TP_MULTIPLIER

    if signal.direction == "LONG":
        sl = price - sl_distance
        tp = price + tp_distance
    else:  # SHORT
        sl = price + sl_distance
        tp = price - tp_distance

    # --- Position Sizing (Anti-Martingale enabled) ---
    symbol_info = mt5.symbol_info(SYMBOL)
    if symbol_info is None or symbol_info.trade_tick_size <= 0:
        return None
    step = symbol_info.trade_tick_size
    sl = round(round(sl / step) * step, symbol_info.digits)
    tp = round(round(tp / step) * step, symbol_info.digits)
    min_stop = symbol_info.trade_stops_level * symbol_info.point
    if signal.direction == "LONG":
        valid_stops = 0 < sl < tick.bid and tp > tick.ask and tick.bid - sl >= min_stop and tp - tick.bid >= min_stop
    else:
        valid_stops = sl > tick.ask and 0 < tp < tick.bid and sl - tick.ask >= min_stop and tick.ask - tp >= min_stop
    if not valid_stops:
        log.info("Order blocked: stops do not satisfy broker limits")
        return None
    order_type = mt5.ORDER_TYPE_BUY if signal.direction == "LONG" else mt5.ORDER_TYPE_SELL
    # Calculate in account currency using the actual contract and normalized stop.
    reference_volume = symbol_info.volume_min
    loss = mt5.order_calc_profit(order_type, SYMBOL, reference_volume, price, sl)
    if loss is None or not math.isfinite(loss) or loss >= 0 or reference_volume <= 0:
        return None
    sl_distance = abs(price - sl)
    value_per_price = -loss / reference_volume / sl_distance
    lots = position_size(account_equity, MAX_RISK_PER_TRADE, sl_distance, value_per_price,
                         symbol_info.volume_min, symbol_info.volume_max, symbol_info.volume_step)

    if lots <= 0:
        from core.affordability import affordability
        details = affordability(account_equity, MAX_RISK_PER_TRADE,
                                symbol_info.volume_min, -loss / reference_volume)
        log.warning("Entry skipped: budget %.4f account-currency units; minimum-volume stop loss %.4f; "
                    "equity needed at configured risk %.2f (before fees/slippage).",
                    details["budget"], details["minimum_loss"], details["minimum_equity"])
        log_trade_event("order_blocked", reason="minimum_volume_exceeds_risk_or_loss_scaling", **details)
        return None

    margin = mt5.order_calc_margin(order_type, SYMBOL, lots, price)
    if margin is None or not math.isfinite(margin) or margin < 0 or margin > account.margin_free:
        log.info("Order blocked: insufficient or unknown margin")
        return None

    # --- Detect broker's allowed filling mode ---
    filling_mode = mt5.ORDER_FILLING_FOK  # default
    if symbol_info and hasattr(symbol_info, "filling_mode"):
        fm = symbol_info.filling_mode
        if fm & 1:
            filling_mode = mt5.ORDER_FILLING_FOK
        elif fm & 2:
            filling_mode = mt5.ORDER_FILLING_IOC
        elif fm & 4:
            filling_mode = mt5.ORDER_FILLING_RETURN

    # --- Build Order Request ---
    order_type = mt5.ORDER_TYPE_BUY if signal.direction == "LONG" else mt5.ORDER_TYPE_SELL
    safe_strategy = re.sub(r'[^A-Za-z0-9_-]', '', signal.strategy or 'rule')
    comment = f"gb-{signal.direction[:1]}-{safe_strategy}"[:31]

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       SYMBOL,
        "volume":       float(lots),
        "type":         order_type,
        "price":        float(price),
        "sl":           float(sl),
        "tp":           float(tp),
        "deviation":    20,
        "magic":        int(MAGIC_NUMBER),
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    # --- Send Order ---
    log.info(
        "Sending %s order: %.2f lots @ %.2f | SL: %.2f (%.2f dist) | TP: %.2f (%.2f dist) | ATR: %.2f",
        signal.direction, lots, price, sl, sl_distance, tp, tp_distance, atr
    )

    check = mt5.order_check(request)
    if check is None or check.retcode != 0:
        log.warning("Broker preflight rejected order")
        return None
    result = mt5.order_send(request)

    if result is None:
        err = mt5.last_error()
        log.error("order_send returned None | MT5 error: %s", err)
        log_trade_event("order_failed", reason="null_result", mt5_error=str(err))
        return None

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("Order failed | Retcode: %d | Comment: %s", result.retcode, result.comment)
        log_trade_event(
            "order_failed",
            retcode=result.retcode,
            comment=result.comment,
            direction=signal.direction,
            lots=lots,
            price=price
        )
        return None

    # --- Slippage Check ---
    filled_price = result.price if hasattr(result, 'price') and result.price > 0 else price
    slippage = abs(filled_price - price)
    if slippage > 0.50:  # $0.50 slippage on gold
        log.warning("⚠️ High slippage detected: requested %.2f, filled at %.2f (diff $%.2f)",
                    price, filled_price, slippage)

    # --- Success ---
    log.info(
        "✅ Order placed | Ticket: %d | %s %.2f lots @ %.2f | SL: %.2f | TP: %.2f",
        result.order, signal.direction, lots, filled_price, sl, tp
    )
    log_trade_event(
        "order_placed",
        ticket=result.order,
        direction=signal.direction,
        lots=lots,
        price=filled_price,
        sl=sl,
        tp=tp,
        atr=round(atr, 2),
        confidence=signal.confidence,
        reason=signal.reason
    )

    record_trade_open(
        ticket=result.order,
        symbol=SYMBOL,
        direction=signal.direction,
        open_price=filled_price,
        volume=lots,
        sl=sl,
        tp=tp,
        signal_confidence=signal.confidence,
        signal_reason=signal.reason,
        magic_number=MAGIC_NUMBER
    )

    return result
