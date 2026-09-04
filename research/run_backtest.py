"""
DCC Strategy CLI Runner with 1.5R Partial Booking & Safe Trade Execution
Executes full London & NY sessions on XAUUSD MT5 tick data, prints full metrics,
and exports trade log and equity curve chart.
"""

import argparse
from datetime import datetime, time
import os
import matplotlib.pyplot as plt
import pandas as pd

from backtester import DCCBacktester
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider


def main():
    parser = argparse.ArgumentParser(description="Run DCC Strategy Tick Backtest with 1.5R Partial Booking")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol to backtest")
    parser.add_argument("--start", type=str, default="2026-07-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2026-08-31", help="End date YYYY-MM-DD")
    parser.add_argument("--balance", type=float, default=100_000.0, help="Initial account balance")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (0.01 = 1%)")
    parser.add_argument("--tp1", type=float, default=1.5, help="Target 1 RR for partial booking (default 1.5)")
    parser.add_argument("--tp2", type=float, default=2.0, help="Target 2 RR for runner (default 2.0)")
    parser.add_argument("--partial-ratio", type=float, default=0.5, help="Partial close ratio (0.5 = 50%)")
    parser.add_argument("--adx-min", type=float, default=15.0, help="Minimum 1H ADX threshold (default 15.0)")
    parser.add_argument("--atr-sl", type=float, default=0.9, help="1H ATR SL multiplier (default 0.9)")
    parser.add_argument("--session-start", type=int, default=6, help="Session start UTC hour (default 6)")
    parser.add_argument("--session-end", type=int, default=21, help="Session end UTC hour (default 21)")
    parser.add_argument("--plot", action="store_true", default=True, help="Generate equity curve chart")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end + " 23:59:59", "%Y-%m-%d %H:%M:%S")

    # Initialize Engine with full London + NY session coverage
    engine = DCCEngine(
        adx_min_threshold=args.adx_min,
        atr_sl_multiplier=args.atr_sl,
        risk_reward_ratio=args.tp1,
        check_2h_room=False,
        use_daily_open_filter=False,
        london_session_start=time(args.session_start, 0),
        london_session_end=time(args.session_end, 0),
        ny_session_start=time(args.session_start, 0),
        ny_session_end=time(args.session_end, 0),
        avoid_monday_london=False,
        avoid_friday_ny=False,
        avoid_nfp_friday=False,
        eod_exit_time=time(21, 0)
    )

    data_provider = MT5DataProvider(cache_dir="d:\\FOREX\\DC\\data_cache")

    backtester = DCCBacktester(
        engine=engine,
        data_provider=data_provider,
        initial_balance=args.balance,
        risk_per_trade=args.risk,
        contract_size=100.0,
        use_partial_tp=True,
        partial_ratio=args.partial_ratio,
        tp1_rr=args.tp1,
        tp2_rr=args.tp2,
        move_sl_to_be=True
    )

    trades_df = backtester.run(
        symbol=args.symbol,
        start_date=start_dt,
        end_date=end_dt
    )

    if not trades_df.empty:
        # Save trade log
        csv_path = "d:\\FOREX\\DC\\trades_log.csv"
        trades_df.to_csv(csv_path, index=False)
        print(f"[Run] Saved detailed trade log to: {csv_path}")

        # Generate Equity Curve Chart
        if args.plot:
            chart_path = "d:\\FOREX\\DC\\equity_curve.png"
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})

            dates = trades_df['exit_time']
            balances = trades_df['account_balance']
            win_count = len(trades_df[trades_df['net_pnl'] > 0])
            loss_count = len(trades_df[trades_df['net_pnl'] <= 0])
            gross_win = trades_df[trades_df['net_pnl'] > 0]['net_pnl'].sum()
            gross_loss = abs(trades_df[trades_df['net_pnl'] <= 0]['net_pnl'].sum())
            pf = gross_win / (gross_loss + 1e-9)

            ax1.plot(dates, balances, color='#2962FF', linewidth=2.5, marker='o', markersize=3, label='Account Equity ($)')
            ax1.axhline(y=args.balance, color='#787B86', linestyle='--', alpha=0.7, label=f'Initial Balance (${args.balance:,.0f})')
            ax1.set_title(
                f"DCC Strategy Tick Backtest: {args.symbol} (July & August 2026)\n"
                f"1.5R Partial Booking + Safe Breakeven | Trades: {len(trades_df)} | Win Rate: {win_count/len(trades_df)*100:.1f}% | "
                f"Profit Factor: {pf:.2f} | Net Return: +${trades_df['net_pnl'].sum():,.2f} (+{trades_df['net_pnl'].sum()/args.balance*100:.2f}%)",
                fontsize=11, fontweight='bold', pad=12
            )
            ax1.set_ylabel("Equity ($)", fontsize=10)
            ax1.grid(True, alpha=0.3)
            ax1.legend(loc='upper left')

            cum_max = balances.cummax()
            dd = (cum_max - balances) / cum_max * 100.0
            ax2.fill_between(dates, 0, -dd, color='#EF5350', alpha=0.45, label=f'Drawdown % (Max: {dd.max():.2f}%)')
            ax2.set_ylabel("Drawdown %", fontsize=10)
            ax2.set_xlabel("Date", fontsize=10)
            ax2.grid(True, alpha=0.3)
            ax2.legend(loc='lower left')

            plt.tight_layout()
            plt.savefig(chart_path, dpi=150)
            plt.close()
            print(f"[Run] Saved equity curve plot to: {chart_path}")


if __name__ == "__main__":
    main()
