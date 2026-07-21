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
4. **Institutional VWAP & Volume Momentum (NEW):** Price interaction with VWAP + high relative volume (>1.1x avg) + candle quality.
5. **Market Structure & Ichimoku Confluence (NEW):** Higher Highs/Lows (uptrend) or Lower Highs/Lows (downtrend) + Ichimoku Cloud position.
- **ADX Trend Strength Gate:** Blocks entries when ADX < 20 (ranging/choppy market).
- **Confidence Gate:** Raised minimum signal confidence to **65%** for high-quality entries.

### 🛡️ 4. Anti-Martingale Risk Control (`core/risk_manager.py`)
- **Anti-Stacking Rules:** Maximum **2 positions in the same direction** (prevents over-exposure).
- **Wide Entry Spacing:** Requires minimum **5 minutes AND 1.0 ATR price distance** between consecutive same-direction entries.
- **Anti-Martingale Sizing:** Automatically reduces lot size after losses (**50% after 1 loss, 25% after 2+ losses**) to protect capital during drawdowns. Resets to normal after a win.
- **Spread Spike Filter:** Rejects orders if the current broker spread exceeds **3x normal average spread**.

### 🎯 5. 4-Step Position Management (`core/position_manager.py`)
- **Step 1 — Fast Breakeven Lock (+0.3R):** Moves SL to entry price at +0.3R profit (eliminates downside early).
- **Step 2 — Partial Profit Lock (+1.0R):** Moves SL to entry + 0.5R at +1.0R profit (guarantees cash profit).
- **Step 3 — 0.75x ATR Trail (+1.5R onwards):** Continuously ratchets SL 0.75x ATR behind current price.
- **Step 4 — Time-Based Stale Position Close:** Force-closes stagnant trades open for > 4 hours with < +0.3R profit.

---

## 🛠️ Technology Stack & Dependencies

The bot is built using modern, production-grade Python components:

| Technology | Purpose | Description |
| :--- | :--- | :--- |
| **Python 3.10+** | Core Runtime | High-performance Python async execution environment |
| **MetaTrader 5 API** | Broker Ingestion & Execution | Official `MetaTrader5` bindings for order placement, data copy, and tick processing |
| **Pandas** | Vectorized Calculations | Efficient DataFrame manipulation for OHLCV data and indicators |
| **NumPy** | Numerical Operations | Fast mathematical computations for indicators and feature vectors |
| **XGBoost / Scikit-Learn** | Machine Learning Layer | Optional gradient-boosted trees for confidence score blending |
| **SQLite3 (WAL Mode)** | Persistent Audit Journal | Thread-safe, non-blocking local database recording every trade & event |
| **Python `logging`** | System Diagnostics | Structured dual console & file logger with sensitive credential scrubbing |
| **Requests** | Webhook Alerting | Async push notifications to Telegram Bot API or Slack webhooks |
| **python-dotenv** | Configuration Security | Environment variable loader keeping credentials out of version control |

---

## 📂 Directory Structure

```text
h:\Treading bot\
├── .env                        # Private broker credentials & risk settings (never committed)
├── .env.example                # Template configuration file
├── requirements.txt            # Python package dependencies
├── run.py                      # Main CLI entry point & main event loop
│
├── config/
│   └── settings.py             # Configuration loader, validator & MT5 connector
│
├── core/                       # Core Trading Engine
│   ├── emergency_closer.py     # [v2.0] Force-close module for emergency loss protection
│   ├── mtf_filter.py           # [v2.0] H1/H4 multi-timeframe trend alignment filter
│   ├── indicators.py           # [v2.0] ADX, VWAP, Ichimoku, RSI Divergence & base indicators
│   ├── signals.py              # [v2.0] 5-Strategy confluence signal engine
│   ├── risk_manager.py         # [v2.0] Anti-martingale, spread filter, entry spacing & risk gates
│   ├── position_manager.py     # [v2.0] 4-step smart trailing SL & stale trade closer
│   ├── execution.py            # [v2.0] Order placement with 1.5x/3.0x ATR SL/TP & slippage check
│   └── data_feed.py            # Real-time OHLCV data ingestion with reconnect backoff
│
├── ml/                         # Optional Machine Learning Engine
│   ├── features.py             # Feature vector extraction from indicators
│   └── model.py                # XGBoost confidence model training & scoring
│
├── utils/                      # Infrastructure & Utilities
│   ├── alerting.py             # Telegram / Slack webhook alerts
│   ├── logger.py               # Dual file & console logging engine
│   └── trade_journal.py        # SQLite persistent audit journal
│
├── backtest/                   # Backtesting Framework
│   └── backtester.py           # Historical backtesting & vectorized performance statistics
│
└── tests/                      # Unit Test Suite
    ├── test_indicators.py
    ├── test_signals.py
    ├── test_risk_manager.py
    └── test_position_manager.py
```

---

## ⚙️ Configuration Reference (`.env`)

All key parameters can be customized via the `.env` file without changing source code:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `BROKER_LOGIN` | `0` | MT5 Account Account Number |
| `BROKER_PASSWORD` | `""` | MT5 Account Trading Password |
| `BROKER_SERVER` | `""` | Broker MT5 Server Name (e.g. `Exness-MT5Trial16`) |
| `SYMBOL` | `XAUUSDm` | Trading symbol for Gold |
| `TIMEFRAME` | `M15` | Main chart timeframe |
| `TRADING_MODE` | `demo` | `demo`, `live`, or `backtest` |
| `MAX_RISK_PER_TRADE` | `0.003` | Fixed fractional risk per trade (0.3% of equity) |
| `MAX_OPEN_POSITIONS` | `3` | Maximum total open positions across symbol |
| `MAX_SAME_DIRECTION_POS` | `2` | Maximum open positions in the same direction (LONG or SHORT) |
| `MIN_SIGNAL_CONFIDENCE` | `0.65` | Minimum confidence score required to enter a trade (65%) |
| `ATR_SL_MULTIPLIER` | `1.5` | Stop Loss distance multiplier relative to ATR |
| `ATR_TP_MULTIPLIER` | `3.0` | Take Profit distance multiplier relative to ATR (1:2 R:R Ratio) |
| `MAX_LOSS_PER_POSITION` | `50.0` | Emergency close trigger if single position loss exceeds $50 |
| `MAX_PORTFOLIO_DRAWDOWN` | `0.03` | Emergency close trigger if total floating loss > 3% equity |
| `CONSECUTIVE_LOSS_HALT` | `5` | Stop trading for the day after 5 consecutive losses |
| `MIN_ENTRY_SPACING_SEC` | `300` | Minimum seconds required between entries in same direction (5 mins) |
| `MIN_ENTRY_SPACING_ATR` | `1.0` | Minimum price distance in ATRs between entries in same direction |
| `MAX_SPREAD_MULTIPLIER` | `3.0` | Block orders if broker spread exceeds 3x normal spread |
| `ADX_MIN_TREND_STRENGTH`| `20.0` | Minimum ADX value required to enter trade (avoids choppy markets) |
| `ANTI_MARTINGALE_ENABLED`| `True` | Enable lot size reduction after losing trades |
| `SIZE_AFTER_1_LOSS` | `0.5` | Lot size scale after 1 loss (50% size) |
| `SIZE_AFTER_2_LOSS` | `0.25` | Lot size scale after 2+ losses (25% size) |
| `MTF_ENABLED` | `True` | Enable H1/H4 Multi-Timeframe trend filter |

---

## 💻 Installation & Setup

### System Requirements
1. **Windows 10 / 11** or **Windows Server** (Required for `MetaTrader5` Python library).
2. **Python 3.10+** (64-bit).
3. **MetaTrader 5 Client Terminal** installed and logged into your broker account.

### Step-by-Step Installation

1. **Clone or download the project:**
   ```bash
   git clone https://github.com/yourusername/gold-trading-bot.git
   cd "gold-trading-bot"
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up `.env` Configuration:**
   Copy `.env.example` to `.env` and fill in your MT5 broker login details:
   ```bash
   cp .env.example .env
   ```

4. **Verify Broker Setup in MetaTrader 5:**
   - Open MT5 Terminal -> Options -> Expert Advisors -> Enable **"Allow Algo Trading"**.
   - Ensure `XAUUSD` or your broker's gold symbol (e.g. `XAUUSDm`) is added to **Market Watch**.

---

## 🕹️ Usage Instructions

Execute the bot via `run.py`:

### 1. Paper Trading (Demo Mode)
Test the bot safely on live streaming market data without risking real money:
```bash
python run.py --mode demo
```

### 2. Live Trading (Real Account)
Executes real money orders. Requires explicit console confirmation (`CONFIRM` prompt):
```bash
python run.py --mode live
```

### 3. Historical Backtesting
Runs vectorized historical strategy testing over historical bars:
```bash
python run.py --mode backtest
```
Reports are automatically saved to `data/backtest_report.txt`.

---

## 🔒 Safety & Operational Best Practices

1. **Paper-Trade First:** Always run in `demo` mode for at least 2–4 weeks to observe behavior across different market volatility conditions.
2. **Keep MT5 Open:** The MetaTrader 5 terminal must remain open and connected to your broker server while the bot is running.
3. **Use a VPS:** For live trading, run both Python and MT5 on a low-latency Windows Virtual Private Server (VPS) near your broker's data center.
4. **Emergency Stop:** If you ever need to manually stop the bot, press `Ctrl + C` in the console window. The bot will complete its current cycle gracefully and exit. You can close any active trades manually inside the MT5 terminal or MT5 mobile app.

---

## 📄 License

This project is licensed under the MIT License — see the LICENSE file for details.
