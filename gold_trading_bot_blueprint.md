# XAU/USD Algorithmic Trading Bot — Technical Blueprint

**Scope:** Architecture, signal logic, risk management, execution, and security design for an automated gold-trading system.
**Format:** Implementation-ready blueprint with Python-style pseudocode.
**Audience:** Experienced developers / quant engineering team.

---

## 0. Read this first

**On "Exnexx":** I couldn't verify this as an established, regulated trading platform. The real, well-documented broker in this space is Exness, which fully supports MT4/MT5. The "Exnexx" references that do exist online are an unrelated parked domain listing and an individual trader's personally-named strategy shared on a forex forum — not an institutional platform with a documented API or a "proven analytical framework" worth building against. This is very likely a name mix-up. Before pointing any credentials at a platform, confirm its exact legal name, its regulator, and that it publishes a documented trading API. This blueprint is written against the **MetaTrader 5 Python API**, the closest thing to a broker-agnostic standard for retail gold trading — the module boundaries below (risk gates, signal logic, execution, position management) hold regardless of which broker's SDK ultimately sits behind `connect_broker()` and `place_order()`.

**This is architecture, not financial advice.** Gold is typically traded as a leveraged CFD or spot-margin product. Leverage scales losses as fast as gains, and automation doesn't remove that risk — it just removes the pause where a human might reconsider. Nothing in this document should be read as a signal to trade, and no indicator combination or ML model here is presented as reliably profitable. Backtest and paper-trade extensively (Section 11) before any live capital is at risk.

**Regulatory footing.** Confirm: (a) your broker is licensed by a recognized regulator for your jurisdiction, (b) their terms of service permit automated/API/EA trading, and (c) if this system will ever trade on behalf of anyone other than you, whether that crosses into regulated investment-advice or asset-management territory where you operate. Rules vary significantly by country.

---

## 1. Architecture overview

The system is a five-stage pipeline plus two cross-cutting concerns that touch every stage.

| Stage | Responsibility |
|---|---|
| Market data feed | Pulls live and historical OHLCV bars for XAU/USD |
| Indicator engine | Computes RSI, MACD, ATR, EMAs, Bollinger Bands from raw bars |
| Signal generator | Rule-based confluence logic, optionally re-weighted by an ML confidence score |
| Risk manager | Position sizing and circuit breakers (per-trade, daily, drawdown, overtrading) |
| Order execution | Turns an approved signal into a live order with SL/TP attached, then continuously trails the position |

Cross-cutting: **config & secrets management** (Section 2) feeds credentials into the data feed and execution stages; **logging, monitoring & alerting** (Section 10) observes every stage. Data flows one direction through the pipeline; only order execution loops back on itself, continuously re-checking open positions to trail SL/TP (Section 8).

---

## 2. Configuration & secure credential handling

Credentials are never hardcoded. They're loaded from environment variables injected at deploy time — by a secrets manager, a container orchestrator's secret store, or (for local development only) a `.env` file excluded from version control.

```python
import os

# ---------------------------------------------------------------------------
# CONFIGURATION — loaded from environment, never from source
# ---------------------------------------------------------------------------
BROKER_LOGIN    = int(os.environ["BROKER_LOGIN"])
BROKER_PASSWORD = os.environ["BROKER_PASSWORD"]      # never logged, never printed
BROKER_SERVER   = os.environ["BROKER_SERVER"]
ALERT_WEBHOOK_URL = os.environ["ALERT_WEBHOOK_URL"]  # Telegram/Slack, for Section 10

SYMBOL                 = "XAUUSD"
TIMEFRAME              = "M15"        # 15-minute bars; adjust per strategy horizon
POLL_INTERVAL_SECONDS  = 30

# Risk parameters — conservative defaults; tune deliberately, not by trial and error
MAX_RISK_PER_TRADE      = 0.005   # 0.5% of equity risked per trade
MAX_DAILY_LOSS_PCT      = 0.02    # 2% daily loss halts trading until next session
MAX_OPEN_POSITIONS      = 2       # caps concurrent exposure
MAX_TRADES_PER_DAY      = 5       # hard cap regardless of signal count — prevents overtrading
COOLDOWN_AFTER_LOSS_MIN = 30      # minutes of no new signals after a stopped-out trade
ATR_SL_MULTIPLIER       = 1.5
ATR_TP_MULTIPLIER       = 3.0     # ~1:2 risk:reward by default
MIN_SIGNAL_CONFIDENCE   = 0.6     # signals below this confidence are discarded


def connect_broker() -> bool:
    """
    Initializes a session with the broker's trade server using credentials
    pulled from environment variables. Swap the body for your broker's
    actual SDK/REST auth call — the surrounding contract (raise on failure,
    return True on success) stays the same.
    """
    import MetaTrader5 as mt5  # reference implementation; swap per broker
    if not mt5.initialize(login=BROKER_LOGIN, password=BROKER_PASSWORD, server=BROKER_SERVER):
        raise ConnectionError(f"Broker init failed: {mt5.last_error()}")
    return True
```

Security practices for this module are consolidated in Section 12 — read that before deploying anything.

---

## 3. Market data ingestion

```python
import pandas as pd

def fetch_ohlcv(symbol: str, timeframe: str, n_bars: int = 500) -> pd.DataFrame:
    """
    Pulls the most recent n_bars of OHLCV candles. Validates that data
    actually arrived and isn't stale before handing it downstream — a bot
    that trades on a frozen price feed is worse than a bot that trades
    on nothing.
    """
    import MetaTrader5 as mt5
    tf_map = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
              "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
    rates = mt5.copy_rates_from_pos(symbol, tf_map[timeframe], 0, n_bars)

    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No data returned for {symbol}")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    latest_bar_age = pd.Timestamp.utcnow().tz_localize(None) - df["time"].iloc[-1]
    if latest_bar_age > pd.Timedelta(minutes=5):
        raise RuntimeError(f"Stale feed: latest bar is {latest_bar_age} old")

    return df
```

Production hardening to add here: automatic reconnect with exponential backoff on feed drops, a secondary data source for cross-validation (a feed that silently returns wrong prices is more dangerous than one that errors out), and gap detection between consecutive bar timestamps.

---

## 4. Indicator engine & parameter reference

| Indicator | Parameters | Purpose | Typical gold setting |
|---|---|---|---|
| RSI | period = 14 | Momentum, overbought/oversold | OB 70 / OS 30 (some gold traders tighten to 65/35 given its volatility — keep configurable) |
| MACD | fast 12, slow 26, signal 9 | Trend-momentum crossover | Standard; histogram sign-flip is the trigger |
| ATR | period = 14 | Volatility measure, drives SL/TP distance | 1.5× ATR for SL, 3× ATR for TP (~1:2 R:R) |
| EMA | 50, 200 | Trend filter | Price vs EMA200 sets directional bias |
| Bollinger Bands | period 20, stddev 2 | Volatility envelope | Used as a secondary confluence check |

```python
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Adds RSI(14), MACD(12,26,9), ATR(14), EMA50/200, and Bollinger(20,2)."""
    df["ema50"]  = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = -delta.clip(upper=0).rolling(14).mean()
    df["rsi14"] = 100 - (100 / (1 + gain / loss))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    df["atr14"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()

    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    df["bb_upper"] = sma20 + 2 * std20
    df["bb_lower"] = sma20 - 2 * std20
    return df
```

For production, prefer a vetted library (`TA-Lib` or `pandas-ta`) over hand-rolled formulas — they handle edge cases (warm-up periods, NaN propagation) more robustly than the illustrative version above.

---

## 5. Signal generation: rule confluence + ML confidence layer

The rule layer votes on trend, momentum turn, and MACD cross; the optional ML layer re-weights confidence — it doesn't invent trades the rules didn't already flag. That constraint matters: an ML model that can independently trigger trades is much harder to audit when something goes wrong.

```python
from dataclasses import dataclass

@dataclass
class Signal:
    direction: str     # "LONG", "SHORT", or "FLAT"
    confidence: float  # 0.0 - 1.0
    reason: str

def generate_signal(df: pd.DataFrame, ml_model=None) -> Signal:
    last, prev = df.iloc[-1], df.iloc[-2]
    trend_up   = last["close"] > last["ema200"]
    trend_down = last["close"] < last["ema200"]

    long_votes = sum([
        trend_up,
        last["rsi14"] < 35 and prev["rsi14"] <= last["rsi14"],   # turning up from oversold
        last["macd_hist"] > 0 and prev["macd_hist"] <= 0,        # bullish MACD cross
    ])
    short_votes = sum([
        trend_down,
        last["rsi14"] > 65 and prev["rsi14"] >= last["rsi14"],
        last["macd_hist"] < 0 and prev["macd_hist"] >= 0,
    ])
    confidence = max(long_votes, short_votes) / 3.0

    if ml_model is not None:
        features = extract_feature_vector(df)   # your feature engineering, e.g. normalized
                                                  # indicator values + distance from EMA200 in ATRs
        ml_prob_up = ml_model.predict_proba([features])[0][1]
        confidence = 0.5 * confidence + 0.5 * ml_prob_up   # blend, don't replace

    if long_votes >= 2 and confidence >= MIN_SIGNAL_CONFIDENCE:
        return Signal("LONG", confidence, f"{long_votes}/3 bullish confluence")
    if short_votes >= 2 and confidence >= MIN_SIGNAL_CONFIDENCE:
        return Signal("SHORT", confidence, f"{short_votes}/3 bearish confluence")
    return Signal("FLAT", confidence, "no confluence")
```

**On the ML layer specifically:** if you add one, train it with walk-forward (rolling-origin) validation, not a random k-fold split — a random split lets the model peek at data from after the point it's meant to be predicting, which inflates backtest accuracy in a way that vanishes in live trading. Retrain on a rolling window on a fixed cadence (e.g. weekly) rather than once. Treat any accuracy number from a backtest as optimistic until confirmed in paper trading. Gradient-boosted trees (XGBoost/LightGBM) on tabular indicator features are a reasonable starting point; deep sequence models add complexity that's rarely justified at this data scale.

---

## 6. Risk management & position sizing

```python
def position_size(account_equity: float, risk_pct: float,
                   sl_distance_price: float, pip_value_per_lot: float) -> float:
    """
    Fixed-fractional sizing: risk a fixed % of equity per trade regardless
    of stop distance, so losses stay statistically comparable across trades
    of different volatility.
    """
    risk_amount = account_equity * risk_pct
    return round(risk_amount / (sl_distance_price * pip_value_per_lot), 2)


def daily_loss_breaker_triggered(account_info, get_realized_pnl_since) -> bool:
    """Halts new orders once today's realized loss exceeds the cap."""
    from datetime import datetime
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_pnl = get_realized_pnl_since(today_start)
    return today_pnl <= -(MAX_DAILY_LOSS_PCT * account_info.equity)


def overtrading_guard(trades_today: int, last_loss_time) -> bool:
    """Returns True if a new trade should be BLOCKED."""
    from datetime import datetime, timedelta
    if trades_today >= MAX_TRADES_PER_DAY:
        return True
    if last_loss_time and datetime.utcnow() - last_loss_time < timedelta(minutes=COOLDOWN_AFTER_LOSS_MIN):
        return True
    return False
```

`open_position_count()`, `trades_today()`, `last_loss_time()`, and `get_realized_pnl_since()` above are thin wrappers over your broker's position and trade-history API — implementations are broker-specific and omitted here for brevity.

---

## 7. Order execution with SL/TP

```python
def place_order(signal: Signal, df: pd.DataFrame, account_equity: float):
    """Places a market order with ATR-derived SL/TP, gated by every risk check."""
    import MetaTrader5 as mt5

    if (daily_loss_breaker_triggered(mt5.account_info(), get_realized_pnl_since)
            or open_position_count() >= MAX_OPEN_POSITIONS
            or overtrading_guard(trades_today(), last_loss_time())):
        log_trade_event("order_blocked", reason="risk_gate")
        return None

    tick = mt5.symbol_info_tick(SYMBOL)
    price = tick.ask if signal.direction == "LONG" else tick.bid
    atr = df["atr14"].iloc[-1]
    sl_distance, tp_distance = atr * ATR_SL_MULTIPLIER, atr * ATR_TP_MULTIPLIER
    sl = price - sl_distance if signal.direction == "LONG" else price + sl_distance
    tp = price + tp_distance if signal.direction == "LONG" else price - tp_distance
    lots = position_size(account_equity, MAX_RISK_PER_TRADE, sl_distance, pip_value_per_lot=10)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lots,
        "type": mt5.ORDER_TYPE_BUY if signal.direction == "LONG" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "deviation": 10,               # max acceptable slippage, in points
        "magic": 20260719,             # unique ID so the bot only manages its own orders
        "comment": f"gold-bot|{signal.reason}"[:31],   # MT5 comment field is length-limited
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log_trade_event("order_failed", retcode=result.retcode, comment=result.comment)
        return None

    log_trade_event("order_placed", direction=signal.direction, lots=lots, price=price, sl=sl, tp=tp)
    return result
```

---

## 8. Dynamic SL/TP management

Moves the stop to breakeven once a trade is +1R in its favor, then trails it by one ATR beyond +2R. Never widens a stop.

```python
def manage_open_positions():
    import MetaTrader5 as mt5
    for pos in mt5.positions_get(symbol=SYMBOL):
        df = compute_indicators(fetch_ohlcv(SYMBOL, TIMEFRAME, n_bars=50))
        atr = df["atr14"].iloc[-1]
        tick = mt5.symbol_info_tick(SYMBOL)
        current_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        risk_per_unit = abs(pos.price_open - pos.sl)
        favorable_move = ((current_price - pos.price_open) if pos.type == mt5.ORDER_TYPE_BUY
                           else (pos.price_open - current_price))

        new_sl = pos.sl
        if favorable_move >= risk_per_unit:                 # +1R -> lock in breakeven
            new_sl = pos.price_open
        if favorable_move >= 2 * risk_per_unit:              # beyond +2R -> trail by 1 ATR
            trail_sl = current_price - atr if pos.type == mt5.ORDER_TYPE_BUY else current_price + atr
            new_sl = max(new_sl, trail_sl) if pos.type == mt5.ORDER_TYPE_BUY else min(new_sl, trail_sl)

        if new_sl != pos.sl:
            mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "sl": round(new_sl, 2),
                "tp": pos.tp,
            })
            log_trade_event("sl_trailed", ticket=pos.ticket, new_sl=new_sl)
```

A common extension is scaled exits: close 50% of the position at TP1 (e.g. 1.5× ATR), move the stop to breakeven on the remainder, and let the rest run to TP2. That's a straightforward addition once the base trailing logic above is stable.

---

## 9. Main event loop

```python
import time

def run():
    connect_broker()
    while True:
        try:
            df = compute_indicators(fetch_ohlcv(SYMBOL, TIMEFRAME))
            signal = generate_signal(df, ml_model=load_model_if_available())
            if signal.direction != "FLAT":
                place_order(signal, df, account_equity=get_account_equity())
            manage_open_positions()
        except Exception:
            log.exception("Loop error — continuing after backoff")
            send_alert("Bot loop threw an exception, see logs", severity="ERROR")
        time.sleep(POLL_INTERVAL_SECONDS)
```

---

## 10. Logging, monitoring & alerting

```python
import logging
from datetime import datetime
import requests

log = logging.getLogger("gold_bot")

def log_trade_event(event_type: str, **fields):
    """Structured record for every signal, order, fill, and error. Never logs credentials."""
    log.info({"event": event_type, "ts": datetime.utcnow().isoformat(), **fields})

def send_alert(message: str, severity: str = "INFO"):
    """Pushes critical events to an external channel so a human is looped in fast —
    swap the webhook for Telegram, Slack, or PagerDuty as preferred."""
    requests.post(ALERT_WEBHOOK_URL, json={"text": f"[{severity}] {message}"}, timeout=5)
```

Alert on, at minimum: risk-gate trips (daily loss breaker, overtrading guard), broker connectivity loss, order rejections, and any unhandled exception in the main loop. A bot that fails silently is more dangerous than one that doesn't run at all.

---

## 11. Backtesting & validation protocol

- Model realistic spread, commission, and slippage — gold spreads widen sharply around news releases and session rollovers; a backtest with zero-cost fills will look far better than live trading ever will.
- Use walk-forward (rolling train/validate/test) windows, never a single random split — especially for the ML layer.
- Require a meaningful out-of-sample sample size: aim for hundreds of trades across more than one market regime (trending and ranging), not a curve-fit to one good quarter.
- Hold out a final test window and look at it exactly once, at the end.
- Compare against a naive baseline — a strategy that doesn't beat random entries with identical risk management isn't earning its complexity.
- Paper-trade on a demo account for a meaningful stretch (a full quarter is a reasonable minimum) across varied conditions before committing live capital.
- Re-validate periodically. Markets regime-shift, and a strategy tuned on one volatility environment may not hold in another.

---

## 12. Security checklist

- Never hardcode credentials — load via environment variables or a secrets manager (AWS Secrets Manager, HashiCorp Vault, Azure Key Vault).
- Exclude `.env` / credential files from version control.
- Scope API keys to trading only; disable withdrawal permissions on any key the bot holds, wherever the broker supports scoped permissions.
- Encrypt any credential cache kept locally (e.g. `cryptography.fernet`).
- Enforce TLS on every connection; never disable certificate verification.
- IP-allowlist the bot's server on the broker side if that's supported.
- Rotate API keys and passwords on a regular schedule.
- Run the bot under a least-privilege service account, not root.
- Never log secrets — scrub credential fields before anything hits a log file.
- Enable 2FA on the underlying broker account itself, separate from the bot's API credentials.

---

## 13. Suggested tech stack

- **Language:** Python 3.11+ for the strategy/orchestration layer (or MQL5 directly for a native, lower-latency MT5 Expert Advisor)
- **Data & indicators:** pandas, numpy, TA-Lib or pandas-ta
- **ML (optional):** scikit-learn, XGBoost or LightGBM
- **Broker connectivity:** `MetaTrader5` Python package as the reference implementation, or your broker's documented REST/FIX/WebSocket SDK
- **Scheduling:** asyncio event loop or APScheduler
- **Secrets:** python-dotenv for local dev; a managed secrets store in production
- **Trade journal:** PostgreSQL or SQLite
- **Alerting:** Telegram Bot API or a Slack webhook
- **Backtesting:** `backtesting.py` or `vectorbt`
- **Deployment:** Dockerized process on a low-latency VPS near the broker's servers, under systemd or Docker restart policies for automatic recovery

---

## 14. Pre-go-live checklist

- [ ] Backtested across ≥ 2 distinct market regimes with realistic costs
- [ ] ML layer (if used) walk-forward validated, not randomly split
- [ ] Paper-traded live for a meaningful stretch with no unexplained divergence from backtest
- [ ] All credentials in a secrets manager or encrypted store, none in source control
- [ ] API keys scoped to trade-only, not withdrawal-enabled
- [ ] Circuit breakers tested by deliberately tripping the daily-loss cap and confirming the bot halts
- [ ] Alerting verified — kill the network connection and confirm a notification arrives
- [ ] Broker terms of service reviewed for automated-trading permissions
- [ ] Broker's regulatory status confirmed
- [ ] Manual kill switch documented and tested
