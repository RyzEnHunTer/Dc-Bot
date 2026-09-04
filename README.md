# Institutional DCC Algorithmic Trading System

A zero-latency, high-precision algorithmic trading bot and backtesting engine for MetaTrader 5 (MT5), implementing the **DCC (Dynamic Compression & Trend Confirmation) Strategy** on **Gold (XAUUSD)** and **Nasdaq (NAS100)**.

---

## 📁 Workspace Architecture

```
d:\FOREX\DC\
│
├── live_bot.py                       # [MAIN] Institutional Live/Demo MT5 Trading Bot
├── dcc_engine.py                     # [CORE] Strategy Engine (EMA / VWAP / ADX / Bias)
├── mt5_data.py                       # [CORE] MetaTrader 5 Live & Historical Data Provider
├── run_6month_backtest.py            # [MAIN] 6-Month High-Precision Tick Backtest Engine
├── README.md                         # Workspace Documentation & Quick Start Guide
│
├── tests/                            # Live MT5 Verification & Diagnostic Suite
│   ├── test_mt5_live_order.py        # Complete order lifecycle & execution audit
│   ├── test_live_2min_tick_stream.py # 2-minute live tick streaming & latency audit
│   ├── test_valid_sl_mod.py          # Gold SL modification & stops_level test
│   ├── test_valid_sl_mod_nas.py      # Nasdaq SL modification & stops_level test
│   ├── check_stops_level.py          # Broker trade_stops_level inspector
│   └── check_open_positions.py       # Live account open position auditor
│
├── reports/                          # Audit Reports, Trade Logs & Equity Curves
│   ├── trades_log_strict_6month.csv  # 389 trades log (6-Month Strict, Max 2 Trades)
│   ├── trades_log_optimized_low_dd.csv # 347 trades log (4.16% Low-DD Session Filter)
│   ├── trades_log_jan_jun_2026.csv   # Initial backtest trades log
│   ├── portfolio_trades_log.csv      # Initial portfolio trades log
│   ├── equity_curve_strict_jan_jun_2026.png
│   ├── equity_curve_optimized_low_dd.png
│   ├── equity_curve_jan_jun_2026.png
│   └── portfolio_equity_curve.png
│
├── docs/                             # Strategy Manuals, Playbooks & Specifications
│   ├── DCC STRATEGY.pdf              # Official DCC Strategy Rulebook
│   ├── Marco Trades Playbook.pdf     # Marco Trades Playbook
│   ├── strategy_rules_specification.md # Formal Strategy Architecture Spec
│   └── shahzeb trades.txt            # Trade notes & trade logs
│
├── research/                         # Experimental Tuning & Historical Scripts
│   ├── backtester.py                 # Core historical backtesting engine
│   ├── portfolio_backtester.py       # Multi-asset portfolio backtester
│   ├── data_ingestion.py             # CSV-to-Parquet conversion utility
│   ├── optimize_sessions.py          # Session hour optimization script
│   ├── monthly_audit.py              # Monthly performance breakdown script
│   ├── forex_tuner.py                # Parameter grid search tuner
│   └── tune_gold.py                  # Gold-specific parameter optimizer
│
├── Data/                             # Raw Historical Tick Data (CSV / Cache)
└── data_cache/                       # Partitioned Parquet Caches
```

---

## 🚀 Quick Start Guide

### 1. Run the Live Trading Bot
Ensure MetaTrader 5 is running and logged into your trading account.

```powershell
# Launch with the Interactive Control Menu (Recommended):
python live_bot.py

# Launch directly without menu using saved account settings:
python live_bot.py --auto

# Launch in Paper/Dry-Run mode directly:
python live_bot.py --auto --dry-run
```

When you launch `python live_bot.py`, the bot:
1. **Detects the active MT5 account** (e.g. `213877054`).
2. **Prompts you once** if it's a new account for your **Daily DD Limit %** (default 3.0%) and **Max Total DD %** (default 8.0%), and remembers them permanently in `bot_accounts_config.json`.
3. Displays the **Interactive Control Menu**:
   * `[1]` Start LIVE Trading (Real MT5 Orders)
   * `[2]` Start PAPER Trading (Safe Simulation)
   * `[3]` Edit Risk & Drawdown Rules (Update Daily DD, Max DD, or Risk %)
   * `[4]` Configure Remote Notifications (Telegram Bot or Discord Webhook)
   * `[5]` Toggle 5M Liquidity Sweep Confluence (Turtle Soup Filter: ON / OFF)
   * `[6]` Exit

### 2. Run the 6-Month High-Precision Tick Backtest
Replays millions of real ticks with dynamic 1% risk compounding, twin-ticket TP1/TP2 execution, and breakeven stop loss.

```powershell
python run_6month_backtest.py
```

### 3. Run MT5 Live Diagnostics
Verify order execution, tick streaming latency, and broker stop-level compatibility at any time:

```powershell
# Audit complete order send, SL/TP, and close:
python tests/test_mt5_live_order.py

# Test 2-minute live tick streaming latency:
python tests/test_live_2min_tick_stream.py

# Check currently open positions on your account:
python tests/check_open_positions.py
```

---

## ⚡ Core Strategy Parameters

| Parameter | Gold (`XAUUSD`) | Nasdaq (`NAS100`) | Description |
| :--- | :--- | :--- | :--- |
| **Timeframe** | 5M Entry / 1H Bias | 5M Entry / 1H Bias | Multi-Timeframe Alignment |
| **Trend Bias** | 1H EMA 20 & 1H ADX $\ge 15.0$ | 1H EMA 20 & 1H ADX $\ge 15.0$ | Macro Trend Direction |
| **Entry Trigger** | 5M EMA 9 / EMA 20 Flip | 5M EMA 9 / EMA 20 Flip | Micro Momentum Cross |
| **Value Filter** | 5M Session VWAP | 5M Session VWAP | Institutional Value Line |
| **Max Concurrent Trades** | **Max 1 Position** | **Max 1 Position** | Max 2 Portfolio Trades Total |
| **Risk Per Trade** | **1.0%** of Current Equity | **1.0%** of Current Equity | Dynamic Compounding |
| **Stop Loss (SL)** | $0.90 \times \text{ATR}_{1h}$ | $1.00 \times \text{ATR}_{1h}$ | Dynamic Volatility Stop |
| **Ticket A (50%)** | **1.4 R** | **1.5 R** | Partial Profit Lock |
| **Ticket B (Runner)** | **2.2 R** | **2.0 R** | Trend Continuation Target |
| **Breakeven Shift** | Entry + Spread | Entry + Spread | Active immediately on TP1 fill |
| **Dead-Hour Filter** | Skip 09:00 & 13:00 UTC | Skip 09:00 & 13:00 UTC | Avoids false breakouts (4.16% Max DD) |
