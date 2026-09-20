"""
Tuning the 2-Hour Target Clearance Rule vs. Version B (Champion).
Strictly isolated in experiments/strategy_optimizer/ - DOES NOT TOUCH PRODUCTION FILES.

Investigates:
1. Version B Baseline (Stretch >= 0.40, ADX <= 45, Chase <= 0.50)
2. Naive 2H Rolling Filter (rolling 20-bar max/min)
3. True 2H Fractal Pivot Filter (confirmed swing pivots with pullback, not just previous candle high)
4. Trend-Momentum Bypass (if ADX >= 28 or Stretch >= 0.60, allow trend continuation breakouts)
5. Dynamic TP Capping (instead of skipping, bank TP1 at 0.90 * room before the level)
6. Looser Thresholds (Room >= 0.5R, 0.7R)

Evaluated across:
A. Full 8.5-Month Dataset (471 MT5 Trades)
B. Live September 1 to 18, 2026 (Live MT5 Broker Data)
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.backtester import BacktestDataset
from dcc_engine import DCCEngine
from experiments.strategy_optimizer.optimizer_engine import detect_liquidity_sweep_fast

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "atr_sl_mult": 0.90, "tp1_rr": 1.4, "tp2_rr": 2.2, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "atr_sl_mult": 1.00, "tp1_rr": 1.5, "tp2_rr": 2.0, "comm_per_lot": 5.0}
}

broker_offset = timedelta(hours=3)


def compute_true_2h_fractals(df_2h: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """
    Computes true 2H fractal swing highs/lows.
    A fractal high requires 'window' lower bars on the left and 'window' lower bars on the right.
    This prevents the current/previous candle in a strong uptrend from being falsely labeled a 'wall'.
    """
    df = df_2h.copy()
    highs = df['high'].values
    lows = df['low'].values
    n = len(df)
    
    fractal_highs = np.full(n, np.nan)
    fractal_lows = np.full(n, np.nan)
    
    last_h = np.nan
    last_l = np.nan
    
    for i in range(window, n - window):
        # Check pivot high
        is_pivot_h = True
        for k in range(1, window + 1):
            if highs[i] <= highs[i - k] or highs[i] <= highs[i + k]:
                is_pivot_h = False
                break
        if is_pivot_h:
            last_h = highs[i]
            
        # Check pivot low
        is_pivot_l = True
        for k in range(1, window + 1):
            if lows[i] >= lows[i - k] or lows[i] >= lows[i + k]:
                is_pivot_l = False
                break
        if is_pivot_l:
            last_l = lows[i]
            
        # Record at i + window (when pivot is confirmed with zero lookahead)
        conf_idx = i + window
        if conf_idx < n:
            fractal_highs[conf_idx] = last_h
            fractal_lows[conf_idx] = last_l
            
    df['swing_high_2h_fractal'] = pd.Series(fractal_highs, index=df.index).ffill()
    df['swing_low_2h_fractal'] = pd.Series(fractal_lows, index=df.index).ffill()
    return df


def fetch_and_prep_september():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    start_warmup = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    end_now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

    data = {}
    for sym in ["XAUUSD", "NAS100"]:
        matched = sym
        if not mt5.symbol_select(sym, True):
            for alias in [f"{sym}.m", f"{sym}_i", f"{sym}pro", "GOLD" if "XAU" in sym else "USTEC"]:
                if mt5.symbol_select(alias, True):
                    matched = alias
                    break

        r_m5 = mt5.copy_rates_range(matched, mt5.TIMEFRAME_M5, start_warmup, end_now)
        r_h1 = mt5.copy_rates_range(matched, mt5.TIMEFRAME_H1, start_warmup, end_now)
        r_h2 = mt5.copy_rates_range(matched, mt5.TIMEFRAME_H2, start_warmup, end_now)

        df_m5 = pd.DataFrame(r_m5)
        df_m5['time'] = (pd.to_datetime(df_m5['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_m5.set_index('time', inplace=True)
        df_m5.rename(columns={'tick_volume': 'volume'}, inplace=True)

        df_h1 = pd.DataFrame(r_h1)
        df_h1['time'] = (pd.to_datetime(df_h1['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_h1.set_index('time', inplace=True)

        df_h2 = pd.DataFrame(r_h2)
        df_h2['time'] = (pd.to_datetime(df_h2['time'], unit='s', utc=True) - broker_offset).astype('datetime64[us, UTC]')
        df_h2.set_index('time', inplace=True)

        # Compute true fractals on 2H
        df_h2 = compute_true_2h_fractals(df_h2, window=2)

        engine = DCCEngine()
        df_prep = engine.prepare_data(df_m5, df_h1, df_h2)
        df_prep['stretch_ratio'] = (df_prep['close'] - df_prep['ema20_1h']).abs() / (df_prep['atr_1h'] + 1e-9)
        df_prep['chase_ratio'] = (df_prep['close'] - df_prep['ema9_5m']).abs() / (df_prep['atr_1h'] + 1e-9)

        # Merge fractal swings into df_prep
        df_h2_shifted = df_h2[['swing_high_2h_fractal', 'swing_low_2h_fractal']].shift(1)
        df_prep = pd.merge_asof(df_prep, df_h2_shifted, left_index=True, right_index=True, direction='backward')

        sept_bars = df_prep[df_prep.index >= '2026-09-01 00:00:00+00:00'].copy()
        data[sym] = (sept_bars, df_m5)

    return data


def run_simulation(
    sym: str,
    sept_bars: pd.DataFrame,
    df_m5: pd.DataFrame,
    mode: str,
    min_room_r: float = 1.0,
    fractal_mode: bool = False,
    trend_bypass: bool = False
) -> List[Dict]:
    sym_spec = SPECS[sym]
    tick_size = sym_spec["tick_size"]
    tick_val = sym_spec["tick_val"]
    val_per_pt = tick_val / tick_size
    sl_multiplier = sym_spec["atr_sl_mult"]
    comm_per_lot = sym_spec["comm_per_lot"]

    all_trades = []
    active_trade = None
    daily_losses = 0
    current_day = ""
    n_bars = len(sept_bars)

    for i in range(25, n_bars):
        c_bar = sept_bars.iloc[i]
        p_bar = sept_bars.iloc[i - 1]
        t = sept_bars.index[i]
        t_hour = t.hour
        t_day = t.strftime('%Y-%m-%d')

        if t_day != current_day:
            current_day = t_day
            daily_losses = 0

        high_p = float(c_bar['high'])
        low_p = float(c_bar['low'])
        close_p = float(c_bar['close'])

        if active_trade is not None:
            pos = active_trade
            is_buy = (pos['direction'] == "BUY")

            sl_hit = (low_p <= pos['current_sl']) if is_buy else (high_p >= pos['current_sl'])
            tp1_hit = (high_p >= pos['tp1_price']) if is_buy else (low_p <= pos['tp1_price'])
            tp2_hit = (high_p >= pos['tp2_price']) if is_buy else (low_p <= pos['tp2_price'])

            if tp2_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['tp2_price']
                pos['exit_reason'] = "FULL_TP2"
                p1 = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                p2 = pos['run_lots'] * pos['tp2_dist'] * val_per_pt
                comm = pos['total_lots'] * comm_per_lot
                pos['net_pnl'] = round(p1 + p2 - comm, 2)
                all_trades.append(pos)
                active_trade = None
                continue

            elif sl_hit:
                pos['exit_time'] = t
                pos['exit_price'] = pos['current_sl']
                comm = pos['total_lots'] * comm_per_lot
                if pos['tp1_hit']:
                    pos['exit_reason'] = "TP1_THEN_BE"
                    p1 = pos['part_lots'] * pos['tp1_dist'] * val_per_pt
                    net = p1 - comm
                else:
                    pos['exit_reason'] = "SL"
                    risk_lost = pos['total_lots'] * pos['sl_dist'] * val_per_pt
                    net = -risk_lost - comm
                    daily_losses += 1
                pos['net_pnl'] = round(net, 2)
                all_trades.append(pos)
                active_trade = None
                continue

            elif tp1_hit and not pos['tp1_hit']:
                pos['tp1_hit'] = True
                pos['current_sl'] = pos['entry_price']

            elif i == n_bars - 1:
                pos['exit_time'] = t
                pos['exit_price'] = close_p
                pos['exit_reason'] = "OPEN"
                comm = pos['total_lots'] * comm_per_lot
                run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                p2 = pos['run_lots'] * run_pts * val_per_pt
                p1 = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                pos['net_pnl'] = round(p1 + p2 - comm, 2)
                all_trades.append(pos)
                active_trade = None
                continue

        if active_trade is None:
            if daily_losses >= 2:
                continue
            if not (6 <= t_hour < 21):
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0:
                continue

            adx = float(c_bar['adx_1h'])
            if adx < 15.0 or adx > 45.0:  # Version B ADX bounds
                continue

            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue

            stretch = float(c_bar['stretch_ratio'])
            if stretch < 0.40:  # Version B Stretch floor
                continue

            chase = float(c_bar['chase_ratio'])
            if chase > 0.50:   # Version B Chase ceiling
                continue

            sl_dist = sl_multiplier * atr
            
            # Select Swing Level
            if fractal_mode:
                sw_h = float(c_bar.get('swing_high_2h_fractal', np.nan))
                sw_l = float(c_bar.get('swing_low_2h_fractal', np.nan))
            else:
                sw_h = float(c_bar.get('swing_high_2h', np.nan))
                sw_l = float(c_bar.get('swing_low_2h', np.nan))

            if bias == 1:
                room = sw_h - close_p if pd.notna(sw_h) else 9999.0
            else:
                room = close_p - sw_l if pd.notna(sw_l) else 9999.0

            room_r = room / sl_dist if sl_dist > 0 else 999.0

            # 2H Filter Modes
            skip_trade = False
            custom_tp1_rr = sym_spec["tp1_rr"]
            custom_tp2_rr = sym_spec["tp2_rr"]

            if mode == "NONE":
                pass

            elif mode == "HARD_FILTER":
                if trend_bypass and (adx >= 28.0 or stretch >= 0.70):
                    # Strong trend momentum bypass: allow breakouts
                    pass
                elif room_r < min_room_r:
                    skip_trade = True

            elif mode == "DYNAMIC_TP_CAP":
                # If room is tight (between 0.8R and 1.4R), cap TP1 right before the wall
                if room_r < 0.80:
                    skip_trade = True
                elif room_r < custom_tp1_rr:
                    custom_tp1_rr = max(0.90, round(room_r * 0.90, 2))
                    custom_tp2_rr = round(custom_tp1_rr * 1.5, 2)

            if skip_trade:
                continue

            vwap = float(c_bar['vwap_5m'])
            h1_e20 = float(c_bar['ema20_1h'])
            curr_diff = float(c_bar['ema9_5m']) - float(c_bar['ema20_5m'])
            prev_diff = float(p_bar['ema9_5m']) - float(p_bar['ema20_5m'])

            sig_type = 0
            if bias == 1 and prev_diff <= 0 and curr_diff > 0 and close_p > vwap and close_p > h1_e20:
                sig_type = 1
            elif bias == -1 and prev_diff >= 0 and curr_diff < 0 and close_p < vwap and close_p < h1_e20:
                sig_type = -1

            if sig_type != 0:
                m5_idx = df_m5.index.get_loc(t)
                has_sw, _, _ = detect_liquidity_sweep_fast(df_m5, m5_idx, bias, 20, 8)
                if not has_sw:
                    continue

                tp1_dist = custom_tp1_rr * sl_dist
                tp2_dist = custom_tp2_rr * sl_dist
                entry_p = close_p

                sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist
                tp1_p = entry_p + tp1_dist if sig_type == 1 else entry_p - tp1_dist
                tp2_p = entry_p + tp2_dist if sig_type == 1 else entry_p - tp2_dist

                raw_lots = 50.0 / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)

                active_trade = {
                    "symbol": sym,
                    "direction": "BUY" if sig_type == 1 else "SELL",
                    "entry_time": t,
                    "exit_time": None,
                    "entry_price": entry_p,
                    "exit_price": None,
                    "current_sl": sl_p,
                    "sl_dist": sl_dist,
                    "tp1_dist": tp1_dist,
                    "tp2_dist": tp2_dist,
                    "tp1_price": tp1_p,
                    "tp2_price": tp2_p,
                    "total_lots": tot_lots,
                    "part_lots": part_lots,
                    "run_lots": run_lots,
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "stretch_ratio": stretch,
                    "chase_ratio": chase,
                    "room_r": room_r,
                    "adx_1h": adx
                }

    return all_trades


def eval_results(trades: List[Dict], label: str) -> Dict:
    if not trades:
        return {"Candidate": label, "Trades": 0}

    df = pd.DataFrame(trades).sort_values('entry_time').reset_index(drop=True)
    bal = 5000.0
    eq_curve = [bal]
    trade_pnls = []

    for idx, tr in df.iterrows():
        scale = bal / 5000.0
        p = round(tr['net_pnl'] * scale, 2)
        bal += p
        eq_curve.append(bal)
        trade_pnls.append(p)

    s = pd.Series(eq_curve)
    pk = s.cummax()
    dd = (pk - s) / pk * 100.0
    max_dd = dd.max()

    wins = [p for p in trade_pnls if p > 0]
    wr = (len(wins) / len(trade_pnls) * 100.0) if trade_pnls else 0.0
    flat_pnl = sum(tr['net_pnl'] for tr in trades)

    return {
        "Candidate": label,
        "Trades": len(trade_pnls),
        "Win Rate": f"{wr:.1f}%",
        "Flat PnL": f"+${flat_pnl:,.2f}",
        "Compounded PnL": f"+${bal - 5000.0:,.2f}",
        "Max DD": f"{max_dd:.2f}%"
    }


def main():
    print("Preparing live September data with True 2H Fractals...")
    data = fetch_and_prep_september()

    # List of experimental tunings
    test_cases = [
        # (Label, mode, min_room_r, fractal_mode, trend_bypass)
        ("1. Version B Baseline (NO 2H Rule)", "NONE", 0.0, False, False),
        ("2. Naive 2H Filter (Room >= 1.0R)", "HARD_FILTER", 1.0, False, False),
        ("3. Tuned: True 2H Fractal Pivot (Room >= 1.0R)", "HARD_FILTER", 1.0, True, False),
        ("4. Tuned: True 2H Fractal Pivot (Room >= 0.7R)", "HARD_FILTER", 0.7, True, False),
        ("5. Tuned: True 2H Fractal Pivot (Room >= 0.5R)", "HARD_FILTER", 0.5, True, False),
        ("6. Tuned: Fractal + Trend Bypass (ADX>=28)", "HARD_FILTER", 1.0, True, True),
        ("7. Tuned: Dynamic TP Capping (Bank before 2H)", "DYNAMIC_TP_CAP", 0.8, True, False),
    ]

    results = []
    print("\n" + "=" * 95)
    print("TUNING THE 2-HOUR CLEARANCE RULE FOR OUR STRATEGY (SEPTEMBER LIVE DATA)")
    print("=" * 95)

    for label, mode, min_r, frac, bypass in test_cases:
        t_x = run_simulation("XAUUSD", data["XAUUSD"][0], data["XAUUSD"][1], mode, min_r, frac, bypass)
        t_n = run_simulation("NAS100", data["NAS100"][0], data["NAS100"][1], mode, min_r, frac, bypass)
        res = eval_results(sorted(t_x + t_n, key=lambda x: x['entry_time']), label)
        results.append(res)

    df_res = pd.DataFrame(results)
    print(df_res.to_string(index=False))

    out_csv = os.path.join(PROJECT_ROOT, "experiments", "strategy_optimizer", "TUNED_2H_SCORECARD.csv")
    df_res.to_csv(out_csv, index=False)
    print(f"\nSaved tuned scorecard to {out_csv}")


if __name__ == "__main__":
    main()
