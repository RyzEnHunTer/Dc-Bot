"""
Ultra-Fast Forex Parameter Optimization and Tuning Engine
Analyzes Major Forex Pairs: EURUSD, GBPUSD, USDJPY, GBPJPY, AUDUSD
Precomputes signals and tests 100+ parameter combinations in seconds.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider


class InstantForexTuner:
    PAIR_SPECS = {
        "EURUSD": {"digits": 5, "pip_size": 0.0001, "spread_pips": 1.3, "contract": 100_000.0},
        "GBPUSD": {"digits": 5, "pip_size": 0.0001, "spread_pips": 1.7, "contract": 100_000.0},
        "USDJPY": {"digits": 3, "pip_size": 0.01, "spread_pips": 1.6, "contract": 100_000.0},
        "GBPJPY": {"digits": 3, "pip_size": 0.01, "spread_pips": 2.2, "contract": 100_000.0},
        "AUDUSD": {"digits": 5, "pip_size": 0.0001, "spread_pips": 1.5, "contract": 100_000.0},
    }

    def __init__(self, data_provider: Optional[MT5DataProvider] = None):
        self.dp = data_provider if data_provider is not None else MT5DataProvider()
        self.raw_dfs = {}

    def prepare_all_pairs(self, symbols: List[str], start_date: datetime, end_date: datetime):
        print(f"[InstantForexTuner] Precomputing data for: {', '.join(symbols)}...")
        base_engine = DCCEngine()

        for sym in symbols:
            df_m5, df_1h, df_2h = self.dp.fetch_multi_timeframe_rates(sym, start_date, end_date, warmup_days=20)
            df_prep = base_engine.prepare_data(df_m5, df_1h, df_2h)
            eval_mask = (df_prep.index >= pd.Timestamp(start_date, tz=timezone.utc)) & \
                        (df_prep.index <= pd.Timestamp(end_date, tz=timezone.utc))
            self.raw_dfs[sym] = df_prep.loc[eval_mask]
            print(f"  {sym}: Precomputed {len(self.raw_dfs[sym])} bars")

    def run_pair_grid_search(self, symbol: str) -> Tuple[Dict, pd.DataFrame]:
        df = self.raw_dfs[symbol]
        spec = self.PAIR_SPECS[symbol]
        pip_size = spec["pip_size"]
        spread_price = spec["spread_pips"] * pip_size
        contract_size = spec["contract"]

        n_bars = len(df)
        times = df.index
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values

        m5_e9 = df['ema9_5m'].values
        m5_e20 = df['ema20_5m'].values
        vwap = df['vwap_5m'].values

        h1_bias = df['bias_1h'].values
        h1_e20 = df['ema20_1h'].values
        h1_adx = df['adx_1h'].values
        h1_atr = df['atr_1h'].values

        sw_high = df['swing_high_2h'].values
        sw_low = df['swing_low_2h'].values
        d_open = df['daily_open'].values

        # 1. Pre-extract all raw EMA flips across the dataset
        raw_flips = []
        for i in range(1, n_bars):
            prev_diff = m5_e9[i - 1] - m5_e20[i - 1]
            curr_diff = m5_e9[i] - m5_e20[i]
            bias = h1_bias[i]
            c = closes[i]

            sig_type = 0  # 1 for BUY, -1 for SELL
            if bias == 1 and prev_diff <= 0 and curr_diff > 0:
                if c > vwap[i] and c > h1_e20[i]:
                    sig_type = 1
            elif bias == -1 and prev_diff >= 0 and curr_diff < 0:
                if c < vwap[i] and c < h1_e20[i]:
                    sig_type = -1

            if sig_type != 0:
                t = times[i].time()
                raw_flips.append({
                    "idx": i,
                    "time": times[i],
                    "time_obj": t,
                    "sig_type": sig_type,
                    "close": c,
                    "adx": h1_adx[i],
                    "atr": h1_atr[i],
                    "sw_high": sw_high[i],
                    "sw_low": sw_low[i],
                    "d_open": d_open[i]
                })

        # Grid parameters
        atr_mults = [1.0, 1.2, 1.5, 1.8]
        min_sls = [8.0, 10.0, 12.0, 15.0]
        tp_configs = [(1.0, 1.5), (1.2, 1.8), (1.5, 2.0)]
        adx_thresholds = [15.0, 20.0, 25.0, 30.0]
        sessions = ["peak", "full"]
        room_filters = [False, True]

        results = []
        best_res = None
        best_score = -999999.0

        for sess in sessions:
            if sess == "peak":
                sess_windows = [(time(7, 0), time(11, 0)), (time(12, 0), time(16, 0))]
            else:
                sess_windows = [(time(6, 0), time(16, 0))]

            # Filter flips by session
            sess_flips = [
                f for f in raw_flips
                if any(start <= f["time_obj"] < end for start, end in sess_windows)
            ]

            for adx_th in adx_thresholds:
                adx_flips = [f for f in sess_flips if f["adx"] >= adx_th]

                for room in room_filters:
                    # Filter by daily open / 2H room
                    cand_flips = []
                    for f in adx_flips:
                        c = f["close"]
                        st = f["sig_type"]
                        if room:
                            if st == 1 and c < f["d_open"]:
                                continue
                            if st == -1 and c > f["d_open"]:
                                continue
                        cand_flips.append(f)

                    if len(cand_flips) < 8:
                        continue

                    for atr_m in atr_mults:
                        for min_sl in min_sls:
                            for tp1_rr, tp2_rr in tp_configs:
                                # Run trade simulation
                                balance = 100_000.0
                                initial_balance = 100_000.0
                                last_exit_idx = -1
                                trades_pnl = []

                                for f in cand_flips:
                                    sig_idx = f["idx"]
                                    if sig_idx <= last_exit_idx:
                                        continue
                                    if sig_idx + 1 >= n_bars:
                                        break

                                    st = f["sig_type"]
                                    sl_dist = f["atr"] * atr_m
                                    sl_dist = max(sl_dist, min_sl * pip_size)

                                    # Check 2H room
                                    if room:
                                        if st == 1 and not np.isnan(f["sw_high"]):
                                            r_space = f["sw_high"] - f["close"]
                                            if 0 < r_space < (tp1_rr * sl_dist):
                                                continue
                                        elif st == -1 and not np.isnan(f["sw_low"]):
                                            r_space = f["close"] - f["sw_low"]
                                            if 0 < r_space < (tp1_rr * sl_dist):
                                                continue

                                    entry_p = opens[sig_idx + 1] + (spread_price if st == 1 else -spread_price)
                                    risk_amt = balance * 0.01
                                    lots = max(0.02, round(risk_amt / (sl_dist * contract_size), 2))
                                    p_lots = round(lots * 0.5, 2)
                                    r_lots = round(lots - p_lots, 2)

                                    if st == 1:
                                        sl_p = entry_p - sl_dist
                                        tp1_p = entry_p + (tp1_rr * sl_dist)
                                        tp2_p = entry_p + (tp2_rr * sl_dist)
                                    else:
                                        sl_p = entry_p + sl_dist
                                        tp1_p = entry_p - (tp1_rr * sl_dist)
                                        tp2_p = entry_p - (tp2_rr * sl_dist)

                                    tp1_hit = False
                                    curr_sl = sl_p
                                    p_exit = entry_p
                                    r_exit = entry_p
                                    trade_exit = sig_idx + 1

                                    # Forward path
                                    for j in range(sig_idx + 1, min(n_bars, sig_idx + 120)):
                                        h = highs[j]
                                        l = lows[j]
                                        cl = closes[j]
                                        trade_exit = j
                                        bt = times[j].time()

                                        if st == 1:
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

                                        else:  # Short
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

                                    last_exit_idx = trade_exit

                                    if st == 1:
                                        p_gross = (p_exit - entry_p) * p_lots * contract_size
                                        r_gross = (r_exit - entry_p) * r_lots * contract_size
                                    else:
                                        p_gross = (entry_p - p_exit) * p_lots * contract_size
                                        r_gross = (entry_p - r_exit) * r_lots * contract_size

                                    comm = lots * 5.0
                                    net_pnl = p_gross + r_gross - comm
                                    balance += net_pnl
                                    trades_pnl.append(net_pnl)

                                if not trades_pnl:
                                    continue

                                pnl_arr = np.array(trades_pnl)
                                wins = pnl_arr[pnl_arr > 0]
                                losses = pnl_arr[pnl_arr <= 0]
                                wr = (len(wins) / len(pnl_arr)) * 100.0

                                gp = wins.sum() if len(wins) > 0 else 0.0
                                gl = abs(losses.sum()) if len(losses) > 0 else 0.0
                                pf = (gp / gl) if gl > 0 else 999.0

                                eq = initial_balance + np.cumsum(pnl_arr)
                                cm = np.maximum.accumulate(eq)
                                max_dd = np.max((cm - eq) / cm * 100.0)
                                net_pnl_val = balance - initial_balance

                                res_dict = {
                                    "symbol": symbol,
                                    "total_trades": len(pnl_arr),
                                    "win_rate": wr,
                                    "profit_factor": pf,
                                    "net_pnl": net_pnl_val,
                                    "max_dd": max_dd,
                                    "atr_mult": atr_m,
                                    "min_sl": min_sl,
                                    "tp1": tp1_rr,
                                    "tp2": tp2_rr,
                                    "adx": adx_th,
                                    "session": sess,
                                    "room2h": room
                                }
                                results.append(res_dict)

                                if len(pnl_arr) >= 10:
                                    score = net_pnl_val - (max_dd * 400.0)
                                    if score > best_score:
                                        best_score = score
                                        best_res = res_dict

        df_res = pd.DataFrame(results)
        return best_res, df_res


def main():
    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)

    pairs = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "AUDUSD"]
    tuner = InstantForexTuner()
    tuner.prepare_all_pairs(pairs, start_dt, end_dt)

    all_dfs = []
    best_configs = {}

    print("\n" + "=" * 80)
    print("RUNNING HIGH-SPEED PARAMETER OPTIMIZATION ACROSS FOREX PAIRS")
    print("=" * 80)

    for sym in pairs:
        best, df_sym = tuner.run_pair_grid_search(sym)
        best_configs[sym] = best
        all_dfs.append(df_sym)

        if best:
            pnl_s = "+" if best['net_pnl'] >= 0 else "-"
            print(f"\n>>> OPTIMAL SETTINGS FOR {sym:7s} <<<")
            print(f"    Trades: {best['total_trades']} (~{best['total_trades']/8.8:.1f}/wk) | Win Rate: {best['win_rate']:.1f}% | Profit Factor: {best['profit_factor']:.2f}")
            print(f"    Net PnL: {pnl_s}${abs(best['net_pnl']):,.2f} (+{best['net_pnl']/1000.0:.2f}%) | Max DD: {best['max_dd']:.2f}%")
            print(f"    Best Formula: ATR_mult={best['atr_mult']}x | Min_SL={best['min_sl']} pips | TP={best['tp1']}R / {best['tp2']}R | ADX>={best['adx']} | Session={best['session']} | RoomFilter={best['room2h']}")

    # Save to CSV
    df_combined = pd.concat(all_dfs, ignore_index=True)
    df_combined.to_csv("d:\\FOREX\\DC\\forex_tuning_grid_results.csv", index=False)
    print(f"\n[ForexTuner] Saved {len(df_combined)} grid evaluations to d:\\FOREX\\DC\\forex_tuning_grid_results.csv")

    print("\n" + "=" * 90)
    print("FINAL OPTIMIZED FOREX PARAMETERS MATRIX")
    print("=" * 90)
    print(f"{'Pair':7s} | {'ATR Mult':8s} | {'Min SL':8s} | {'Target RR':10s} | {'ADX Min':7s} | {'Session':7s} | {'Win Rate':8s} | {'PF':4s} | {'Net PnL':10s} | {'Max DD':6s}")
    print("-" * 90)
    for sym in pairs:
        b = best_configs[sym]
        if b:
            print(
                f"{sym:7s} | {b['atr_mult']:4.1f}x     | {b['min_sl']:4.1f} pips | "
                f"{b['tp1']:3.1f}R / {b['tp2']:3.1f}R | {b['adx']:2.0f}      | {b['session']:7s} | "
                f"{b['win_rate']:5.1f}%   | {b['profit_factor']:4.2f} | ${b['net_pnl']:+9,.2f} | {b['max_dd']:4.2f}%"
            )
    print("=" * 90)


if __name__ == "__main__":
    main()
