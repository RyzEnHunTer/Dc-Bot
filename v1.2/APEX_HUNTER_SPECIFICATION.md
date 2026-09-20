## Strategy Version Hierarchy
- **v1.0 (Baseline DCC)**: Original reference continuous strategy with baseline filters (All 471 trades).
- **v1.1 (Early ApexHunter)**: Early evolution adding TripleGuard volatility shields (Anti-Chop Floor >= 0.40x ATR, ADX Ceiling <= 45.0, No-Chase Guard <= 0.50x ATR, Monday PM block), with hard daily killzone pause (hours 09:00 & 13:00 UTC paused daily).
- **v1.2 (DCC ApexHunter - Flagship)**: The production flagship engine with Smart Hybrid Killzone (09:00 & 13:00 UTC allowed when Stretch >= 1.10x ATR), Dual-Gear Architecture (Dynamic 1.30%/1.00% Challenge -> Static 1.00% Funded), Dynamic Cushion Sizing, and TripleGuard.

---

## 1. System Overview
- **Official System Name**: **DCC ApexHunter (v1.2)**
- **Operating Architecture**: Dual-Gear Hybrid Prop Firm Engine
- **Target Assets**: Gold (`XAUUSD`) & Nasdaq 100 (`NAS100`)
- **Execution Engine**: 5-Minute Execution + 1-Hour Trend & Volatility Normalization
- **Base Capital**: $5,000.00 USD
- **Prop Firm Target Application**: Two-Phase & Single-Phase Institutional Accounts (FTMO, FundedNext, MFF)

---

## 2. The Dual-Gear Operating Architecture

| Parameter | Gear 1: Challenge Phase ("Sprint Gear") | Gear 2: Funded Phase ("Harvest Gear") |
| :--- | :--- | :--- |
| **Primary Goal** | Fast, safe challenge pass (+14% / $5,700) | Maximum capital preservation & consistent bi-weekly payouts |
| **Risk Model** | **Dynamic Regime-Adaptive Risk** (1.30% Trend / 1.00% Base) | **Static Fixed 1.00% Risk** ($50 per trade on $5,000 base) |
| **Smart Killzone Filter**| **Active** (Hours 09:00 & 13:00 UTC require `stretch_ratio >= 1.10`) | **Active** (Hours 09:00 & 13:00 UTC require `stretch_ratio >= 1.10`) |
| **Monday PM Filter** | **Active** (14:00 - 18:00 UTC paused) | **Active** (14:00 - 18:00 UTC paused) |
| **Trading Halt Rule** | **Stop-on-Pass Protocol** (Halts immediately at +$700 / $5,700) | Continuous (14-day bi-weekly payout cycles) |
| **Daily Circuit Breaker**| **Hard 3.0% Pre-Trade Cap** ($150 limit) | **Hard 3.0% Pre-Trade Cap** ($150 limit) |
| **Pass Time (8.25-Month)**| **6 days (9 trades)** | N/A (Funded live ledger) |
| **Banked Cash (80%)** | Unlocks funded contract | **$7,125.44** transferred to bank |
| **Worst Single Day** | **-$132.33 (2.65%)** | **-$132.33 (2.65%)** |
| **Prop Firm Safety Score**| **100% ZERO BREACH** | **100% ZERO BREACH** |

---

## 3. Mathematical Rules & Indicators

### 3.1 Smart Hybrid Killzone Filter
- **Rule**:
  $$\text{At Hours 09:00 \& 13:00 UTC:} \quad \text{Allow entry ONLY IF } \text{Stretch Ratio} \ge 1.10$$
  $$\text{Stretch Ratio} = \frac{|\text{Close}_{5M} - \text{EMA}_{20}^{1H}|}{\text{ATR}_{1H}}$$
- **Empirical Justification**:
  - $\text{Stretch Ratio} < 1.10$ (Open Trap / Chop): 15.0% Win Rate, **-$842.58 Loss** $\rightarrow$ **BLOCKED**.
  - $\text{Stretch Ratio} \ge 1.10$ (Momentum Rocket): **68.4% Win Rate**, **+$805.46 Profit** $\rightarrow$ **CAPTURED**.

### 3.2 Dynamic Regime Detection (Challenge Phase Only)
$$\text{Market Regime} = \begin{cases} 
\text{TREND REGIME} & \text{if } \text{ADX}_{1H} \ge 22.0 \text{ and } \text{Stretch Ratio} \ge 0.85 \text{ and } \text{WinRate}_{\text{last 3 trades}} \ge 50\% \\
\text{CHOP REGIME} & \text{otherwise}
\end{cases}$$

- **Trend Regime**: Risk = **1.30%** ($\approx \$65$ on $\$5,000$ base).
- **Chop Regime / Post-Loss**: Risk = **1.00%** ($\approx \$50$ on $\$5,000$ base).

### 3.3 Static Fixed Risk (Funded Phase Only)
- Fixed risk per trade: **1.00%** ($\$50.00$ on $\$5,000.00$ base capital).
- Sizing does not fluctuate with intraday floating profits, ensuring stable drawdown defense.

### 3.4 Pre-Trade 3.0% Daily Circuit Breaker
Before ANY entry request is sent to the broker:
$$\text{Loss}_{\text{closed today}} + \text{Risk}_{\text{existing open}} + \text{Risk}_{\text{new trade}} \le \text{StartOfDayBalance} \times 0.030$$
If this threshold is exceeded, the trade is **instantly rejected**.

---

## 4. Benchmark Verification Files

- **Benchmark Runner**: [`v1.2/run_apexhunter_benchmark.py`](file:///d:/FOREX/DC/v1.2/run_apexhunter_benchmark.py)
- **Official Scorecard CSV**: [`v1.2/APEXHUNTER_BENCHMARK.csv`](file:///d:/FOREX/DC/v1.2/APEXHUNTER_BENCHMARK.csv)
- **Dataset**: 342 real-broker continuous trades (Jan 1 – Sep 8, 2026).
