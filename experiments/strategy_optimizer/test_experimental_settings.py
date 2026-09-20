"""
Experimental Tuning Benchmark: Test Top 4 Candidate Settings
Evaluates:
1. Baseline Locked (Original verified baseline)
2. Candidate 1 (Anti-Chop Guard): Min 1H EMA20 separation >= 0.50x ATR
3. Candidate 2 (Exhaustion Shield): ADX ceiling <= 42.0 (Page 7 PDF rule)
4. Candidate 3 (Golden A+ Balanced): Min 1H separation >= 0.45x ATR + ADX ceiling <= 42.0 + 5M No-Chase (dist_5m_ema9 <= 0.35x ATR)
5. Candidate 4 (Full Session Golden A+): Candidate 3 with hours 9 & 13 active
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.strategy_optimizer.backtester import BacktestDataset
from experiments.strategy_optimizer.optimizer_engine import detect_liquidity_sweep_fast

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "atr_sl_mult": 0.90, "tp1_rr": 1.4, "tp2_rr": 2.2, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "atr_sl_mult": 1.00, "tp1_rr": 1.5, "tp2_rr": 2.0, "comm_per_lot": 5.0}
}

class CandidateConfig:
    def __init__(
        self,
        name: str,
        min_1h_stretch: Optional[float] = None,
        max_adx: Optional[float] = None,
        max_5m_chase: Optional[float] = None,
        blocked_hours: Optional[List[int]] = None
    ):
        self.name = name
        self.min_1h_stretch = min_1h_stretch
        self.max_adx = max_adx
        self.max_5m_chase = max_5m_chase
        self.blocked_hours = blocked_hours if blocked_hours is not None else [9, 13]


def run_candidate(cfg: CandidateConfig) -> pd.DataFrame:
    all_trades = []
    
    for sym in ["XAUUSD", "NAS100"]:
        eval_bars, df_m5 = BacktestDataset.get_data(sym)
        sym_spec = SPECS[sym]
        val_per_pt = sym_spec["tick_val"] / sym_spec["tick_size"]
        sl_multiplier = sym_spec["atr_sl_mult"]
        comm_per_lot = sym_spec["comm_per_lot"]

        active_trade = None
        daily_losses = 0
        current_day = ""
        n_bars = len(eval_bars)

        for i in range(25, n_bars):
            c_bar = eval_bars.iloc[i]
            p_bar = eval_bars.iloc[i - 1]
            t = eval_bars.index[i]
            t_hour = t.hour
            t_day = t.strftime('%Y-%m-%d')

            if t_day != current_day:
                current_day = t_day
                daily_losses = 0

            high_p = float(c_bar['high'])
            low_p = float(c_bar['low'])
            close_p = float(c_bar['close'])

            # 1. Manage Active Trade
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
                    pos['exit_reason'] = "END_OF_DATA"
                    comm = pos['total_lots'] * comm_per_lot
                    run_pts = (close_p - pos['entry_price']) if is_buy else (pos['entry_price'] - close_p)
                    p2 = pos['run_lots'] * run_pts * val_per_pt
                    p1 = (pos['part_lots'] * pos['tp1_dist'] * val_per_pt) if pos['tp1_hit'] else (pos['part_lots'] * run_pts * val_per_pt)
                    pos['net_pnl'] = round(p1 + p2 - comm, 2)
                    all_trades.append(pos)
                    active_trade = None
                    continue

            # 2. Entry Signals
            if active_trade is None:
                if daily_losses >= 2:
                    continue
                if not (6 <= t_hour < 21):
                    continue
                if cfg.blocked_hours and t_hour in cfg.blocked_hours:
                    continue

                bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
                if bias == 0:
                    continue
                
                adx = float(c_bar['adx_1h'])
                if adx < 15.0:
                    continue
                if cfg.max_adx is not None and adx > cfg.max_adx:
                    continue

                atr = float(c_bar['atr_1h'])
                if atr <= 0:
                    continue

                vwap = float(c_bar['vwap_5m'])
                h1_e20 = float(c_bar['ema20_1h'])
                curr_diff = float(c_bar['ema9_5m']) - float(c_bar['ema20_5m'])
                prev_diff = float(p_bar['ema9_5m']) - float(p_bar['ema20_5m'])

                # Trend separation check
                dist_1h_e20 = abs(close_p - h1_e20)
                stretch_1h = dist_1h_e20 / atr
                if cfg.min_1h_stretch is not None and stretch_1h < cfg.min_1h_stretch:
                    continue

                # 5M chase check
                e9_5m = float(c_bar['ema9_5m'])
                dist_5m_e9 = abs(close_p - e9_5m)
                if cfg.max_5m_chase is not None and (dist_5m_e9 / atr) > cfg.max_5m_chase:
                    continue

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

                    sl_dist = sl_multiplier * atr
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
                        "symbol": sym,
                        "direction": "BUY" if sig_type == 1 else "SELL",
                        "entry_time": t,
                        "entry_price": entry_p,
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
                    }

    if not all_trades:
        return pd.DataFrame()
    df_res = pd.DataFrame(all_trades)
    df_res['entry_dt'] = pd.to_datetime(df_res['entry_time'])
    df_res['exit_dt'] = pd.to_datetime(df_res['exit_time'])
    return df_res.sort_values('entry_dt').reset_index(drop=True)


def evaluate_summary(name: str, df: pd.DataFrame) -> Dict:
    if df.empty:
        return {"name": name, "trades": 0}
    tot = len(df)
    wins = df[df['net_pnl'] > 0]
    losses = df[df['net_pnl'] <= 0]
    wr = len(wins) / tot * 100
    pnl = df['net_pnl'].sum()
    gp = wins['net_pnl'].sum()
    gl = abs(losses['net_pnl'].sum())
    pf = gp / gl if gl > 0 else 999.0

    # Drawdown
    bal = 5000.0
    equity = [bal]
    for p in df['net_pnl']:
        bal += p
        equity.append(bal)
    eq_s = pd.Series(equity)
    peak = eq_s.cummax()
    max_dd = ((peak - eq_s) / peak * 100).max()
    lowest = eq_s.min()

    # Daily DD
    df['exit_date'] = df['exit_dt'].dt.date
    daily = df.groupby('exit_date')['net_pnl'].sum()
    worst_day = abs(daily.min()) if len(daily) > 0 else 0
    worst_day_dd = worst_day / 5000.0 * 100

    # Challenge pass speed (to +$700)
    ch_bal = 5000.0
    ch_trades = 0
    ch_days = 0
    for idx, r in df.iterrows():
        ch_bal += r['net_pnl']
        ch_trades += 1
        if ch_bal >= 5700.0:
            ch_days = (r['exit_dt'] - df['entry_dt'].iloc[0]).days
            break

    return {
        "Candidate": name,
        "Total Trades": tot,
        "Win Rate": f"{wr:.1f}%",
        "Profit Factor": f"{pf:.2f}",
        "Net Profit": f"+${pnl:,.2f}",
        "Max DD": f"{max_dd:.2f}%",
        "Worst Day DD": f"{worst_day_dd:.2f}%",
        "Challenge Pass": f"{ch_days}d ({ch_trades} tr)" if ch_days > 0 else "N/A"
    }


def main():
    candidates = [
        CandidateConfig("1. Baseline Locked (Original)", min_1h_stretch=None, max_adx=None, max_5m_chase=None, blocked_hours=[9, 13]),
        CandidateConfig("2. Anti-Chop (1H Stretch >= 0.50x)", min_1h_stretch=0.50, max_adx=None, max_5m_chase=None, blocked_hours=[9, 13]),
        CandidateConfig("3. Exhaustion Shield (ADX <= 42)", min_1h_stretch=None, max_adx=42.0, max_5m_chase=None, blocked_hours=[9, 13]),
        CandidateConfig("4. Golden A+ Combo (Anti-Chop + ADX + No-Chase)", min_1h_stretch=0.45, max_adx=42.0, max_5m_chase=0.35, blocked_hours=[9, 13]),
        CandidateConfig("5. Full Session Golden A+ (No Blocked Hours)", min_1h_stretch=0.45, max_adx=42.0, max_5m_chase=0.35, blocked_hours=[])
    ]

    print("=" * 95)
    print("      EXPERIMENTAL TUNING BENCHMARK: 8-MONTH ACCURATE AUDIT")
    print("=" * 95)
    
    rows = []
    for c in candidates:
        print(f"Running {c.name}...")
        df_res = run_candidate(c)
        stats = evaluate_summary(c.name, df_res)
        rows.append(stats)
        print(f"  -> {stats['Total Trades']} Trades | WR: {stats['Win Rate']} | PF: {stats['Profit Factor']} | Net: {stats['Net Profit']} | Max DD: {stats['Max DD']} | Eval: {stats['Challenge Pass']}")

    summary_df = pd.DataFrame(rows)
    print("\n" + "=" * 95)
    print("                              FINAL SCORECARD")
    print("=" * 95)
    print(summary_df.to_string(index=False))

if __name__ == "__main__":
    main()
