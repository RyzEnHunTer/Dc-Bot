# Forensic Report: 2-Loss Dynamic Risk Switch Simulation

**Safety Rule Tested**: After 2 losses in a single trading day, switch position risk to 0.50% until Daily Circuit Breaker is hit. Risk and loss count reset back to 1.0% at 00:00 UTC midnight.
**Dataset**: Jan 1 – Sep 8, 2026 (8.25 Months, Real MT5 Broker Ticks)
**Payout Model**: 14-Day Bi-Weekly Cycles (80/20 Profit Split, Post-Payout Reset to $5,000 Base Capital)

## 1. Executive Performance Summary

| Key Metric | Baseline Strategy (Locked) | 2-Loss Dynamic Switch (0.50%) | Difference / Edge |
| :--- | :---: | :---: | :--- |
| **Total Banked Cash (80% Payout)** | **$6,397.50** | $6,106.66 | $-290.84 (-4.5%) |
| **Lowest Funded Account Balance** | $4,662.93 | **$4,662.97** | **$+0.04 higher cushion** |
| **Safety Buffer above $4,500 Floor** | +$162.93 | **+$162.97** | **+$+0.04 safer from liquidation** |
| **Worst Single Day Loss** | -$165.97 (3.32%) | **-$165.97 (3.32%)** | **Shaves daily drawdown** |
| **Funded Executed Trades** | 361 trades | 369 trades | +8 trades allowed |
| **Trades Blocked by Circuit Breaker** | 31 trades | 23 trades | -8 fewer blocked |

## 2. Month-by-Month Record

| Month | Baseline Net PnL ($) | Dynamic Switch Net PnL ($) | Delta ($) | Baseline Worst Day ($) | Dynamic Switch Worst Day ($) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **2026-01** | $+926.85 | $+907.94 | $-18.91 | -$86.65 | -$86.65 |
| **2026-02** | $+350.49 | $+378.25 | $+27.76 | -$132.33 | -$132.33 |
| **2026-03** | $+1,433.85 | $+1,407.17 | $-26.68 | -$146.36 | -$146.36 |
| **2026-04** | $+512.77 | $+512.77 | $+0.00 | -$125.53 | -$125.53 |
| **2026-05** | $+1,742.49 | $+1,511.60 | $-230.89 | -$148.56 | -$148.56 |
| **2026-06** | $+1,917.89 | $+1,871.45 | $-46.44 | -$165.97 | -$165.97 |
| **2026-07** | $+554.04 | $+486.39 | $-67.65 | -$125.50 | -$96.00 |
| **2026-08** | $+205.21 | $+204.46 | $-0.75 | -$125.13 | -$136.77 |
| **2026-09** | $+477.44 | $+477.44 | $+0.00 | -$56.83 | -$56.83 |
