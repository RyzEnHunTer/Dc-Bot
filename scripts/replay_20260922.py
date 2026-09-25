import os
import sys
from datetime import datetime, timezone
import MetaTrader5 as mt5

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nightly_reconciler import NightlyReconciler

def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return

    target_d = datetime(2026, 9, 22, tzinfo=timezone.utc).date()
    reconciler = NightlyReconciler(symbols=['XAUUSD', 'NAS100'], strategy_version='v1.2')
    trades = reconciler.run_daily_backtest(target_d)
    print(f"\n[APEXHUNTER v1.2 BACKTEST REPLAY: 2026-09-22]")
    print(f"Total Trades Detected: {len(trades)}")
    for idx, t in enumerate(trades, 1):
        print(f"  • Setup #{idx}: {t.get('symbol')} {t.get('direction')}")
        print(f"    Trigger Bar Time: {t.get('trigger_bar_time')} UTC")
        print(f"    Execution Entry:  {t.get('actual_entry')} | Spread: {t.get('entry_spread')}")
        print(f"    Stop Loss:        {t.get('sl_price')} | TP1: {t.get('tp1_price')} | TP2: {t.get('tp2_price')}")
        print(f"    Lots:             Total: {t.get('total_lots')} (A: {t.get('partial_lots')} / B: {t.get('runner_lots')})")
        print(f"    Exit Time:        {t.get('exit_time')} UTC")
        print(f"    Exit Reason:      {t.get('exit_reason')}")
        print(f"    Dollars A:        ${t.get('dollars_a')} | Dollars B: ${t.get('dollars_b')}")
        print(f"    Net Realized PnL: ${t.get('net_pnl')}")

    mt5.shutdown()

if __name__ == '__main__':
    main()
