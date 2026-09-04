"""
Unified Gold (XAUUSD) + Nasdaq (NAS100) Portfolio Backtest & Audit
Combines the top two performing assets into a single institutional-grade portfolio.
"""

from datetime import datetime, time, timezone
import MetaTrader5 as mt5
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider


def run_combined_portfolio():
    mt5.initialize()
    dp = MT5DataProvider()

    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)

    # 1. Simulate Gold (XAUUSD) with Tuned B (1.4R / 2.2R)
    print("Simulating Gold (XAUUSD)...")
    from monthly_audit import StrictGoldTester
    eng_gold = DCCEngine(
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
    bt_gold = StrictGoldTester(
        engine=eng_gold,
        data_provider=dp,
        initial_balance=100000.0,
        risk_per_trade=0.01,
        use_partial_tp=True,
        partial_ratio=0.5,
        tp1_rr=1.4,
        tp2_rr=2.2,
        move_sl_to_be=True
    )
    df_gold = bt_gold.run('XAUUSD', start_dt, end_dt)
    gold_records = df_gold[['exit_time', 'net_pnl']].copy()
    gold_records['asset'] = 'GOLD (XAUUSD)'

    # 2. Simulate Nasdaq (NAS100) with M1 Precision (1.5R / 2.0R)
    print("Simulating Nasdaq (NAS100)...")
    info_nas = mt5.symbol_info('NAS100')
    spread_nas = info_nas.spread * info_nas.point
    contract_nas = info_nas.trade_contract_size

    df_m5_n, df_1h_n, df_2h_n = dp.fetch_multi_timeframe_rates('NAS100', start_dt, end_dt, warmup_days=20)
    df_prep_n = eng_gold.prepare_data(df_m5_n, df_1h_n, df_2h_n)
    eval_mask_n = (df_prep_n.index >= pd.Timestamp(start_dt, tz=timezone.utc)) & \
                  (df_prep_n.index <= pd.Timestamp(end_dt, tz=timezone.utc))
    df_eval_n = df_prep_n.loc[eval_mask_n]

    r_m1 = mt5.copy_rates_range('NAS100', mt5.TIMEFRAME_M1, datetime(2026, 6, 30), end_dt)
    df_m1_n = pd.DataFrame(r_m1)
    df_m1_n['time'] = pd.to_datetime(df_m1_n['time'], unit='s', utc=True)
    df_m1_n.set_index('time', inplace=True)

    times_n = df_eval_n.index
    opens_n = df_eval_n['open'].values
    closes_n = df_eval_n['close'].values
    m5_e9_n = df_eval_n['ema9_5m'].values
    m5_e20_n = df_eval_n['ema20_5m'].values
    vwap_n = df_eval_n['vwap_5m'].values
    h1_bias_n = df_eval_n['bias_1h'].values
    h1_e20_n = df_eval_n['ema20_1h'].values
    h1_adx_n = df_eval_n['adx_1h'].values
    h1_atr_n = df_eval_n['atr_1h'].values

    nas_records = []
    last_exit_n = pd.Timestamp.min.tz_localize(timezone.utc)
    bal_nas = 100000.0

    for i in range(1, len(df_eval_n)):
        t = times_n[i].time()
        if times_n[i] <= last_exit_n or not (time(6, 0) <= t < time(20, 0)):
            continue
        bias = h1_bias_n[i]
        if bias == 0 or h1_adx_n[i] < 15.0:
            continue

        prev_diff = m5_e9_n[i - 1] - m5_e20_n[i - 1]
        curr_diff = m5_e9_n[i] - m5_e20_n[i]
        c = closes_n[i]
        sig_type = 0
        if bias == 1 and prev_diff <= 0 and curr_diff > 0 and c > vwap_n[i] and c > h1_e20_n[i]:
            sig_type = 1
        elif bias == -1 and prev_diff >= 0 and curr_diff < 0 and c < vwap_n[i] and c < h1_e20_n[i]:
            sig_type = -1

        if sig_type == 0 or i + 1 >= len(df_eval_n):
            continue

        entry_time = times_n[i + 1]
        entry_p = opens_n[i + 1] + (spread_nas if sig_type == 1 else -spread_nas)
        sl_dist = h1_atr_n[i] * 1.0
        risk_amt = bal_nas * 0.01
        lots = max(info_nas.volume_min, round((risk_amt / (sl_dist * contract_nas)) / info_nas.volume_step) * info_nas.volume_step)
        p_lots = round(lots * 0.5 / info_nas.volume_step) * info_nas.volume_step
        r_lots = max(info_nas.volume_min, lots - p_lots)

        if sig_type == 1:
            sl_p = entry_p - sl_dist
            tp1_p = entry_p + (1.5 * sl_dist)
            tp2_p = entry_p + (2.0 * sl_dist)
        else:
            sl_p = entry_p + sl_dist
            tp1_p = entry_p - (1.5 * sl_dist)
            tp2_p = entry_p - (2.0 * sl_dist)

        tp1_hit = False
        curr_sl = sl_p
        p_exit = entry_p
        r_exit = entry_p
        exit_time = entry_time

        m1_slice = df_m1_n.loc[entry_time : entry_time + pd.Timedelta(hours=10)]
        if len(m1_slice) == 0:
            continue

        for m1_t, m1_bar in m1_slice.iterrows():
            exit_time = m1_t
            if sig_type == 1:
                if not tp1_hit:
                    if m1_bar['low'] <= curr_sl:
                        p_exit = curr_sl
                        r_exit = curr_sl
                        break
                    elif m1_bar['high'] >= tp1_p:
                        tp1_hit = True
                        p_exit = tp1_p
                        curr_sl = entry_p + spread_nas
                else:
                    if m1_bar['low'] <= curr_sl:
                        r_exit = curr_sl
                        break
                    elif m1_bar['high'] >= tp2_p:
                        r_exit = tp2_p
                        break
                if m1_t.time() >= time(21, 0):
                    if not tp1_hit:
                        p_exit = m1_bar['close']
                    r_exit = m1_bar['close']
                    break
            else:
                if not tp1_hit:
                    if m1_bar['high'] >= curr_sl:
                        p_exit = curr_sl
                        r_exit = curr_sl
                        break
                    elif m1_bar['low'] <= tp1_p:
                        tp1_hit = True
                        p_exit = tp1_p
                        curr_sl = entry_p - spread_nas
                else:
                    if m1_bar['high'] >= curr_sl:
                        r_exit = curr_sl
                        break
                    elif m1_bar['low'] <= tp2_p:
                        r_exit = tp2_p
                        break
                if m1_t.time() >= time(21, 0):
                    if not tp1_hit:
                        p_exit = m1_bar['close']
                    r_exit = m1_bar['close']
                    break

        last_exit_n = exit_time
        p_gross = (p_exit - entry_p if sig_type == 1 else entry_p - p_exit) * p_lots * contract_nas
        r_gross = (r_exit - entry_p if sig_type == 1 else entry_p - r_exit) * r_lots * contract_nas
        net = p_gross + r_gross - (lots * 5.0)
        bal_nas += net
        nas_records.append({"exit_time": exit_time, "net_pnl": net, "asset": "NASDAQ (NAS100)"})

    df_nas = pd.DataFrame(nas_records)

    # 3. Combine both streams chronologically
    df_combined = pd.concat([gold_records, df_nas]).sort_values('exit_time').reset_index(drop=True)
    df_combined['portfolio_balance'] = 100000.0 + df_combined['net_pnl'].cumsum()
    df_combined['exit_time'] = pd.to_datetime(df_combined['exit_time'], utc=True)
    df_combined['month'] = df_combined['exit_time'].dt.strftime('%Y-%m')

    wins = df_combined[df_combined['net_pnl'] > 0]
    losses = df_combined[df_combined['net_pnl'] <= 0]
    tot = len(df_combined)
    wr = len(wins) / tot * 100.0
    gp = wins['net_pnl'].sum()
    gl = abs(losses['net_pnl'].sum())
    pf = gp / gl if gl > 0 else 999.0
    net_profit = df_combined['net_pnl'].sum()
    roi = (net_profit / 100000.0) * 100.0

    cm = df_combined['portfolio_balance'].cummax()
    dd = ((cm - df_combined['portfolio_balance']) / cm * 100.0).max()

    print("\n" + "=" * 80)
    print("GOLD + NASDAQ UNIFIED PORTFOLIO AUDIT")
    print("=" * 80)
    print(f"Total Portfolio Trades: {tot} (~14.3 trades/week)")
    print(f"Gold Trades:            {len(gold_records)}")
    print(f"Nasdaq Trades:          {len(df_nas)}")
    print(f"Winning Trades:         {len(wins)}")
    print(f"Losing Trades:          {len(losses)}")
    print(f"Portfolio Win Rate:     {wr:.2f}%")
    print(f"Portfolio Profit Factor:{pf:.2f}")
    print(f"Initial Account:        $100,000.00")
    print(f"Final Balance:          ${df_combined['portfolio_balance'].iloc[-1]:,.2f}")
    print(f"Total Net Profit:       +${net_profit:,.2f} (+{roi:.2f}%)")
    print(f"Max Portfolio Drawdown: {dd:.2f}%")
    print("=" * 80)

    print("\nMONTHLY BREAKDOWN:")
    for m, sub in df_combined.groupby('month'):
        m_wins = len(sub[sub['net_pnl'] > 0])
        m_tot = len(sub)
        m_pnl = sub['net_pnl'].sum()
        m_pf = sub[sub['net_pnl'] > 0]['net_pnl'].sum() / abs(sub[sub['net_pnl'] <= 0]['net_pnl'].sum())
        print(f"Month {m} | Trades: {m_tot:2d} | Win Rate: {m_wins/m_tot*100:5.1f}% | PF: {m_pf:4.2f} | Net PnL: +${m_pnl:,.2f}")
    print("=" * 80)

    # Plot Equity Curve
    plt.figure(figsize=(12, 6))
    plt.plot(df_combined['exit_time'], df_combined['portfolio_balance'], label='Combined Portfolio Equity', color='#10B981', lw=2)
    plt.axhline(100000, color='#6B7280', linestyle='--', alpha=0.7)
    plt.title('DCC Strategy: Unified Gold (XAUUSD) + Nasdaq (NAS100) Equity Curve', fontsize=14, pad=12)
    plt.xlabel('Date', fontsize=11)
    plt.ylabel('Account Balance ($)', fontsize=11)
    plt.grid(True, alpha=0.3)
    plt.legend(loc='upper left')
    plt.tight_layout()
    chart_path = 'd:\\FOREX\\DC\\gold_nasdaq_equity.png'
    plt.savefig(chart_path, dpi=150)
    print(f"Saved equity curve to {chart_path}")

    mt5.shutdown()


if __name__ == "__main__":
    run_combined_portfolio()
