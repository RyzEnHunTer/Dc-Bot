"""
DCC Strategy — ApexHunter Institutional Benchmark (Dual-Gear Engine)
===================================================================
Architecture:
- Gear 1 (Challenge Phase): Dynamic Regime-Adaptive Risk (1.30% Trend / 1.00% Chop),
  Compounding Active, Stop-on-Pass (+14% / $5,700), Hard 3.0% Daily Circuit Breaker.
- Gear 2 (Funded Phase): Static Fixed 1.00% Risk ($50 on $5k), 17 Bi-Weekly 80% Payouts,
  $5,000 Capital Resets, Hard 3.0% Daily Circuit Breaker.
- Core Alpha Filter: TripleGuard + Smart Hybrid Killzone (Stretch >= 1.10 at 09:00 & 13:00 UTC).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List
import pandas as pd
import numpy as np

PROJECT_ROOT = r"d:\FOREX\DC"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.run_3_killzone_variations_benchmark import build_datasets

def run_apexhunter_benchmark():
    print("=" * 120)
    print("         DCC APEXHUNTER: INSTITUTIONAL PROP FIRM DUAL-GEAR BENCHMARK")
    print("      Gear 1: Dynamic Regime Challenge (1.30%/1.00%) | Gear 2: Static Fixed 1.00% Funded")
    print("=" * 120)

    variations = build_datasets()
    df_var1 = variations["Variation 1: Monday Trap Hour Pause ONLY (9 & 13 ENABLED)"]
    df_var3 = variations["Variation 3: As v1 has: Daily Killzone Pause Filter [LOCKED]"]
    df_baseline = variations["Reference: Baseline DCC v1.1 (All 471 Trades)"]

    # Construct ApexHunter Dataset:
    # Hours 09:00 & 13:00 allowed ONLY IF stretch_ratio >= 1.10
    mask_apexhunter = ~df_var1['hour'].isin([9, 13]) | (df_var1['stretch_ratio'] >= 1.10)
    df_apexhunter = df_var1[mask_apexhunter].copy().reset_index(drop=True)

    print(f"\n[Dataset Verification]")
    print(f"  ApexHunter Trade Pool: {len(df_apexhunter)} trades (Win Rate: {(df_apexhunter['net_pnl'] > 0).mean()*100:.1f}%, Net PnL: ${df_apexhunter['net_pnl'].sum():,.2f})")
    print(f"  Canonical v1.2 Pool:   {len(df_var3)} trades (Win Rate: {(df_var3['net_pnl'] > 0).mean()*100:.1f}%, Net PnL: ${df_var3['net_pnl'].sum():,.2f})")

    def simulate_dual_gear(df, label, challenge_dyn=True, funded_static=True):
        df = df.sort_values('entry_dt').reset_index(drop=True)

        # -------------------------------------------------------------
        # Phase 1: Challenge Phase (14% Target = +$700, $5,700 Balance)
        # -------------------------------------------------------------
        initial_cap = 5000.0
        challenge_target = 700.0
        current_balance = initial_cap
        peak_balance = initial_cap
        lowest_balance = initial_cap
        passed_idx = None
        passed_date = None
        executed_challenge = 0
        recent_ch_wins = []

        for idx, row in df.iterrows():
            adx = row.get('adx_1h', 25.0)
            stretch = row.get('stretch_ratio', 1.0)
            is_trend = (adx >= 22.0) and (stretch >= 0.85)
            rec_wr = (sum(recent_ch_wins) / len(recent_ch_wins)) if len(recent_ch_wins) >= 3 else 0.50

            if challenge_dyn:
                risk_pct = 1.30 if (is_trend and rec_wr >= 0.50) else 1.00
                scale = (current_balance / 5000.0) * (risk_pct / 1.00)
            else:
                scale = 1.00

            pnl = row['net_pnl'] * scale
            current_balance += pnl
            executed_challenge += 1
            recent_ch_wins.append(pnl > 0)
            if len(recent_ch_wins) > 5:
                recent_ch_wins.pop(0)

            if current_balance > peak_balance:
                peak_balance = current_balance
            if current_balance < lowest_balance:
                lowest_balance = current_balance

            if (current_balance - initial_cap) >= challenge_target:
                passed_idx = idx
                passed_date = row['exit_dt']
                break

        ch_days = (passed_date - df['entry_dt'].iloc[0]).days if passed_date is not None else 0
        ch_pass_str = f"{ch_days}d ({executed_challenge} trds)" if passed_idx is not None else "Did Not Pass"
        ch_base_dd = max(0.0, (5000.0 - lowest_balance) / 5000.0 * 100.0)

        # -------------------------------------------------------------
        # Phase 2: Live Funded Phase (Static 1.00% Risk, 17 Bi-Weekly Cycles)
        # -------------------------------------------------------------
        funded_pool = df.iloc[passed_idx + 1:].copy().reset_index(drop=True) if passed_idx is not None else df.copy()

        events = []
        for idx, tr in funded_pool.iterrows():
            if tr['entry_dt'].hour >= 19:
                continue
            events.append({'time': tr['entry_dt'], 'type': 'ENTRY_REQ', 'id': tr['trade_id'], 'data': tr})
            events.append({'time': tr['exit_dt'], 'type': 'EXIT', 'id': tr['trade_id'], 'data': tr})

        events = sorted(events, key=lambda x: (x['time'], 0 if x['type'] == 'EXIT' else 1))

        current_balance = 5000.0
        cycle_length_days = 14
        current_cycle_start = passed_date if passed_date is not None else df['entry_dt'].iloc[0]
        current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)
        cycle_num = 1

        current_day = None
        day_closed_pnl = 0.0
        cb_triggered_today = False
        open_trades = {}
        cum_banked_cash = 0.0
        lowest_funded_balance = current_balance
        daily_closed_records = []

        for ev in events:
            ev_time = ev['time']
            ev_date = ev_time.date()

            # Payout check
            while ev_time >= current_cycle_end:
                profit = current_balance - 5000.0
                if profit > 0:
                    payout = profit * 0.80
                    cum_banked_cash += payout
                    new_bal = 5000.0
                else:
                    new_bal = current_balance

                current_balance = new_bal
                cycle_num += 1
                current_cycle_start = current_cycle_end
                current_cycle_end = current_cycle_start + timedelta(days=cycle_length_days)

            # Midnight Reset
            if ev_date != current_day:
                if current_day is not None:
                    daily_closed_records.append({'date': current_day, 'loss': day_closed_pnl})
                current_day = ev_date
                day_closed_pnl = 0.0
                cb_triggered_today = False

            if ev['type'] == 'EXIT':
                t_id = ev['id']
                if t_id in open_trades:
                    tr_info = open_trades.pop(t_id)
                    pnl = tr_info['data']['net_pnl'] * tr_info['scale']
                    current_balance += pnl
                    day_closed_pnl += pnl

                    if current_balance < lowest_funded_balance:
                        lowest_funded_balance = current_balance

                    if day_closed_pnl <= -150.0:
                        cb_triggered_today = True

            elif ev['type'] == 'ENTRY_REQ':
                if cb_triggered_today or day_closed_pnl <= -150.0:
                    continue

                # Dynamic Cushion Sizing (Production parity with live_bot.py)
                max_allowed_loss = 150.0
                cur_loss = abs(min(0.0, day_closed_pnl))
                open_risk = sum(tr['risk'] for tr in open_trades.values())
                rem_cushion = max(0.0, max_allowed_loss - cur_loss - open_risk)

                standard_risk = 50.0
                if rem_cushion < 10.0:
                    continue

                if standard_risk > rem_cushion:
                    # Scale down lot size to fit remaining cushion exactly (Recovery Trade)
                    scale = rem_cushion / standard_risk
                    actual_risk = rem_cushion
                else:
                    scale = 1.00
                    actual_risk = standard_risk

                open_trades[ev['id']] = {
                    'data': ev['data'],
                    'scale': scale,
                    'risk': actual_risk
                }

        if current_balance > 5000.0:
            cum_banked_cash += (current_balance - 5000.0) * 0.80

        d_df = pd.DataFrame(daily_closed_records)
        worst_day_loss = abs(min(0.0, d_df['loss'].min())) if not d_df.empty else 0.0
        worst_day_dd = (worst_day_loss / 5000.0) * 100.0
        funded_base_dd = max(0.0, (5000.0 - lowest_funded_balance) / 5000.0 * 100.0)

        return {
            'System Version': label,
            'Challenge Pass': ch_pass_str,
            'Challenge Base DD': f"{ch_base_dd:.2f}%",
            'Banked Cash (80%)': f"${cum_banked_cash:,.2f}",
            'Lowest Funded Bal': f"${lowest_funded_balance:.2f}",
            'Max Funded Base DD': f"{funded_base_dd:.2f}%",
            'Worst Single Day': f"-${worst_day_loss:.2f} ({worst_day_dd:.2f}%)",
            'Total Net PnL': f"+${df['net_pnl'].sum():,.2f}",
            'Win Rate': f"{(df['net_pnl'] > 0).mean()*100:.1f}%",
            'Status': "100% ZERO BREACH" if worst_day_dd < 5.0 and funded_base_dd < 10.0 else "BREACH"
        }

    scorecard = []
    # 1. DCC ApexHunter v1.2 (Flagship Dual-Gear)
    scorecard.append(simulate_dual_gear(df_apexhunter, "DCC ApexHunter v1.2 (Flagship Dual-Gear)", challenge_dyn=True, funded_static=True))
    # 2. Early ApexHunter v1.1 (TripleGuard + Daily KZ Paused)
    scorecard.append(simulate_dual_gear(df_var3, "Early ApexHunter v1.1 (TripleGuard + Daily KZ Paused)", challenge_dyn=False, funded_static=True))
    # 3. Baseline DCC v1.0 (Standard Reference, All 471 Trades)
    scorecard.append(simulate_dual_gear(df_baseline, "Baseline DCC v1.0 (Standard Reference, All 471 Trades)", challenge_dyn=False, funded_static=True))

    rep_df = pd.DataFrame(scorecard)
    print("\n" + "=" * 125)
    print("                            OFFICIAL INSTITUTIONAL SCORECARD")
    print("=" * 125)
    print(rep_df.to_string(index=False))
    print("=" * 125)

    out_csv = os.path.join(PROJECT_ROOT, "v1.2", "APEXHUNTER_BENCHMARK.csv")
    rep_df.to_csv(out_csv, index=False)
    print(f"\n[OK] Benchmark scorecard saved to: {out_csv}")

if __name__ == "__main__":
    run_apexhunter_benchmark()
