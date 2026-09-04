# DCC Strategy: Complete Technical Specification

## Overview & Goal
The **DCC Strategy** is a mechanical, multi-timeframe day trading methodology engineered specifically for prop firm challenges and steady payouts. It combines institutional trend following, momentum filtering, volume-weighted pricing, and volatility-adjusted risk sizing.

- **Primary Asset**: `XAUUSD` (Gold) / Major FX pairs (`EURUSD`, `GBPUSD`, `GBPJPY`, `USDJPY`).
- **Target Win Rate**: 50% – 60%.
- **Risk to Reward (RR)**: 1:1.5 to 1:2.0.
- **Trade Duration**: Intraday (1 – 4 hours average hold). Zero overnight exposure.
- **Trade Frequency**: ~8 – 12 trades per week (~1 – 2 per day across watched pairs).

---

## 1. Multi-Timeframe Architecture

The system operates across three synchronized timeframes:
```
2H (Map / Context)  -->  1H (Bias & Volatility)  -->  5M (PFG Execution)
```

### 1.1. 2H Timeframe (Map / Key Zones)
- **Daily Open Line**: The opening price of the current trading day (00:00 UTC). Functions as intraday support/resistance and dynamic defense for Stop Loss.
- **Swing High / Swing Low**: The most recent local swing high and swing low on the 2H chart (calculated over recent 2H bars, e.g. rolling 20-30 bars / fractal pivot points).
- **Target Clearance Rule (Room to Target)**:
  - For **Longs**: Distance from potential entry to the nearest 2H Swing High must be $\ge$ planned Take Profit distance ($1.5 \times SL$ or $2.0 \times SL$).
  - For **Shorts**: Distance from potential entry to the nearest 2H Swing Low must be $\ge$ planned Take Profit distance.
  - If a 2H major obstacle is within the TP zone, the trade is skipped or scaled to 1:1.5 max.

### 1.2. 1H Timeframe (Trend Bias, Strength & Volatility)
- **Exponential Moving Averages (EMA)**:
  - **Fast EMA**: 9-period EMA.
  - **Slow EMA**: 20-period EMA.
  - **Bullish Bias**: `1H EMA(9) > 1H EMA(20)` with clear separation (not entangled or flat).
  - **Bearish Bias**: `1H EMA(9) < 1H EMA(20)` with clear separation.
- **1H 20 EMA Level**:
  - Horizontal reference line taken from the close value of the latest completed 1H 20 EMA.
  - Acts as dynamic structural support/resistance on the 5M chart.
- **ADX (Average Directional Index)**:
  - Period: 14.
  - Threshold: $ADX \ge 25$ (minimum baseline 20, ideal $\ge 25$). Confirms the market has sufficient momentum.
- **ATR (Average True Range)**:
  - Period: 14.
  - Calculates volatility-based stop loss size:
    $$\text{Stop Loss Distance (Price Units)} = 0.9 \times \text{1H ATR(14)}$$

### 1.3. 5M Timeframe (Execution / PFG Entry Model)
The PFG model standardizes entries mechanically:
- **P (Pullback)**:
  - When 1H bias is Bullish: Price pulls back downward and 5M EMA(9) falls below 5M EMA(20) (or price retraces towards/below EMAs).
  - When 1H bias is Bearish: Price pulls back upward and 5M EMA(9) rises above 5M EMA(20).
- **F (Flip)**:
  - 5M EMA(9) crosses back over 5M EMA(20) in the direction of the 1H bias:
    - **Long Flip**: `ta.crossover(5M EMA 9, 5M EMA 20)`
    - **Short Flip**: `ta.crossunder(5M EMA 9, 5M EMA 20)`
  - Trigger occurs on the **close** of the 5M candle where the crossover completes.
- **G (Go Filters Checklist)**:
  At the close of the flip candle, all of the following conditions must evaluate to `True`:
  1. **1H Bias Alignment**: 1H EMA(9) > 1H EMA(20) for Long; 1H EMA(9) < 1H EMA(20) for Short.
  2. **1H Trend Strength**: 1H ADX(14) $\ge 25$ (or configurable $\ge 20$).
  3. **Session VWAP Position**:
     - Long: $Close_{5M} > VWAP_{session}$
     - Short: $Close_{5M} < VWAP_{session}$
  4. **1H 20 EMA Level Filter**:
     - Long: $Close_{5M} > \text{1H EMA 20 Level}$
     - Short: $Close_{5M} < \text{1H EMA 20 Level}$
  5. **Daily Open Defense (Optional / Recommended)**:
     - Long: $Close_{5M} > \text{Daily Open}$
     - Short: $Close_{5M} < \text{Daily Open}$
  6. **Room to Target**:
     - Long: $\text{Nearest 2H Swing High} - Close_{5M} \ge TP_{distance}$
     - Short: $Close_{5M} - \text{Nearest 2H Swing Low} \ge TP_{distance}$

---

## 2. Risk Management & Order Execution

### 2.1. Position Sizing
- Account Risk: Configurable (default $0.5\%$ to $1.0\%$ of account equity).
$$\text{Lot Size} = \frac{\text{Account Equity} \times \text{Risk \%}}{\text{Stop Loss Points} \times \text{Tick Value}}$$

### 2.2. Order Pricing & Slippage
- **Long Entry**: Bought at market `Ask`.
  $$SL = \text{Entry Ask} - (0.9 \times \text{1H ATR})$$
  $$TP = \text{Entry Ask} + (\text{RR} \times (\text{Entry Ask} - SL))$$
- **Short Entry**: Sold at market `Bid`.
  $$SL = \text{Entry Bid} + (0.9 \times \text{1H ATR})$$
  $$TP = \text{Entry Bid} - (\text{RR} \times (SL - \text{Entry Bid}))$$
- **Slippage Simulation**: Executed against actual tick `bid` and `ask` prices. Ticks are checked sequentially to trigger stop out or target hit.

### 2.3. Trade Management & Exits
1. **Target Hit**: Price touches or crosses TP.
2. **Stop Loss Hit**: Price touches or crosses SL.
3. **Trailing Stop / Break-Even**: Move SL to breakeven once profit reaches $+1.0$ RR.
4. **End of Day (EOD) Force Close**: Close any remaining open trades at 21:00 UTC (no overnight hold).

---

## 3. Session & Day Timing Filters

- **London Trading Window**: 06:00 UTC to 09:00 UTC.
- **New York Trading Window**: 12:00 UTC to 14:00 UTC.
- **Disallowed Sessions**:
  - Monday London session (06:00–09:00 UTC on Mondays): Skip entries as weekly trends establish.
  - Friday New York session (12:00–14:00 UTC on Fridays): Skip entries ahead of weekly close.
  - Non-Farm Payrolls (NFP): First Friday of every calendar month.
