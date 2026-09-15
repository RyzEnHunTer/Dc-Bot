# PRODUCTION CODEBASE LOCK & EXPERIMENTATION PROTOCOL

## Core Rule
The root production codebase of the DCC Trading Bot is **OFFICIALLY LOCKED AND FROZEN**. 
Under NO circumstances should any agent or developer directly edit, overwrite, or refactor the root production files for testing, optimization, or experimental features.

## Locked Production Files:
- live_bot.py (Live execution engine)
- nightly_reconciler.py (Automated nightly forensic reconciler)
- storage_manager.py (Cloud archival and disk pruning)
- notifier.py (Remote Telegram/Discord notification engine)
- live_server.py (Web visualizer API server)
- dcc_engine.py (Core DCC calculation strategy engine)
- news_engine.py (High-impact economic calendar shield)
- mt5_data.py (MetaTrader 5 data provider)
- visualizer/live_chart.html (Production dashboard and chart UI)
- final_dcc_strategy/ (Immutable golden baseline backtest engines)

## Protocol for Improvements & Experiments:
1. **Dedicated Directory**: ALL new experiments, parameter tuning, ML filters, alternative entry modes, or experimental features MUST be created in the experiments/ directory.
2. **Isolation**: When testing improvements, copy or import from production modules into experiments/<feature_name>/ without modifying the root files.
3. **Verification Before Promotion**: An experimental feature can only be promoted to production after extensive out-of-sample backtesting, 100% test pass rate, and explicit user approval.
