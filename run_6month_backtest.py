"""
DCC Strategy 6-Month High-Precision Tick Backtest (Jan - Jun 2026)
Features:
- Base Balance: $5,000.00 | Risk: 1.0% per trade (dynamically compounded)
- 1.4R / 1.5R Partial Profit Booking (50% position close)
- Breakeven Stop Loss (SL moved to entry + spread upon TP1)
- 2.0R / 2.2R Runner Target
- Exact tick-by-tick bid/ask replay from partitioned parquet cache in data_cache/
- Month-by-Month Consistency Breakdown (Jan, Feb, Mar, Apr, May, Jun 2026)
- Comprehensive trade logs and equity curve visualizations
"""

import argparse
import os
import sys
import time
from datetime import datetime, time as dtime, timezone
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from dcc_engine import DCCEngine, SignalType, TradeSignal
from mt5_data import MT5DataProvider


class FastTickBacktester:
    def __init__(
        self,
        symbol: str = "XAUUSD",
        cache_dir: Optional[str] = None,
        initial_balance: float = 5000.0,
        risk_per_trade: float = 0.01,
        tp1_rr: float = 1.4,
        tp2_rr: float = 2.2,
        partial_ratio: float = 0.5,
        atr_sl_multiplier: float = 0.9,
        adx_min_threshold: float = 15.0,
        commission_per_lot: float = 5.0,
    ):
        self.symbol = symbol
        if cache_dir is None:
            self.cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")
        else:
            self.cache_dir = cache_dir
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.tp1_rr = tp1_rr
        self.tp2_rr = tp2_rr
        self.partial_ratio = partial_ratio
        self.atr_sl_multiplier = atr_sl_multiplier
        self.adx_min_threshold = adx_min_threshold
        self.commission_per_lot = commission_per_lot

        # Configure Engine
        self.engine = DCCEngine(
            adx_min_threshold=self.adx_min_threshold,
            atr_sl_multiplier=self.atr_sl_multiplier,
            check_2h_room=False,
            use_daily_open_filter=False,
            london_session_start=dtime(6, 0),
            london_session_end=dtime(21, 0),
            ny_session_start=dtime(6, 0),
            ny_session_end=dtime(21, 0),
            avoid_monday_london=False,
            avoid_friday_ny=False,
            avoid_nfp_friday=False,
            eod_exit_time=dtime(21, 0)
        )
        self.dp = MT5DataProvider(cache_dir=self.cache_dir)
        self.trades: List[Dict] = []

    def get_symbol_specs(self) -> Dict:
        if not mt5.initialize():
            mt5.initialize()
        mt5.symbol_select(self.symbol, True)
        info = mt5.symbol_info(self.symbol)
        if info is None:
            if "NAS" in self.symbol or "100" in self.symbol:
                return {
                    "contract_size": 10.0,
                    "volume_min": 0.01,
                    "volume_step": 0.01,
                    "point": 0.1,
                    "digits": 1,
                    "spread_price": 3.4
                }
            else:
                return {
                    "contract_size": 100.0,
                    "volume_min": 0.01,
                    "volume_step": 0.01,
                    "point": 0.01,
                    "digits": 2,
                    "spread_price": 0.33
                }
        return {
            "contract_size": info.trade_contract_size,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "point": info.point,
            "digits": info.digits,
            "spread_price": info.spread * info.point
        }

    def load_day_ticks(self, target_date: datetime.date) -> Optional[pd.DataFrame]:
        date_str = target_date.strftime("%Y%m%d")
        cache_file = os.path.join(self.cache_dir, f"ticks_{self.symbol}_{date_str}.parquet")
        if os.path.exists(cache_file):
            try:
                df = pd.read_parquet(cache_file)
                if not pd.api.types.is_datetime64_any_dtype(df['time_dt']):
                    df['time_dt'] = pd.to_datetime(df['time_dt'], utc=True)
                return df
            except Exception as e:
                print(f"[Warn] Failed reading {cache_file}: {e}")
        return None

    def run(
        self,
        start_date: datetime = datetime(2026, 1, 2),
        end_date: datetime = datetime(2026, 6, 30, 23, 59, 59),
    ) -> pd.DataFrame:
        specs = self.get_symbol_specs()
        contract_size = specs["contract_size"]
        vol_step = specs["volume_step"]
        vol_min = specs["volume_min"]

        print(f"\n=======================================================")
        print(f"RUNNING 6-MONTH TICK BACKTEST: {self.symbol}")
        print(f"Window: {start_date.date()} -> {end_date.date()}")
        print(f"Initial Balance: ${self.initial_balance:,.2f} | Risk: {self.risk_per_trade*100:.1f}%")
        print(f"Specs: Contract={contract_size} | VolMin={vol_min} | VolStep={vol_step}")
        print(f"Execution: TP1={self.tp1_rr}R (50%) + BE | Runner TP2={self.tp2_rr}R")
        print(f"=======================================================")

        # 1. Fetch bars from MT5
        df_m5, df_1h, df_2h = self.dp.fetch_multi_timeframe_rates(
            self.symbol, start_date, end_date, warmup_days=20
        )

        # 2. Prepare indicators
        df_prepared = self.engine.prepare_data(df_m5, df_1h, df_2h)
        eval_mask = (df_prepared.index >= pd.Timestamp(start_date, tz=timezone.utc)) & \
                    (df_prepared.index <= pd.Timestamp(end_date, tz=timezone.utc))
        df_eval = df_prepared.loc[eval_mask]
        n_bars = len(df_eval)
        print(f"[{self.symbol}] Rapidly scanning {n_bars} 5M bars with NumPy arrays...")

        # Extract NumPy arrays for high-speed evaluation (sub-second)
        times = df_eval.index
        closes = df_eval['close'].values
        m5_e9 = df_eval['ema9_5m'].values
        m5_e20 = df_eval['ema20_5m'].values
        vwap = df_eval['vwap_5m'].values
        h1_bias = df_eval['bias_1h'].values
        h1_e20 = df_eval['ema20_1h'].values
        h1_adx = df_eval['adx_1h'].values
        h1_atr = df_eval['atr_1h'].values

        trade_id = 0
        i = 1
        curr_date = None
        day_ticks: Optional[pd.DataFrame] = None
        missing_tick_days = set()

        while i < n_bars:
            bar_time = times[i]
            t = bar_time.time()

            # Session check (06:00 to 21:00 UTC)
            if not (dtime(6, 0) <= t < dtime(21, 0)):
                i += 1
                continue

            bias = h1_bias[i]
            if bias == 0 or np.isnan(bias):
                i += 1
                continue

            adx = h1_adx[i]
            if np.isnan(adx) or adx < self.adx_min_threshold:
                i += 1
                continue

            atr = h1_atr[i]
            if np.isnan(atr) or atr <= 0:
                i += 1
                continue

            prev_diff = m5_e9[i - 1] - m5_e20[i - 1]
            curr_diff = m5_e9[i] - m5_e20[i]
            c = closes[i]

            sig_type = 0
            # Bullish PFG: Long
            if bias == 1 and prev_diff <= 0 and curr_diff > 0:
                if c > vwap[i] and c > h1_e20[i]:
                    sig_type = 1
            # Bearish PFG: Short
            elif bias == -1 and prev_diff >= 0 and curr_diff < 0:
                if c < vwap[i] and c < h1_e20[i]:
                    sig_type = -1

            if sig_type != 0:
                sl_distance = self.atr_sl_multiplier * atr
                tp_distance = self.tp1_rr * sl_distance
                
                t_date = bar_time.date()
                if curr_date != t_date:
                    curr_date = t_date
                    day_ticks = self.load_day_ticks(curr_date)
                    if day_ticks is None or len(day_ticks) == 0:
                        missing_tick_days.add(curr_date)

                if day_ticks is not None and len(day_ticks) > 0:
                    trade_res, exit_time = self._simulate_trade_fast(
                        trade_id=trade_id + 1,
                        sig_type=sig_type,
                        c_price=c,
                        sl_distance=sl_distance,
                        bar_close_time=bar_time,
                        day_ticks=day_ticks,
                        specs=specs,
                        adx=adx,
                        atr=atr
                    )

                    if trade_res is not None:
                        trade_id += 1
                        self.trades.append(trade_res)
                        self.balance = trade_res["account_balance"]

                        # Advance index past exit_time (or at least 15 mins)
                        adv_time = min(exit_time, bar_time + pd.Timedelta(minutes=15))
                        while i < n_bars and times[i] <= adv_time:
                            i += 1
                        continue

            i += 1

        if missing_tick_days:
            print(f"[Notice] Missing tick data for {len(missing_tick_days)} trading days (skipped).")

        df_trades = pd.DataFrame(self.trades)
        if len(df_trades) > 0:
            df_trades['month'] = pd.to_datetime(df_trades['exit_time'], utc=True).dt.strftime('%Y-%m')
        return df_trades

    def _simulate_trade_fast(
        self,
        trade_id: int,
        sig_type: int,
        c_price: float,
        sl_distance: float,
        bar_close_time: datetime,
        day_ticks: pd.DataFrame,
        specs: Dict,
        adx: float,
        atr: float
    ) -> Tuple[Optional[Dict], datetime]:
        mask = day_ticks['time_dt'] >= bar_close_time
        sub_ticks = day_ticks.loc[mask]
        if len(sub_ticks) == 0:
            return None, bar_close_time

        first_tick = sub_ticks.iloc[0]
        entry_time = first_tick['time_dt']
        contract_size = specs["contract_size"]
        vol_step = specs["volume_step"]
        vol_min = specs["volume_min"]

        # Fill at exact market quote
        if sig_type == 1:
            actual_entry = float(first_tick['ask'])
            entry_spread = float(first_tick['ask'] - first_tick['bid'])
            direction = "BUY"
            sl_p = actual_entry - sl_distance
            tp1_p = actual_entry + (self.tp1_rr * sl_distance)
            tp2_p = actual_entry + (self.tp2_rr * sl_distance)
        else:
            actual_entry = float(first_tick['bid'])
            entry_spread = float(first_tick['ask'] - first_tick['bid'])
            direction = "SELL"
            sl_p = actual_entry + sl_distance
            tp1_p = actual_entry - (self.tp1_rr * sl_distance)
            tp2_p = actual_entry - (self.tp2_rr * sl_distance)

        # Dynamic Position Sizing for $5k base balance
        risk_amount = self.balance * self.risk_per_trade
        total_lots = risk_amount / (sl_distance * contract_size)
        
        # Round lots to volume_step, min 0.02 to allow 50/50 split
        min_split_lot = max(vol_min * 2, 0.02)
        total_lots = max(min_split_lot, round(total_lots / vol_step) * vol_step)
        total_lots = round(total_lots, 2)

        partial_lots = max(vol_min, round((total_lots * self.partial_ratio) / vol_step) * vol_step)
        partial_lots = round(partial_lots, 2)
        runner_lots = round(max(vol_min, total_lots - partial_lots), 2)

        curr_sl = sl_p
        tp1_hit = False
        tp2_hit = False
        be_hit = False
        p_exit = actual_entry
        r_exit = actual_entry
        exit_time = entry_time
        exit_reason = "EOD"

        # Replay ticks
        tick_arr = sub_ticks[['time_dt', 'bid', 'ask']].values

        for row in tick_arr:
            t_dt = row[0]
            bid = row[1]
            ask = row[2]
            exit_time = t_dt

            # BUY
            if sig_type == 1:
                if not tp1_hit:
                    # SL hit before TP1
                    if bid <= curr_sl:
                        p_exit = curr_sl
                        r_exit = curr_sl
                        exit_reason = "SL"
                        break
                    # TP1 hit
                    elif bid >= tp1_p:
                        tp1_hit = True
                        p_exit = tp1_p
                        curr_sl = actual_entry + entry_spread  # Breakeven Stop
                else:
                    # Runner check
                    if bid <= curr_sl:
                        r_exit = curr_sl
                        be_hit = True
                        exit_reason = "TP1_THEN_BE"
                        break
                    elif bid >= tp2_p:
                        r_exit = tp2_p
                        tp2_hit = True
                        exit_reason = "FULL_TP2"
                        break

                # EOD Exit
                if t_dt.time() >= dtime(21, 0):
                    if not tp1_hit:
                        p_exit = bid
                    r_exit = bid
                    exit_reason = "EOD_EXIT"
                    break

            # SELL
            else:
                if not tp1_hit:
                    # SL hit before TP1
                    if ask >= curr_sl:
                        p_exit = curr_sl
                        r_exit = curr_sl
                        exit_reason = "SL"
                        break
                    # TP1 hit
                    elif ask <= tp1_p:
                        tp1_hit = True
                        p_exit = tp1_p
                        curr_sl = actual_entry - entry_spread  # Breakeven Stop
                else:
                    # Runner check
                    if ask >= curr_sl:
                        r_exit = curr_sl
                        be_hit = True
                        exit_reason = "TP1_THEN_BE"
                        break
                    elif ask <= tp2_p:
                        r_exit = tp2_p
                        tp2_hit = True
                        exit_reason = "FULL_TP2"
                        break

                # EOD Exit
                if t_dt.time() >= dtime(21, 0):
                    if not tp1_hit:
                        p_exit = ask
                    r_exit = ask
                    exit_reason = "EOD_EXIT"
                    break

        # Calculate PnL
        if sig_type == 1:
            partial_pnl = (p_exit - actual_entry) * partial_lots * contract_size
            runner_pnl = (r_exit - actual_entry) * runner_lots * contract_size
        else:
            partial_pnl = (actual_entry - p_exit) * partial_lots * contract_size
            runner_pnl = (actual_entry - r_exit) * runner_lots * contract_size

        comm = total_lots * self.commission_per_lot
        net_pnl = partial_pnl + runner_pnl - comm
        self.balance += net_pnl
        duration_m = (exit_time - entry_time).total_seconds() / 60.0

        return {
            "trade_id": trade_id,
            "symbol": self.symbol,
            "direction": direction,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "duration_m": duration_m,
            "planned_entry": c_price,
            "actual_entry": actual_entry,
            "entry_spread": entry_spread,
            "total_lots": total_lots,
            "partial_lots": partial_lots,
            "runner_lots": runner_lots,
            "tp1_hit": tp1_hit,
            "tp2_hit": tp2_hit,
            "be_hit": be_hit,
            "exit_reason": exit_reason,
            "partial_pnl": partial_pnl,
            "runner_pnl": runner_pnl,
            "commission": comm,
            "net_pnl": net_pnl,
            "account_balance": self.balance,
            "return_pct": (net_pnl / (self.balance - net_pnl)) * 100.0,
            "adx_1h": adx,
            "atr_1h": atr
        }, exit_time


def print_performance_report(df_trades: pd.DataFrame, title: str, initial_balance: float = 5000.0):
    if len(df_trades) == 0:
        print(f"\nNo trades generated for {title}")
        return

    wins = df_trades[df_trades['net_pnl'] > 0]
    losses = df_trades[df_trades['net_pnl'] <= 0]
    tot = len(df_trades)
    wr = len(wins) / tot * 100.0
    gp = wins['net_pnl'].sum()
    gl = abs(losses['net_pnl'].sum())
    pf = gp / gl if gl > 0 else 999.0
    net_pnl = df_trades['net_pnl'].sum()
    roi = (net_pnl / initial_balance) * 100.0

    eq = initial_balance + df_trades['net_pnl'].cumsum()
    cm = eq.cummax()
    dd = ((cm - eq) / cm * 100.0).max()

    tp1_cnt = len(df_trades[df_trades['tp1_hit'] == True])
    tp2_cnt = len(df_trades[df_trades['tp2_hit'] == True])
    be_cnt = len(df_trades[df_trades['be_hit'] == True])

    print("\n" + "=" * 85)
    print(f"PERFORMANCE REPORT: {title}")
    print("=" * 85)
    print(f"Total Trades:           {tot} (~{tot/26:.1f} trades/week)")
    print(f"Winning Trades:         {len(wins)} ({wr:.1f}%)")
    print(f"Losing Trades:          {len(losses)} ({100-wr:.1f}%)")
    print(f"Trades Made Safe (TP1): {tp1_cnt} ({tp1_cnt/tot*100:.1f}%)")
    print(f"Full TP2 Runner Hit:    {tp2_cnt} ({tp2_cnt/tot*100:.1f}%)")
    print(f"Breakeven Runner Hit:   {be_cnt} ({be_cnt/tot*100:.1f}%) [Rescued from Loss!]")
    print(f"Profit Factor:          {pf:.2f}")
    print(f"Initial Account:        ${initial_balance:,.2f}")
    print(f"Final Balance:          ${eq.iloc[-1]:,.2f}")
    print(f"Total Net Profit:       +${net_pnl:,.2f} (+{roi:.2f}%)")
    print(f"Max Overall Drawdown:   {dd:.2f}%")
    print(f"Avg Trade Duration:     {df_trades['duration_m'].mean():.1f} mins")
    print("=" * 85)

    print("\nMONTH-BY-MONTH CONSISTENCY AUDIT:")
    print(f"{'Month':8s} | {'Trades':6s} | {'Wins':4s} | {'Losses':6s} | {'Win Rate':8s} | {'PF':4s} | {'Net Profit':12s} | {'ROI':7s}")
    print("-" * 75)
    for m, sub in df_trades.groupby('month'):
        m_wins = len(sub[sub['net_pnl'] > 0])
        m_losses = len(sub[sub['net_pnl'] <= 0])
        m_tot = len(sub)
        m_wr = (m_wins / m_tot) * 100.0 if m_tot > 0 else 0
        m_gp = sub[sub['net_pnl'] > 0]['net_pnl'].sum()
        m_gl = abs(sub[sub['net_pnl'] <= 0]['net_pnl'].sum())
        m_pf = m_gp / m_gl if m_gl > 0 else 999.0
        m_pnl = sub['net_pnl'].sum()
        m_roi = (m_pnl / initial_balance) * 100.0
        pnl_str = f"+${m_pnl:,.2f}" if m_pnl >= 0 else f"-${abs(m_pnl):,.2f}"
        print(f"{m:8s} | {m_tot:2d}     | {m_wins:2d}   | {m_losses:2d}     | {m_wr:5.1f}%   | {m_pf:4.2f} | {pnl_str:12s} | {m_roi:+6.2f}%")
    print("=" * 75)


def run_portfolio():
    print("\n" + "#" * 85)
    print("RUNNING 6-MONTH DUAL PORTFOLIO (GOLD + NASDAQ)")
    print("#" * 85)

    bt_gold = FastTickBacktester(
        symbol="XAUUSD",
        initial_balance=5000.0,
        risk_per_trade=0.01,
        tp1_rr=1.4,
        tp2_rr=2.2,
        atr_sl_multiplier=0.9
    )
    df_gold = bt_gold.run()

    bt_nas = FastTickBacktester(
        symbol="NAS100",
        initial_balance=5000.0,
        risk_per_trade=0.01,
        tp1_rr=1.5,
        tp2_rr=2.0,
        atr_sl_multiplier=1.0
    )
    df_nas = bt_nas.run()

    # Combine both chronologically
    df_all = pd.concat([df_gold, df_nas]).sort_values('exit_time').reset_index(drop=True)
    
    # Compute unified shared balance compounding
    curr_unified = 5000.0
    unified_bals = []
    for pnl in df_all['net_pnl']:
        curr_unified += pnl
        unified_bals.append(curr_unified)
    df_all['portfolio_balance'] = unified_bals
    df_all['month'] = pd.to_datetime(df_all['exit_time'], utc=True).dt.strftime('%Y-%m')

    # Save trades log
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base_dir, "reports", "trades_log_jan_jun_2026.csv")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    df_all.to_csv(log_path, index=False)
    print(f"\n[Saved] Complete trade log saved to {log_path}")

    # Print reports
    print_performance_report(df_gold, "GOLD (XAUUSD) 6-MONTH AUDIT", initial_balance=5000.0)
    print_performance_report(df_nas, "NASDAQ (NAS100) 6-MONTH AUDIT", initial_balance=5000.0)
    print_performance_report(df_all, "COMBINED GOLD + NASDAQ 6-MONTH PORTFOLIO", initial_balance=5000.0)

    # Plot Equity Curves
    plt.figure(figsize=(14, 7))
    plt.plot(pd.to_datetime(df_all['exit_time']), df_all['portfolio_balance'], label='Combined Portfolio ($5k Base)', color='#10B981', lw=2.2)
    if len(df_gold) > 0:
        plt.plot(pd.to_datetime(df_gold['exit_time']), 5000.0 + df_gold['net_pnl'].cumsum(), label='Gold (XAUUSD)', color='#F59E0B', lw=1.5, alpha=0.8)
    if len(df_nas) > 0:
        plt.plot(pd.to_datetime(df_nas['exit_time']), 5000.0 + df_nas['net_pnl'].cumsum(), label='Nasdaq (NAS100)', color='#3B82F6', lw=1.5, alpha=0.8)

    plt.axhline(5000.0, color='#6B7280', linestyle='--', alpha=0.7, label='Base Capital ($5,000)')
    plt.title('DCC Strategy: 6-Month Real Tick Backtest (Jan - Jun 2026)', fontsize=15, pad=12)
    plt.xlabel('Exit Date', fontsize=11)
    plt.ylabel('Account Balance ($)', fontsize=11)
    plt.grid(True, alpha=0.3)
    plt.legend(loc='upper left', fontsize=10)
    plt.tight_layout()

    chart_path = os.path.join(base_dir, "reports", "equity_curve_jan_jun_2026.png")
    plt.savefig(chart_path, dpi=150)
    print(f"[Saved] High-resolution equity curve saved to {chart_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="ALL")
    args = parser.parse_args()

    if args.symbol.upper() == "ALL" or args.symbol.lower() == "portfolio":
        run_portfolio()
    else:
        bt = FastTickBacktester(symbol=args.symbol.upper())
        df = bt.run()
        print_performance_report(df, f"{args.symbol.upper()} 6-MONTH AUDIT", initial_balance=5000.0)
