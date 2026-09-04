"""
Forex Trend Separation & High-Probability Tuning Engine
Tests the author's core rule from Video 2:
"We want to see clear separation between 1H EMAs.
If the EMAs keep crossing or are ranging, we DO NOT trade."
"""

from datetime import datetime, time, timedelta, timezone
import numpy as np
import pandas as pd
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider


def test_forex_separation_filter():
    dp = MT5DataProvider()
    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)
    pairs = ["GBPJPY", "USDJPY", "EURUSD", "GBPUSD", "AUDUSD"]

    pair_specs = {
        "EURUSD": {"pip_size": 0.0001, "spread_pips": 1.3, "contract": 100_000.0},
        "GBPUSD": {"pip_size": 0.0001, "spread_pips": 1.7, "contract": 100_000.0},
        "USDJPY": {"pip_size": 0.01, "spread_pips": 1.6, "contract": 100_000.0},
        "GBPJPY": {"pip_size": 0.01, "spread_pips": 2.2, "contract": 100_000.0},
        "AUDUSD": {"pip_size": 0.0001, "spread_pips": 1.5, "contract": 100_000.0},
    }

    base_engine = DCCEngine()

    print("=" * 90)
    print("TESTING 1H EMA SEPARATION FILTER & DYNAMIC RR ON FOREX PAIRS")
    print("=" * 90)

    for sym in pairs:
        spec = pair_specs[sym]
        pip_size = spec["pip_size"]
        spread_price = spec["spread_pips"] * pip_size
        contract = spec["contract"]

        df_m5, df_1h, df_2h = dp.fetch_multi_timeframe_rates(sym, start_dt, end_dt, warmup_days=20)
        df = base_engine.prepare_data(df_m5, df_1h, df_2h)
        eval_mask = (df.index >= pd.Timestamp(start_dt, tz=timezone.utc)) & (df.index <= pd.Timestamp(end_dt, tz=timezone.utc))
        df_eval = df.loc[eval_mask]

        n_bars = len(df_eval)
        times = df_eval.index
        opens = df_eval['open'].values
        highs = df_eval['high'].values
        lows = df_eval['low'].values
        closes = df_eval['close'].values

        m5_e9 = df_eval['ema9_5m'].values
        m5_e20 = df_eval['ema20_5m'].values
        vwap = df_eval['vwap_5m'].values

        h1_e9 = df_eval['ema9_1h'].values
        h1_e20 = df_eval['ema20_1h'].values
        h1_adx = df_eval['adx_1h'].values
        h1_atr = df_eval['atr_1h'].values
        h1_bias = df_eval['bias_1h'].values
        d_open = df_eval['daily_open'].values

        # Test varying separation thresholds (0.0 = baseline, 0.15 = moderate, 0.25 = strong)
        sep_thresholds = [0.0, 0.15, 0.25, 0.35]
        rr_configs = [(1.0, 1.5), (1.2, 1.8), (1.5, 2.0)]
        atr_mults = [1.2, 1.5, 1.8]

        best_pnl = -999999.0
        best_cfg = None

        for sep in sep_thresholds:
            for tp1, tp2 in rr_configs:
                for atr_m in atr_mults:
                    trades = []
                    balance = 100_000.0
                    last_exit = -1

                    for i in range(2, n_bars):
                        t = times[i].time()
                        # Trading window: 07:00 to 16:00 UTC
                        if not (time(7, 0) <= t < time(16, 0)):
                            continue

                        bias = h1_bias[i]
                        if bias == 0 or h1_adx[i] < 20.0:
                            continue

                        # Check 1H EMA Separation
                        ema_diff = abs(h1_e9[i] - h1_e20[i])
                        if ema_diff < (sep * h1_atr[i]):
                            continue

                        # Check 5M Pullback & Flip (require at least 2 bars of pullback)
                        c = closes[i]
                        sig_type = 0

                        if bias == 1:  # LONG
                            if m5_e9[i - 2] < m5_e20[i - 2] and m5_e9[i - 1] < m5_e20[i - 1] and m5_e9[i] > m5_e20[i]:
                                if c > vwap[i] and c > h1_e20[i] and c > d_open[i]:
                                    sig_type = 1
                        elif bias == -1:  # SHORT
                            if m5_e9[i - 2] > m5_e20[i - 2] and m5_e9[i - 1] > m5_e20[i - 1] and m5_e9[i] < m5_e20[i]:
                                if c < vwap[i] and c < h1_e20[i] and c < d_open[i]:
                                    sig_type = -1

                        if sig_type == 0:
                            continue

                        if i <= last_exit:
                            continue
                        if i + 1 >= n_bars:
                            break

                        sl_dist = max(h1_atr[i] * atr_m, 10.0 * pip_size)
                        entry_p = opens[i + 1] + (spread_price if sig_type == 1 else -spread_price)

                        risk_amt = balance * 0.01
                        lots = max(0.02, round(risk_amt / (sl_dist * contract), 2))
                        p_lots = round(lots * 0.5, 2)
                        r_lots = round(lots - p_lots, 2)

                        if sig_type == 1:
                            sl_p = entry_p - sl_dist
                            tp1_p = entry_p + (tp1 * sl_dist)
                            tp2_p = entry_p + (tp2 * sl_dist)
                        else:
                            sl_p = entry_p + sl_dist
                            tp1_p = entry_p - (tp1 * sl_dist)
                            tp2_p = entry_p - (tp2 * sl_dist)

                        tp1_hit = False
                        curr_sl = sl_p
                        p_exit = entry_p
                        r_exit = entry_p
                        t_exit = i + 1

                        for j in range(i + 1, min(n_bars, i + 120)):
                            h = highs[j]
                            l = lows[j]
                            cl = closes[j]
                            t_exit = j
                            bt = times[j].time()

                            if sig_type == 1:
                                if not tp1_hit:
                                    if h >= tp1_p:
                                        tp1_hit = True
                                        p_exit = tp1_p
                                        curr_sl = entry_p + spread_price
                                    if l <= curr_sl:
                                        p_exit = curr_sl if not tp1_hit else p_exit
                                        r_exit = curr_sl
                                        break
                                else:
                                    if h >= tp2_p:
                                        r_exit = tp2_p
                                        break
                                    elif l <= curr_sl:
                                        r_exit = curr_sl
                                        break
                                if bt >= time(21, 0):
                                    if not tp1_hit:
                                        p_exit = cl
                                    r_exit = cl
                                    break
                            else:
                                if not tp1_hit:
                                    if l <= tp1_p:
                                        tp1_hit = True
                                        p_exit = tp1_p
                                        curr_sl = entry_p - spread_price
                                    if h >= curr_sl:
                                        p_exit = curr_sl if not tp1_hit else p_exit
                                        r_exit = curr_sl
                                        break
                                else:
                                    if l <= tp2_p:
                                        r_exit = tp2_p
                                        break
                                    elif h >= curr_sl:
                                        r_exit = curr_sl
                                        break
                                if bt >= time(21, 0):
                                    if not tp1_hit:
                                        p_exit = cl
                                    r_exit = cl
                                    break

                        last_exit = t_exit

                        if sig_type == 1:
                            p_gross = (p_exit - entry_p) * p_lots * contract
                            r_gross = (r_exit - entry_p) * r_lots * contract
                        else:
                            p_gross = (entry_p - p_exit) * p_lots * contract
                            r_gross = (entry_p - r_exit) * r_lots * contract

                        comm = lots * 5.0
                        net = p_gross + r_gross - comm
                        balance += net
                        trades.append(net)

                    if len(trades) >= 8:
                        t_arr = np.array(trades)
                        wins = t_arr[t_arr > 0]
                        losses = t_arr[t_arr <= 0]
                        wr = len(wins) / len(t_arr) * 100.0
                        gp = wins.sum() if len(wins) > 0 else 0.0
                        gl = abs(losses.sum()) if len(losses) > 0 else 0.0
                        pf = gp / gl if gl > 0 else 999.0
                        pnl = balance - 100_000.0
                        eq = 100_000.0 + np.cumsum(t_arr)
                        cm = np.maximum.accumulate(eq)
                        dd = np.max((cm - eq) / cm * 100.0)

                        if pnl > best_pnl:
                            best_pnl = pnl
                            best_cfg = {
                                "trades": len(t_arr),
                                "wins": len(wins),
                                "wr": wr,
                                "pf": pf,
                                "pnl": pnl,
                                "dd": dd,
                                "sep": sep,
                                "tp1": tp1,
                                "tp2": tp2,
                                "atr_m": atr_m
                            }

        if best_cfg:
            b = best_cfg
            print(f"  {sym:7s} | Trades: {b['trades']:2d} | Win Rate: {b['wr']:5.1f}% | PF: {b['pf']:4.2f} | Net PnL: ${b['pnl']:+9,.2f} | MaxDD: {b['dd']:4.2f}% | Sep: {b['sep']:4.2f}x | ATR: {b['atr_m']}x | TP: {b['tp1']}R/{b['tp2']}R")
        else:
            print(f"  {sym:7s} | No profitable set with >= 8 trades")

    print("=" * 90)


if __name__ == "__main__":
    test_forex_separation_filter()
