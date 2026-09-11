# Official DCC Strategy Specification (Institutional Final Version)

## 1. System Overview
The **DCC (Dynamic Compression & Confirmation) Strategy** is a multi-timeframe, rule-based algorithmic trading system engineered for prop firm evaluation and steady payout harvesting.

- **Primary Assets**: Gold (`XAUUSD`) & Nasdaq (`NAS100`).
- **Timeframe Stack**:
  - `1H`: Macro Trend Bias (`EMA 9` vs `EMA 20`), Volatility (`ATR 14`), and Strength (`ADX 14`).
  - `5M`: Micro Execution (`EMA 9` / `EMA 20` Crossover, Session `VWAP`, and `5M Liquidity Sweep`).
- **Initial Account Capital**: $5,000.00
- **Risk Per Trade**: 1.0% of current equity (Dynamic Compounding).

---

## 2. Entry Confluence Checklist (PFG Model)

Every trade must satisfy the strict **Pullback - Flip - Go (PFG)** model at candle close:

1. **Macro Trend Bias (1H)**:
   - Longs: `1H EMA 9 > 1H EMA 20`
   - Shorts: `1H EMA 9 < 1H EMA 20`
2. **Trend Momentum (1H ADX)**:
   - `1H ADX(14) >= 15.0` (eliminates dead market regimes).
3. **Micro Flip (5M Crossover)**:
   - Longs: `5M EMA 9` crosses over `5M EMA 20` on candle close.
   - Shorts: `5M EMA 9` crosses under `5M EMA 20` on candle close.
4. **Institutional Value Filter (Session VWAP)**:
   - Longs: `Close > Session VWAP`
   - Shorts: `Close < Session VWAP`
5. **Structural Defense (1H EMA 20 Level)**:
   - Longs: `Close > 1H EMA 20 Level`
   - Shorts: `Close < 1H EMA 20 Level`
6. **5M Liquidity Sweep (Turtle Soup Confluence)**:
   - Prior to crossover, price must have swept the recent 5M swing liquidity pool (false breakout trap confirmation).
7. **Session Timing Window**:
   - Active entries between **06:00 UTC and 21:00 UTC** (London & New York sessions).
   - Asia range formation (21:00–06:00 UTC) paused for fresh entries.

---

## 3. Position Sizing & Order Execution

| Parameter | Gold (`XAUUSD`) | Nasdaq (`NAS100`) |
| :--- | :--- | :--- |
| **Stop Loss (SL)** | $0.90 \times \text{ATR}_{1h}$ | $1.00 \times \text{ATR}_{1h}$ |
| **Ticket A (50% Partial)** | **+1.4 R** | **+1.5 R** |
| **Breakeven Shift** | Entry + Spread (Immediate upon TP1 fill) | Entry + Spread (Immediate upon TP1 fill) |
| **Ticket B (50% Runner)** | **+2.2 R** | **+2.0 R** |
| **Commission Deduction** | $5.00 / lot | $5.00 / lot |
| **Position Holding** | **Continuous Multi-Session (No artificial EOD exit)** | **Continuous Multi-Session (No artificial EOD exit)** |

---

## 4. Capital Preservation & Prop Firm Circuit Breaker

1. **3.0% Daily Loss Circuit Breaker**:
   - Monitored continuously in real time.
   - If cumulative daily portfolio drawdown reaches **>= 3.0%** of the daily starting balance (00:00 UTC anchor), the system halts all new trade entries for the remainder of the trading day.
   - Entries automatically resume at 06:00 UTC the following session.
2. **Maximum Concurrent Positions**:
   - Max 1 open trade on Gold and Max 1 open trade on Nasdaq (Max 2 total concurrent portfolio positions).
3. **Prop Firm Drawdown Floor Safety**:
   - The account balance **never dropped below the initial $5,000 capital** throughout the entire 2026 backtest (0.00% Max Base Loss).
