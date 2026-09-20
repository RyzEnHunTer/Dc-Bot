# DCC Strategy v1.2 "TripleGuard" — Technical Specification

## 1. System Overview
- **Codename**: DCC v1.2 TripleGuard
- **Asset Universe**: Gold (`XAUUSD`) & Nasdaq 100 (`NAS100`)
- **Primary Timeframe**: 5-Minute (Execution) with 1-Hour (Trend Bias & Volatility Normalization)
- **Engine Version**: 1.2.0-LOCKED
- **Base Capital**: $5,000.00 USD
- **Target Audience**: Prop Firm (FTMO / FundedNext / MFF) & Live Institutional MT5 Accounts

---

## 2. Mathematical Formulations & Indicator Parameters

### 2.1 Trend Bias (1-Hour Timeframe)
- **Fast EMA**: $\text{EMA}_9 = \text{EMA}(\text{Close}, 9)$
- **Slow EMA**: $\text{EMA}_{20} = \text{EMA}(\text{Close}, 20)$
- **Bias Definition**:
  $$\text{Bias}_{1H} = \begin{cases} +1 & \text{if } \text{EMA}_9 > \text{EMA}_{20} \\ -1 & \text{if } \text{EMA}_9 < \text{EMA}_{20} \\ 0 & \text{otherwise} \end{cases}$$
- **1H ADX Baseline**:
  $$\text{ADX}_{1H}(14) \ge 15.0$$
- **1H ATR**: $\text{ATR}_{1H}(14)$ calculated using standard Wilder's/RMA smoothing.

### 2.2 Execution Triggers (5-Minute Timeframe)
- **PFG Flip**:
  - Bullish: $\text{EMA}_9[t-1] - \text{EMA}_{20}[t-1] \le 0$ AND $\text{EMA}_9[t] - \text{EMA}_{20}[t] > 0$
  - Bearish: $\text{EMA}_9[t-1] - \text{EMA}_{20}[t-1] \ge 0$ AND $\text{EMA}_9[t] - \text{EMA}_{20}[t] < 0$
- **Level Confluence**:
  - Long: $\text{Close} > \text{VWAP}_{5M}$ AND $\text{Close} > \text{EMA}_{20}^{1H}$
  - Short: $\text{Close} < \text{VWAP}_{5M}$ AND $\text{Close} < \text{EMA}_{20}^{1H}$
- **Fractal Liquidity Sweep**:
  - Prior 20 bars fractal swing high/low swept within the last 8 bars and reclaimed at current bar close.

---

## 3. The TripleGuard Volatility Shields

To eliminate false signals without compromising breakout profits, DCC v1.2 deploys 3 strictly bounded mathematical filters:

### Shield 1: 1H Anti-Chop Floor
- **Formula**:
  $$\text{Stretch Ratio} = \frac{|\text{Close} - \text{EMA}_{20}^{1H}|}{\text{ATR}_{1H}} \ge 0.40$$
- **Purpose**: Rejects flat, consolidating markets where price is stuck hovering inside the 1H EMA20 chop zone.
- **Ceiling**: NONE (Permanent removal of the $\le 1.35x / 1.50x$ ceiling to ensure runaway breakout momentum is captured).

### Shield 2: 1H ADX Exhaustion Ceiling
- **Formula**:
  $$\text{ADX}_{1H} \le 45.0$$
- **Purpose**: Eliminates parabolic top/bottom blow-off traps where the trend is overextended and susceptible to violent mean-reversion whipsaws.

### Shield 3: 5M No-Chase Guard
- **Formula**:
  $$\text{Chase Ratio} = \frac{|\text{Close}_{5M} - \text{EMA}_{9}^{5M}|}{\text{ATR}_{1H}} \le 0.50$$
- **Purpose**: Guards against entering after massive bloated engulfing candles that blow out stop losses.

### Permanent Bypass: 2H Swing Room Rule
- **Status**: **PERMANENTLY DISABLED** (`check_2h_room = False`)
- **Reasoning**: Empirical autopsy proved that requiring 1.5R–2.0R clearance to prior 2H swing highs/lows filters out +$10,000 of high-probability trend continuation moves while failing to prevent choppy intraday reversals.

---

## 4. Calendar & Session Timing Governance

| Rule | Parameter | Purpose |
| :--- | :--- | :--- |
| **Trading Hours** | 06:00 to 19:00 UTC | Avoids illiquid Asian session and late-night swap rollover. |
| **Dead Trap Hours** | 09:00 & 13:00 UTC | Avoids London/NY pre-market dead zones and liquidity vacuum chop. |
| **Monday Afternoon Block** | 14:00 to 18:00 UTC | **Active**. Backtest data proved Monday US afternoon has negative expectancy (-$170) and forms deceptive weekly fakeouts. |
| **Monday Morning Session** | 06:00 to 13:00 UTC | **Enabled**. Traded at full 1.0% risk. |
| **Friday Trading** | 06:00 to 19:00 UTC | **Unchanged**. Friday is the strategy's most profitable day (64.4% win rate, +$3,135 profit). |

---

## 5. Risk Management & Position Sizing Architecture

### 5.1 Twin-Ticket Split
- **Total Risk**: 1.0% of current equity per trade ($50 on $5,000).
- **Ticket A (Partial Profit)**:
  - Lot Size: 50% of Total Lots
  - Take Profit: TP1 (1.4R on XAUUSD, 1.5R on NAS100)
- **Ticket B (Trend Runner)**:
  - Lot Size: 50% of Total Lots
  - Take Profit: TP2 (2.2R on XAUUSD, 2.0R on NAS100)
- **Breakeven Shift**:
  - The instant Ticket A hits TP1, Ticket B Stop Loss is automatically shifted to $\text{Entry Price} + \text{Spread}$.

### 5.2 Stop Loss & Take Profit Multipliers
| Symbol | ATR SL Multiplier | TP1 Ratio | TP2 Ratio | Max Allowed Spread |
| :--- | :---: | :---: | :---: | :---: |
| **XAUUSD** | 0.90x $\text{ATR}_{1H}$ | 1.40R | 2.20R | 0.65 ($0.65) |
| **NAS100** | 1.00x $\text{ATR}_{1H}$ | 1.50R | 2.00R | 7.00 pts |

### 5.3 Daily Portfolio Circuit Breaker
- **Daily Loss Limit**: 3.0% of starting daily equity ($150 on $5,000 account).
- **Max Consecutive Daily Stop-Outs**: 2 Trades.
- **Action**: Trading is locked for the remainder of the calendar day upon reaching the limit.
