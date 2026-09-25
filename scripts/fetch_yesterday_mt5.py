import os
import sys
from datetime import datetime, timezone, timedelta
import pandas as pd
import MetaTrader5 as mt5

def main():
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return

    print("MT5 initialized successfully!")
    acc = mt5.account_info()
    if acc:
        print(f"Connected Account: {acc.login} | Server: {acc.server} | Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")
    else:
        print("Warning: Could not get account info.")

    # Target date: yesterday (2026-09-22 UTC)
    t_start = datetime(2026, 9, 22, 0, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 9, 23, 0, 0, 0, tzinfo=timezone.utc)

    # 1. Deals history
    print("\n--- QUERYING LIVE MT5 BROKER DEALS (2026-09-22) ---")
    deals = mt5.history_deals_get(t_start, t_end + timedelta(hours=3)) # extra buffer for broker timezone
    if deals:
        print(f"Found {len(deals)} raw deal records:")
        for d in deals:
            d_time = datetime.fromtimestamp(d.time, timezone.utc)
            deal_dir = "BUY" if d.type == 0 else "SELL" if d.type == 1 else f"TYPE_{d.type}"
            entry_type = "ENTRY_IN" if d.entry == 0 else "ENTRY_OUT" if d.entry == 1 else f"ENTRY_{d.entry}"
            print(f"  • {d_time} | Ticket #{d.ticket} | Order #{d.order} | {d.symbol} | {deal_dir} {entry_type} | Vol: {d.volume} | Price: {d.price} | Profit: ${d.profit:,.2f} | Comment: {d.comment}")
    else:
        print("No live broker deals recorded on 2026-09-22.")

    # 2. Rates data for XAUUSD & NAS100
    print("\n--- FETCHING 5M RATES & TICKS (2026-09-22) ---")
    for sym in ["XAUUSD", "NAS100"]:
        rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, t_start, t_end)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df['time_utc'] = pd.to_datetime(df['time'], unit='s', utc=True)
            print(f"[{sym}] 5M Bars: {len(df)} candles fetched.")
            print(f"       Range: {df['time_utc'].iloc[0]} -> {df['time_utc'].iloc[-1]}")
            print(f"       High: {df['high'].max()} | Low: {df['low'].min()}")
            
            # Save to disk for inspection
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            logs_dir = os.path.join(project_root, "logs")
            os.makedirs(logs_dir, exist_ok=True)
            out_csv = os.path.join(logs_dir, f"rates_{sym}_20260922.csv")
            df.to_csv(out_csv, index=False)
            print(f"       Saved to: {out_csv}")
        else:
            print(f"[{sym}] No rates found. Error: {mt5.last_error()}")

        ticks = mt5.copy_ticks_range(sym, t_start + timedelta(hours=6), t_start + timedelta(hours=19), mt5.COPY_TICKS_ALL)
        if ticks is not None and len(ticks) > 0:
            print(f"[{sym}] Ticks: {len(ticks)} ticks fetched for London/NY session.")
        else:
            print(f"[{sym}] No ticks returned. Error: {mt5.last_error()}")

    mt5.shutdown()
    print("\nMT5 connection closed cleanly.")

if __name__ == "__main__":
    main()
