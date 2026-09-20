# DCC Institutional Trading System — Production Lock Specification

## Status: LOCKED & FROZEN FOR PRODUCTION (v1.2.0-ApexHunter)
**Release Date:** September 20, 2026  
**Active Production Architecture:** DCC v1.2 ApexHunter Dual-Gear Engine  
**Verification Status:** 100% PASS (35/35 Unit Tests Verified, Zero Prop Firm Breaches)  

---

### 1. Locked Production Core Files
The following files constitute the verified production trading environment and are **STRICTLY IMMUTABLE**:

| File | Purpose | Protection Level |
| :--- | :--- | :--- |
| `live_bot.py` | Production MetaTrader 5 execution bot (Dual-gear, Bar-Close mode, Twin orders, 3% CB) | **LOCKED** |
| `nightly_reconciler.py` | Automated tick-by-tick forensic reconciliation & deal auditor | **LOCKED** |
| `storage_manager.py` | Rolling 7-day disk retention & Google Drive archival | **LOCKED** |
| `notifier.py` | Non-blocking Discord/Telegram remote notification engine | **LOCKED** |
| `live_server.py` | Web visualizer & telemetry API server | **LOCKED** |
| `dcc_engine.py` | Core mathematical DCC indicator & bias computation engine | **LOCKED** |
| `news_engine.py` | High-impact economic news blackout shield | **LOCKED** |
| `mt5_data.py` | MetaTrader 5 institutional rate & tick data provider | **LOCKED** |
| `visualizer/live_chart.html` | TradingView visualizer, telemetry radar & audit modal | **LOCKED** |
| `v1.2/APEX_HUNTER_SPECIFICATION.md` | Official DCC ApexHunter Technical Architecture Specification | **LOCKED** |
| `v1.2/APEXHUNTER_JOURNAL.md` | Institutional Forensic Research & Parameter Sensitivity Journal | **LOCKED** |
| `v1.2/run_apexhunter_benchmark.py` | Production Dual-Gear Benchmark Verification Engine | **LOCKED** |
| `v1.2/APEXHUNTER_BENCHMARK.csv` | Immutable Multi-Month MT5 Broker Benchmark Ledger | **LOCKED** |
| `final_dcc_strategy/` | Immutable golden reference backtest engines | **LOCKED** |

---

### 2. The ApexHunter Dual-Gear Policy
1. **Gear 1 (Challenge Phase)**:
   - Dynamic Regime-Adaptive Risk: **1.30%** in Trend Regimes (`ADX >= 22.0` and `Stretch >= 0.85`), **1.00%** in Chop.
   - Smart Killzone Filter: Active (London 09:00 UTC & US 13:00 UTC allowed only when `Stretch >= 1.10`).
   - Stop-on-Pass Protocol: Immediately halts all trading when **+14.0% ($5,700)** target is secured.
   - Safety: Hard Pre-Trade 3.0% Daily Circuit Breaker.
2. **Gear 2 (Funded Phase)**:
   - Static Fixed Risk: **1.00%** per trade ($50.00 on $5,000.00 base capital).
   - Smart Killzone Filter: Active (captures +$805 edge at 68.4% win rate).
   - Bi-Weekly Payout Ledger: 14-day 80% payout cycles with $5,000 capital resets.
   - Safety: Hard Pre-Trade 3.0% Daily Circuit Breaker.

---

### 3. Experimentation & Change Control Protocol
To protect production stability, **NO direct modifications or ad-hoc testing are permitted on locked production files**.

All future research, enhancements, and experiments must be isolated inside the `experiments/` or `scratch/` directory:
- **Promotion to Production**: Any experimental logic may only be promoted after:
  1. Multi-month tick-by-tick real broker backtest verification across all regimes.
  2. Parameter sensitivity audit proving absence of curve fitting (wide parameter plateau).
  3. 100% unit test pass rate (35/35 tests passing).
  4. Explicit approval from the user.
