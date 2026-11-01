> **September 2026 safety revision:** Read [SAFETY_REVIEW.md](SAFETY_REVIEW.md) first. Demo mode now submits orders only to verified demo accounts. Below-minimum risk sizing skips trades. Older feature descriptions and example limits below are historical; the review and current configuration describe the updated behavior. Profitability has not been established.

# 🥇 XAU/USD Algorithmic Gold Trading Bot v2.0

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-orange.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Version](https://img.shields.io/badge/version-2.0.0-brightgreen.svg)

An institutional-grade, fully automated Algorithmic Trading Bot specifically engineered for high-frequency precision trading of **Gold (XAU/USD)** via the **MetaTrader 5 (MT5)** platform.

> **⚠️ DISCLAIMER: NOT FINANCIAL ADVICE**
> Gold (XAU/USD) is a highly volatile, leveraged financial instrument. Automated trading software carries substantial financial risk. This project is intended for educational, research, and institutional testing purposes. **Always paper-trade extensive backtests and demo accounts before deploying live capital.**

---

## 📖 How It Works

The bot operates on a continuous event-driven loop (polling every 30s by default). Each cycle follows a strict **6-Stage Protection & Execution Pipeline**:

```text
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             STAGE 1: EMERGENCY CHECK                              │
│ Check per-position loss ($50 limit), portfolio drawdown (3%), losing streaks     │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼─────────────────────────────────────────┐
│                           STAGE 2: DATA & INDICATORS                             │
│ Fetch M15 OHLCV bars → Compute RSI, MACD, ATR, EMA, ADX, VWAP, Ichimoku, Divergence │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼─────────────────────────────────────────┐
│                           STAGE 3: 5-STRATEGY ENGINE                             │
│ Evaluate 5 strategies + ADX filter + Multi-Timeframe (H1/H4) trend check          │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼─────────────────────────────────────────┐
│                             STAGE 4: RISK GATES                                  │
│ Verify position count (max 2 same-dir), entry spacing (5m + 1 ATR), spread filter│
│ Apply Anti-Martingale lot sizing (halved after loss)                              │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼─────────────────────────────────────────┐
│                            STAGE 5: ORDER EXECUTION                              │
│ Send market order with 1.5x ATR SL & 3.0x ATR TP (1:2 R:R Ratio) via MT5 API     │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼─────────────────────────────────────────┐
│                          STAGE 6: POSITION MANAGEMENT                            │
│ +0.3R Fast Breakeven ➔ +1.0R Partial Lock ➔ +1.5R 0.75x ATR Trail ➔ Stale Close  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ v2.0 Key Upgrade Features

### 🚨 1. Emergency Loss Closer (`core/emergency_closer.py`)
Protects your account against runaway losses **without waiting for the Stop Loss to be hit**:
- **Per-Position Max Loss:** Automatically force-closes any single position losing > **$50** (configurable).
- **Portfolio Drawdown Protection:** Immediately force-closes ALL open positions if combined floating drawdown exceeds **3% of equity**.
- **Losing Streak Halt:** Suspends trading automatically for 30 minutes after 3 consecutive losses, or halts trading for the rest of the day after 5 losses.
- **Floating Loss Gate:** Blocks placing new trades if existing open trades are losing > 1% equity.

### 📊 2. Multi-Timeframe (MTF) Trend Alignment (`core/mtf_filter.py`)
Prevents buying into higher-timeframe downtrends or selling into higher-timeframe uptrends:
- Fetches real-time **H1 and H4** data every cycle.
- Calculates H1/H4 EMA20 vs EMA50 trend direction.
- **Counter-Trend Block:** Rejects BUY signals when H1/H4 is bearish, and rejects SHORT signals when H1/H4 is bullish.

### 🧠 3. Multi-Strategy Confluence Engine (`core/signals.py`)
Runs **5 independent technical strategies** in parallel and selects the highest conviction signal:
1. **Trend Momentum:** EMA50/200 trend alignment + price pullback to EMA50 + RSI momentum + MACD histogram slope.
2. **Breakout Momentum:** 20-bar high/low breakout + expanding ATR volatility + confirming RSI.
3. **RSI Mean Reversion:** Overbought (>65) / Oversold (<35) reversal + MACD histogram zero cross.
4. **Institutional VWAP & Volume Momentum:** Price interaction with VWAP + high relative volume (>1.1x avg) + candle quality.
5. **Market Structure & Ichimoku Confluence:** Higher Highs/Lows (uptrend) or Lower Highs/Lows (downtrend) + Ichimoku Cloud position.
- **ADX Trend Strength Gate:** Blocks entries when ADX < 20 (ranging/choppy market).
- **Confidence Gate:** Minimum signal confidence of **65%** for high-quality entries.

### 🛡️ 4. Anti-Martingale Risk Control (`core/risk_manager.py`)
- **Anti-Stacking Rules:** Maximum **1-2 positions in the same direction** (prevents over-exposure).
- **Wide Entry Spacing:** Requires minimum **5 minutes AND 1.0 ATR price distance** between consecutive same-direction entries.
- **Anti-Martingale Sizing:** Automatically reduces lot size after losses (**50% after 1 loss, 25% after 2+ losses**) to protect capital during drawdowns. Resets to normal after a win.
- **Spread Spike Filter:** Rejects orders if the current broker spread exceeds **3x normal average spread**.

### 🎯 5. 4-Step Position Management (`core/position_manager.py`)
- **Step 1 — Fast Breakeven Lock (+0.3R):** Moves SL to entry price at +0.3R profit (eliminates downside early).
- **Step 2 — Partial Profit Lock (+1.0R):** Moves SL to entry + 0.5R at +1.0R profit (guarantees cash profit).
- **Step 3 — 0.75x ATR Trail (+1.5R onwards):** Continuously ratchets SL 0.75x ATR behind current price.
- **Step 4 — Time-Based Stale Position Close:** Force-closes stagnant trades open for > 4 hours with < +0.3R profit.

### 🖥️ 6. Real-Time Web Command Center (`dashboard/app.py`)
- Dark-themed Bloomberg-style browser UI running on `http://localhost:5555`.
- Live equity curve chart (Chart.js), active position tracking, risk status dials, and recent trade logs.

---

## 🛠️ Technology Stack & Dependencies

| Technology | Purpose | Description |
| :--- | :--- | :--- |
| **Python 3.10+** | Core Runtime | High-performance Python execution environment |
| **MetaTrader 5 API** | Broker Ingestion & Execution | Official `MetaTrader5` bindings for order execution and live ticks |
| **Pandas & NumPy** | Vectorized Calculations | High-speed OHLCV math for indicators and features |
| **Flask** | Web Dashboard | Real-time monitoring UI on port 5555 |
| **XGBoost & Scikit-Learn** | Machine Learning Layer | Walk-forward signal confidence model |
| **SQLite3 (WAL Mode)** | Persistent Audit Journal | Thread-safe database storing all trades and events |
| **Requests** | Webhook Alerting | Push notifications to Telegram or Slack |

---

## 📂 Directory Structure

```text
h:\Treading bot\
├── .env                        # Private broker credentials & risk settings (never committed)
├── .env.example                # Template configuration file
├── requirements.txt            # Python package dependencies
├── run.py                      # Main CLI entry point & event loop
│
├── config/
│   └── settings.py             # Configuration loader, validator & MT5 connector
│
├── core/                       # Core Trading Engine
│   ├── emergency_closer.py     # Force-close module for emergency loss protection
│   ├── mtf_filter.py           # H1/H4 multi-timeframe trend alignment filter
│   ├── indicators.py           # ADX, VWAP, Ichimoku, RSI Divergence & base indicators
│   ├── signals.py              # 5-Strategy confluence signal engine
│   ├── risk_manager.py         # Anti-martingale, spread filter, entry spacing & risk gates
│   ├── position_manager.py     # 4-step smart trailing SL & stale trade closer
│   ├── execution.py            # Order placement with 1.5x/3.0x ATR SL/TP & slippage check
│   ├── affordability.py        # Small account affordability & margin validation
│   └── data_feed.py            # Real-time OHLCV data ingestion with reconnect backoff
│
├── dashboard/                  # Real-Time Monitoring
│   ├── __init__.py
│   └── app.py                  # Web dashboard UI & JSON APIs (Port 5555)
│
├── ml/                         # Machine Learning Layer
│   ├── features.py             # Feature vector extraction from indicators
│   └── model.py                # XGBoost confidence model loader & saver
│
├── scripts/                    # Maintenance & Diagnostics
│   ├── health_check.py         # Broker, feed, DB & pre-flight diagnostics
│   ├── performance_report.py   # Trade journal analytics & ASCII equity curve
│   ├── service_runner.py       # Auto-restarting Windows daemon runner
│   └── train_model.py          # Walk-forward ML training pipeline
│
├── utils/                      # Infrastructure & Utilities
│   ├── alerting.py             # Telegram / Slack webhook alerts
│   ├── logger.py               # Dual file & console logging with credential scrubbing
│   └── trade_journal.py        # SQLite persistent audit journal
│
├── backtest/                   # Backtesting Framework
│   └── backtester.py           # Historical backtesting & vectorized performance
│
└── tests/                      # Automated Test Suite (70 tests)
    ├── test_dashboard.py
    ├── test_indicators.py
    ├── test_position_manager.py
    ├── test_research.py
    ├── test_risk_manager.py
    ├── test_safety.py
    ├── test_scripts.py
    └── test_signals.py
```

---

## 🕹️ Complete Usage Guide

### 1. Pre-Flight Health Diagnostic
Run this first to verify broker connection, tick feed, spread, and account status:
```bash
python scripts/health_check.py
```

### 2. Paper Trading (Demo Mode)
Run the bot on a verified demo account:
```bash
python run.py --mode demo
```

### 3. Run with Live Web Dashboard
Start the bot and launch the web dashboard at `http://localhost:5555`:
```bash
python run.py --mode demo --dashboard
```

You can also run the dashboard standalone at any time:
```bash
python dashboard/app.py
```

### 4. Continuous Service Daemon (Auto-Restart)
Run the bot under the Windows service runner to automatically recover from network hiccups or system restarts:
```bash
python scripts/service_runner.py
```

### 5. Performance Report & Equity Curve
View full historical statistics, strategy performance, win rates, and ASCII equity chart:
```bash
python scripts/performance_report.py --days 30 --save
```

### 6. Train / Retrain ML Model
Train the XGBoost signal confidence model with walk-forward validation:
```bash
python scripts/train_model.py --bars 5000
```

### 7. Historical Backtesting
Run backtest simulation over historical candles:
```bash
python run.py --mode backtest
```

### 8. Run Automated Test Suite
Execute the entire test suite of 70 unit and safety tests:
```bash
pytest -v
```

---

## 📄 License
This project is licensed under the MIT License.
