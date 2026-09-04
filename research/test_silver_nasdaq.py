"""
DCC Strategy Verification on Silver (XAGUSD) and Nasdaq (NAS100)
Replays M5, H1, H2 market data from MT5 for July and August 2026.
Evaluates:
- Silver (XAGUSD): High-beta commodity sister to Gold
- Nasdaq (NAS100): High-momentum US Tech index during London/NY sessions
"""

from datetime import datetime, time, timezone
import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider


def analyze_trades(trades, initial_balance=100_000.0):
    if not trades:
        return None
    t_arr = np.array(trades)
    wins = t_arr[t_arr > 0]
    losses = t_arr[t_arr <= 0]
    wr = len(wins) / len(t_arr) * 100.0
    gp = wins.sum() if len(wins) > 0 else 0.0
    gl = abs(losses.sum()) if len(losses) > 0 else 0.0
    pf = gp / gl if gl > 0 else 999.0
    net_pnl = t_arr.sum()
    roi = (net_pnl / initial_balance) * 100.0

    eq = initial_balance + np.cumsum(t_arr)
    cm = np.maximum.accumulate(eq)
    dd = np.max((cm - eq) / cm * 100.0)

    return {
        "trades": len(t_arr),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": wr,
        "pf": pf,
        "pnl": net_pnl,
        "roi": roi,
        "max_dd": dd
    }


def run_symbol_test(sym: str, start_dt: datetime, end_dt: datetime):
    dp = MT5DataProvider()
    if not mt5.initialize():
        print("MT5 initialization failed")
        return

    info = mt5.symbol_info(sym)
    if info is None:
        print(f"Symbol {sym} not found in MT5!")
        return
    mt5.symbol_select(sym, True)

    contract_size = info.trade_contract_size
    digits = info.digits
    point = info.point
    spread_points = info.spread
    spread_price = spread_points * point

    print(f"\n{'=' * 80}")
    print(f"TESTING {sym} | Contract Size: {contract_size} | Digits: {digits} | Point: {point} | Spread: {spread_price:.4f}")
    print(f"{'=' * 80}")

    df_m5, df_1h, df_2h = dp.fetch_multi_timeframe_rates(sym, start_dt, end_dt, warmup_days=20)
    print(f"Loaded {len(df_m5)} M5 bars, {len(df_1h)} H1 bars, {len(df_2h)} H2 bars.")

    engine = DCCEngine(
        london_session_start=time(6, 0),
        london_session_end=time(21, 0),
        ny_session_start=time(6, 0),
        ny_session_end=time(21, 0),
        eod_exit_time=time(21, 0)
    )

    df_prep = engine.prepare_data(df_m5, df_1h, df_2h)
    eval_mask = (df_prep.index >= pd.Timestamp(start_dt, tz=timezone.utc)) & \
                (df_prep.index <= pd.Timestamp(end_dt, tz=timezone.utc))
    df_eval = df_prep.loc[eval_mask]
    n_bars = len(df_eval)

    times = df_eval.index
    opens = df_eval['open'].values
    highs = df_eval['high'].values
    lows = df_eval['low'].values
    closes = df_eval['close'].values

    m5_e9 = df_eval['ema9_5m'].values
    m5_e20 = df_eval['ema20_5m'].values
    vwap = df_eval['vwap_5m'].values

    h1_bias = df_eval['bias_1h'].values
    h1_e20 = df_eval['ema20_1h'].values
    h1_adx = df_eval['adx_1h'].values
    h1_atr = df_eval['atr_1h'].values

    # Test parameter matrix
    atr_mults = [0.75, 0.9, 1.0, 1.2, 1.5]
    rr_configs = [(1.4, 2.2), (1.5, 2.0), (1.5, 2.5), (1.2, 1.8)]

    results = []

    for atr_m in atr_mults:
        for tp1_rr, tp2_rr in rr_configs:
            trades = []
            balance = 100_000.0
            last_exit_idx = -1

            for i in range(1, n_bars):
                t = times[i].time()
                # Operational hours: 06:00 to 20:00 UTC
                if not (time(6, 0) <= t < time(20, 0)):
                    continue

                bias = h1_bias[i]
                if bias == 0 or h1_adx[i] < 15.0:
                    continue

                prev_diff = m5_e9[i - 1] - m5_e20[i - 1]
                curr_diff = m5_e9[i] - m5_e20[i]
                c = closes[i]

                sig_type = 0
                if bias == 1 and prev_diff <= 0 and curr_diff > 0:
                    if c > vwap[i] and c > h1_e20[i]:
                        sig_type = 1
                elif bias == -1 and prev_diff >= 0 and curr_diff < 0:
                    if c < vwap[i] and c < h1_e20[i]:
                        sig_type = -1

                if sig_type == 0:
                    continue

                # Strict single position
                if i <= last_exit_idx or i + 1 >= n_bars:
                    continue

                sl_dist = max(h1_atr[i] * atr_m, 5.0 * point)
                entry_p = opens[i + 1] + (spread_price if sig_type == 1 else -spread_price)

                risk_amt = balance * 0.01  # 1% risk
                raw_lots = risk_amt / (sl_dist * contract_size)
                # Ensure minimum lot and rounding
                lots = max(info.volume_min, round(raw_lots / info.volume_step) * info.volume_step)
                p_lots = round(lots * 0.5 / info.volume_step) * info.volume_step
                r_lots = max(info.volume_min, lots - p_lots)

                if sig_type == 1:
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
                                curr_sl = entry_p + spread_price  # Breakeven stop
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
                                curr_sl = entry_p - spread_price  # Breakeven stop
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

                last_exit_idx = t_exit

                if sig_type == 1:
                    p_gross = (p_exit - entry_p) * p_lots * contract_size
                    r_gross = (r_exit - entry_p) * r_lots * contract_size
                else:
                    p_gross = (entry_p - p_exit) * p_lots * contract_size
                    r_gross = (entry_p - r_exit) * r_lots * contract_size

                comm = lots * 5.0
                net = p_gross + r_gross - comm
                balance += net
                trades.append(net)

            res = analyze_trades(trades)
            if res:
                res["atr_m"] = atr_m
                res["tp1"] = tp1_rr
                res["tp2"] = tp2_rr
                results.append(res)

    # Print summary of top 5 results
    results.sort(key=lambda x: x["pnl"], reverse=True)
    print(f"\nTOP 5 CONFIGURATIONS FOR {sym}:")
    print(f"{'Config':32s} | {'Trades':6s} | {'Win Rate':8s} | {'PF':4s} | {'Net Profit':12s} | {'ROI':7s} | {'Max DD':6s}")
    print("-" * 85)
    for r in results[:5]:
        cfg_str = f"ATR: {r['atr_m']}x | TP: {r['tp1']}R / {r['tp2']}R"
        print(f"{cfg_str:32s} | {r['trades']:2d}     | {r['win_rate']:5.1f}%   | {r['pf']:4.2f} | ${r['pnl']:+10,.2f} | {r['roi']:+6.2f}% | {r['max_dd']:4.2f}%")


if __name__ == "__main__":
    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)

    print("STARTING SILVER (XAGUSD) & NASDAQ (NAS100) AUDIT...")
    run_symbol_test("XAGUSD", start_dt, end_dt)
    run_symbol_test("NAS100", start_dt, end_dt)
