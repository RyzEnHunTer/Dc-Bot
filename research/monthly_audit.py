from datetime import datetime, time
import pandas as pd
from backtester import DCCBacktester
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider

dp = MT5DataProvider()

class StrictGoldTester(DCCBacktester):
    def run(self, symbol, start_date, end_date):
        df_m5, df_1h, df_2h = self.data_provider.fetch_multi_timeframe_rates(symbol, start_date, end_date, warmup_days=20)
        df_prep = self.engine.prepare_data(df_m5, df_1h, df_2h)
        eval_mask = (df_prep.index >= pd.Timestamp(start_date, tz="UTC")) & (df_prep.index <= pd.Timestamp(end_date, tz="UTC"))
        df_eval = df_prep.loc[eval_mask]
        
        trade_id = 0
        i = 1
        n_bars = len(df_eval)
        curr_date = None
        day_ticks = None
        
        while i < n_bars:
            prev_b = df_eval.iloc[i-1]
            curr_b = df_eval.iloc[i]
            b_time = curr_b.name
            sig = self.engine.evaluate_bar(prev_b, curr_b, symbol=symbol)
            if sig:
                t_date = b_time.date()
                if curr_date != t_date:
                    curr_date = t_date
                    day_ticks = self.data_provider.fetch_ticks_day(symbol, t_date)
                if day_ticks is not None and len(day_ticks) > 0:
                    rec, exit_t = self._simulate_tick_trade(trade_id + 1, sig, b_time, day_ticks)
                    if rec:
                        trade_id += 1
                        self.trades.append(rec)
                        self.balance = rec.account_balance
                        while i < n_bars and df_eval.index[i] <= exit_t:
                            i += 1
                        continue
            i += 1
        return self.get_trade_analysis()

eng = DCCEngine(
    adx_min_threshold=15.0,
    atr_sl_multiplier=0.9,
    check_2h_room=False,
    use_daily_open_filter=False,
    london_session_start=time(6, 0),
    london_session_end=time(21, 0),
    ny_session_start=time(6, 0),
    ny_session_end=time(21, 0),
    avoid_monday_london=False,
    avoid_friday_ny=False,
    avoid_nfp_friday=False,
    eod_exit_time=time(21, 0)
)

bt = StrictGoldTester(
    engine=eng,
    data_provider=dp,
    initial_balance=100000.0,
    risk_per_trade=0.01,
    use_partial_tp=True,
    partial_ratio=0.5,
    tp1_rr=1.4,
    tp2_rr=2.2,
    move_sl_to_be=True
)

df = bt.run("XAUUSD", datetime(2026, 7, 1), datetime(2026, 8, 31, 23, 59, 59))
df["month"] = df["exit_time"].dt.strftime("%Y-%m")

print("\n" + "=" * 80)
print("MONTH-BY-MONTH CONSISTENCY AUDIT (JULY vs AUGUST)")
print("=" * 80)
for m, sub in df.groupby("month"):
    wins = len(sub[sub["net_pnl"] > 0])
    losses = len(sub[sub["net_pnl"] <= 0])
    tot = len(sub)
    wr = wins / tot * 100
    pnl = sub["net_pnl"].sum()
    gp = sub[sub["net_pnl"] > 0]["net_pnl"].sum()
    gl = abs(sub[sub["net_pnl"] <= 0]["net_pnl"].sum())
    pf = gp / gl if gl > 0 else 999.0
    print(f"Month {m} | Trades: {tot:2d} | Wins: {wins:2d} | Losses: {losses:2d} | Win Rate: {wr:5.1f}% | Profit Factor: {pf:4.2f} | Net PnL: +${pnl:,.2f}")
print("=" * 80)
