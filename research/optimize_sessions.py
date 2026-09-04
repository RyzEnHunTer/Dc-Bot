"""
DCC Strategy Session & Filter Comparison
Tests various session windows and filters across July and August 2026 on XAUUSD ticks.
"""

from datetime import datetime, time
import pandas as pd

from backtester import DCCBacktester
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider


def test_configs():
    dp = MT5DataProvider()
    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)

    configs = [
        {
            'name': 'Strict Author Baseline (06-09 & 12-14 UTC, ADX 25, 2H Room)',
            'london_start': time(6, 0), 'london_end': time(9, 0),
            'ny_start': time(12, 0), 'ny_end': time(14, 0),
            'adx': 25.0, 'room': True, 'open_filter': True
        },
        {
            'name': 'Author Session (06-09 & 12-14 UTC, ADX 20, Relaxed 2H Room)',
            'london_start': time(6, 0), 'london_end': time(9, 0),
            'ny_start': time(12, 0), 'ny_end': time(14, 0),
            'adx': 20.0, 'room': False, 'open_filter': True
        },
        {
            'name': 'Full Core London & NY (06-11 & 12-16 UTC, ADX 25, Relaxed 2H Room)',
            'london_start': time(6, 0), 'london_end': time(11, 0),
            'ny_start': time(12, 0), 'ny_end': time(16, 0),
            'adx': 25.0, 'room': False, 'open_filter': True
        },
        {
            'name': 'Full Core London & NY (06-11 & 12-16 UTC, ADX 20, Relaxed 2H Room)',
            'london_start': time(6, 0), 'london_end': time(11, 0),
            'ny_start': time(12, 0), 'ny_end': time(16, 0),
            'adx': 20.0, 'room': False, 'open_filter': True
        },
        {
            'name': 'Active Day Hours (07-17 UTC, ADX 25, Relaxed 2H Room)',
            'london_start': time(7, 0), 'london_end': time(17, 0),
            'ny_start': time(12, 0), 'ny_end': time(17, 0),
            'adx': 25.0, 'room': False, 'open_filter': True
        }
    ]

    results = []

    for cfg in configs:
        print(f"\n=======================================================")
        print(f"Testing Config: {cfg['name']}")
        print(f"=======================================================")

        engine = DCCEngine(
            adx_min_threshold=cfg['adx'],
            check_2h_room=cfg['room'],
            use_daily_open_filter=cfg['open_filter'],
            london_session_start=cfg['london_start'],
            london_session_end=cfg['london_end'],
            ny_session_start=cfg['ny_start'],
            ny_session_end=cfg['ny_end'],
            avoid_monday_london=True,
            avoid_friday_ny=True,
            avoid_nfp_friday=True
        )

        bt = DCCBacktester(
            engine=engine,
            data_provider=dp,
            initial_balance=100_000.0,
            risk_per_trade=0.01
        )

        df_trades = bt.run('XAUUSD', start_dt, end_dt)

        if not df_trades.empty:
            wins = len(df_trades[df_trades['net_pnl'] > 0])
            losses = len(df_trades[df_trades['net_pnl'] <= 0])
            total = len(df_trades)
            win_rate = (wins / total) * 100.0
            gross_p = df_trades[df_trades['net_pnl'] > 0]['net_pnl'].sum()
            gross_l = abs(df_trades[df_trades['net_pnl'] <= 0]['net_pnl'].sum())
            pf = gross_p / (gross_l + 1e-9)
            net_pnl = df_trades['net_pnl'].sum()

            results.append({
                'Config': cfg['name'],
                'Trades': total,
                'Wins': wins,
                'Losses': losses,
                'Win Rate %': f"{win_rate:.1f}%",
                'Profit Factor': f"{pf:.2f}",
                'Net PnL ($)': f"${net_pnl:,.2f}",
                'Avg Trade Duration': f"{df_trades['duration_minutes'].mean():.0f} min",
                'Avg Slippage ($)': f"${df_trades['entry_slippage'].abs().mean():.2f}"
            })
        else:
            results.append({
                'Config': cfg['name'],
                'Trades': 0,
                'Wins': 0,
                'Losses': 0,
                'Win Rate %': "0.0%",
                'Profit Factor': "0.00",
                'Net PnL ($)': "$0.00",
                'Avg Trade Duration': "N/A",
                'Avg Slippage ($)': "N/A"
            })

    summary_df = pd.DataFrame(results)
    print("\n\n=======================================================")
    print("           FILTER & SESSION COMPARISON SUMMARY         ")
    print("=======================================================")
    print(summary_df.to_string(index=False))
    print("=======================================================\n")

    summary_df.to_csv("d:\\FOREX\\DC\\session_comparison_summary.csv", index=False)


if __name__ == "__main__":
    test_configs()
