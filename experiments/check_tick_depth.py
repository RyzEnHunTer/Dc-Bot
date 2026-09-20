import MetaTrader5 as mt5
from datetime import datetime, timedelta

if not mt5.initialize():
    print("MT5 Init Failed")
    exit(1)

symbols = ['XAUUSD', 'EURUSD', 'USDJPY', 'GBPNZD', 'US30', 'NAS100']
print("--- Checking Historical Tick Depth in MT5 ---")

for s in symbols:
    # Check 1 year ago, 6 months, 3 months, 1 month
    ranges = [
        ("1 Year Ago (2025-09)", datetime(2025, 9, 15)),
        ("6 Months Ago (2026-03)", datetime(2026, 3, 15)),
        ("3 Months Ago (2026-06)", datetime(2026, 6, 15)),
        ("1 Month Ago (2026-08)", datetime(2026, 8, 15)),
        ("2 Weeks Ago (2026-09-01)", datetime(2026, 9, 1))
    ]
    
    found = None
    for label, dt in ranges:
        ticks = mt5.copy_ticks_from(s, dt, 10, mt5.COPY_TICKS_ALL)
        if ticks is not None and len(ticks) > 0:
            found = (label, datetime.fromtimestamp(ticks[0]['time']))
            break
            
    if found:
        print(f"  {s}: Earliest tick verified around {found[0]} ({found[1]})")
    else:
        print(f"  {s}: Could not fetch ticks from tested ranges")

mt5.shutdown()
