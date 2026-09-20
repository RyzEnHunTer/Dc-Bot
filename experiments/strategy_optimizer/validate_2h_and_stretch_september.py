"""
September Live Broker Validation: Baseline vs 1H Stretch vs 2H Clearance vs Combined
Evaluates live MT5 broker data from September 1, 2026 through September 18, 2026 (Today).

Tests 5 specific variants:
1. Baseline (Official DCC rules)
2. Variant 1: 1H Stretch Guard Only (Stretch >= 0.40, Chase <= 0.50)
3. Variant 2: 2H Clearance Rule Only (Room to 2H Swing >= 1.0x SL)
4. Variant 3: Combined (1H Stretch Guard + 2H Clearance Rule)
5. Variant 4: Triple Shield (+ ADX <= 45.0)
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

from dcc_engine import DCCEngine
from experiments.strategy_optimizer.optimizer_engine import detect_liquidity_sweep_fast

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "atr_sl_mult": 0.90, "tp1_rr": 1.4, "tp2_rr": 2.2, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "atr_sl_mult": 1.00, "tp1_rr": 1.5, "tp2_rr": 2.0, "comm_per_lot": 5.0}
}

broker_offset = timedelta(hours=3)

def fetch_mt5_data(sym: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not mt5.initialize():
        raise RuntimeError("Failed to initialize MT5")

    matched = sym
    if not mt5.symbol_select(sym, True):
        for alias in [f"{sym}.m", f"{sym}_i", f"{sym}pro", "GOLD" if "XAU" in sym else "USTEC"]:
            if mt5.symbol_select(alias, True):
                matched = alias
                break

    start_warmup = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    end_now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

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

    return df_m5, df_h1, df_h2


def prepare_september_bars(sym: str):
    df_m5, df_h1, df_h2 = fetch_mt5_data(sym)
    engine = DCCEngine()
    df_prep = engine.prepare_data(df_m5, df_h1, df_h2)

    df_prep['stretch_ratio'] = (df_prep['close'] - df_prep['ema20_1h']).abs() / (df_prep['atr_1h'] + 1e-9)
    df_prep['chase_ratio'] = (df_prep['close'] - df_prep['ema9_5m']).abs() / (df_prep['atr_1h'] + 1e-9)

    sept_bars = df_prep[df_prep.index >= '2026-09-01 00:00:00+00:00'].copy()
    return sept_bars, df_m5


def simulate_version(
    sym: str,
    sept_bars: pd.DataFrame,
    df_m5: pd.DataFrame,
    name: str,
    min_stretch: Optional[float] = None,
    max_adx: Optional[float] = None,
    max_chase: Optional[float] = None,
    min_room_r: Optional[float] = None,
    blocked_hours: Optional[List[int]] = None
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
            if blocked_hours and t_hour in blocked_hours:
                continue

            bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
            if bias == 0:
                continue
            
            adx = float(c_bar['adx_1h'])
            if adx < 15.0:
                continue
            if max_adx is not None and adx > max_adx:
                continue

            atr = float(c_bar['atr_1h'])
            if atr <= 0:
                continue

            stretch = float(c_bar['stretch_ratio'])
            if min_stretch is not None and stretch < min_stretch:
                continue

            chase = float(c_bar['chase_ratio'])
            if max_chase is not None and chase > max_chase:
                continue

            # 2H Swing Clearance Check
            sw_h = float(c_bar.get('swing_high_2h', np.nan))
            sw_l = float(c_bar.get('swing_low_2h', np.nan))
            sl_dist = sl_multiplier * atr

            if bias == 1:
                room = sw_h - close_p if pd.notna(sw_h) else 9999.0
            else:
                room = close_p - sw_l if pd.notna(sw_l) else 9999.0

            room_r = room / sl_dist if sl_dist > 0 else 999.0
            if min_room_r is not None and room_r < min_room_r:
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

                tp1_dist = sym_spec["tp1_rr"] * sl_dist
                tp2_dist = sym_spec["tp2_rr"] * sl_dist
                entry_p = close_p
                
                sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist
                tp1_p = entry_p + tp1_dist if sig_type == 1 else entry_p - tp1_dist
                tp2_p = entry_p + tp2_dist if sig_type == 1 else entry_p - tp2_dist

                raw_lots = 50.0 / (sl_dist * val_per_pt)
                tot_lots = max(0.01, round(raw_lots, 2))
                part_lots = round(tot_lots * 0.5, 2)
                run_lots = round(tot_lots - part_lots, 2)

                active_trade = {
                    "version": name,
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


def run_portfolio_september(trades: List[Dict], label: str) -> Dict:
    if not trades:
        return {"Candidate": label, "Trades": 0}

    df = pd.DataFrame(trades).sort_values('entry_time').reset_index(drop=True)
    bal = 5000.0
    eq_curve = [bal]
    daily_tracker = {}
    trade_pnls = []

    for idx, tr in df.iterrows():
        t_date = tr['entry_time'].strftime('%Y-%m-%d')
        if t_date not in daily_tracker:
            daily_tracker[t_date] = 0.0

        if daily_tracker[t_date] <= -150.0:
            continue

        base_pnl = tr['net_pnl']
        scale = bal / 5000.0
        scaled_pnl = round(base_pnl * scale, 2)

        bal += scaled_pnl
        daily_tracker[t_date] += scaled_pnl
        eq_curve.append(bal)
        trade_pnls.append(scaled_pnl)

    s = pd.Series(eq_curve)
    pk = s.cummax()
    dd = (pk - s) / pk * 100.0
    max_dd = dd.max()

    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    wr = (len(wins) / len(trade_pnls) * 100.0) if trade_pnls else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) if losses else 1.0
    pf = gross_win / gross_loss if gross_loss > 0 else 99.0
    worst_day = min(daily_tracker.values()) if daily_tracker else 0.0

    return {
        "Candidate": label,
        "Total Trades": len(trade_pnls),
        "Win Rate": f"{wr:.1f}%",
        "Net Profit": f"+${bal - 5000.0:,.2f}",
        "Profit Factor": f"{pf:.2f}",
        "Max Drawdown": f"{max_dd:.2f}%",
        "Worst Day": f"-${abs(worst_day):.2f}"
    }


def main():
    print("Fetching live MT5 data for September 1 to 18, 2026...")
    xau_bars, xau_m5 = prepare_september_bars("XAUUSD")
    nas_bars, nas_m5 = prepare_september_bars("NAS100")
    print(f"Loaded {len(xau_bars)} Gold bars and {len(nas_bars)} Nasdaq bars for September.")

    configs = [
        ("Baseline (Locked)", None, None, None, None),
        ("Variant 1: 1H Stretch Guard ONLY", 0.40, None, 0.50, None),
        ("Variant 2: 2H Clearance Rule ONLY (>= 1.0R)", None, None, None, 1.0),
        ("Variant 3: 1H Stretch + 2H Clearance COMBINED", 0.40, None, 0.50, 1.0),
        ("Variant 4: Triple Shield (+ ADX <= 45)", 0.40, 45.0, 0.50, 1.0),
    ]

    results = []
    print("\n" + "=" * 90)
    print("SEPTEMBER 1 - 18, 2026 LIVE BROKER COMPARISON SCORECARD")
    print("=" * 90)

    for label, min_s, max_adx, max_ch, min_room in configs:
        t_xau = simulate_version("XAUUSD", xau_bars, xau_m5, label, min_s, max_adx, max_ch, min_room)
        t_nas = simulate_version("NAS100", nas_bars, nas_m5, label, min_s, max_adx, max_ch, min_room)
        combined_trades = t_xau + t_nas
        res = run_portfolio_september(combined_trades, label)
        results.append(res)

    df_res = pd.DataFrame(results)
    print(df_res.to_string(index=False))

    out_csv = os.path.join(PROJECT_ROOT, "experiments", "strategy_optimizer", "SEPTEMBER_2H_CLEARANCE_SCORECARD.csv")
    df_res.to_csv(out_csv, index=False)
    print(f"\nSaved September scorecard to {out_csv}")


if __name__ == "__main__":
    main()
