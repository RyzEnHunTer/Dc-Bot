"""
Comprehensive 6-Month Backtest Audit WITH 3.0% Daily Circuit Breaker
Simulates the exact chronological portfolio execution:
- At 00:00 UTC each day, anchor daily starting equity.
- Track daily cumulative PnL.
- If daily loss reaches >= 3.0% of daily start equity, CIRCUIT BREAKER TRIGGERS:
  -> All subsequent trades for that day are BLOCKED.
- Resets at 00:00 UTC next day.
"""

import os
import sys
from datetime import datetime, timezone
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

def run_circuit_breaker_audit(csv_path: str, title: str, daily_dd_limit_pct: float = 3.0):
    print("=" * 80)
    print(f"AUDIT: {title}")
    print(f"Daily Drawdown Circuit Breaker: {daily_dd_limit_pct:.1f}%")
    print("=" * 80)

    df = pd.read_csv(csv_path)
    df['entry_time'] = pd.to_datetime(df['entry_time'], format='ISO8601')
    df['exit_time'] = pd.to_datetime(df['exit_time'], format='ISO8601')
    df['date'] = df['entry_time'].dt.date
    df = df.sort_values('entry_time').reset_index(drop=True)

    # 1. Chronological Replay with Dynamic Compounding and Circuit Breaker
    initial_balance = 5000.0
    current_balance = initial_balance

    days = sorted(df['date'].unique())
    
    executed_trades = []
    blocked_trades = []
    breaker_triggered_days = []

    for d in days:
        day_trades = df[df['date'] == d].sort_values('entry_time')
        day_start_equity = current_balance
        max_allowed_loss = day_start_equity * (daily_dd_limit_pct / 100.0)
        
        day_pnl = 0.0
        circuit_breaker_active = False

        for _, trade in day_trades.iterrows():
            if circuit_breaker_active:
                blocked_trades.append({
                    **trade.to_dict(),
                    'block_reason': 'Daily 3% Circuit Breaker Active'
                })
                continue

            # Trade is executed
            # Note: with dynamic compounding, risk was 1% of current equity
            # We scale the PnL to reflect actual compounded account equity at entry
            scale_factor = current_balance / trade['account_balance'] if 'account_balance' in trade else 1.0
            actual_net_pnl = trade['net_pnl'] * scale_factor
            
            current_balance += actual_net_pnl
            day_pnl += actual_net_pnl

            exec_record = trade.to_dict()
            exec_record['compounded_net_pnl'] = actual_net_pnl
            exec_record['compounded_balance'] = current_balance
            executed_trades.append(exec_record)

            # Check if circuit breaker triggers after this trade
            if day_pnl <= -max_allowed_loss:
                circuit_breaker_active = True
                breaker_triggered_days.append({
                    'date': d,
                    'start_equity': day_start_equity,
                    'day_loss': day_pnl,
                    'day_loss_pct': (abs(day_pnl) / day_start_equity) * 100.0
                })

    df_exec = pd.DataFrame(executed_trades)
    df_blocked = pd.DataFrame(blocked_trades)

    # Calculate Performance Metrics
    total_trades = len(df_exec)
    wins = len(df_exec[df_exec['compounded_net_pnl'] > 0])
    losses = len(df_exec[df_exec['compounded_net_pnl'] <= 0])
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0

    gross_profit = df_exec[df_exec['compounded_net_pnl'] > 0]['compounded_net_pnl'].sum()
    gross_loss = abs(df_exec[df_exec['compounded_net_pnl'] <= 0]['compounded_net_pnl'].sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    net_profit = current_balance - initial_balance
    roi_pct = (net_profit / initial_balance) * 100.0

    # Drawdown
    equity_series = pd.Series([initial_balance] + df_exec['compounded_balance'].tolist())
    peaks = equity_series.cummax()
    drawdowns = (peaks - equity_series) / peaks * 100.0
    max_dd = drawdowns.max()

    print(f"Initial Capital:         ${initial_balance:,.2f}")
    print(f"Final Balance:           ${current_balance:,.2f}")
    print(f"Net Profit:              +${net_profit:,.2f} (+{roi_pct:.2f}%)")
    print(f"Total Trades Taken:      {total_trades}")
    print(f"Trades Blocked by CB:    {len(df_blocked)}")
    print(f"Days CB Triggered:       {len(breaker_triggered_days)} days out of {len(days)} trading days")
    print(f"Win Rate:                {win_rate:.2f}% ({wins}W / {losses}L)")
    print(f"Profit Factor:           {profit_factor:.2f}")
    print(f"Maximum Peak Drawdown:   {max_dd:.2f}%")

    if len(breaker_triggered_days) > 0:
        print("\nDays Circuit Breaker Triggered:")
        for b in breaker_triggered_days:
            print(f"  Date: {b['date']} | Start: ${b['start_equity']:,.2f} | Loss: -${abs(b['day_loss']):,.2f} (-{b['day_loss_pct']:.2f}%)")

    # Monthly consistency
    df_exec['month'] = pd.to_datetime(df_exec['exit_time'], utc=True).dt.strftime('%Y-%m')
    print("\nMonth-by-Month Consistency with 3% Circuit Breaker:")
    print("-" * 65)
    print(f"{'Month':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Net Profit':<14} | {'Monthly ROI':<12}")
    print("-" * 65)
    
    m_equity = initial_balance
    for m, grp in df_exec.groupby('month'):
        m_wins = len(grp[grp['compounded_net_pnl'] > 0])
        m_tot = len(grp)
        m_wr = m_wins / m_tot * 100.0
        m_pnl = grp['compounded_net_pnl'].sum()
        m_roi = (m_pnl / m_equity) * 100.0
        m_equity += m_pnl
        print(f"{m:<10} | {m_tot:<8} | {m_wr:<9.1f}% | +${m_pnl:<12,.2f} | +{m_roi:<10.2f}%")
    print("-" * 65)

    return df_exec, df_blocked, current_balance, roi_pct, max_dd, len(df_blocked)

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Test on the Optimized Low-DD dataset
    csv_opt = os.path.join(base_dir, "reports", "trades_log_optimized_low_dd.csv")
    df_exec, df_blocked, final_bal, roi, max_dd, n_blocked = run_circuit_breaker_audit(
        csv_opt, "6-Month Low-DD Strategy (Jan - Jun 2026)", daily_dd_limit_pct=3.0
    )

    # Save trades log with circuit breaker
    out_csv = os.path.join(base_dir, "reports", "trades_log_with_3pct_circuit_breaker.csv")
    df_exec.to_csv(out_csv, index=False)
    print(f"\nSaved updated trades log to: {out_csv}")

    # Plot Equity Curve with Circuit Breaker
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)

    equity_curve = [5000.0] + df_exec['compounded_balance'].tolist()
    trade_nums = list(range(len(equity_curve)))

    ax1.plot(trade_nums, equity_curve, color='#00ffaa', linewidth=2.0, label='Equity Curve (3% Daily Circuit Breaker)')
    ax1.axhline(5000.0, color='#888888', linestyle='--', alpha=0.6, label='Initial Balance ($5,000)')
    ax1.set_title(f"DCC Strategy 6-Month Backtest WITH 3.0% Daily Circuit Breaker\nNet Profit: +${final_bal - 5000:,.2f} (+{roi:.1f}%) | Max DD: {max_dd:.2f}% | Blocked Trades: {n_blocked}",
                  fontsize=14, fontweight='bold', pad=12, color='white')
    ax1.set_ylabel("Account Balance ($)", fontsize=12)
    ax1.grid(True, linestyle=':', alpha=0.3)
    ax1.legend(loc='upper left', framealpha=0.8)

    # Drawdown chart
    eq_series = pd.Series(equity_curve)
    peaks = eq_series.cummax()
    dds = (peaks - eq_series) / peaks * 100.0

    ax2.fill_between(trade_nums, 0, -dds, color='#ff4444', alpha=0.5, label='Drawdown (%)')
    ax2.plot(trade_nums, -dds, color='#ff2222', linewidth=1.2)
    ax2.axhline(-3.0, color='#ffbb00', linestyle='--', alpha=0.8, label='Daily CB Threshold (-3.0%)')
    ax2.set_ylabel("Drawdown %", fontsize=12)
    ax2.set_xlabel("Trade Number", fontsize=12)
    ax2.grid(True, linestyle=':', alpha=0.3)
    ax2.legend(loc='lower left', framealpha=0.8)

    plt.tight_layout()
    out_png = os.path.join(base_dir, "reports", "equity_curve_with_3pct_circuit_breaker.png")
    plt.savefig(out_png, dpi=300)
    plt.close()
    print(f"Saved equity curve plot to: {out_png}")
