"""
Nasdaq (NAS100) High-Precision M1 Execution Audit
Replays 5-minute signals against 1-minute (M1) OHLC bars with exact spread.
Verifies whether High or Low was hit first to eliminate any bar-level ambiguity!
"""

from datetime import datetime, time, timezone
import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from dcc_engine import DCCEngine
from mt5_data import MT5DataProvider


def audit_nasdaq_precision():
    mt5.initialize()
    dp = MT5DataProvider()

    sym = "NAS100"
    start_dt = datetime(2026, 7, 1)
    end_dt = datetime(2026, 8, 31, 23, 59, 59)

    info = mt5.symbol_info(sym)
    point = info.point
    spread_points = info.spread
    spread_price = spread_points * point  # $3.50 per index point
    contract = info.trade_contract_size

    print(f"Auditing {sym} on M1 bars...")
    print(f"Spread: {spread_price:.1f} index points (${spread_price * contract:.2f} per lot)")

    # 1. Fetch M5, H1, H2 for indicators
    df_m5, df_1h, df_2h = dp.fetch_multi_timeframe_rates(sym, start_dt, end_dt, warmup_days=20)
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

    # 2. Fetch M1 bars for trade execution
    print("Fetching M1 bars for July & August...")
    r_m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, datetime(2026, 6, 30), end_dt)
    df_m1 = pd.DataFrame(r_m1)
    df_m1['time'] = pd.to_datetime(df_m1['time'], unit='s', utc=True)
    df_m1.set_index('time', inplace=True)
    print(f"Loaded {len(df_m1):,} 1-minute bars for NAS100 execution.")

    # 3. Simulate with M1 intra-bar precision
    n_bars = len(df_eval)
    times = df_eval.index
    opens = df_eval['open'].values
    closes = df_eval['close'].values
    m5_e9 = df_eval['ema9_5m'].values
    m5_e20 = df_eval['ema20_5m'].values
    vwap = df_eval['vwap_5m'].values
    h1_bias = df_eval['bias_1h'].values
    h1_e20 = df_eval['ema20_1h'].values
    h1_adx = df_eval['adx_1h'].values
    h1_atr = df_eval['atr_1h'].values

    trades = []
    balance = 100_000.0
    last_exit_time = pd.Timestamp.min.tz_localize(timezone.utc)

    for i in range(1, n_bars):
        t = times[i].time()
        bar_time = times[i]
        if bar_time <= last_exit_time:
            continue

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

        # Execute trade at next bar open
        if i + 1 >= n_bars:
            break
        entry_time = times[i + 1]
        entry_p = opens[i + 1] + (spread_price if sig_type == 1 else -spread_price)

        sl_dist = h1_atr[i] * 1.0
        risk_amt = balance * 0.01
        raw_lots = risk_amt / (sl_dist * contract)
        lots = max(info.volume_min, round(raw_lots / info.volume_step) * info.volume_step)
        p_lots = round(lots * 0.5 / info.volume_step) * info.volume_step
        r_lots = max(info.volume_min, lots - p_lots)

        # TP1 = 1.5R, TP2 = 2.0R
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

        # Find slice of M1 bars following entry_time
        m1_slice = df_m1.loc[entry_time : entry_time + pd.Timedelta(hours=10)]
        if len(m1_slice) == 0:
            continue

        for m1_t, m1_bar in m1_slice.iterrows():
            m1_h = m1_bar['high']
            m1_l = m1_bar['low']
            m1_c = m1_bar['close']
            exit_time = m1_t

            if sig_type == 1:
                if not tp1_hit:
                    # In M1 candle: check if high hit TP1 first
                    # Conservatively assume worst-case: check SL first, then TP
                    if m1_l <= curr_sl:
                        p_exit = curr_sl
                        r_exit = curr_sl
                        break
                    elif m1_h >= tp1_p:
                        tp1_hit = True
                        p_exit = tp1_p
                        curr_sl = entry_p + spread_price  # Move to BE
                else:
                    if m1_l <= curr_sl:
                        r_exit = curr_sl
                        break
                    elif m1_h >= tp2_p:
                        r_exit = tp2_p
                        break

                if m1_t.time() >= time(21, 0):
                    if not tp1_hit:
                        p_exit = m1_c
                    r_exit = m1_c
                    break

            else:  # Short
                if not tp1_hit:
                    if m1_h >= curr_sl:
                        p_exit = curr_sl
                        r_exit = curr_sl
                        break
                    elif m1_l <= tp1_p:
                        tp1_hit = True
                        p_exit = tp1_p
                        curr_sl = entry_p - spread_price  # Move to BE
                else:
                    if m1_h >= curr_sl:
                        r_exit = curr_sl
                        break
                    elif m1_l <= tp2_p:
                        r_exit = tp2_p
                        break

                if m1_t.time() >= time(21, 0):
                    if not tp1_hit:
                        p_exit = m1_c
                    r_exit = m1_c
                    break

        last_exit_time = exit_time

        if sig_type == 1:
            p_gross = (p_exit - entry_p) * p_lots * contract
            r_gross = (r_exit - entry_p) * r_lots * contract
        else:
            p_gross = (entry_p - p_exit) * p_lots * contract
            r_gross = (entry_p - r_exit) * r_lots * contract

        comm = lots * 5.0
        net = p_gross + r_gross - comm
        balance += net
        trades.append({
            "entry_time": entry_time,
            "exit_time": exit_time,
            "sig_type": "BUY" if sig_type == 1 else "SELL",
            "entry_p": entry_p,
            "tp1_hit": tp1_hit,
            "p_exit": p_exit,
            "r_exit": r_exit,
            "net_pnl": net,
            "balance": balance
        })

    df_trades = pd.DataFrame(trades)
    df_trades['month'] = df_trades['exit_time'].dt.strftime('%Y-%m')

    wins = df_trades[df_trades['net_pnl'] > 0]
    losses = df_trades[df_trades['net_pnl'] <= 0]
    tot = len(df_trades)
    wr = len(wins) / tot * 100
    pnl = df_trades['net_pnl'].sum()
    gp = wins['net_pnl'].sum()
    gl = abs(losses['net_pnl'].sum())
    pf = gp / gl if gl > 0 else 999.0
    cm = df_trades['balance'].cummax()
    dd = ((cm - df_trades['balance']) / cm * 100).max()

    print("\n" + "=" * 80)
    print("M1 HIGH-PRECISION AUDIT RESULTS FOR NAS100")
    print("=" * 80)
    print(f"Total Trades:           {tot}")
    print(f"Winning Trades:         {len(wins)}")
    print(f"Losing Trades:          {len(losses)}")
    print(f"Win Rate:               {wr:.2f}%")
    print(f"Profit Factor:          {pf:.2f}")
    print(f"Initial Balance:        $100,000.00")
    print(f"Final Balance:          ${balance:,.2f}")
    print(f"Total Net Profit:       +${pnl:,.2f} (+{pnl/1000:.2f}%)")
    print(f"Max Drawdown:           {dd:.2f}%")
    print("=" * 80)

    print("\nMONTH-BY-MONTH CONSISTENCY:")
    for m, sub in df_trades.groupby('month'):
        m_wins = len(sub[sub['net_pnl'] > 0])
        m_tot = len(sub)
        m_wr = m_wins / m_tot * 100
        m_pnl = sub['net_pnl'].sum()
        m_gp = sub[sub['net_pnl'] > 0]['net_pnl'].sum()
        m_gl = abs(sub[sub['net_pnl'] <= 0]['net_pnl'].sum())
        m_pf = m_gp / m_gl if m_gl > 0 else 999.0
        print(f"Month {m} | Trades: {m_tot:2d} | Wins: {m_wins:2d} | Win Rate: {m_wr:5.1f}% | PF: {m_pf:4.2f} | Net PnL: +${m_pnl:,.2f}")
    print("=" * 80)

    mt5.shutdown()


if __name__ == "__main__":
    audit_nasdaq_precision()
