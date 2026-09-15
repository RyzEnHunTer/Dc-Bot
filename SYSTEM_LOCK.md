# DCC Institutional Trading System - Production Lock Specification

## Status: LOCKED & FROZEN FOR PRODUCTION (v1.0.0-production)
**Release Date:** September 15, 2026
**Commit Hash:** 41ae155

---

### 1. Locked Production Core Files
The following files constitute the verified production trading environment and are **STRICTLY IMMUTABLE**:

| File | Purpose | Protection Level |
| :--- | :--- | :--- |
| live_bot.py | Production MetaTrader 5 execution bot (Bar-Close mode, Twin orders, 3% CB) | **LOCKED** |
| 
ightly_reconciler.py | Automated tick-by-tick forensic reconciliation & deal auditor | **LOCKED** |
| storage_manager.py | Rolling 7-day disk retention & Google Drive archival | **LOCKED** |
| 
otifier.py | Non-blocking Discord/Telegram remote notification engine | **LOCKED** |
| live_server.py | Web visualizer & telemetry API server | **LOCKED** |
| dcc_engine.py | Core mathematical DCC indicator & bias computation engine | **LOCKED** |
| 
ews_engine.py | High-impact economic news blackout shield | **LOCKED** |
| mt5_data.py | MetaTrader 5 institutional rate & tick data provider | **LOCKED** |
| isualizer/live_chart.html | TradingView visualizer, telemetry radar & audit modal | **LOCKED** |
| inal_dcc_strategy/ | Immutable golden reference backtest engines | **LOCKED** |

---

### 2. Experimentation Protocol
To protect production stability, **NO direct modifications or ad-hoc testing are permitted on root files**.

All future research, enhancements, and experiments must be isolated inside the experiments/ directory:
- **experiments/**: Create subfolders for specific research topics (e.g. experiments/ml_filter/, experiments/trailing_stop_v2/).
- **Imports**: Experiments should import from or copy root modules as isolated testbeds.
- **Promotion**: Any experimental logic may only be promoted to production after:
  1. Multi-month tick-by-tick backtest verification.
  2. 100% unit test pass rate.
  3. Explicit approval from the user.
