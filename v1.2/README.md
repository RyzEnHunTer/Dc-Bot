# DCC Strategy v1.2 "TripleGuard" Package

Official standalone implementation of **DCC v1.2 TripleGuard**, housed cleanly in `d:\FOREX\DC\v1.2\`.

## 📁 Package Contents

```
d:\FOREX\DC\v1.2\
├── config_v1_2.py          # Dataclass configurations, symbol specs, and risk parameters
├── dcc_engine_v1_2.py      # Core TripleGuard strategy engine & liquidity sweep detector
├── reconciler_v1_2.py      # Daily forensic trade auditor & MT5 broker deal reconciler
├── run_v1_2_benchmark.py   # Multi-period benchmark replay against v1.1 baseline
├── test_v1_2_suite.py      # Comprehensive 9-test unit validation suite
├── SPECIFICATION.md        # Mathematical formulas, shield boundaries, and governance rules
└── README.md               # Architecture documentation & deployment guide
```

---

## 🛡️ The TripleGuard Shields Summary

1. **[FLOOR] 1H Anti-Chop Floor**:
   - `Stretch Ratio >= 0.40x ATR`
   - Rejects stagnant consolidation chop inside 1H EMA20.
2. **[CEILING] 1H ADX Exhaustion Ceiling**:
   - `1H ADX <= 45.0`
   - Kills parabolic blow-off top/bottom traps.
3. **[EXECUTION] 5M No-Chase Guard**:
   - `Chase Ratio <= 0.50x ATR`
   - Kills bloated candle chasing.
4. **[CALENDAR] Monday US Afternoon Shield**:
   - Blocks new entries on Monday between 14:00 and 18:00 UTC (negative expectancy trap hours).
   - Monday London session (06:00 to 13:00 UTC) remains active at full 1.0% risk.
5. **[BYPASS] 2H Room Rule Permanently Disabled**:
   - `check_2h_room = False`
   - Unshackles high-momentum breakout trends.

---

## 🚀 Usage & Commands

### 1. Run Unit Tests
```bash
python v1.2/test_v1_2_suite.py
```
Validates all shield boundaries, calendar blocks, and signal generation.

### 2. Run Daily Forensic Reconciliation
```bash
python v1.2/reconciler_v1_2.py --date 2026-09-18
```
Audits live MT5 broker deals against true tick replay, grouping twin tickets and validating match rates.

### 3. Run Strategy Benchmark
```bash
python v1.2/run_v1_2_benchmark.py
```
Replays 8.5 months of market data comparing v1.1 vs v1.2.
