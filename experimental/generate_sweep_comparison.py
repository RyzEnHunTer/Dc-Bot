"""
Generate complete side-by-side comparison data and chart for:
Version A: Standard DCC Strategy (Without Liquidity Sweep)
Version B: DCC Strategy WITH 5M Liquidity Sweep Confluence
"""

import os
import sys
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mt5_data import MT5DataProvider
from experimental.test_5m_liquidity_sweep import detect_liquidity_sweep

def run_full_comparison():
    dp = MT5DataProvider()
    start_date = datetime(2026, 1, 2)
    end_date = datetime(2026, 6, 30, 23, 59, 59)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(base_dir, "reports", "trades_log_with_3pct_circuit_breaker.csv")
    df_trades = pd.read_csv(csv_path)
    df_trades['entry_time'] = pd.to_datetime(df_trades['entry_time'], format='ISO8601')
    df_trades['exit_time'] = pd.to_datetime(df_trades['exit_time'], format='ISO8601')
    df_trades['month'] = df_trades['exit_time'].dt.strftime('%Y-%m')

    # Fetch 5M rates
    rates_5m = {}
    for s in ["XAUUSD", "NAS100"]:
        df_m5, _, _ = dp.fetch_multi_timeframe_rates(s, start_date, end_date, warmup_days=20)
        rates_5m[s] = df_m5

    # Tag each trade with sweep status
    sweep_status = []
    for _, trade in df_trades.iterrows():
        sym = trade['symbol']
        e_time = trade['entry_time']
        direction = 1 if trade['direction'] == 'BUY' else -1
        df_m = rates_5m[sym]

        matching = df_m.index[df_m.index <= e_time]
        if len(matching) < 20:
            sweep_status.append(False)
            continue
        entry_idx = len(matching) - 1

        has_sweep, _, _ = detect_liquidity_sweep(
            df_m, entry_idx, direction,
            swing_lookback=20,
            pullback_window=8
        )
        sweep_status.append(has_sweep)

    df_trades['has_sweep'] = sweep_status

    # Simulate Compounded Performance for Both Versions
    def simulate_version(sub_trades, name):
        init_bal = 5000.0
        bal = init_bal
        equity_hist = [init_bal]
        trade_logs = []

        for _, t in sub_trades.iterrows():
            # 1% risk per trade dynamic compounding
            scale = bal / t['account_balance'] if 'account_balance' in t else 1.0
            pnl = t['net_pnl'] * scale
            bal += pnl
            equity_hist.append(bal)

            rec = t.to_dict()
            rec['sim_pnl'] = pnl
            rec['sim_bal'] = bal
            trade_logs.append(rec)

        res_df = pd.DataFrame(trade_logs)
        tot = len(res_df)
        wins = len(res_df[res_df['sim_pnl'] > 0])
        wr = wins / tot * 100.0 if tot > 0 else 0
        pnl_tot = bal - init_bal
        roi = pnl_tot / init_bal * 100.0

        gp = res_df[res_df['sim_pnl'] > 0]['sim_pnl'].sum()
        gl = abs(res_df[res_df['sim_pnl'] <= 0]['sim_pnl'].sum())
        pf = gp / gl if gl > 0 else 999.0

        eq_s = pd.Series(equity_hist)
        peaks = eq_s.cummax()
        dds = (peaks - eq_s) / peaks * 100.0
        max_dd = dds.max()

        return {
            "name": name,
            "df": res_df,
            "trades": tot,
            "wins": wins,
            "losses": tot - wins,
            "wr": wr,
            "pf": pf,
            "init_bal": init_bal,
            "final_bal": bal,
            "net_profit": pnl_tot,
            "roi": roi,
            "max_dd": max_dd,
            "equity_curve": equity_hist
        }

    # Version A: Baseline (All trades)
    res_a = simulate_version(df_trades, "Baseline DCC (No Sweep Filter)")

    # Version B: WITH Sweep Filter
    res_b = simulate_version(df_trades[df_trades['has_sweep'] == True], "DCC + 5M Liquidity Sweep")

    # The 22 Filtered Trades (Chop Traps)
    res_filtered = simulate_version(df_trades[df_trades['has_sweep'] == False], "Excluded Trades (No Sweep)")

    print("\n" + "=" * 80)
    print("COMPARISON RESULTS:")
    print("=" * 80)
    print(f"{'Metric':<30} | {'Version A (No Sweep)':<22} | {'Version B (WITH Sweep)':<22}")
    print("-" * 80)
    print(f"{'Total Trades Taken':<30} | {res_a['trades']:<22} | {res_b['trades']:<22}")
    print(f"{'Win Rate (%)':<30} | {res_a['wr']:<21.2f}% | {res_b['wr']:<21.2f}%")
    print(f"{'Profit Factor':<30} | {res_a['pf']:<22.2f} | {res_b['pf']:<22.2f}")
    print(f"{'Initial Capital':<30} | ${res_a['init_bal']:<21,.2f} | ${res_b['init_bal']:<21,.2f}")
    print(f"{'Ending Capital':<30} | ${res_a['final_bal']:<21,.2f} | ${res_b['final_bal']:<21,.2f}")
    print(f"{'Total Net Profit':<30} | +${res_a['net_profit']:<20,.2f} | +${res_b['net_profit']:<20,.2f}")
    print(f"{'Return on Investment (ROI)':<30} | +{res_a['roi']:<21.2f}% | +{res_b['roi']:<21.2f}%")
    print(f"{'Maximum Peak Drawdown':<30} | {res_a['max_dd']:<21.2f}% | {res_b['max_dd']:<21.2f}%")
    print("-" * 80)

    # Monthly breakdown comparison
    print("\nMONTHLY NET PROFIT COMPARISON ($):")
    print("-" * 65)
    print(f"{'Month':<10} | {'Version A (No Sweep)':<24} | {'Version B (WITH Sweep)':<24}")
    print("-" * 65)

    months = sorted(df_trades['month'].unique())
    for m in months:
        pnl_a = res_a['df'][res_a['df']['month'] == m]['sim_pnl'].sum() if len(res_a['df'][res_a['df']['month'] == m]) > 0 else 0.0
        pnl_b = res_b['df'][res_b['df']['month'] == m]['sim_pnl'].sum() if len(res_b['df'][res_b['df']['month'] == m]) > 0 else 0.0
        print(f"{m:<10} | +${pnl_a:<22,.2f} | +${pnl_b:<22,.2f}")
    print("-" * 65)

    # Plot Equity Curves
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 10), gridspec_kw={'height_ratios': [3, 1]}, sharex=False)

    ax1.plot(res_a['equity_curve'], color='#888888', linewidth=1.5, linestyle='--', label=f"Version A: Baseline (No Sweep) [+{res_a['roi']:.1f}% | WR: {res_a['wr']:.1f}%]")
    ax1.plot(res_b['equity_curve'], color='#00ffaa', linewidth=2.5, label=f"Version B: WITH 5M Liquidity Sweep [+{res_b['roi']:.1f}% | WR: {res_b['wr']:.1f}%]")
    ax1.axhline(5000.0, color='#ff5555', linestyle=':', alpha=0.6, label='Initial Balance ($5,000)')
    ax1.set_title("DCC Strategy: Baseline vs 5M Liquidity Sweep Confluence (Jan - Jun 2026)\nComparison of Compounded Growth ($5,000 Starting Equity)",
                  fontsize=14, fontweight='bold', pad=12, color='white')
    ax1.set_ylabel("Account Balance ($)", fontsize=12)
    ax1.grid(True, linestyle=':', alpha=0.3)
    ax1.legend(loc='upper left', framealpha=0.8, fontsize=11)

    # Drawdowns
    eq_b = pd.Series(res_b['equity_curve'])
    peaks_b = eq_b.cummax()
    dds_b = (peaks_b - eq_b) / peaks_b * 100.0

    eq_a = pd.Series(res_a['equity_curve'])
    peaks_a = eq_a.cummax()
    dds_a = (peaks_a - eq_a) / peaks_a * 100.0

    ax2.plot(range(len(dds_a)), -dds_a, color='#888888', linewidth=1.2, linestyle='--', label=f"Version A Drawdown (Max: {res_a['max_dd']:.2f}%)")
    ax2.fill_between(range(len(dds_b)), 0, -dds_b, color='#00ffaa', alpha=0.3)
    ax2.plot(range(len(dds_b)), -dds_b, color='#00ffaa', linewidth=1.8, label=f"Version B Drawdown (Max: {res_b['max_dd']:.2f}%)")
    ax2.axhline(-3.0, color='#ffbb00', linestyle=':', alpha=0.7, label='Daily CB Threshold (-3.0%)')
    ax2.set_ylabel("Drawdown %", fontsize=12)
    ax2.set_xlabel("Trade Number", fontsize=12)
    ax2.grid(True, linestyle=':', alpha=0.3)
    ax2.legend(loc='lower left', framealpha=0.8)

    plt.tight_layout()
    plot_path = os.path.join(base_dir, "reports", "liquidity_sweep_comparison_curve.png")
    plt.savefig(plot_path, dpi=300)
    plt.close()
    print(f"\nSaved comparison equity curve to: {plot_path}")

    # Copy to artifact folder as well
    artifact_plot = r"C:\Users\rafta\.gemini\antigravity-ide\brain\43f8c7db-8f03-4ddf-b8f3-de0cc5c39463\liquidity_sweep_comparison_curve.png"
    try:
        import shutil
        shutil.copy(plot_path, artifact_plot)
        print(f"Copied comparison plot to artifacts directory: {artifact_plot}")
    except Exception as e:
        print(f"Artifact copy notice: {e}")

    return res_a, res_b, res_filtered

if __name__ == "__main__":
    run_full_comparison()
