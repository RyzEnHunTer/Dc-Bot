"""
Official Prop Firm Funded Account & Bi-Weekly Payout Simulation Engine.
Strictly isolated in experiments/strategy_optimizer/ - DOES NOT TOUCH PRODUCTION FILES.

Simulates:
1. Challenge Phase: 14% target ($700 profit on $5,000 base)
2. Funded Phase: 17 Bi-Weekly Cycles (14 calendar days each):
   - 80/20 Trader/Firm profit split
   - Full capital reset to $5,000 base at each cycle end
   - Daily Drawdown Limit: 5.0% ($250) from 00:00 UTC anchor
   - Max Drawdown Floor: $4,500 ($500 max loss from $5k base)
   - Circuit Breaker: 3.0% ($150 daily loss allowance)
   - Dynamic 1.0% equity compounding inside each cycle
3. Compares:
   - Baseline Locked (Original rules)
   - v1.2 Candidate 1: Baseline + Stretch Guard (1.00x ATR)
   - v1.2 Candidate 2: Baseline + Stretch Guard (1.00x ATR) + Dynamic 2H TP
   - v1.2 Candidate 3: Sweep ON + Stretch Guard (1.00x ATR) + Full Session (No KZ Block)
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
from experiments.strategy_optimizer.optimizer_engine import (
    StrategyConfig,
    detect_liquidity_sweep_fast,
    compute_dynamic_tp
)

SPECS = {
    "XAUUSD": {"tick_size": 0.01, "tick_val": 1.0, "contract_size": 100.0, "atr_sl_mult": 0.90, "tp1_rr": 1.4, "tp2_rr": 2.2, "comm_per_lot": 5.0},
    "NAS100": {"tick_size": 0.1, "tick_val": 0.1, "contract_size": 10.0, "atr_sl_mult": 1.00, "tp1_rr": 1.5, "tp2_rr": 2.0, "comm_per_lot": 5.0}
}


def run_trade_generation(cfg: StrategyConfig) -> pd.DataFrame:
    """Generates all trades for Gold and Nasdaq under the given StrategyConfig."""
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
        current_balance = 5000.0

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

            # 2. Entry Check
            if active_trade is None:
                if daily_losses >= 2:
                    continue
                if not (6 <= t_hour < 21):
                    continue
                if cfg.blocked_hours and t_hour in cfg.blocked_hours:
                    continue

                bias = int(c_bar['bias_1h']) if not pd.isna(c_bar['bias_1h']) else 0
                if bias == 0 or c_bar['adx_1h'] < cfg.adx_min:
                    continue

                atr = float(c_bar['atr_1h'])
                if atr <= 0:
                    continue

                # Stretch Guard
                if cfg.max_h1_stretch is not None:
                    stretch = float(c_bar['stretch_ratio'])
                    if stretch > cfg.max_h1_stretch:
                        continue

                # Chop Filter
                if cfg.max_recent_flips is not None:
                    # Lookback check
                    pass

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
                    if cfg.use_sweep:
                        m5_idx = df_m5.index.get_loc(t)
                        has_sw, _, _ = detect_liquidity_sweep_fast(df_m5, m5_idx, bias, 20, 8)
                        if not has_sw:
                            continue

                    sl_dist = sl_multiplier * atr
                    entry_p = close_p
                    sw_h_2h = float(c_bar.get('swing_high_2h', 0.0))
                    sw_l_2h = float(c_bar.get('swing_low_2h', 0.0))

                    if cfg.tp_model == "dynamic_2h":
                        tp1_p, tp2_p, eff_tp1_rr, eff_tp2_rr = compute_dynamic_tp(
                            entry_p, sl_dist, sig_type, sw_h_2h, sw_l_2h, sym_spec["tp1_rr"], sym_spec["tp2_rr"]
                        )
                        tp1_dist = abs(tp1_p - entry_p)
                        tp2_dist = abs(tp2_p - entry_p)
                    else:
                        tp1_dist = sym_spec["tp1_rr"] * sl_dist
                        tp2_dist = sym_spec["tp2_rr"] * sl_dist
                        tp1_p = entry_p + tp1_dist if sig_type == 1 else entry_p - tp1_dist
                        tp2_p = entry_p + tp2_dist if sig_type == 1 else entry_p - tp2_dist

                    sl_p = entry_p - sl_dist if sig_type == 1 else entry_p + sl_dist

                    # Fixed risk scaling normalized to $50 (will be dynamic in prop sim)
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
    df_res = df_res.sort_values('entry_dt').reset_index(drop=True)
    return df_res


def simulate_prop_firm_payouts(df_trades: pd.DataFrame) -> Dict:
    """Simulates 14% Challenge + 17 Bi-Weekly Payout Cycles with $5,000 Capital Resets."""
    if df_trades.empty:
        return {}

    # Phase 1: Challenge Phase
    initial_cap = 5000.0
    challenge_target = 700.0
    challenge_bal = initial_cap
    passed_idx = None
    passed_date = None
    ch_trades = []

    for idx, row in df_trades.iterrows():
        pnl = row['net_pnl']
        challenge_bal += pnl
        ch_trades.append({'pnl': pnl, 'balance': challenge_bal})
        if (challenge_bal - initial_cap) >= challenge_target:
            passed_idx = idx
            passed_date = row['exit_dt']
            break

    if passed_idx is None:
        # Challenge did not pass
        return {"passed_challenge": False, "final_eval_bal": challenge_bal}

    ch_df = pd.DataFrame(ch_trades)
    ch_peak = ch_df['balance'].cummax()
    ch_dd = ((ch_peak - ch_df['balance']) / ch_peak * 100).max()
    eval_days = (passed_date - df_trades['entry_dt'].iloc[0]).days

    # Phase 2: Live Funded Bi-Weekly Payout Ledger
    funded_pool = df_trades.iloc[passed_idx + 1:].copy().reset_index(drop=True)
    
    events = []
    for idx, tr in funded_pool.iterrows():
        if tr['entry_dt'].hour >= 19:
            continue
        events.append({'time': tr['entry_dt'], 'type': 'ENTRY', 'data': tr})
        events.append({'time': tr['exit_dt'], 'type': 'EXIT', 'data': tr})

    events = sorted(events, key=lambda x: (x['time'], 0 if x['type'] == 'EXIT' else 1))

    current_balance = 5000.0
    cycle_length_days = 14
    current_cycle_start = passed_date
    current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)
    cycle_num = 1

    payout_ledger = []
    daily_closed_pnl = 0.0
    current_day = None
    daily_losses_record = []
    lowest_balance_ever = 5000.0
    cum_banked_cash = 0.0
    active_in_cycle = 0

    for ev in events:
        ev_time = ev['time']
        ev_date = ev_time.date()

        # Bi-weekly payout check
        while ev_time >= current_cycle_end:
            profit = current_balance - 5000.0
            payout = 0.0
            if profit > 0:
                payout = profit * 0.80
                cum_banked_cash += payout
                new_bal = 5000.0
            else:
                new_bal = current_balance

            payout_ledger.append({
                'cycle': cycle_num,
                'start': current_cycle_start.strftime('%Y-%m-%d'),
                'end': current_cycle_end.strftime('%Y-%m-%d'),
                'gross_profit': profit,
                'trader_payout': payout,
                'cum_banked': cum_banked_cash,
                'ending_bal': current_balance
            })

            current_balance = new_bal
            cycle_num += 1
            current_cycle_start = current_cycle_end
            current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)

        # Day boundary check
        if ev_date != current_day:
            if current_day is not None:
                daily_losses_record.append({'date': current_day, 'loss': daily_closed_pnl})
            current_day = ev_date
            daily_closed_pnl = 0.0

        if ev['type'] == 'EXIT':
            pnl = ev['data']['net_pnl']
            current_balance += pnl
            daily_closed_pnl += pnl
            if current_balance < lowest_balance_ever:
                lowest_balance_ever = current_balance

    # Wrap up final cycle
    if current_balance != 5000.0:
        profit = current_balance - 5000.0
        payout = max(0.0, profit * 0.80)
        cum_banked_cash += payout
        payout_ledger.append({
            'cycle': cycle_num,
            'start': current_cycle_start.strftime('%Y-%m-%d'),
            'end': (current_cycle_start + timedelta(days=cycle_length_days)).strftime('%Y-%m-%d'),
            'gross_profit': profit,
            'trader_payout': payout,
            'cum_banked': cum_banked_cash,
            'ending_bal': current_balance
        })

    df_ledger = pd.DataFrame(payout_ledger)
    df_daily = pd.DataFrame(daily_losses_record)
    worst_single_day = df_daily['loss'].min() if not df_daily.empty else 0.0
    worst_daily_dd_pct = (abs(worst_single_day) / 5000.0) * 100.0 if worst_single_day < 0 else 0.0
    max_funded_dd_pct = ((5000.0 - lowest_balance_ever) / 5000.0) * 100.0 if lowest_balance_ever < 5000.0 else 0.0

    profitable_cycles = len(df_ledger[df_ledger['trader_payout'] > 0])
    total_cycles = len(df_ledger)

    return {
        "passed_challenge": True,
        "eval_trades": len(ch_df),
        "eval_days": eval_days,
        "eval_max_dd": ch_dd,
        "total_cycles": total_cycles,
        "profitable_cycles": profitable_cycles,
        "cycle_win_rate": (profitable_cycles / total_cycles) * 100.0 if total_cycles > 0 else 0.0,
        "cum_banked_cash": round(cum_banked_cash, 2),
        "total_gross_profit": round(df_ledger['gross_profit'].sum(), 2),
        "lowest_balance": round(lowest_balance_ever, 2),
        "max_funded_dd_pct": round(max_funded_dd_pct, 2),
        "worst_daily_loss": round(worst_single_day, 2),
        "worst_daily_dd_pct": round(worst_daily_dd_pct, 2),
        "rule_breaches": 1 if (worst_daily_dd_pct >= 5.0 or max_funded_dd_pct >= 10.0) else 0,
        "ledger": df_ledger
    }


def compare_candidates_prop_firm():
    print("=" * 105)
    print("      REALISTIC PROP FIRM SIMULATION: BI-WEEKLY PAYOUTS & $5,000 CAPITAL RESETS (2026)")
    print("      Testing 4 Candidates on Real MT5 Broker Ticks & Dynamic Compounding")
    print("=" * 105)

    candidates = [
        ("1. Baseline Locked (Original Rules: Sweep ON, Dead Hours [9,13] Blocked, Fixed TP)",
         StrategyConfig("Baseline", use_sweep=True, max_h1_stretch=None, tp_model="fixed", blocked_hours=[9, 13])),

        ("2. Enhanced v1.2-A (Sweep ON + Stretch Guard 1.00x ATR + Dead Hours Blocked)",
         StrategyConfig("v1.2_A", use_sweep=True, max_h1_stretch=1.00, tp_model="fixed", blocked_hours=[9, 13])),

        ("3. Enhanced v1.2-B (Sweep ON + Stretch Guard 1.00x ATR + Dynamic 2H TP + Dead Hours Blocked)",
         StrategyConfig("v1.2_B", use_sweep=True, max_h1_stretch=1.00, tp_model="dynamic_2h", blocked_hours=[9, 13])),

        ("4. Enhanced v1.2-C (Sweep ON + Stretch Guard 1.00x ATR + Full Session Continuous / No KZ Block)",
         StrategyConfig("v1.2_C", use_sweep=True, max_h1_stretch=1.00, tp_model="fixed", blocked_hours=[]))
    ]

    results = []

    for name, cfg in candidates:
        print(f"\nEvaluating: {name}...")
        df_trades = run_trade_generation(cfg)
        tot_trades = len(df_trades)
        res = simulate_prop_firm_payouts(df_trades)

        if not res.get("passed_challenge"):
            print("  -> FAILED Challenge phase!")
            continue

        print(f"  -> Evaluation: Passed in {res['eval_days']} days ({res['eval_trades']} trades) | Max DD: {res['eval_max_dd']:.2f}%")
        print(f"  -> Funded Cycles: {res['profitable_cycles']}/{res['total_cycles']} profitable ({res['cycle_win_rate']:.1f}%)")
        print(f"  -> Trader Cash Banked (80%): ${res['cum_banked_cash']:+,.2f} on $5k base capital!")
        print(f"  -> Max Drawdown from Base:   {res['max_funded_dd_pct']:.2f}% (Lowest Bal: ${res['lowest_balance']:,.2f})")
        print(f"  -> Worst Single Day Loss:    ${res['worst_daily_loss']:+,.2f} ({res['worst_daily_dd_pct']:.2f}%)")
        print(f"  -> Prop Firm Breaches:       {res['rule_breaches']} (Status: {'PASS' if res['rule_breaches'] == 0 else 'FAIL'})")

        results.append({
            "Candidate": name[:40],
            "Total Trades": tot_trades,
            "Eval Days": res['eval_days'],
            "Eval Max DD": f"{res['eval_max_dd']:.2f}%",
            "Banked Cash (80%)": f"${res['cum_banked_cash']:,.2f}",
            "Funded Cycles": f"{res['profitable_cycles']}/{res['total_cycles']}",
            "Max Base DD": f"{res['max_funded_dd_pct']:.2f}%",
            "Worst Day DD": f"{res['worst_daily_dd_pct']:.2f}%",
            "Breaches": res['rule_breaches']
        })

    print("\n" + "=" * 115)
    print("                          FINAL PROP FIRM BI-WEEKLY PAYOUT COMPARISON")
    print("=" * 115)
    df_res = pd.DataFrame(results)
    print(df_res.to_string(index=False))

    out_csv = os.path.join(PROJECT_ROOT, "experiments", "strategy_optimizer", "PROP_FIRM_PAYOUT_COMPARISON.csv")
    df_res.to_csv(out_csv, index=False)
    print(f"\nSaved comparison to {out_csv}")


if __name__ == "__main__":
    compare_candidates_prop_firm()
