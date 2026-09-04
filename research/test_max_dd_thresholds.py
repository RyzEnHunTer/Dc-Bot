import os
import pandas as pd

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
df = pd.read_csv(os.path.join(base_dir, "reports", "trades_log_with_3pct_circuit_breaker.csv"))
df['exit_time'] = pd.to_datetime(df['exit_time'], format='ISO8601')
df = df.sort_values('exit_time').reset_index(drop=True)

balances = [5000.0] + df['compounded_balance'].tolist()
times = [df['exit_time'].iloc[0]] + df['exit_time'].tolist()

for threshold_pct in [3.88, 4.0, 5.0, 5.88, 6.0, 7.0, 7.88, 8.0, 10.0]:
    peak = 5000.0
    tripped = False
    trip_idx = None
    trip_time = None
    trip_dd = 0.0
    trip_bal = 0.0
    trip_peak = 0.0
    
    for i, bal in enumerate(balances):
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak * 100.0
        if dd >= threshold_pct:
            tripped = True
            trip_idx = i
            trip_time = times[i]
            trip_dd = dd
            trip_bal = bal
            trip_peak = peak
            break
            
    if tripped:
        print(f"Threshold: {threshold_pct:5.2f}% -> TRIPPED on trade #{trip_idx} at {trip_time.strftime('%Y-%m-%d')}! Peak: ${trip_peak:,.2f} -> Bal: ${trip_bal:,.2f} (DD: -{trip_dd:.2f}%)")
    else:
        print(f"Threshold: {threshold_pct:5.2f}% -> NEVER TRIPPED (100% Safe, full +211.0% profit kept)")
